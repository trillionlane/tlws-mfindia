"""Narrow Cloud Run deployment adapter. Commands are argv, never shell strings."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from mfdataindia.review.rollout import API_SA, CALLER_SA, CONNECTION, PROJECT, REGION, SERVICE, Manifest, service_origin

REVIEW_FACTORY = 'mfdataindia.review.app:create_review_app'
RESEARCH_FACTORY = 'mfdataindia.review.app:create_research_app'
CALENDAR_FACTORY = 'mfdataindia.review.app:create_calendar_app'
ROLLING_FACTORY = 'mfdataindia.review.app:create_rolling_app'
CALCULATOR_FACTORY = 'mfdataindia.review.app:create_calculator_app'


def api_args(factory):
    if factory not in (REVIEW_FACTORY, RESEARCH_FACTORY, CALENDAR_FACTORY, ROLLING_FACTORY, CALCULATOR_FACTORY):
        raise ValueError('unsupported private API deployment profile')
    return [factory, '--factory', '--host', '0.0.0.0', '--port', '8080', '--no-access-log']


def revision_name(manifest, factory=REVIEW_FACTORY):
    api_args(factory)
    # One rollout can first establish a compatible six-route rollback target,
    # then stage the seven-route profile without reusing an immutable name or
    # manufacturing a different actor/canary solely for that second revision.
    suffix = manifest.rollout_id + {REVIEW_FACTORY:'',RESEARCH_FACTORY:'-research',CALENDAR_FACTORY:'-calendar',ROLLING_FACTORY:'-rolling',CALCULATOR_FACTORY:'-calculator'}[factory]
    return SERVICE+'-'+suffix


class Cloud:
    def __init__(self, *, account=None):
        # Explicit local operator pin never changes shared gcloud configuration.
        self.account = account

    def command(self, *args, input_text=None, json_output=False):
        argv = ['gcloud', *(['--account=' + self.account] if self.account else []), *args]
        result = subprocess.run(argv, input=input_text, text=True, capture_output=True, timeout=1800)
        if result.returncode:
            # gcloud errors can include secret-bearing arguments/payloads.
            raise RuntimeError('cloud command failed: ' + args[0])
        return json.loads(result.stdout) if json_output else result.stdout.strip()

    def secret(self, name, version):
        return self.command('secrets', 'versions', 'access', str(version), '--secret=' + name, '--project=' + PROJECT)

    def write_secret(self, name, value, account):
        secrets = self.command('secrets', 'list', '--project=' + PROJECT, '--format=json', json_output=True)
        existing = [s for s in secrets if s['name'].split('/')[-1] == name]
        if existing:
            raise ValueError('refusing existing credential replacement; reconcile explicitly')
        self.command('secrets', 'create', name, '--project=' + PROJECT, '--replication-policy=automatic', '--labels=product=mfdataindia,environment=dev')
        version = self.command('secrets', 'versions', 'add', name, '--project=' + PROJECT, '--data-file=-', '--format=json', input_text=value, json_output=True)
        self.command('secrets', 'add-iam-policy-binding', name, '--project=' + PROJECT,
                     '--member=serviceAccount:' + account, '--role=roles/secretmanager.secretAccessor', '--condition=None', '--quiet')
        return int(version['name'].split('/')[-1])

    def ensure_identity(self, account):
        policy = self.command('projects', 'get-iam-policy', PROJECT, '--format=json', json_output=True)
        for binding in policy.get('bindings', []):
            if 'serviceAccount:' + account in binding.get('members', []) and (binding['role'] != 'roles/cloudsql.client' or binding.get('condition')):
                raise ValueError('dedicated runtime identity has unexpected project privileges')
        accounts = self.command('iam', 'service-accounts', 'list', '--project=' + PROJECT, '--format=json', json_output=True)
        if not any(a['email'] == account for a in accounts):
            self.command('iam', 'service-accounts', 'create', account.split('@')[0], '--project=' + PROJECT)
        self.command('projects', 'add-iam-policy-binding', PROJECT, '--member=serviceAccount:' + account,
                     '--role=roles/cloudsql.client', '--condition=None', '--quiet')
        self.command('iam', 'service-accounts', 'add-iam-policy-binding', account, '--project=' + PROJECT,
                     '--member=serviceAccount:tlws-mf-data-deploy-dev@' + PROJECT + '.iam.gserviceaccount.com',
                     '--role=roles/iam.serviceAccountUser', '--condition=None', '--quiet')

    def service(self):
        services = self.command('run', 'services', 'list', '--project=' + PROJECT, '--region=' + REGION, '--format=json', json_output=True)
        matches = [s for s in services if s['metadata']['name'] == SERVICE]
        if len(matches) > 1:
            raise ValueError('ambiguous review service')
        return self.command('run', 'services', 'describe', SERVICE, '--project=' + PROJECT, '--region=' + REGION,
                            '--format=json', json_output=True) if matches else None

    def iam(self, *, complete):
        policy = self.command('run', 'services', 'get-iam-policy', SERVICE, '--project=' + PROJECT, '--region=' + REGION,
                              '--format=json', json_output=True)
        members = []
        for binding in policy.get('bindings', []):
            if binding['role'] == 'roles/run.invoker':
                if binding.get('condition') or not isinstance(binding.get('members'), list):
                    raise ValueError('unexpected invoker condition')
                members.extend(binding['members'])
        expected = ['serviceAccount:' + CALLER_SA]
        if len(members) != len(set(members)) or set(members) - set(expected) or (complete and members != expected):
            raise ValueError('unexpected private invoker policy')
        return policy

    def revision(self, name):
        return self.command('run', 'revisions', 'describe', name, '--project=' + PROJECT, '--region=' + REGION, '--format=json', json_output=True)

    def promote(self, revision):
        self.command('run', 'services', 'update-traffic', SERVICE, '--project=' + PROJECT, '--region=' + REGION,
                     '--to-revisions=' + revision + '=100', '--quiet')

    def close_access(self):
        # No schema deletion, record deletion, or restoring over the database.
        # Attempt revocation before any lookup: describe/list outages must never
        # prevent a safety write that could still succeed independently.
        try:
            self.command('run', 'services', 'remove-iam-policy-binding', SERVICE, '--project=' + PROJECT, '--region=' + REGION,
                         '--member=serviceAccount:' + CALLER_SA, '--role=roles/run.invoker', '--condition=None', '--quiet')
        except RuntimeError:
            # Idempotent absence is only accepted after a successful exact list.
            if self.service() is None:
                return
        policy = self.iam(complete=False)
        if any(b['role'] == 'roles/run.invoker' and b.get('members') for b in policy.get('bindings', [])):
            raise ValueError('access revocation not confirmed')

    def stage(self, manifest: Manifest, versions: dict, directory: Path, *, factory=REVIEW_FACTORY):
        arguments = ','.join(api_args(factory))
        previous = self.service()
        if previous:
            verify_service(previous)
            self.iam(complete=False)
            traffic = previous['status'].get('traffic', [])
            if any(t.get('tag') for t in traffic) or len([t for t in traffic if t.get('percent', 0) > 0]) != 1:
                raise ValueError('require one untagged prior traffic revision')
        env_file = directory / 'api-env.json'
        env_file.write_text(json.dumps({'DEPLOYMENT_GIT_SHA': manifest.git_sha, 'MFDATAINDIA_REVIEW_TRUST_FILE': '/trust/config.json'}))
        candidate = revision_name(manifest, factory)
        # Arm deterministic cleanup BEFORE a possibly successful failed lookup.
        try:
            if previous is None:
                dormant_file = directory / 'dormant-env.json'
                dormant_file.write_text(json.dumps({'DEPLOYMENT_GIT_SHA': manifest.git_sha}))
                # First deployments necessarily establish a serving revision.
                # Bootstrap one without trust (503/no startup DB calls), then
                # stage the configured candidate at zero traffic beneath it.
                self.command('run', 'deploy', SERVICE, '--project=' + PROJECT, '--region=' + REGION, '--image=' + manifest.image,
                             '--service-account=' + API_SA, '--ingress=internal', '--no-allow-unauthenticated',
                             '--revision-suffix=dormant-' + manifest.rollout_id, '--set-cloudsql-instances=' + CONNECTION,
                             '--network=tl-dev-vpc', '--subnet=tl-dev-mumbai', '--vpc-egress=all-traffic',
                             '--command=uvicorn', '--args=' + arguments,
                             '--env-vars-file=' + str(dormant_file), '--set-secrets=MFDATAINDIA_DSN=tlws-mf-review-admin-dsn-dev:' + str(versions['mfdata_verified_admin']),
                             '--cpu=1', '--memory=512Mi', '--max-instances=2', '--quiet')
            self.command('run', 'deploy', SERVICE, '--project=' + PROJECT, '--region=' + REGION, '--image=' + manifest.image,
                         '--service-account=' + API_SA, '--ingress=internal', '--no-allow-unauthenticated', '--no-traffic',
                         '--tag=cert-' + manifest.rollout_id,
                         '--revision-suffix=' + candidate.removeprefix(SERVICE+'-'), '--set-cloudsql-instances=' + CONNECTION,
                         '--network=tl-dev-vpc', '--subnet=tl-dev-mumbai', '--vpc-egress=all-traffic',
                         '--command=uvicorn', '--args=' + arguments,
                         '--env-vars-file=' + str(env_file), '--set-secrets=MFDATAINDIA_DSN=tlws-mf-review-admin-dsn-dev:' + str(versions['mfdata_verified_admin'])
                         + ',/trust/config.json=' + manifest.trust_secret + ':' + str(manifest.trust_version),
                         '--cpu=1', '--memory=512Mi', '--max-instances=2', '--quiet')
            service = self.service()
            verify_service(service)
            verify_revision(self.revision(candidate), manifest, versions, factory=factory)
            self.command('run', 'services', 'add-iam-policy-binding', SERVICE, '--project=' + PROJECT, '--region=' + REGION,
                         '--member=serviceAccount:' + CALLER_SA, '--role=roles/run.invoker', '--condition=None', '--quiet')
            self.iam(complete=True)
            tagged = [t for t in service['status']['traffic'] if t.get('tag') == 'cert-' + manifest.rollout_id and t.get('revisionName') == candidate]
            if len(tagged) != 1:
                raise ValueError('candidate tag does not identify verified revision')
            return {'candidate': candidate, 'origin': service_origin(service['status']['url']), 'candidate_origin': service_origin(tagged[0]['url']),
                    'previous_revision': next((t['revisionName'] for t in previous['status']['traffic'] if t.get('percent', 0) > 0), None) if previous else SERVICE + '-dormant-' + manifest.rollout_id}
        except Exception:
            # New service has no known good prior revision: revoke invoker access.
            # Never infer absence from a failed describe or delete its artifacts.
            if previous is None:
                self.close_access()
            raise

    def job(self, manifest, name, account, arguments, environment, secrets, directory, *, image=None):
        if image is not None and not re.fullmatch(r'asia-south1-docker\.pkg\.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:[0-9a-f]{64}', image):
            raise ValueError('unsupported immutable tooling image')
        approved_image = image or manifest.image
        file = directory / (name + '-env.json')
        file.write_text(json.dumps(environment))
        self.command('run', 'jobs', 'deploy', name, '--project=' + PROJECT, '--region=' + REGION, '--image=' + approved_image,
                     '--service-account=' + account, '--command=python', '--args=' + ','.join(arguments),
                     '--set-cloudsql-instances=' + CONNECTION, '--network=tl-dev-vpc', '--subnet=tl-dev-mumbai', '--vpc-egress=all-traffic',
                     '--env-vars-file=' + str(file), '--set-secrets=' + ','.join(secrets),
                     '--tasks=1', '--parallelism=1', '--max-retries=0', '--task-timeout=10m', '--cpu=1', '--memory=1Gi', '--quiet')
        # --wait failure, missing conditions or NonZeroExitCode are terminal.
        execution = self.command('run', 'jobs', 'execute', name, '--project=' + PROJECT, '--region=' + REGION,
                                 '--wait', '--format=json', '--quiet', json_output=True)
        observed, evidence = self.inspect_execution(manifest, execution['metadata']['name'], account, arguments,
                                                    environment, secrets, image=approved_image)
        return observed['metadata']['name'], evidence

    def inspect_execution(self, manifest, name, account, arguments, environment, secrets, *, image=None):
        """Read-only immutable execution verification; never runs/deploys a job."""
        if not re.fullmatch('[a-z][a-z0-9-]{1,62}', name):
            raise ValueError('require an exact immutable execution name')
        approved_image = image or manifest.image
        if approved_image != manifest.image and not re.fullmatch(r'asia-south1-docker\.pkg\.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:[0-9a-f]{64}', approved_image):
            raise ValueError('unsupported immutable execution image')
        execution = self.command('run', 'jobs', 'executions', 'describe', name,
                                 '--project=' + PROJECT, '--region=' + REGION, '--format=json', json_output=True)
        if execution['metadata']['name'] != name:
            raise ValueError('returned execution identity differs')
        conditions = execution['status'].get('conditions', [])
        if (not any(c['type'] == 'Completed' and c['status'] == 'True' for c in conditions)
                or execution['status'].get('failedCount', 0) or execution['status'].get('cancelledCount', 0)
                or execution['status'].get('retriedCount', 0)
                or execution['status'].get('succeededCount') != 1 or execution['spec'].get('taskCount') != 1):
            raise ValueError('job execution did not complete successfully')
        containers = execution['spec']['template']['spec']['containers']
        task = execution['spec']['template']['spec']
        observed_env = {e['name']: e.get('value') for e in containers[0].get('env', [])}
        if (len(containers) != 1 or containers[0]['image'] != approved_image or task['serviceAccountName'] != account
                or any(observed_env.get(k) != v for k, v in environment.items())
                or task.get('maxRetries') != 0 or str(task.get('timeoutSeconds', '600')) != '600'
                or containers[0].get('command') != ['python'] or containers[0].get('args') != arguments):
            raise ValueError('execution identity/image/config differs from approved request')
        # Execution's immutable network annotations are top-level ObjectMeta;
        # TaskTemplateSpec contains only spec (unlike a Job's task template).
        verify_network(execution['metadata'].get('annotations', {}))
        for assignment in secrets:
            target, reference = assignment.split('=', 1)
            secret_name, version = reference.rsplit(':', 1)
            if target.startswith('/'):
                if not any(v.get('secret', {}).get('secretName') == secret_name
                           and any(i.get('key') == version and i.get('path') == target.rsplit('/', 1)[1] for i in v['secret'].get('items', []))
                           for v in task.get('volumes', [])):
                    raise ValueError('execution mounted secret/version differs')
            else:
                env = {e['name']: e for e in containers[0].get('env', [])}
                if env.get(target, {}).get('valueFrom', {}).get('secretKeyRef') != {'name': secret_name, 'key': version}:
                    raise ValueError('execution credential secret/version differs')
        logs = self.command('logging', 'read', 'resource.type="cloud_run_job" AND labels."run.googleapis.com/execution_name"="' + execution['metadata']['name'] + '"',
                            '--project=' + PROJECT, '--format=json', '--limit=100', json_output=True)
        evidence = []
        for log in logs:
            value = log.get('jsonPayload')
            if value is None and log.get('textPayload'):
                try:
                    value = json.loads(log['textPayload'])
                except ValueError:
                    continue
            if isinstance(value, dict) and value.get('format') == 'mfdata-review-rollout-evidence-v1':
                evidence.append(value)
        if len(evidence) != 1:
            raise ValueError('require exactly one structured execution certificate')
        return execution, evidence[0]


def verify_service(service):
    annotations = service['metadata'].get('annotations', {})
    if (annotations.get('run.googleapis.com/ingress') != 'internal'
            or annotations.get('run.googleapis.com/invoker-iam-disabled', 'false') != 'false'):
        raise ValueError('review ingress/IAM must remain private')


def verify_revision(revision, manifest, versions, *, factory=REVIEW_FACTORY):
    spec = revision['spec']
    container = spec['containers'][0]
    env = {e['name']: e for e in container.get('env', [])}
    annotations = revision['metadata'].get('annotations', {})
    if (spec['serviceAccountName'] != API_SA or container['image'] != manifest.image
            or container.get('command') != ['uvicorn'] or container.get('args') != api_args(factory)
            or env.get('DEPLOYMENT_GIT_SHA', {}).get('value') != manifest.git_sha
            or env.get('MFDATAINDIA_REVIEW_TRUST_FILE', {}).get('value') != '/trust/config.json'
            or env.get('MFDATAINDIA_DSN', {}).get('valueFrom', {}).get('secretKeyRef') != {'name': 'tlws-mf-review-admin-dsn-dev', 'key': str(versions['mfdata_verified_admin'])}
            or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in revision['status'].get('conditions', []))):
        raise ValueError('candidate identity/image/configuration/readiness differs')
    verify_network(annotations)
    volumes = spec.get('volumes', [])
    if not any(v.get('secret', {}).get('secretName') == manifest.trust_secret
               and any(i.get('key') == str(manifest.trust_version) and i.get('path') == 'config.json' for i in v['secret'].get('items', [])) for v in volumes):
        raise ValueError('candidate trust version differs')


def verify_network(annotations):
    networks = json.loads(annotations.get('run.googleapis.com/network-interfaces', '[]'))
    allowed = ([{'network': 'tl-dev-vpc', 'subnetwork': 'tl-dev-mumbai'}],
               [{'network': 'projects/trillionlane-dev/global/networks/tl-dev-vpc',
                 'subnetwork': 'projects/trillionlane-dev/regions/asia-south1/subnetworks/tl-dev-mumbai'}])
    if (annotations.get('run.googleapis.com/cloudsql-instances') != CONNECTION
            or annotations.get('run.googleapis.com/vpc-access-egress') != 'all-traffic' or networks not in allowed):
        raise ValueError('candidate/execution network or SQL connection differs')
