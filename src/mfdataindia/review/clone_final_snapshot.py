"""Exact helper-owned checkpoint prefix, shared by generated code and native CI.

Not a caller-owned transaction helper or a live writer authorization. The clone
controller creates a new RR connection and uses this prefix before any proof
query. All original queries, complete seals and dump cleanup remain outside it.
"""

from pathlib import Path


def create_rehearsal_output(incoming: Path) -> Path:
    """Never overwrite the imported dump/state or reuse a partial export."""
    if incoming.is_symlink() or not incoming.is_dir():
        raise ValueError("helper-owned incoming checkpoint directory required")
    output = incoming / "rehearsal-export"
    output.mkdir(mode=0o700)  # No exist_ok, deletion, retry or alternate path.
    return output


DUMP_START = r'''
    # This final connection belongs to the clone helper; no caller work exists.
    # The frozen source's snapshot=True establishes RR but deliberately permits
    # local install probes. Checkpoint export is exclusively READ ONLY.
    conn.execute('SET TRANSACTION READ ONLY')
    from mfdataindia.review.clone_final_snapshot import create_rehearsal_output
    checkpoint_output=create_rehearsal_output(CLONE_ROOT)
    snapshot=conn.execute('SELECT pg_export_snapshot() AS snapshot').fetchone()['snapshot']
    dump_started=time.monotonic()
    # Same RR snapshot; overlaps read-only proof, not another cloud job or writer.
    dump_child=subprocess.Popen(['pg_dump','--format=custom','--snapshot='+snapshot,
        '--file='+str(checkpoint_output/'checkpoint.dump')],env=pg_env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
'''

# Shared by the generated continuation and the full native transport lifecycle.
# No incoming path can accidentally become the next export destination.
DUMP_EXPORT = r'''
checkpoint=cp.export_checkpoint(session,identity.json()['access_token'],checkpoint_output,
    NEXT_CHECKPOINT_CONTEXT,checkpoint_state,checkpoint_logical)
'''
