"""Private installation locality must not weaken locks, pins or cleanup."""
import pytest

from mfdataindia.review.installation import MIGRATION_SA, ROLE_ENV, cloud_install, install_job
from mfdataindia.review.rollout import ROLES, SECRETS, seal_evidence
from test_verified_review_rollout import manifest, ROOT


class Installer:
    def __init__(self, failure=None, evidence=None):
        self.calls = []
        self.failure = failure
        self.evidence = evidence
        self.job_calls = []

    def command(self, *args, **kwargs):
        self.calls.append(args)
        if self.failure == 'grant' and args[:2] == ('secrets', 'add-iam-policy-binding'):
            raise RuntimeError('possibly applied grant')
        if self.failure == 'remove' and args[:2] == ('secrets', 'remove-iam-policy-binding'):
            raise RuntimeError('unconfirmed revocation')
        return ''

    def job(self, item, name, account, arguments, environment, secrets, directory):
        self.job_calls.append((name, account, arguments, environment, secrets))
        if self.failure == 'job':
            raise RuntimeError('job lookup failure')
        evidence = self.evidence if self.evidence is not None else {
            'level': 'DEV_CLOUD_SQL_JOB', 'original_source_rows_changed': 0, 'transaction_bound_seconds': 180,
            'source_ledger_action': 'APPEND_PINNED_OPTIONAL_MIGRATION_RECEIPT',
            'optional_migration': '023_verified_fund_review.sql',
            'preservation': {'source_values_preserved': True}}
        return 'actual-install-execution', seal_evidence(item, 'database-installation', evidence)


def test_private_installation_uses_existing_migrator_exact_immutable_secrets_and_same_bound(tmp_path):
    cloud = Installer()
    item = manifest()
    versions = dict(zip(ROLES, (2, 3, 4)))
    result = cloud_install(item, cloud, versions, tmp_path)
    assert result['source_values_preserved'] is True
    name, account, arguments, environment, secrets = cloud.job_calls[0]
    assert name == 'mf-review-install-' + item.rollout_id
    assert account == MIGRATION_SA
    assert arguments == ['scripts/install_verified_review_dev.py']
    assert environment == {'DEPLOYMENT_GIT_SHA': item.git_sha, 'REVIEW_MANIFEST': item.model_dump_json()}
    assert secrets == ['MFDATAINDIA_DSN=tlws-mf-data-admin-dsn-dev:1'] + [
        ROLE_ENV[r] + '=' + SECRETS[r] + ':' + str(versions[r]) for r in ROLES]
    assert (tmp_path / '03-install-execution.json').is_file()
    assert len([a for a in cloud.calls if a[:2] == ('secrets', 'remove-iam-policy-binding')]) == 3
    assert all('--member=serviceAccount:' + MIGRATION_SA in a for a in cloud.calls)


@pytest.mark.parametrize('failure,removed', [('grant', 1), ('job', 3), ('remove', 3)])
def test_installation_cleanup_is_armed_before_grant_and_attempts_all_removals(tmp_path, failure, removed):
    cloud = Installer(failure=failure)
    with pytest.raises(RuntimeError):
        cloud_install(manifest(), cloud, dict(zip(ROLES, (1, 1, 1))), tmp_path)
    assert len([a for a in cloud.calls if a[:2] == ('secrets', 'remove-iam-policy-binding')]) == removed
    assert not any('delete' in a or 'destroy' in a for a in cloud.calls)


@pytest.mark.parametrize('evidence', [
    {'level': 'LOCAL_TEST'}, {'level': 'DEV_CLOUD_SQL_JOB', 'original_source_rows_changed': 1},
    {'level': 'DEV_CLOUD_SQL_JOB', 'original_source_rows_changed': 0, 'transaction_bound_seconds': 300},
    {'level': 'DEV_CLOUD_SQL_JOB', 'original_source_rows_changed': 0, 'transaction_bound_seconds': 180,
     'optional_migration': 'other.sql', 'preservation': {'source_values_preserved': True}},
])
def test_incomplete_or_weakened_installation_certificate_is_rejected(tmp_path, evidence):
    cloud = Installer(evidence=evidence)
    with pytest.raises(ValueError):
        cloud_install(manifest(), cloud, dict(zip(ROLES, (1, 1, 1))), tmp_path)
    assert len([a for a in cloud.calls if a[:2] == ('secrets', 'remove-iam-policy-binding')]) == 3


@pytest.mark.parametrize('versions', [{}, {ROLES[0]: 1}, dict(zip(ROLES, (True, 1, 1))),
                                      dict(zip(ROLES, (0, 1, 1)))])
def test_invalid_credential_version_map_makes_no_cloud_calls(tmp_path, versions):
    cloud = Installer()
    with pytest.raises(ValueError):
        cloud_install(manifest(), cloud, versions, tmp_path)
    assert cloud.calls == cloud.job_calls == []


def test_unbound_installation_source_credential_cannot_connect_or_install():
    with pytest.raises(ValueError, match='source credential'):
        install_job(manifest(), {'MFDATAINDIA_DSN': 'postgresql://postgres:fixture@127.0.0.1/other'}, ROOT)


def test_private_job_validates_all_optional_roles_before_atomic_install(monkeypatch):
    from psycopg.conninfo import make_conninfo
    from mfdataindia.review.rollout import CONNECTION
    calls = []
    class Identity:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, *args):
            return self
        def fetchone(self):
            return {'database': 'mfdataindia', 'principal': 'mfdata_admin', 'version': 180006}
    monkeypatch.setattr('mfdataindia.review.installation.readonly', lambda _: Identity())
    def atomic(source, root, item, passwords):
        calls.append((source, root, item, passwords))
        return {'source_values_preserved': True, 'bounded_lock_seconds': 180}
    monkeypatch.setattr('mfdataindia.review.installation.install_and_reconcile', atomic)
    source = make_conninfo(host='/cloudsql/' + CONNECTION, dbname='mfdataindia', user='mfdata_admin', password='fixture-only')
    environment = {'MFDATAINDIA_DSN': source, **{
        ROLE_ENV[r]: make_conninfo(host='/cloudsql/' + CONNECTION, dbname='mfdataindia', user=r, password='fixture-' + r)
        for r in ROLES}}
    bad = {**environment, ROLE_ENV[ROLES[1]]: source}
    with pytest.raises(ValueError, match='source/role'):
        install_job(manifest(), bad, ROOT)
    assert calls == []
    result = install_job(manifest(), environment, ROOT)
    assert calls[0][3] == {r: 'fixture-' + r for r in ROLES}
    assert result['evidence']['original_source_rows_changed'] == 0
    assert result['evidence']['transaction_bound_seconds'] == 180
