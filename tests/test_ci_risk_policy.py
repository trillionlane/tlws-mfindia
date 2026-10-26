"""Provider-free routing, failure propagation, actual workflow and parent proofs."""
from copy import deepcopy
import base64
import gzip
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

import ci_risk_preservation as preservation
from ci_risk_preservation import PRESENTATION, RECIPES, restore_policy_parent

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ci_risk_policy as policy  # noqa: E402
import run_ci_targeted as targeted  # noqa: E402


@pytest.mark.parametrize('paths,expected', [
    (['README.md'], 'docs'), (['docs/guide.md'], 'docs'),
    (['docs/deep/a.md', 'README.md'], 'docs'),
    (['src/mfdataindia/web/index.js'], 'targeted'),
    (['docs/guide.md', 'src/mfdataindia/web/style.css'], 'targeted'),
    (['src/mfdataindia/web/new.js'], 'full'), (['AGENTS.md'], 'full'),
    (['docs/authority.json'], 'full'), (['docs/source.yaml'], 'full'),
    (['docs/RESEARCH.md'], 'full'), (['docs/DEPLOYMENT.md'], 'full'),
    (['docs/evidence/new.md'], 'full'), (['docs/runbooks/new.md'], 'full'),
    (['README.md', 'docs/NAV_CORRECTION_RUNBOOK.md'], 'full'),
    (['src/mfdataindia/api/app.py'], 'full'), (['sql/001.sql'], 'full'),
    (['pyproject.toml'], 'full'), (['uv.lock'], 'full'),
    (['scripts/ci_risk_policy.py'], 'full'), (['.github/workflows/verify.yml'], 'full'),
    (['src/mfdataindia/review/broad_reader_policy.py'], 'full'),
    (['src/mfdataindia/ingest/amfi_navall.py'], 'full'),
    (['tests/test_postgres_integration.py'], 'full'),
    (['README.md', 'unknown.py'], 'full'), ([], 'full'),
])
def test_risk_routes_only_exact_reviewed_paths(paths, expected):
    assert policy.classify(paths) == expected


@pytest.mark.parametrize('path', ['/docs/a.md', '../README.md', 'docs/../a.md',
                                  './README.md', 'docs//a.md', 'docs/a\x00.md', 'docs\\a.md'])
def test_ambiguous_paths_rejected(path):
    with pytest.raises(ValueError):
        policy.classify([path])


@pytest.mark.parametrize('path', ['README.md', *sorted(policy.WEB)])
def test_nonregular_or_executable_never_downgraded(path):
    assert policy.classify([path], regular=False) == 'full'


def test_rename_deletion_and_status_malformed_cannot_disappear():
    assert policy.changed_paths(b'D\0sql/old.sql\0A\0docs/new.md\0') == ['docs/new.md', 'sql/old.sql']
    assert policy.classify(policy.changed_paths(b'D\0sql/old.sql\0A\0docs/new.md\0')) == 'full'
    for raw in (b'R100\0old\0new\0', b'M\0README.md', b'M\0README.md\0M\0README.md\0', b'M\0\xff\0'):
        with pytest.raises((ValueError, UnicodeError)):
            policy.changed_paths(raw)


