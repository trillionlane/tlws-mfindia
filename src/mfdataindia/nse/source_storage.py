"""Source-only ownership composition of immutable NSE table templates.

No role creation, credentials, canonical grants or database CREATE. A reviewed
administrator separately provisions the two fixed roles and an empty namespace.
The direct non-elevated owner installs here; legacy importer guards stay intact.
"""

from hashlib import sha256

from psycopg.pq import TransactionStatus

from . import contract as c, store, source_roles as roles

PROFILE = "nse-source-native-storage-v1"
TEMPLATE45_SHA256 = "bd2edd3201a37721f0493e242a4a2a192146b0a3518ff5965e017c34204a5207"
TEMPLATE48_SHA256 = "9803ac14cd50f98063f0754f77bec1dc5f0ccb1c47c7891b43b269c6978d729b"
MIGRATION = store.MIGRATION.with_name("049_nse_sch44_source_native.sql")


def definition():
    old = store.MIGRATION.read_bytes()
    newer = store.MIGRATION.with_name("048_nse_sch44_uat_publication.sql").read_bytes()
    c.require(
        sha256(old).hexdigest() == TEMPLATE45_SHA256
        and sha256(newer).hexdigest() == TEMPLATE48_SHA256,
        "NSE_SOURCE_TEMPLATE_DIFFERS",
    )
    ddl = old.decode()
    # Exact versioned adaptations, not an assertion that the changed SQL is 045.
    # The source-only writer cannot advance the separate FULL head/current table.
    for removed in (
        "CREATE SCHEMA mf_nse AUTHORIZATION mfdata_admin;",
        "GRANT SELECT, UPDATE ON mf_nse.family_heads TO mfdata_ingest;",
        "GRANT SELECT, INSERT, DELETE ON mf_nse.current_sch TO mfdata_ingest;",
        "GRANT USAGE ON SCHEMA mf TO mfdata_ingest;",
        "GRANT SELECT ON mf.amcs, mf.funds, mf.nav_history TO mfdata_ingest;",
    ):
        c.require(ddl.count(removed) == 1, "NSE_SOURCE_TEMPLATE_DIFFERS")
        ddl = ddl.replace(removed, "")
    c.require(ddl.count("TO mfdata_ingest") == 3, "NSE_SOURCE_TEMPLATE_DIFFERS")
    ddl = ddl.replace("TO mfdata_ingest", "TO " + roles.WRITER)
    extension = MIGRATION.read_text()
    executable = ddl + "\n" + newer.decode() + "\n" + extension
    document = {
        "format": PROFILE,
        "owner": roles.OWNER,
        "writer": roles.WRITER,
        "template45_sha256": TEMPLATE45_SHA256,
        "template48_sha256": TEMPLATE48_SHA256,
        "derived45_sha256": sha256(ddl.encode()).hexdigest(),
        "extension49_sha256": sha256(extension.encode()).hexdigest(),
        "executable_sha256": sha256(executable.encode()).hexdigest(),
    }
    return executable, c.digest(document)


def namespace(conn):
    row = conn.execute(
        "SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_nse'"
    ).fetchone()
    c.require(row and row["owner"] == roles.OWNER, "NSE_SOURCE_NAMESPACE_PREPROVISION_REQUIRED")


def verify(conn):
    namespace(conn)
    _, expected = definition()
    row = conn.execute(
        "SELECT version,definition_hash FROM mf_nse.configuration WHERE singleton"
    ).fetchone()
    source = conn.execute(
        "SELECT definition_hash FROM mf_nse.source_configuration WHERE singleton"
    ).fetchone()
    c.require(
        row == {"version": PROFILE, "definition_hash": expected}
        and source == {"definition_hash": expected},
        "SOURCE_INSTALLATION_DIFFERS",
    )


def install(conn):
    c.require(
        conn.info.transaction_status == TransactionStatus.INTRANS, "INSTALL_TRANSACTION_REQUIRED"
    )
    roles.identity(conn, owner=True)
    namespace(conn)
    exists = conn.execute("SELECT to_regclass('mf_nse.source_configuration') AS value").fetchone()[
        "value"
    ]
    if exists:
        verify(conn)
        return
    # Never reinterpret or revoke grants on an existing legacy/UAT installation.
    occupied = conn.execute("""SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='mf_nse') OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='mf_nse') OR EXISTS(SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname='mf_nse') AS value""").fetchone()["value"]
    c.require(not occupied, "NSE_SOURCE_EXISTING_NAMESPACE_INCOMPATIBLE")
    ddl, expected = definition()
    conn.execute(ddl)
    conn.execute("INSERT INTO mf_nse.configuration VALUES(true,%s,%s)", (PROFILE, expected))
    conn.execute("INSERT INTO mf_nse.source_configuration VALUES(true,%s)", (expected,))
    verify(conn)
