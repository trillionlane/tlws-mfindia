"""Best-effort tagging refresh after a source publication has committed.

The caller must invoke this only after its own successful transaction.  This
module uses a separate dedicated writer DSN, performs no retry, and converts a
tagging-side failure into a bounded receipt so source ingestion stays committed.
"""
from __future__ import annotations

from datetime import date
import hashlib
import os
from pathlib import Path
import re

import psycopg
from psycopg.rows import dict_row

from mfdataindia.review.policy import fingerprint
from . import (
    entity_registry,
    exposure_policy,
    policy,
    store,
    values_policy,
    values_registry,
)

FORMAT = 'mfdata-tag-refresh-attempt-v1'
DSN_ENV = 'MFDATAINDIA_TAG_REFRESH_DSN'
SOURCE_KIND = re.compile(r'[a-z][a-z0-9-]{0,63}')
HASH = re.compile(r'[0-9a-f]{64}')


def _trigger(value):
    if (type(value) is not dict
            or set(value) != {'source_kind', 'source_content_hash', 'observed_on'}
            or type(value['source_kind']) is not str
            or not SOURCE_KIND.fullmatch(value['source_kind'])
            or type(value['source_content_hash']) is not str
            or not HASH.fullmatch(value['source_content_hash'])
            or type(value['observed_on']) is not str):
        raise ValueError('invalid tagging refresh trigger')
    parsed = date.fromisoformat(value['observed_on'])
    if parsed.isoformat() != value['observed_on']:
        raise ValueError('invalid tagging refresh trigger')
    return value


def policy_evidence():
    screens = values_registry.screens()
    activities = values_registry.activities()
    entities = entity_registry.load()
    factual_source_hash = hashlib.sha256(Path(policy.__file__).read_bytes()).hexdigest()
    return {
        'factual_policy_hash': policy.digest({
            'contract': policy.CONTRACT,
            'implementation_sha256': factual_source_hash,
        }),
        'exposure_policy_hash': hashlib.sha256(
            Path(exposure_policy.__file__).read_bytes()
        ).hexdigest(),
        'exposure_registry_hash': fingerprint(entities),
        'values_screen_registry_hash': fingerprint(screens),
        'values_activity_registry_hash': fingerprint(activities),
        'values_policy_hash': hashlib.sha256(
            Path(values_policy.__file__).read_bytes()
        ).hexdigest(),
    }


def after_success(trigger, *, environ=None, connector=None, publisher=None):
    """Attempt one non-replayable refresh and return a value-safe receipt."""
    environment = os.environ if environ is None else environ
    validated_trigger = None
    try:
        validated_trigger = _trigger(trigger)
        evidence = policy_evidence()
        attempt_id = fingerprint({
            'format': FORMAT, 'trigger': validated_trigger, 'policy': evidence
        })
        base = {
            'format': FORMAT,
            'attempt_id': attempt_id,
            'trigger': validated_trigger,
            **evidence,
            'retry_performed': False,
            'source_ingestion_outcome': 'PRESERVED',
            'consumer_acceptance': 'NOT_OBSERVED',
        }
        dsn = environment.get(DSN_ENV)
        if not dsn:
            return {**base, 'state': 'NOT_CONFIGURED', 'tag_snapshot_id': None}
        connect = psycopg.connect if connector is None else connector
        publish = store.publish if publisher is None else publisher
        with connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
            report = publish(conn)
        if type(report) is not dict or report.get('state') not in {'PUBLISHED', 'UNCHANGED'}:
            raise ValueError('tag refresh result differs')
        snapshot = report.get('snapshot_id')
        if type(snapshot) is not str or not HASH.fullmatch(snapshot):
            raise ValueError('tag refresh result differs')
        return {**base, 'state': report['state'], 'tag_snapshot_id': snapshot}
    except Exception:
        # Do not leak DSNs, SQL, source values, or provider details. The source
        # transaction already committed and must not be rolled back or retried.
        return {
            'format': FORMAT,
            'attempt_id': None,
            'trigger': validated_trigger,
            'state': 'FAILED',
            'reason': 'TAG_REFRESH_FAILED',
            'retry_performed': False,
            'source_ingestion_outcome': 'PRESERVED',
            'consumer_acceptance': 'NOT_OBSERVED',
            'tag_snapshot_id': None,
        }
