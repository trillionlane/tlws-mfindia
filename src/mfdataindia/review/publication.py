"""Dated certification, never a pointer that blesses later source inputs."""
from __future__ import annotations

from copy import deepcopy

from psycopg.types.json import Jsonb

from mfdataindia.review import store
from mfdataindia.review.policy import ENGINE_VERSION, fingerprint, policy as metric_policy

_POLICY = {
    "id": "certified-research-publication", "version": 1,
    "engine_version": "research-publication-1",
    "schema_sha256": "4f4590e193dd86e9026f096e3a761b18d28dc46bd763ca7641f86079b98571ed",
    "supported_metric_policy_hash": fingerprint(metric_policy()),
    "supported_engine_version": ENGINE_VERSION,
    "supported_engine_code_hash": store.engine_identity()['code_hash'],
    "certificate_scope": "provider_contract_conformance_not_fund_correctness",
    "selection": "latest successful service-origin certificate completion; certificate hash breaks ties",
    "current": "exact retained input hash equals complete current capture hash",
    "historical": "inclusive retained first_date..as_of window only; exact observations and identity except last_seen_in_source",
    "historical_quality": "eligible at capture; no later disclosed quality signal; no inference from absent assessment",
    "current_comparison": "current exact inputs AND captured eligibility AND current methodology eligibility",
    "historical_comparison": "never eligible for current comparisons",
    "invalidated": "withhold metrics; retained original result remains accessible for audit",
    "missing": "null last_certified; never fall back to uncertified or legacy derived values",
    "consistency": "one repeatable-read read-only transaction for context, certificate and retained inputs",
    "math": "no new return calculation; referenced immutable result policy defines all formulas and units",
    "imputation": "none", "mutation": "append-only certificates; no source or existing artifact edits",
}


def policy():
    return deepcopy(_POLICY)


def verify_installation(conn):
    if not conn.execute("SELECT to_regclass('mf_review.certifications') AS relation").fetchone()['relation']:
        raise ValueError('research publication extension is not installed')
    row = conn.execute('SELECT document FROM mf_review.policies WHERE policy_hash=%s', (fingerprint(policy()),)).fetchone()
    if not row or row['document'] != policy():
        raise ValueError('research publication policy is not installed or differs')


def present(snapshot, retained, result, certificate):
    """Pure dependency decision; historical values never masquerade as current."""
    current_hash = fingerprint(snapshot)
    base = {'format': 'certified-research-publication-v1', 'code': snapshot['code'],
            'publication_policy_hash': fingerprint(policy()), 'certificate_scope': _POLICY['certificate_scope'],
            'current_input_hash': current_hash, 'current_dataset_version': snapshot['dataset_version'],
            'current_source_revision': snapshot['source_revision'], 'current_context': snapshot['context'],
            'current_comparison_eligible': False, 'historical_window_reusable': False,
            'last_certified': None, 'withheld_reasons': []}
    if result is None:
        return {**base, 'state': 'not_certified', 'withheld_reasons': ['no_certificate']}
    exact = current_hash == result['input_hash']
    reasons = []
    if not result['eligible_at_capture']:
        reasons.append('ineligible_at_certification')
    if (result['policy_hash'] != _POLICY['supported_metric_policy_hash']
            or result['engine_version'] != _POLICY['supported_engine_version']
            or result['engine'].get('code_hash') != _POLICY['supported_engine_code_hash']):
        reasons.append('metric_policy_or_engine_not_supported')
    # An appended observation may leave the retained window intact, but an
    # inserted/removed/corrected old observation must invalidate its metrics.
    as_of = result['as_of']
    original_identity = {k: v for k, v in retained['identity'].items() if k != 'last_seen_in_source'}
    live_identity = {k: v for k, v in snapshot['identity'].items() if k != 'last_seen_in_source'}
    if original_identity != live_identity:
        reasons.append('identity_changed')
    first_date = result['first_date']
    historical_points = [p for p in snapshot['observations'] if first_date and as_of and first_date <= p['date'] <= as_of]
    if historical_points != retained['observations']:
        reasons.append('historical_observations_changed')
    quality = snapshot['context']['nav_quality']
    if quality['signals']:
        reasons.append('disclosed_quality_signal')
    if quality['signals'] is None:
        reasons.append('quality_evidence_unavailable')
    if quality['methodology_version'] != retained['context']['nav_quality']['methodology_version']:
        reasons.append('quality_methodology_changed')
    reusable = not reasons
    comparable = exact and reusable and snapshot['context']['methodology']['comparison_eligible']
    # API conformance certification includes ineligible control funds too. The
    # state describes certified ARTIFACTS; eligibility and null reasons remain
    # independent and must not be derived from a green certificate.
    state = 'current_artifact_certified' if exact else 'historical_certified' if reusable else 'historical_invalidated'
    return {**base, 'state': state, 'current_comparison_eligible': comparable,
            'historical_window_reusable': reusable, 'withheld_reasons': reasons,
            'last_certified': {'result_id': result['result_id'], 'input_hash': result['input_hash'],
                'certificate_id': certificate['certificate_id'], 'certified_at': certificate['certified_at'],
                'runtime_git_sha': certificate['runtime_git_sha'],
                'source_revision': result['source_revision'], 'dataset_version': result['dataset_version'],
                'first_date': result['first_date'], 'as_of': as_of, 'captured_context': result['context'],
                'metric_policy_hash': result['policy_hash'],
                'metrics': result['metrics'] if reusable else None, 'unavailable': result['unavailable']}}


def get_publication(conn, code):
    snapshot = store.capture(conn, code)
    row = conn.execute("""
        SELECT c.certificate_id,c.certified_at,c.runtime_git_sha,r.document AS result,s.document AS input
          FROM mf_review.certifications c
          CROSS JOIN LATERAL jsonb_array_elements(c.document->'certificate'->'evidence'->'cohort') member
          JOIN mf_review.results r ON r.result_id=member->>'result_id'
          JOIN mf_review.snapshots s ON s.input_hash=r.input_hash
          WHERE (member->>'code')::integer=%s AND s.amfi_scheme_code=%s AND c.publication_policy_hash=%s
          ORDER BY c.certified_at DESC,c.certificate_id DESC LIMIT 1
    """, (code, code, fingerprint(policy()))).fetchone()
    if not row:
        return present(snapshot, None, None, None)
    return present(snapshot, row['input'], row['result'], row)


def register(conn, document):
    """Trusted operator only, after immutable Cloud execution verification.

    A genuine older certificate is retained as dated evidence, never relabelled
    as validation of current inputs. Read-time exact dependencies govern reuse.
    """
    identity = conn.execute('SELECT session_user AS principal,current_user=session_user AS dedicated').fetchone()
    if not identity['dedicated'] or identity['principal'] not in ('mfdata_admin', 'postgres'):
        raise ValueError('only the direct migration owner records verified cloud evidence')
    verify_installation(conn)
    identifier = fingerprint(document)
    conn.execute("""INSERT INTO mf_review.certifications(certificate_id,publication_policy_hash,
        runtime_git_sha,certified_at,document) VALUES(%s,%s,%s,%s,%s)
        ON CONFLICT(certificate_id) DO NOTHING""",
        (identifier, fingerprint(policy()), document['manifest']['git_sha'],
         document['execution']['status']['completionTime'], Jsonb(document)))
    recorded = conn.execute('SELECT document FROM mf_review.certifications WHERE certificate_id=%s', (identifier,)).fetchone()
    if recorded['document'] != document:
        raise store.Conflict('stored certification differs from exact replay')
    return identifier
