"""Exact helper-owned checkpoint prefix, shared by generated code and native CI.

Not a caller-owned transaction helper or a live writer authorization. The clone
controller creates a new RR connection and uses this prefix before any proof
query. All original queries, complete seals and dump cleanup remain outside it.
"""

DUMP_START = r'''
    # This final connection belongs to the clone helper; no caller work exists.
    # The frozen source's snapshot=True establishes RR but deliberately permits
    # local install probes. Checkpoint export is exclusively READ ONLY.
    conn.execute('SET TRANSACTION READ ONLY')
    snapshot=conn.execute('SELECT pg_export_snapshot() AS snapshot').fetchone()['snapshot']
    dump_started=time.monotonic()
    # Same RR snapshot; overlaps read-only proof, not another cloud job or writer.
    dump_child=subprocess.Popen(['pg_dump','--format=custom','--snapshot='+snapshot,
        '--file='+str(CLONE_ROOT/'checkpoint.dump')],env=pg_env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
'''