def git_at(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def git_repo(tmp_path):
    git_at(tmp_path, 'init', '-b', 'main')
    git_at(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    git_at(tmp_path, 'config', 'user.name', 'CI fixture')
    (tmp_path / 'README.md').write_text('base\n')
    git_at(tmp_path, 'add', '.')
    git_at(tmp_path, 'commit', '-m', 'base')
    return tmp_path


def pr_event(base, head):
    return {'pull_request': {'base': {'sha': base, 'ref': 'main'}, 'head': {'sha': head}}}


@pytest.mark.parametrize('change,expected', [('docs', 'docs'), ('rename-risk', 'full'),
                                            ('symlink', 'full'), ('executable', 'full'), ('delete', 'full')])
def test_actual_git_objects_bind_plan_and_classify_both_rename_paths(git_repo, change, expected):
    root = git_repo
    base = git_at(root, 'rev-parse', 'HEAD')
    if change == 'rename-risk':
        (root / 'README.md').rename(root / 'script.py')
    elif change == 'symlink':
        (root / 'README.md').unlink()
        (root / 'README.md').symlink_to('somewhere')
    elif change == 'executable':
        (root / 'README.md').chmod(0o755)
    elif change == 'delete':
        (root / 'README.md').unlink()
        (root / 'keep.txt').write_text('unchanged unrelated seed')
        # Keep new unknown path out of diff by including it in the base commit.
        git_at(root, 'add', 'keep.txt')
        git_at(root, 'commit', '-m', 'unrelated base')
        base = git_at(root, 'rev-parse', 'HEAD')
    else:
        (root / 'README.md').write_text('updated prose\n')
    git_at(root, 'add', '-A')
    git_at(root, 'commit', '-m', 'candidate')
    head = git_at(root, 'rev-parse', 'HEAD')
    value = policy.plan('pull_request', pr_event(base, head), head, root=root)
    assert value['mode'] == expected and value['head'] == value['checkout'] == head
    if change == 'rename-risk':
        assert value['paths'] == ['README.md', 'script.py']
    for event in ('push', 'workflow_dispatch'):
        assert policy.plan(event, {'ref': 'refs/heads/main', 'after': head}, head, root=root)['mode'] == 'full'
    with pytest.raises(ValueError):
        policy.plan('pull_request', pr_event(base, head), '0' * 40, root=root)
    with pytest.raises(subprocess.CalledProcessError):
        policy.plan('pull_request', pr_event('f' * 40, head), head, root=root)


@pytest.mark.parametrize('event', ['pull_request_target', 'schedule', 'workflow_run', 'push'])
def test_unknown_event_or_wrong_branch_is_not_a_fast_fallback(git_repo, event):
    head = git_at(git_repo, 'rev-parse', 'HEAD')
    with pytest.raises(ValueError):
        policy.plan(event, {'ref': 'refs/heads/other', 'after': head}, head, root=git_repo)


@pytest.mark.parametrize('defect', ['missing', 'badsha', 'upper_sha', 'branch', 'push_sha', 'oversize'])
def test_malformed_event_never_defaults_to_docs(git_repo, tmp_path, defect):
    head = git_at(git_repo, 'rev-parse', 'HEAD')
    event = pr_event(head, head)
    name = 'pull_request'
    if defect == 'missing':
        event = {}
    elif defect == 'badsha':
        event['pull_request']['head']['sha'] = '--malicious'
    elif defect == 'upper_sha':
        event['pull_request']['head']['sha'] = 'A' * 40
    elif defect == 'branch':
        event['pull_request']['base']['ref'] = 'other'
    elif defect == 'push_sha':
        name, event = 'push', {'ref': 'refs/heads/main', 'after': 'f' * 40}
    event_file = tmp_path / 'event.json'
    event_file.write_text(json.dumps(event) if defect != 'oversize' else ' ' * (4 * 1024 * 1024 + 1))
    with pytest.raises((ValueError, KeyError)):
        policy.environment_plan({'GITHUB_EVENT_PATH': str(event_file), 'GITHUB_SHA': head,
                                 'GITHUB_EVENT_NAME': name}, root=git_repo)


def test_prose_checks_no_dependencies_database_or_build_and_fails_on_scan_errors(git_repo, monkeypatch):
    head = git_at(git_repo, 'rev-parse', 'HEAD')
    value = {'mode': 'docs', 'paths': ['README.md'], 'base': head, 'head': head}
    policy.docs_checks(value, root=git_repo)
    (git_repo / 'README.md').write_bytes(b'invalid\x00prose')
    with pytest.raises(ValueError, match='NUL'):
        policy.docs_checks(value, root=git_repo)
    (git_repo / 'README.md').write_text('valid\n')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(policy.subprocess, 'run', run)
    with pytest.raises(ValueError, match='credential scan'):
        policy.docs_checks(value, root=git_repo)
    assert calls[0][:3] == ['git', 'diff', '--check'] and calls[1][:2] == ['git', 'grep']


@pytest.mark.parametrize('defect', ['result', 'mode', 'binding', 'missing'])
def test_failed_cancelled_missing_or_stale_planner_fails_closed(defect):
    value = {'mode': 'full', 'head': 'a' * 40}
    env = {'CI_PLAN_RESULT': 'success', 'CI_MODE': 'full', 'CI_BINDING': policy.digest(value)}
    assert policy.verify_plan(value, env) == value
    if defect == 'result':
        env['CI_PLAN_RESULT'] = 'cancelled'
    elif defect == 'mode':
        env['CI_MODE'] = 'docs'
    elif defect == 'binding':
        env['CI_BINDING'] = '0' * 64
    else:
        env = {}
    with pytest.raises(ValueError):
        policy.verify_plan(value, env)


MANIFEST = json.loads(policy.INVENTORY.read_text())


def outcomes(job, mode):
    required = policy.required_ids(job, mode, MANIFEST)
    required['policy'] = True
    required['reduced'] = mode != 'full' if job == 'tests' else mode == 'docs'
    return {key: {'outcome': 'success' if needed else 'skipped',
                  'conclusion': 'success' if needed else 'skipped'} for key, needed in required.items()}


@pytest.mark.parametrize('job', ['tests', 'verify'])
@pytest.mark.parametrize('mode', ['full', 'targeted', 'docs'])
def test_required_gate_accepts_only_exact_mode_step_outcomes(job, mode):
    good = outcomes(job, mode)
    policy.gate(job, mode, good, MANIFEST)
    for key in good:
        for wrong in ('failure', 'cancelled', 'unknown', 'success' if good[key]['outcome'] == 'skipped' else 'skipped'):
            changed = deepcopy(good)
            changed[key]['outcome'] = wrong
            with pytest.raises(ValueError):
                policy.gate(job, mode, changed, MANIFEST)
        changed = deepcopy(good)
        changed[key]['conclusion'] = 'success' if good[key]['conclusion'] == 'skipped' else 'skipped'
        with pytest.raises(ValueError):
            policy.gate(job, mode, changed, MANIFEST)
        changed = deepcopy(good)
        del changed[key]
        with pytest.raises(ValueError):
            policy.gate(job, mode, changed, MANIFEST)
    with pytest.raises(ValueError):
        policy.gate(job, mode, {**good, 'unknown': good['policy']}, MANIFEST)


def load_workflow():
    return yaml.load((ROOT / '.github/workflows/verify.yml').read_text(), Loader=yaml.BaseLoader)


def render_authored_gate_projection(job, context):
    """Evaluate only the two exact JSON expression shapes used by our workflow."""
    template = load_workflow()['jobs'][job]['steps'][-1]['env']['CI_STEPS']
    if template == '${{ toJSON(steps) }}':
        return json.loads(json.dumps(context))
    for key in [*[row['id'] for row in MANIFEST[job]], 'policy', 'reduced']:
        expression = '${{ toJSON(steps.' + key + ') }}'
        assert template.count(expression) == 1
        template = template.replace(expression, json.dumps(context.get(key)))
    assert '${{' not in template
    return json.loads(template)


@pytest.mark.parametrize('job', ['tests', 'verify'])
@pytest.mark.parametrize('mode', ['full', 'targeted', 'docs'])
def test_real_service_context_projects_every_authored_step_and_keeps_gate_strict(job, mode):
    authored = outcomes(job, mode)
    authored['original_0']['outputs'] = {'escaped': '"quoted"\nnot JSON injection'}
    # The retained failed native run has this runner-generated service entry.
    # Its opaque ID is deliberately NOT a validator allowlist or predicate.
    context = {'223532dba0f241a2b61aa9707550efbb': {
        'outputs': {}, 'outcome': 'success', 'conclusion': 'success'}, **deepcopy(authored)}
    projected = render_authored_gate_projection(job, context)
    policy.gate(job, mode, projected, MANIFEST)
    assert projected == authored
    for key in authored:
        missing = deepcopy(context)
        del missing[key]
        with pytest.raises((ValueError, AttributeError)):
            policy.gate(job, mode, render_authored_gate_projection(job, missing), MANIFEST)
        for field in ('outcome', 'conclusion'):
            for bad in ('failure', 'cancelled', 'unknown'):
                wrong = deepcopy(context)
                wrong[key][field] = bad
                with pytest.raises(ValueError):
                    policy.gate(job, mode, render_authored_gate_projection(job, wrong), MANIFEST)
    # gate() itself still rejects every unaccounted projected authored entry.
    with pytest.raises(ValueError):
        policy.gate(job, mode, {**projected, 'unreviewed_authored_step': authored['policy']}, MANIFEST)


def test_actual_workflow_preserves_names_full_sequence_and_main_policy_without_pg_cap():
    wf = load_workflow()
    assert set(wf['jobs']) == {'plan', 'tests', 'verify'}
    assert wf['on'] == {'pull_request': {'branches': ['main']}, 'push': {'branches': ['main']}, 'workflow_dispatch': ''}
    assert wf['permissions'] == {'contents': 'read'}
    assert wf['jobs']['plan']['name'] == 'Plan CI coverage'
    assert 'timeout-minutes' not in wf['jobs']['tests']
    assert wf['jobs']['verify']['timeout-minutes'] == '30'
    for key in ('tests', 'verify'):
        job = wf['jobs'][key]
        assert job['needs'] == 'plan' and job['if'] == '${{ always() }}'
        assert job['steps'][-1]['if'] == '${{ always() }}'
        assert job['steps'][-1]['run'] == 'python scripts/ci_risk_policy.py gate --job ' + key
        authored_ids = [*[row['id'] for row in MANIFEST[key]], 'policy', 'reduced']
        projection = '{\n' + ',\n'.join(
            '  ' + json.dumps(step_id) + ': ${{ toJSON(steps.' + step_id + ') }}'
            for step_id in authored_ids) + '\n}'
        assert job['steps'][-1]['env']['CI_STEPS'] == projection
        by_id = {step['id']: step for step in job['steps'] if 'id' in step}
        assert set(by_id) == set(authored_ids)
        assert len(job['steps']) == len(authored_ids) + 1
        assert all('id' in step for step in job['steps'][:-1])
        assert by_id['policy']['run'] == 'python scripts/ci_risk_policy.py validate'
        for row in MANIFEST[key]:
            step = by_id[row['id']]
            if row['modes'] == ['full']:
                assert step['if'] == "${{ needs.plan.outputs.mode == 'full' }}"
            elif row['modes'] == ['targeted', 'full']:
                assert step['if'] == "${{ needs.plan.outputs.mode != 'docs' }}"
            else:
                assert 'if' not in step
        assert list(by_id).index('policy') == 2  # Before dependency install/test/build.
    assert wf['jobs']['tests']['services']['postgres']['image'] == "${{ needs.plan.outputs.mode != 'docs' && 'postgres:18.6-alpine' || '' }}"
    assert not any('continue-on-error' in step for job in wf['jobs'].values() for step in job['steps'])


def synthetic_full_jobs():
    result = [{'name': 'Plan CI coverage', 'status': 'completed', 'conclusion': 'success',
               'steps': [{'name': name, 'status': 'completed', 'conclusion': 'success'}
                         for name in ['Classify exact change', 'CI plan: full']]}]
    for job, label in [('tests', 'PostgreSQL tests, contracts, and audit'), ('verify', 'Test, audit, and build')]:
        result.append({'name': label, 'status': 'completed', 'conclusion': 'success',
            'steps': [{'name': name, 'status': 'completed',
                       'conclusion': 'skipped' if name == 'Run reduced PR checks' else 'success'}
                      for name in [*[r['label'] for r in MANIFEST[job]], 'Validate CI plan',
                                   'Run reduced PR checks', 'CI policy admitted: full']]})
    return result


@pytest.mark.parametrize('label', [
    'Initialize containers', 'Stop containers', 'Post Run actions/setup-python@v5'])
@pytest.mark.parametrize('conclusion', ['success', 'failure', 'cancelled', 'skipped'])
def test_full_admission_checks_every_runner_service_lifecycle_step(label, conclusion):
    jobs = synthetic_full_jobs()
    jobs[1]['steps'].append({'name': label, 'status': 'completed', 'conclusion': conclusion})
    if conclusion == 'success':
        assert policy.require_full_policy_jobs(jobs)['policy'] == 'full'
    else:
        with pytest.raises(ValueError):
            policy.require_full_policy_jobs(jobs)


@pytest.mark.parametrize('defect', ['none', 'planner_fail', 'docs', 'duplicate', 'missing',
                                   'partial_pg', 'unexpected_skip', 'reduced_ran', 'false_marker'])
def test_full_admission_never_substitutes_targeted_or_partial_proof(defect):
    jobs = synthetic_full_jobs()
    if defect == 'planner_fail':
        jobs[0]['conclusion'] = 'failure'
    elif defect == 'docs':
        jobs[0]['steps'][-1]['name'] = 'CI plan: docs'
    elif defect == 'duplicate':
        jobs[-1] = deepcopy(jobs[1])
    elif defect == 'missing':
        jobs[1]['steps'].pop(6)
    elif defect == 'partial_pg':
        jobs[1]['steps'][6]['conclusion'] = 'cancelled'
    elif defect == 'unexpected_skip':
        jobs[2]['steps'][3]['conclusion'] = 'skipped'
    elif defect == 'reduced_ran':
        jobs[1]['steps'][-2]['conclusion'] = 'success'
    elif defect == 'false_marker':
        jobs[1]['steps'][-1]['name'] = 'CI policy admitted: targeted'
    if defect == 'none':
        assert policy.require_full_policy_jobs(jobs)['policy'] == 'full'
        spec = spec_from_file_location('new_full_admission', ROOT / 'scripts/prepush_fs13.py')
        pre = module_from_spec(spec)
        spec.loader.exec_module(pre)
        doc = {'databaseId': 123, 'headSha': 'a' * 40, 'name': 'Verify',
               'status': 'completed', 'conclusion': 'success', 'jobs': jobs}
        assert pre.require_whole_verify(doc, run_id=123, head_sha='a' * 40)['policy'] == 'full'
    else:
        with pytest.raises(ValueError):
            policy.require_full_policy_jobs(jobs)


class Item:
    def __init__(self, nodeid, pg=False):
        self.nodeid, self.pg = nodeid, pg

    def get_closest_marker(self, name):
        assert name == 'postgres'
        return object() if self.pg else None


def test_targeted_keeps_all_units_and_entire_mapped_native_module_in_original_order():
    cases = [Item('tests/unit.py::one'), Item('tests/test_postgres_integration.py::one', True),
             Item('tests/other_pg.py::other', True), Item('tests/unit.py::two'),
             Item('tests/test_postgres_integration.py::two', True)]
    items, deselected = list(cases), []
    plugin = targeted.Targeted()
    plugin.pytest_collection_modifyitems(SimpleNamespace(hook=SimpleNamespace(
        pytest_deselected=lambda items: deselected.extend(items))), items)
    assert items == [cases[i] for i in [0, 1, 3, 4]] and deselected == [cases[2]]
    assert plugin.inventory['all'] == [c.nodeid for c in cases]
    assert plugin.inventory['selected_native'] == [cases[1].nodeid, cases[4].nodeid]
    for broken in ([], [cases[0]], [cases[1], cases[1]]):
        with pytest.raises(pytest.UsageError):
            targeted.Targeted().pytest_collection_modifyitems(None, broken)


@pytest.mark.parametrize('code', [0, 1, 2, 5])
def test_targeted_runner_exit_codes_and_non_targeted_denial(monkeypatch, tmp_path, code):
    receipt = tmp_path / 'targeted.json'
    monkeypatch.setattr(sys, 'argv', ['run_ci_targeted.py', '--evidence', str(receipt)])
    monkeypatch.setattr(targeted, 'environment_plan', lambda: {'mode': 'targeted'})
    monkeypatch.setattr(targeted, 'verify_plan', lambda v: v)
    def run(args, plugins):
        assert args == ['tests/', '-q', '--durations=50', '--durations-min=0.1']
        plugins[0].inventory = {'all': ['unit', 'native']}
        return code
    monkeypatch.setattr(targeted.pytest, 'main', run)
    assert targeted.main() == code
    assert receipt.exists() == (code == 0)
    if receipt.exists():
        assert json.loads(receipt.read_text())['full_native_acceptance'] is False
        receipt.unlink()
    monkeypatch.setattr(targeted, 'environment_plan', lambda: {'mode': 'full'})
    with pytest.raises(ValueError):
        targeted.main()


def test_targeted_stale_receipt_and_missing_collection_fail_without_success(monkeypatch, tmp_path):
    receipt = tmp_path / 'targeted.json'
    receipt.write_text('previous immutable evidence')
    monkeypatch.setattr(sys, 'argv', ['run_ci_targeted.py', '--evidence', str(receipt)])
    monkeypatch.setattr(targeted, 'environment_plan', lambda: pytest.fail('no plan read with stale receipt'))
    with pytest.raises(SystemExit) as error:
        targeted.main()
    assert error.value.code == 2 and receipt.read_text() == 'previous immutable evidence'
    receipt.unlink()
    monkeypatch.setattr(targeted, 'environment_plan', lambda: {'mode': 'targeted'})
    monkeypatch.setattr(targeted, 'verify_plan', lambda value: value)
    monkeypatch.setattr(targeted.pytest, 'main', lambda *a, **kw: 0)
    assert targeted.main() == 5 and not receipt.exists()


def test_all_884_baseline_files_restore_whole_bytes_including_all_five_changed_guards():
    assert hashlib.sha256((ROOT / 'docs/CI_RISK_POLICY_PARENT_AUTHORITY_20261026.json').read_bytes()).hexdigest() == 'd45440ec5e71e49038255525fd5568e543b55cefac202109a3440d7f78d858b0'
    authority = json.loads((ROOT / 'docs/CI_RISK_POLICY_PARENT_AUTHORITY_20261026.json').read_text())
    assert authority['head'] == '038ec40bf5400cf7d2218eedaca151d1048b9f93'
    assert authority['tracked_file_count'] == len(authority['files']) == 884
    assert set(RECIPES) == {'.github/workflows/verify.yml', 'scripts/prepush_fs13.py',
        'tests/broad_transport_preservation.py', 'tests/test_ci_verify_sequence.py',
        'tests/test_broad_reader_preservation.py', 'tests/test_prepush_fs13.py'}
    for name, expected in authority['files'].items():
        path = ROOT / name
        assert path.is_symlink() == (expected['kind'] == 'symlink')
        value = str(path.readlink()).encode() if path.is_symlink() else restore_policy_parent(name, path.read_bytes())
        assert hashlib.sha256(value).hexdigest() == expected['sha256'], name


@pytest.mark.parametrize('name', sorted(RECIPES))
@pytest.mark.parametrize('mutation', ['append', 'drop', 'duplicate', 'change'])
def test_inverse_rejects_every_unapproved_byte_including_old_guard_changes(name, mutation):
    data = (ROOT / name).read_bytes()
    if mutation == 'append':
        data += b'\n# unapproved\n'
    elif mutation == 'drop':
        data = data[1:]
    elif mutation == 'change':
        data = bytes([data[0] ^ 1]) + data[1:]
    else:
        data += data
    with pytest.raises(AssertionError):
        restore_policy_parent(name, data)


def test_human_exception_is_only_readme_and_nine_existing_assets_no_historical_docs():
    assert set(PRESENTATION) == {'README.md', *policy.WEB} and len(PRESENTATION) == 10
    assert not any(name.startswith('docs/') for name in PRESENTATION)
    authority = json.loads((ROOT / 'docs/CI_RISK_POLICY_PARENT_AUTHORITY_20261026.json').read_text())
    for name, row in PRESENTATION.items():
        raw = gzip.decompress(base64.b64decode(row['complete_gzip_base64']))
        assert hashlib.sha256(raw).hexdigest() == row['sha256'] == authority['files'][name]['sha256']


@pytest.mark.parametrize('name', sorted(PRESENTATION))
def test_actual_committed_presentation_edits_work_on_pr_and_full_main_without_masking_functional_bytes(git_repo, monkeypatch, name):
    root = git_repo
    monkeypatch.setattr(preservation, 'ROOT', root)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = gzip.decompress(base64.b64decode(PRESENTATION[name]['complete_gzip_base64']))
    from broad_transport_preservation import restore_broad_parent
    for revision in (1, 2):
        base = git_at(root, 'rev-parse', 'HEAD')
        actual = f'intentional presentation revision {revision}\n'.encode()
        path.write_bytes(actual)
        git_at(root, 'add', name)
        git_at(root, 'commit', '-m', f'presentation revision {revision}')
        head = git_at(root, 'rev-parse', 'HEAD')
        pr = policy.plan('pull_request', pr_event(base, head), head, root=root)
        assert pr['mode'] == ('docs' if name == 'README.md' else 'targeted')
        assert policy.plan('push', {'ref': 'refs/heads/main', 'after': head}, head, root=root)['mode'] == 'full'
        restored = restore_broad_parent(name, path.read_bytes())
        assert restored == baseline and hashlib.sha256(restored).hexdigest() == PRESENTATION[name]['sha256']
        assert path.read_bytes() == actual and actual != restored
        # Already restored bytes may flow through the historical adapter twice,
        # but the actual disk/HEAD must remain attested even then.
        assert restore_policy_parent(name, restored) == baseline


@pytest.mark.parametrize('defect', ['dirty', 'wrong_argument', 'partial', 'symlink', 'executable',
                                   'committed_executable', 'missing'])
def test_presentation_adapter_rejects_unattested_bytes_types_and_deletions(git_repo, monkeypatch, defect):
    root = git_repo
    monkeypatch.setattr(preservation, 'ROOT', root)
    path = root / 'README.md'
    data = path.read_bytes()
    if defect == 'dirty':
        path.write_bytes(b'not committed\n')
    elif defect == 'wrong_argument':
        data = b'arbitrary bytes not GitHEAD'
    elif defect == 'partial':
        data = data[:-1]
    elif defect == 'symlink':
        path.rename(root / 'saved-readme')
        path.symlink_to(root / 'saved-readme')
    elif defect == 'executable':
        path.chmod(0o755)
    elif defect == 'committed_executable':
        path.chmod(0o755)
        git_at(root, 'add', 'README.md')
        git_at(root, 'commit', '-m', 'wrong Git file mode')
        path.chmod(0o644)
    elif defect == 'missing':
        path.rename(root / 'saved-readme')
    with pytest.raises((AssertionError, OSError)):
        restore_policy_parent('README.md', data)


def test_parent_directory_symlink_does_not_create_a_presentation_exception(git_repo, monkeypatch):
    root = git_repo
    monkeypatch.setattr(preservation, 'ROOT', root)
    name = 'src/mfdataindia/web/index.js'
    path = root / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b'actual committed source\n')
    git_at(root, 'add', name)
    git_at(root, 'commit', '-m', 'source')
    (root / 'src').rename(root / 'saved-src')
    (root / 'src').symlink_to(root / 'saved-src', target_is_directory=True)
    with pytest.raises(AssertionError):
        restore_policy_parent(name, path.read_bytes())


@pytest.mark.parametrize('name', ['docs/DEPLOYMENT.md', 'docs/evidence/FS12_DEV_ACCEPTANCE_2026-10-18.md',
                                 'src/mfdataindia/api/app.py', 'sql/001_schema.sql',
                                 'src/mfdataindia/web/new.js', 'docs/new.json'])
def test_human_presentation_scope_never_restores_any_nonallowlisted_change(name):
    assert name not in PRESENTATION and name not in RECIPES
    changed = b'intentional but NOT an approved presentation exception\n'
    assert restore_policy_parent(name, changed) is changed


def test_even_frozen_argument_cannot_hide_dirty_presentation_disk(git_repo, monkeypatch):
    monkeypatch.setattr(preservation, 'ROOT', git_repo)
    old = gzip.decompress(base64.b64decode(PRESENTATION['README.md']['complete_gzip_base64']))
    (git_repo / 'README.md').write_bytes(b'uncommitted data\n')
    with pytest.raises(AssertionError):
        restore_policy_parent('README.md', old)


@pytest.mark.parametrize('head', ['d05ee537d38a0c3afbd93719c7dce119f8be28f8', 'b' * 40])
def test_current_and_future_heads_never_admit_legacy_two_job_metadata(head):
    spec = spec_from_file_location('strict_current_admission', ROOT / 'scripts/prepush_fs13.py')
    pre = module_from_spec(spec)
    spec.loader.exec_module(pre)
    doc = {'databaseId': 123, 'headSha': head, 'name': 'Verify', 'status': 'completed',
           'conclusion': 'success', 'jobs': [
               {'name': name, 'status': 'completed', 'conclusion': 'success',
                'steps': [{'name': 'synthetic', 'status': 'completed', 'conclusion': 'success'}]}
               for name in sorted(pre.JOBS)]}
    with pytest.raises(ValueError):
        pre.require_whole_verify(doc, run_id=123, head_sha=head)


@pytest.mark.parametrize('jobs', [None, {}, 'invalid', [None, None, None],
                                  [{'name': 'Plan CI coverage'}]])
def test_malformed_job_inventory_is_never_admitted(jobs):
    with pytest.raises(ValueError):
        policy.require_full_policy_jobs(jobs)
