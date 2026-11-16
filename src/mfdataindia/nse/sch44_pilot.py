"""One scheduled UAT capture, private archive, and offline SCH44 validation."""

from argparse import ArgumentParser
import base64
from datetime import datetime, timezone
from hashlib import md5, sha256
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import requests

from . import contract, sch44

PREFIX = 'nse-sch44/uat/'
PILOT_JOB = 'tlws-mf-data-nse-sch44-uat'
METADATA_TOKEN = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'
STORAGE = 'https://storage.googleapis.com'
RETAINED = frozenset({'RETAINED_FOR_OFFLINE_VALIDATION', 'RETAINED_EXPORT_DATE_UNVERIFIED'})


class PilotError(ValueError):
    """Fixed, safe diagnostic for the job log."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class PrivateArchive:
    """Create-only objects in a private GCS bucket, with generation-bound deduplication."""

    def __init__(self, session, bucket):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,61}[a-z0-9]', bucket):
            raise PilotError('ARCHIVE_BUCKET_INVALID')
        self.session = session
        self.bucket = bucket
        identity = session.get(METADATA_TOKEN, headers={'Metadata-Flavor': 'Google'},
                               timeout=10, allow_redirects=False)
        if identity.status_code != 200:
            raise PilotError('ARCHIVE_IDENTITY_UNAVAILABLE')
        token = identity.json().get('access_token')
        if not isinstance(token, str) or not token:
            raise PilotError('ARCHIVE_IDENTITY_UNAVAILABLE')
        self.headers = {'Authorization': 'Bearer ' + token}
        self.url = f'{STORAGE}/storage/v1/b/{bucket}'
        policy = session.get(self.url, params={'fields': 'name,iamConfiguration'},
                             headers=self.headers, timeout=15, allow_redirects=False)
        if policy.status_code != 200:
            raise PilotError('ARCHIVE_PRIVACY_UNVERIFIED')
        state = policy.json()
        iam = state.get('iamConfiguration', {})
        if (state.get('name') != bucket or iam.get('publicAccessPrevention') != 'enforced'
                or iam.get('uniformBucketLevelAccess', {}).get('enabled') is not True):
            raise PilotError('ARCHIVE_PRIVACY_UNVERIFIED')

    def create(self, key, content, *, deduplicate=False):
        name = PREFIX + key
        checksum = base64.b64encode(md5(content, usedforsecurity=False).digest()).decode()
        upload = self.session.post(
            f'{STORAGE}/upload/storage/v1/b/{self.bucket}/o',
            params={'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'},
            headers={**self.headers, 'Content-Type': 'application/octet-stream', 'Content-MD5': checksum},
            data=content, timeout=90, allow_redirects=False,
        )
        object_url = self.url + '/o/' + quote(name, safe='')
        reused = upload.status_code == 412 and deduplicate
        if reused:
            upload = self.session.get(object_url, headers=self.headers, timeout=20, allow_redirects=False)
        if upload.status_code not in (200, 201):
            raise PilotError('ARCHIVE_CREATE_OR_READ_FAILED')
        metadata = upload.json()
        generation = str(metadata.get('generation', ''))
        if (metadata.get('name') != name or metadata.get('bucket') != self.bucket
                or not generation.isdigit() or int(generation) <= 0
                or str(metadata.get('size')) != str(len(content)) or metadata.get('md5Hash') != checksum):
            raise PilotError('ARCHIVE_OBJECT_DIFFERS')
        if reused:
            with self.session.get(object_url, params={'alt': 'media', 'generation': generation},
                                  headers=self.headers, stream=True, timeout=60,
                                  allow_redirects=False) as existing:
                if existing.status_code != 200:
                    raise PilotError('ARCHIVE_READBACK_FAILED')
                size, digest = 0, sha256()
                for block in existing.iter_content(65536):
                    size += len(block)
                    if size > len(content):
                        raise PilotError('ARCHIVE_OBJECT_DIFFERS')
                    digest.update(block)
                if size != len(content) or digest.digest() != sha256(content).digest():
                    raise PilotError('ARCHIVE_OBJECT_DIFFERS')
        return {'name': name, 'generation': generation, 'reused': reused}


def json_bytes(record):
    return contract.canonical(record) + b'\n'


def run_retrieval(archive, execution, connector_revision, capture, *, publish=None):
    """Reserve this execution before transport and journal its safe result."""
    if not re.fullmatch(PILOT_JOB + r'-[a-z0-9]{1,20}', execution):
        raise PilotError('PILOT_EXECUTION_INVALID')
    if not re.fullmatch(r'[0-9a-f]{40}', connector_revision):
        raise PilotError('CONNECTOR_REVISION_INVALID')
    key = f'retrievals/{execution}/'
    attempt = {'format': 'nse-sch44-uat-retrieval-v1', 'execution': execution,
               'started_at_utc': utc_now(), 'environment': 'UAT', 'file_type': 'SCH',
               'connector_revision': connector_revision, 'publication': 'NOT_ATTEMPTED'}
    archive.create(key + 'attempt.json', json_bytes(attempt))
    receipt = {**attempt, 'source_scope': 'UNVERIFIED', 'retrieved_at_utc': None,
               'export_date': None, 'result': 'FAILED', 'reason': 'CAPTURE_UNVERIFIED'}
    try:
        with TemporaryDirectory(prefix='nse-sch44-uat-') as work:
            directory = Path(work) / 'capture'
            exit_code = capture(directory)
            capture_receipt = contract.decode(sch44._read(directory / 'receipt.json', 4096))
            if (capture_receipt['format'] != 'nse01-sch-api-capture-v1'
                    or capture_receipt['environment'] != 'UAT' or capture_receipt['file_type'] != 'SCH'
                    or capture_receipt['connector_revision'] != connector_revision
                    or capture_receipt['publication'] != 'NOT_ATTEMPTED'):
                raise PilotError('CAPTURE_RECEIPT_DIFFERS')
            observed = capture_receipt['observed_at_utc']
            status = capture_receipt['http_status']
            if (type(observed) is not str or not observed.endswith('Z')
                    or datetime.fromisoformat(observed).tzinfo is None
                    or (status is not None and (type(status) is not int or not 100 <= status <= 599))):
                raise PilotError('CAPTURE_RECEIPT_DIFFERS')
            receipt['retrieved_at_utc'] = observed
            receipt['http_status'] = status
            if exit_code or capture_receipt['outcome'] not in RETAINED:
                receipt['reason'] = 'CONNECTOR_CAPTURE_FAILED'
            else:
                data = sch44._read(directory / 'source.txt', contract.MAX_BYTES)
                digest = sha256(data).hexdigest()
                if digest != capture_receipt['sha256'] or len(data) != capture_receipt['byte_length']:
                    raise PilotError('CAPTURE_BYTES_DIFFER')
                receipt.update(sha256=digest, byte_length=len(data))
                receipt['payload'] = archive.create(f'payloads/sha256/{digest}.txt', data,
                                                    deduplicate=True)
                source_date = capture_receipt['export_date']
                if source_date is not None:
                    sch44._date(source_date)
                rows = sch44.parse(data)
                source = sch44._api_capture(capture_receipt, digest, len(data), source_date, len(rows))
                receipt.update(result='CAPTURED', reason=None, export_date=source_date,
                               filename=source['filename'], layout=sch44.PROFILE, row_count=len(rows))
                if source_date is None:
                    receipt.update(publication='HELD', publication_reason='SCH44_EXPORT_DATE_UNVERIFIED')
                elif publish is None:
                    receipt.update(publication='HELD', publication_reason='SCH44_APPROVAL_UNAVAILABLE')
                else:
                    receipt.update(publish(data, capture_receipt, str(uuid5(NAMESPACE_URL, execution))))
    except (contract.ImportError, PilotError) as error:
        receipt['reason'] = str(error)
    except (OSError, KeyError, TypeError, ValueError, requests.RequestException,
            subprocess.SubprocessError):
        receipt['reason'] = 'CAPTURE_OR_ARCHIVE_INCOMPLETE'
    receipt['completed_at_utc'] = utc_now()
    archive.create(key + 'receipt.json', json_bytes(receipt))
    return receipt


def main(argv=None):
    args = ArgumentParser(description=__doc__)
    args.add_argument('--bucket', required=True)
    args.add_argument('--connector-root', type=Path, required=True)
    args.add_argument('--connector-revision', required=True)
    args.add_argument('--capture-script', type=Path, required=True)
    args.add_argument('--approval', type=Path)
    args.add_argument('--crosswalk', type=Path)
    options = args.parse_args(argv)
    if os.environ.get('CLOUD_RUN_JOB') != PILOT_JOB:
        print('NSE_SCH44_UAT_JOB_REQUIRED')
        return 1
    root = options.connector_root.resolve()
    script = options.capture_script.resolve()

    def capture(directory):
        env = {name: value for name, value in os.environ.items()
               if name.startswith('NSE_UAT_') or name in {
                   'PATH', 'HOME', 'LANG', 'NODE_ENV', 'DEPLOYMENT_ENVIRONMENT',
                   'TLAM_ENVIRONMENT', 'NSE_PROVIDER_ADMISSION_MODE',
               }}
        env['TSX_TSCONFIG_PATH'] = str(root / 'tsconfig.json')
        result = subprocess.run(['node', '--import', 'tsx', str(script), '--connector-root',
                                 str(root), '--connector-revision', options.connector_revision,
                                 '--output-dir', str(directory)], cwd=root, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=220)
        return result.returncode

    publisher = None
    try:
        if options.approval is not None or options.crosswalk is not None:
            if options.approval is None or options.crosswalk is None:
                raise PilotError('SCH44_PUBLICATION_CONFIGURATION_INCOMPLETE')
            from psycopg import connect
            from psycopg.conninfo import conninfo_to_dict
            from psycopg.rows import dict_row
            from . import sch44_publication, store

            approval_document = contract.decode(sch44._read(options.approval, 256 * 1024))
            walk = sch44._json(sch44._read(options.crosswalk, contract.MAX_BYTES), 'INVALID_CROSSWALK')
            approval_pin = os.environ['NSE_SCH44_APPROVAL_SHA256']
            dsn = os.environ['NSE_SCH44_UAT_DSN']
            instance = os.environ['NSE_SCH44_UAT_CLOUD_SQL_INSTANCE']
            fields = conninfo_to_dict(dsn)
            if (not re.fullmatch(r'trillionlane-dev:asia-south1:[a-z0-9-]+', instance)
                    or fields.get('host') != '/cloudsql/' + instance or fields.get('hostaddr')
                    or fields.get('dbname') != 'mfdataindia' or fields.get('user') != 'mfdata_ingest'):
                raise PilotError('SCH44_UAT_DATABASE_BINDING_DIFFERS')

            def publisher(data, capture_receipt, operation):
                try:
                    approval, approval_sha256 = sch44_publication.approval_for_export(
                        approval_document, approval_pin, data,
                    )
                    sch44_publication.prepare(data, capture_receipt, approval, approval_sha256, walk)
                except contract.ImportError as error:
                    return {'publication': 'HELD', 'publication_reason': str(error)}
                try:
                    conn = connect(dsn, row_factory=dict_row, connect_timeout=5)
                except Exception:
                    return {'publication': 'FAILED', 'publication_reason': 'DATABASE_CONNECTION_FAILED'}
                try:
                    outcome = sch44_publication.publish(conn, data, capture_receipt, approval,
                                                       approval_sha256, walk, operation)
                except store.OutcomeError as error:
                    outcome = error.report
                except contract.ImportError as error:
                    return {'publication': 'HELD', 'publication_reason': str(error)}
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass  # The retained transaction effect remains authoritative.
                return {'publication': outcome['state'], 'publication_reason': outcome.get('reason'),
                        'publication_effect': outcome['effect'], 'operation_id': operation,
                        'snapshot_id': outcome.get('snapshot_id')}

        with requests.Session() as session:
            session.trust_env = False
            archive = PrivateArchive(session, options.bucket)
            receipt = run_retrieval(archive, os.environ['CLOUD_RUN_EXECUTION'],
                                    options.connector_revision, capture, publish=publisher)
    except (PilotError, OSError, KeyError, TypeError, ValueError, requests.RequestException):
        print('NSE_SCH44_UAT_PILOT_INCOMPLETE')
        return 1
    print(json.dumps(receipt, sort_keys=True))
    failed = receipt['publication'] in {'FAILED', 'COMMIT_UNKNOWN', 'COMMITTED_UNVERIFIED',
                                        'PUBLICATION_ROLLED_BACK', 'ROLLBACK_UNCONFIRMED'}
    return 0 if receipt['result'] == 'CAPTURED' and not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
