#!/usr/bin/env python3
"""Bounded private migration-owner job. No source/legacy publisher writes."""
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.review import publication, publication_operations
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import CONNECTION, Manifest, seal_evidence, verify_evidence


def execute(manifest, environment, root):
    if environment['DEPLOYMENT_GIT_SHA'] != manifest.git_sha:
        raise ValueError('publication operator source SHA differs')
    parsed = conninfo_to_dict(environment['MFDATAINDIA_DSN'])
    if (parsed.get('host') != '/cloudsql/'+CONNECTION or parsed.get('dbname') != 'mfdataindia'
            or parsed.get('user') != 'mfdata_admin' or not parsed.get('password') or parsed.get('hostaddr')):
        raise ValueError('publication owner credential differs from approved source')
    operation = environment['PUBLICATION_OPERATION']
    if operation not in ('install','register'):
        raise ValueError('unsupported owner operation')
    with psycopg.connect(environment['MFDATAINDIA_DSN'],row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        if operation == 'install':
            result = publication_operations.install(conn,root)
        else:
            conn.execute("SET LOCAL statement_timeout='30s'")
            document = json.loads(environment['PUBLICATION_RECORD'])
            previous = Manifest.model_validate(document['manifest'])
            evidence = verify_evidence(previous,'certification',document['certificate'])
            if (evidence.get('level') != 'DEV_PRIVATE_HTTP' or evidence.get('origin') != evidence.get('audience')
                    or document.get('publication_policy_hash') != fingerprint(publication.policy())):
                raise ValueError('not matching dated service-origin certification')
            result = {'certificate_id':publication.register(conn,document),'original_source_rows_changed':0}
    return seal_evidence(manifest,'publication-'+operation,{'level':'DEV_CLOUD_SQL_JOB',**result})


if __name__ == '__main__':
    try:
        item = Manifest.model_validate_json(os.environ['REVIEW_MANIFEST'])
        print(json.dumps(execute(item,os.environ,Path('/app')),sort_keys=True))
    except Exception as exc:
        print('research publication operation failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1)
