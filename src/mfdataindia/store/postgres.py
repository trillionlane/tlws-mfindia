"""PostgreSQL storage layer for MFDataIndia.

Design notes
------------
* Bulk loads go through an unpartitioned UNLOGGED staging table + COPY, then a
  single set-based ``INSERT ... SELECT ... ON CONFLICT``. This is the fastest
  idempotent pattern in PostgreSQL and avoids per-row round-trips: COPY is
  streamed, and partition routing for ``nav_history`` happens once, set-wise.
* GENERATED columns (``funds.in_scope``, ``funds.isin_primary``) and IDENTITY
  columns (``amcs.amc_id``) are never written; they are derived by the database
  so they cannot drift from their inputs.
* ``amc_id`` is resolved in SQL by joining staging on the exact AMFI AMC string,
  so the loader never has to round-trip surrogate keys through Python.
* NAV is written as ``Decimal`` to match ``NUMERIC(18,4)`` exactly. Passing a
  float here would reintroduce the representation error the schema exists to
  prevent.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

#: Migration files, in dependency order.
DEFAULT_MIGRATIONS: tuple[str, ...] = (
    "000_schema_migrations.sql",
    "001_core_schema.sql",
    "002_nav_and_views.sql",
    "003_enrichment_recon.sql",
    "004_groww_enrichment.sql",
    "005_groww_deep_enrichment.sql",
    "006_groww_provenance.sql",
    # 007 is superseded by 008 (same view, rebuilt), so only 008 is applied.
    "008_fund_data_status_v2.sql",
    # Derived table (idempotent CREATE TABLE) required by the /api/funds/
    # {code}/risk-reward endpoint, so it is a core migration rather than a
    # manually-applied reporting view like 007/008. Populated by
    # scripts/refresh_risk_profile.py.
    "009_fund_risk_profile.sql",
    "010_computed_metrics.sql",
    "011_amc_factsheets.sql",
    # Compliance purge: drop aggregator-derived identity/provenance columns, the
    # fund_opinions table, and aggregator checkpoint/source values, and add our
    # own fund_family identity table. A DB built via apply_migrations must end in
    # the same post-purge schema as a fresh compose boot (whose initdb runs every
    # file in sql/), so the purge migrations are core, not optional.
    "012_drop_aggregator_identity.sql",
    "013_purge_aggregator_references.sql",
    "014_ingest_role_privileges.sql",
    "015_dataset_summary.sql",
)

#: Columns the loader may write on mf.funds. GENERATED/derived columns omitted.
FUND_COLUMNS: tuple[str, ...] = (
    "amfi_scheme_code",
    "mfapi_scheme_code",
    "scheme_name",
    "scheme_name_norm",
    "amfi_amc_name",          # staging-only: resolved to amc_id by SQL join
    "scheme_type",
    "scheme_category",
    "scheme_category_raw",
    "category_source",
    "plan_type",
    "plan_source",
    "option_type",
    "periodicity",
    "is_etf",
    "is_defunct",
    "nav_not_published",
    "isin_growth_or_div_payout",
    "isin_div_reinvest",
    "first_seen_in_source",
    "last_seen_in_source",
    "is_active",
    "metadata_authority",
)


@dataclass(slots=True)
class LoadResult:
    """Outcome of one bulk load operation."""

    target: str = ""
    staged: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "staged": self.staged,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "errors": self.errors,
        }


def sha256_text(text: str) -> str:
    """SHA-256 of a payload, for mf.source_metadata.content_hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

class PostgresStore:
    """Thin, explicit PostgreSQL gateway. No ORM: the SQL is the contract."""

    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout: int = 15,
        autocommit: bool = True,
        use_copy: Optional[bool] = None,
    ) -> None:
        """
        Parameters
        ----------
        use_copy:
            ``None`` (default) probes COPY support once at connect time. Pass
            ``True``/``False`` to declare it explicitly and skip the probe —
            useful behind proxies that dislike the extra round-trip, or against
            servers known to mishandle the COPY sub-protocol.
        autocommit:
            Defaults True. With autocommit on, a bare read never leaves the shared
            connection INTRANS, which would otherwise turn a later
            ``transaction()`` into a nested savepoint whose commit is not durable.
            Multi-statement atomicity still comes from :meth:`transaction`, which
            issues explicit BEGIN/COMMIT.
        """
        self.dsn = dsn
        self.connect_timeout = connect_timeout
        self.autocommit = autocommit
        self._conn: Optional[psycopg.Connection] = None
        #: COPY is the fast path; some servers do not implement its sub-protocol
        #: faithfully, in which case staging falls back to batched INSERT.
        self.use_copy: bool = True if use_copy is None else bool(use_copy)
        #: Probe verdict, cached for the lifetime of this store instance.
        self._copy_support: Optional[bool] = None if use_copy is None else bool(use_copy)

    # -- connection lifecycle ------------------------------------------------

    def _open(self) -> psycopg.Connection:
        """Open a connection and pin the session search path to mf, public.

        ``prepare_threshold=None`` disables server-side prepared statements.
        psycopg auto-prepares a statement after repeated executions, which
        (a) PGlite's socket bridge rejects with DuplicatePreparedStatement, and
        (b) breaks behind transaction-pooling proxies like pgbouncer. Disabling
        is the portable choice; the performance cost is negligible for this
        workload (bulk loads are set-based, not hot-loop prepared statements).
        """
        conn = psycopg.connect(
            self.dsn,
            connect_timeout=self.connect_timeout,
            autocommit=self.autocommit,
            row_factory=dict_row,
            prepare_threshold=None,
        )
        with conn.cursor() as cur:
            cur.execute("SET search_path TO mf, public")
        return conn

    def connect(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            conn = self._open()
            if self._copy_support is None:
                self._copy_support = self._probe_copy(conn)
                if conn.closed:
                    # Some servers drop the connection when a COPY attempt fails.
                    # Reopen cleanly; the verdict is already cached so we do not
                    # probe (and break) the new connection too.
                    log.info("reopening connection after COPY probe")
                    conn = self._open()
            self.use_copy = self._copy_support
            self._conn = conn
            log.debug("connected to %s", self.dsn.split("@")[-1])
        return self._conn

    @staticmethod
    def _probe_copy(conn: psycopg.Connection) -> bool:
        """Detect whether the server implements COPY ... FROM STDIN faithfully.

        Probing at connect time (rather than catching a failure mid-load) matters:
        a COPY error aborts the surrounding transaction, so recovering inside the
        load would mean discarding work already staged. Deciding up front keeps
        every bulk load single-transaction and all-or-nothing.

        Real PostgreSQL always passes. Embedded/proxied servers that emulate the
        wire protocol (PGlite's socket bridge, some transaction poolers) may not.
        """
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE _mf_copy_probe (a integer)")
                with cur.copy("COPY _mf_copy_probe (a) FROM STDIN") as copy:
                    copy.write_row((1,))
                cur.execute("SELECT count(*) AS n FROM _mf_copy_probe")
                ok = int(cur.fetchone()["n"]) == 1
                cur.execute("DROP TABLE IF EXISTS _mf_copy_probe")
            conn.commit()
            return ok
        except Exception as exc:  # noqa: BLE001 - any failure means "no COPY"
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            log.warning(
                "COPY ... FROM STDIN unavailable on this server (%s); "
                "staging will use batched INSERT instead",
                exc,
            )
            return False

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> "PostgresStore":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """One transaction per bulk operation: all-or-nothing per source file."""
        conn = self.connect()
        with conn.transaction():
            yield conn

    # -- migrations ----------------------------------------------------------

    def apply_migrations(
        self,
        sql_dir: str | Path,
        files: Sequence[str] = DEFAULT_MIGRATIONS,
    ) -> list[str]:
        """Apply each forward migration once and verify its immutable checksum.

        An existing ``mf`` application schema without the migration ledger is a
        restore/baseline case and fails closed. Its historical migrations must be
        recorded only after a separate schema-manifest verification; blindly
        replaying the one-way purge migrations is not supported.
        """
        sql_dir = Path(sql_dir)
        applied: list[str] = []
        conn = self.connect()
        has_ledger = conn.execute(
            "SELECT to_regclass('mf.schema_migrations') IS NOT NULL AS present"
        ).fetchone()["present"]
        has_application_schema = conn.execute(
            "SELECT to_regclass('mf.funds') IS NOT NULL AS present"
        ).fetchone()["present"]
        if has_application_schema and not has_ledger:
            raise RuntimeError(
                "existing mf schema has no migration ledger; verify and baseline "
                "the restored schema before applying forward migrations"
            )

        for name in files:
            path = sql_dir / name
            if not path.exists():
                raise FileNotFoundError(f"migration not found: {path}")
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if has_ledger:
                row = conn.execute(
                    "SELECT content_sha256 FROM mf.schema_migrations "
                    "WHERE migration_name = %s",
                    (name,),
                ).fetchone()
                if row:
                    if row["content_sha256"] != checksum:
                        raise RuntimeError(
                            f"applied migration checksum mismatch: {name}"
                        )
                    continue
            # Each file carries its own BEGIN/COMMIT, so run it in autocommit.
            # connect() issued a SET search_path, which leaves the connection
            # INTRANS; psycopg forbids changing autocommit in that state, so
            # close the pending transaction first.
            if not conn.autocommit:
                conn.commit()
            prev_autocommit = conn.autocommit
            conn.autocommit = True
            try:
                conn.execute(sql)
                has_ledger = conn.execute(
                    "SELECT to_regclass('mf.schema_migrations') IS NOT NULL AS present"
                ).fetchone()["present"]
                if not has_ledger:
                    raise RuntimeError(
                        f"migration {name} did not create mf.schema_migrations"
                    )
                conn.execute(
                    "INSERT INTO mf.schema_migrations (migration_name, content_sha256) "
                    "VALUES (%s, %s)",
                    (name, checksum),
                )
            finally:
                conn.autocommit = prev_autocommit
            applied.append(name)
            log.info("applied migration %s", name)
        return applied

    def server_version(self) -> str:
        with self.connect().cursor() as cur:
            return cur.execute("SELECT version()").fetchone()["version"]

    # -- staging helpers -----------------------------------------------------

    def _drop(self, cur: psycopg.Cursor, name: str) -> None:
        cur.execute(f"DROP TABLE IF EXISTS mf.{name}")

    def _create_staging(self, cur: psycopg.Cursor, name: str, ddl_columns: str) -> None:
        """UNLOGGED + no indexes: staging is write-once, read-once, then dropped.

        UNLOGGED skips WAL, which is the main win on a multi-million-row COPY.
        The table is dropped in the same transaction, so durability is moot.
        """
        self._drop(cur, name)
        cur.execute(f"CREATE UNLOGGED TABLE mf.{name} ({ddl_columns})")

    def _copy_rows(
        self,
        cur: psycopg.Cursor,
        table: str,
        columns: Sequence[str],
        rows: Iterator[Sequence[Any]],
    ) -> int:
        """Stage rows into ``mf.<table>``. Returns the row count written.

        Uses COPY ... FROM STDIN when the server supports it (the fast path for
        millions of NAV rows) and batched INSERT otherwise. The choice was made
        by the connect-time probe, so this never fails mid-transaction.
        """
        if self.use_copy:
            col_sql = ", ".join(columns)
            count = 0
            with cur.copy(f"COPY mf.{table} ({col_sql}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
                    count += 1
            return count
        return self._insert_rows(cur, table, columns, rows)

    @staticmethod
    def _insert_rows(
        cur: psycopg.Cursor,
        table: str,
        columns: Sequence[str],
        rows: Iterator[Sequence[Any]],
        batch_size: int = 2_000,
    ) -> int:
        """Batched-INSERT staging fallback.

        Slower than COPY (one round-trip per batch rather than a stream) but
        portable across servers and proxies that mishandle the COPY sub-protocol.
        """
        col_sql = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        stmt = f"INSERT INTO mf.{table} ({col_sql}) VALUES ({placeholders})"
        count = 0
        batch: list[tuple] = []
        for row in rows:
            batch.append(tuple(row))
            if len(batch) >= batch_size:
                cur.executemany(stmt, batch)
                count += len(batch)
                batch = []
        if batch:
            cur.executemany(stmt, batch)
            count += len(batch)
        return count

    def upsert_table(
        self,
        table: str,
        pk: str,
        columns: Sequence[str],
        rows: Iterator[Sequence[Any]],
        *,
        column_types: Optional[dict[str, str]] = None,
        update_columns: Optional[Sequence[str]] = None,
        coalesce_missing: bool = False,
        fill_only: bool = False,
        overwrite_columns: Sequence[str] = (),
    ) -> LoadResult:
        """Generic set-based upsert into ``mf.<table>`` keyed on ``pk``.

        ``columns`` must include ``pk``. Rows are staged into an all-TEXT staging
        table (so any Python value can be written), then merged with
        ``ON CONFLICT (pk) DO UPDATE``, casting each staged value to its real type
        given by ``column_types`` (default: text). Values written as text — a dict
        serialised to JSON, a Decimal, a date — cast cleanly, which is why the
        staging is untyped. On conflict only non-pk ``update_columns`` refresh.

        ``coalesce_missing`` switches the conflict to *merge* semantics: each
        stored column is set to ``COALESCE(EXCLUDED.col, t.col)``, so a value
        incoming as NULL never blanks a value already present. This is how a
        second source (Groww) fills gaps in a row a first source (Scripbox)
        already owns, keyed on the same ``pk``. The refreshed set is still
        ``update_columns`` (pass it to keep e.g. the ``source`` tag from the
        original writer).

        ``fill_only`` is the strict form of that, and is what a secondary source
        should use when it must never replace data it does not own: each stored
        column is set to ``COALESCE(t.col, EXCLUDED.col)``, i.e. **the existing
        value wins** and the incoming value lands only where the column is still
        NULL. Columns named in ``overwrite_columns`` are exempted and keep normal
        incoming-wins semantics — use it for the columns the secondary source
        genuinely owns (its own ratings, its own fetch stamp) so a re-run can
        still refresh them. The conflict is guarded, so a row that would gain
        nothing is left untouched and its ``fetched_at`` does not move.
        """
        res = LoadResult(target=table)
        materialised = [tuple(r) for r in rows]
        res.staged = len(materialised)
        if not materialised:
            return res
        if pk not in columns:
            raise ValueError(f"pk {pk!r} must be present in columns")

        types = column_types or {}
        non_pk = [c for c in columns if c != pk]
        to_update = list(update_columns) if update_columns is not None else non_pk

        def _cast(col: str) -> str:
            t = types.get(col)
            return f"NULLIF(s.{col}, '')::{t}" if t else f"s.{col}"

        with self.transaction() as conn, conn.cursor() as cur:
            ddl = ", ".join(f"{c} text" for c in columns)
            self._create_staging(cur, f"stg_{table}", ddl)
            self._copy_rows(cur, f"stg_{table}", columns, iter(materialised))

            before = self._count(cur, table)
            if fill_only and coalesce_missing:
                raise ValueError(
                    "fill_only and coalesce_missing are mutually exclusive")
            owned = set(overwrite_columns)
            if fill_only:
                # Gap-fill: the stored value wins, so a secondary source can only
                # write into columns the primary source left NULL. Columns it
                # genuinely owns keep incoming-wins so a re-run can refresh them.
                set_clause = ", ".join(
                    f"{c} = EXCLUDED.{c}" if c in owned
                    else f"{c} = COALESCE(t.{c}, EXCLUDED.{c})"
                    for c in to_update)
                guard = " OR ".join(
                    f"t.{c} IS DISTINCT FROM EXCLUDED.{c}" if c in owned
                    else f"(t.{c} IS NULL AND EXCLUDED.{c} IS NOT NULL)"
                    for c in to_update)
                where_sql = f"WHERE {guard}"
            elif coalesce_missing:
                # Merge: keep the stored value when the incoming value is NULL,
                # so a later source fills gaps without blanking the first. No
                # guard — an upsert always touches the row (fetched_at moves).
                set_clause = ", ".join(
                    f"{c} = COALESCE(EXCLUDED.{c}, t.{c})" for c in to_update)
                where_sql = ""
            else:
                set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in to_update)
                # ON CONFLICT's WHERE sees only the existing row (t) and EXCLUDED;
                # the staging subquery (s) is not in scope here.
                guard = " OR ".join(
                    f"t.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in to_update)
                where_sql = f"WHERE {guard}"
            col_list = ", ".join(columns)
            sel_list = ", ".join(_cast(c) for c in columns)
            cur.execute(
                f"""
                INSERT INTO mf.{table} AS t ({col_list})
                SELECT {sel_list} FROM (
                    SELECT DISTINCT ON ({pk}) * FROM mf.stg_{table} ORDER BY {pk}
                ) s
                ON CONFLICT ({pk}) DO UPDATE SET {set_clause}
                {where_sql}
                """
            )
            written = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            res.inserted = self._count(cur, table) - before
            res.updated = max(0, written - res.inserted)
            res.unchanged = max(0, res.staged - written)
            self._drop(cur, f"stg_{table}")
        log.info("%s: staged=%d inserted=%d updated=%d unchanged=%d",
                 table, res.staged, res.inserted, res.updated, res.unchanged)
        return res

    @staticmethod
    def _count(cur: psycopg.Cursor, table: str) -> int:
        cur.execute(f"SELECT count(*) AS n FROM mf.{table}")
        return int(cur.fetchone()["n"])

    # -- loads ---------------------------------------------------------------

    def load_amcs(self, amc_names: Sequence[str]) -> LoadResult:
        """Register AMCs seen in a source. Existing rows are left untouched.

        Only the exact AMFI header string and its normalisation are written here;
        website/portfolio URLs come from the AMFI directory seed separately.
        """
        from mfdataindia.load.normalise import normalise_amc

        res = LoadResult(target="amcs")
        uniq = sorted({n for n in amc_names if n and n.strip()})
        if not uniq:
            return res

        with self.transaction() as conn, conn.cursor() as cur:
            self._create_staging(
                cur,
                "stg_amcs",
                "amfi_amc_name text NOT NULL, normalised_name text NOT NULL",
            )
            res.staged = self._copy_rows(
                cur,
                "stg_amcs",
                ("amfi_amc_name", "normalised_name"),
                ((n, normalise_amc(n)) for n in uniq),
            )
            before = self._count(cur, "amcs")
            cur.execute(
                """
                INSERT INTO mf.amcs (amfi_amc_name, normalised_name)
                SELECT s.amfi_amc_name, s.normalised_name
                FROM mf.stg_amcs s
                ON CONFLICT (amfi_amc_name) DO NOTHING
                """
            )
            res.inserted = self._count(cur, "amcs") - before
            res.unchanged = res.staged - res.inserted
            self._drop(cur, "stg_amcs")

        log.info("amcs: staged=%d inserted=%d", res.staged, res.inserted)
        return res

    #: Staging DDL for funds. Types must match mf.funds so the merge needs no casts.
    _STG_FUNDS_DDL = """
        amfi_scheme_code          integer NOT NULL,
        mfapi_scheme_code         integer,
        scheme_name               text    NOT NULL,
        scheme_name_norm          text    NOT NULL,
        amfi_amc_name             text,
        scheme_type               text    NOT NULL,
        scheme_category           text    NOT NULL,
        scheme_category_raw       text,
        category_source           text,
        plan_type                 text    NOT NULL,
        plan_source               text,
        option_type               text    NOT NULL,
        periodicity               text,
        is_etf                    boolean NOT NULL DEFAULT false,
        is_defunct                boolean NOT NULL DEFAULT false,
        nav_not_published         boolean NOT NULL DEFAULT false,
        isin_growth_or_div_payout char(12),
        isin_div_reinvest         char(12),
        first_seen_in_source      date,
        last_seen_in_source       date,
        is_active                 boolean NOT NULL DEFAULT true,
        metadata_authority        text
    """

    def load_funds(self, rows: Iterator[Sequence[Any]]) -> LoadResult:
        """Upsert mf.funds from staged AMFI rows.

        ``rows`` yields values in FUND_COLUMNS order. ``amc_id`` is resolved in
        SQL by joining staging to mf.amcs on the exact AMFI string, so an
        unregistered AMC raises instead of silently dropping schemes.
        """
        res = LoadResult(target="funds")
        with self.transaction() as conn, conn.cursor() as cur:
            self._create_staging(cur, "stg_funds", self._STG_FUNDS_DDL)
            res.staged = self._copy_rows(cur, "stg_funds", FUND_COLUMNS, rows)
            if res.staged == 0:
                self._drop(cur, "stg_funds")
                return res

            # Fail loudly on an AMC we have not registered: silently dropping a
            # scheme is a coverage hole that is very hard to find later.
            cur.execute(
                """
                SELECT s.amfi_amc_name, count(*) AS n
                FROM mf.stg_funds s
                LEFT JOIN mf.amcs a ON a.amfi_amc_name = s.amfi_amc_name
                WHERE a.amc_id IS NULL AND s.amfi_amc_name IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC LIMIT 10
                """
            )
            orphans = cur.fetchall()
            if orphans:
                names = [f"{r['amfi_amc_name']} ({r['n']})" for r in orphans]
                raise ValueError(
                    "funds staging references AMC name(s) absent from mf.amcs; "
                    f"call load_amcs() first. Offenders: {names}"
                )
            res.updated, res.inserted, res.unchanged = self._merge_funds(cur, res.staged)
            self._drop(cur, "stg_funds")

        log.info(
            "funds: staged=%d inserted=%d updated=%d unchanged=%d",
            res.staged, res.inserted, res.updated, res.unchanged,
        )
        return res

    def _merge_funds(self, cur: psycopg.Cursor, staged: int) -> tuple[int, int, int]:
        """Set-based upsert. Returns (updated, inserted, unchanged).

        DISTINCT ON guards against a duplicated scheme code in the source, which
        would otherwise abort the statement with "cannot affect row a second
        time". The WHERE clause on DO UPDATE means an unchanged row is not
        rewritten, so a daily re-ingest does not churn the whole table.
        """
        before = self._count(cur, "funds")
        cur.execute(
            """
            INSERT INTO mf.funds AS f (
                amfi_scheme_code, mfapi_scheme_code, scheme_name, scheme_name_norm,
                amc_id, scheme_type, scheme_category, scheme_category_raw,
                category_source, plan_type, plan_source, option_type, periodicity,
                is_etf, is_defunct, nav_not_published,
                isin_growth_or_div_payout, isin_div_reinvest,
                first_seen_in_source, last_seen_in_source, is_active,
                metadata_authority, updated_at
            )
            SELECT s.amfi_scheme_code, s.mfapi_scheme_code, s.scheme_name,
                   s.scheme_name_norm, a.amc_id, s.scheme_type, s.scheme_category,
                   s.scheme_category_raw, s.category_source, s.plan_type,
                   s.plan_source, s.option_type, s.periodicity, s.is_etf, s.is_defunct,
                   s.nav_not_published, s.isin_growth_or_div_payout,
                   s.isin_div_reinvest, s.first_seen_in_source,
                   s.last_seen_in_source, s.is_active, s.metadata_authority, now()
            FROM (
                SELECT DISTINCT ON (amfi_scheme_code) *
                FROM mf.stg_funds ORDER BY amfi_scheme_code
            ) s
            JOIN mf.amcs a ON a.amfi_amc_name = s.amfi_amc_name
            ON CONFLICT (amfi_scheme_code) DO UPDATE SET
                mfapi_scheme_code         = EXCLUDED.mfapi_scheme_code,
                scheme_name               = EXCLUDED.scheme_name,
                scheme_name_norm          = EXCLUDED.scheme_name_norm,
                amc_id                    = EXCLUDED.amc_id,
                scheme_type               = EXCLUDED.scheme_type,
                scheme_category           = EXCLUDED.scheme_category,
                scheme_category_raw       = EXCLUDED.scheme_category_raw,
                category_source           = EXCLUDED.category_source,
                plan_type                 = EXCLUDED.plan_type,
                plan_source               = EXCLUDED.plan_source,
                option_type               = EXCLUDED.option_type,
                periodicity               = EXCLUDED.periodicity,
                is_etf                    = EXCLUDED.is_etf,
                is_defunct                = EXCLUDED.is_defunct,
                nav_not_published         = EXCLUDED.nav_not_published,
                isin_growth_or_div_payout = EXCLUDED.isin_growth_or_div_payout,
                isin_div_reinvest         = EXCLUDED.isin_div_reinvest,
                last_seen_in_source       = EXCLUDED.last_seen_in_source,
                is_active                 = EXCLUDED.is_active,
                metadata_authority        = EXCLUDED.metadata_authority,
                updated_at                = now()
            WHERE f.scheme_name               IS DISTINCT FROM EXCLUDED.scheme_name
               OR f.scheme_category           IS DISTINCT FROM EXCLUDED.scheme_category
               OR f.scheme_type               IS DISTINCT FROM EXCLUDED.scheme_type
               OR f.plan_type                 IS DISTINCT FROM EXCLUDED.plan_type
               OR f.plan_source               IS DISTINCT FROM EXCLUDED.plan_source
               OR f.option_type               IS DISTINCT FROM EXCLUDED.option_type
               OR f.periodicity               IS DISTINCT FROM EXCLUDED.periodicity
               OR f.is_etf                    IS DISTINCT FROM EXCLUDED.is_etf
               OR f.is_defunct                IS DISTINCT FROM EXCLUDED.is_defunct
               OR f.nav_not_published         IS DISTINCT FROM EXCLUDED.nav_not_published
               OR f.is_active                 IS DISTINCT FROM EXCLUDED.is_active
               OR f.amc_id                    IS DISTINCT FROM EXCLUDED.amc_id
               OR f.isin_growth_or_div_payout IS DISTINCT FROM EXCLUDED.isin_growth_or_div_payout
               OR f.isin_div_reinvest         IS DISTINCT FROM EXCLUDED.isin_div_reinvest
            """
        )
        written = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        inserted = self._count(cur, "funds") - before
        updated = written - inserted
        unchanged = staged - written
        return max(0, updated), max(0, inserted), max(0, unchanged)

    # -- NAV -----------------------------------------------------------------

    _STG_NAV_DDL = """
        amfi_scheme_code  integer       NOT NULL,
        nav_date          date          NOT NULL,
        nav               numeric(18,4) NOT NULL,
        source            text          NOT NULL,
        is_cross_verified boolean       NOT NULL DEFAULT false
    """

    def ensure_nav_partitions(self, years: Iterable[int]) -> list[str]:
        """Create any missing yearly partitions before a NAV load.

        Called ahead of every NAV ingest so a new year never aborts a run.
        """
        created: list[str] = []
        with self.transaction() as conn, conn.cursor() as cur:
            for year in sorted(set(years)):
                cur.execute("SELECT mf.ensure_nav_partition(%s) AS p", (year,))
                created.append(cur.fetchone()["p"])
        return created

    def upsert_nav(
        self,
        rows: Iterator[Sequence[Any]],
        *,
        ensure_partitions: bool = True,
    ) -> LoadResult:
        """Bulk-upsert NAV history.

        ``rows`` yields (amfi_scheme_code, nav_date, nav, source,
        is_cross_verified). ``nav`` must be a Decimal or int/str — never a float,
        because mf.nav_history is NUMERIC(18,4) and a float would reintroduce the
        representation error the column type exists to prevent.
        """
        res = LoadResult(target="nav_history")
        staged_rows: list[Sequence[Any]] = []
        years: set[int] = set()
        for r in rows:
            staged_rows.append(r)
            d = r[1]
            if isinstance(d, date):
                years.add(d.year)

        if ensure_partitions and years:
            self.ensure_nav_partitions(years)

        with self.transaction() as conn, conn.cursor() as cur:
            self._create_staging(cur, "stg_nav", self._STG_NAV_DDL)
            res.staged = self._copy_rows(
                cur,
                "stg_nav",
                ("amfi_scheme_code", "nav_date", "nav", "source", "is_cross_verified"),
                iter(staged_rows),
            )
            if res.staged == 0:
                self._drop(cur, "stg_nav")
                return res

            before = self._count(cur, "nav_history")
            cur.execute(
                """
                INSERT INTO mf.nav_history AS n (
                    amfi_scheme_code, nav_date, nav, source, is_cross_verified, ingested_at
                )
                SELECT s.amfi_scheme_code, s.nav_date, s.nav, s.source,
                       s.is_cross_verified, now()
                FROM (
                    SELECT DISTINCT ON (amfi_scheme_code, nav_date) *
                    FROM mf.stg_nav ORDER BY amfi_scheme_code, nav_date
                ) s
                ON CONFLICT (amfi_scheme_code, nav_date) DO UPDATE SET
                    nav               = EXCLUDED.nav,
                    source            = EXCLUDED.source,
                    -- once both sources have been seen and agree, stay verified
                    is_cross_verified = n.is_cross_verified OR EXCLUDED.is_cross_verified,
                    ingested_at       = now()
                WHERE n.nav    IS DISTINCT FROM EXCLUDED.nav
                   OR n.source IS DISTINCT FROM EXCLUDED.source
                   OR (EXCLUDED.is_cross_verified AND NOT n.is_cross_verified)
                """
            )
            written = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            res.inserted = self._count(cur, "nav_history") - before
            res.updated = max(0, written - res.inserted)
            res.unchanged = max(0, res.staged - written)
            self._drop(cur, "stg_nav")

        log.info(
            "nav_history: staged=%d inserted=%d updated=%d unchanged=%d",
            res.staged, res.inserted, res.updated, res.unchanged,
        )
        return res

    def record_flags(
        self,
        rows: Iterator[Sequence[Any]],
        *,
        replace_source: Optional[str] = None,
    ) -> LoadResult:
        """Append mf.quality_flags rows.

        quality_flags has no natural unique key (it is an append-only log with an
        IDENTITY id), so a naive re-run would duplicate every flag. When
        ``replace_source`` is given, that source's previously open flags are
        cleared first, which makes a repeated ingest idempotent while leaving
        manually-resolved history intact.
        """
        res = LoadResult(target="quality_flags")
        materialised = [tuple(r) for r in rows]
        res.staged = len(materialised)

        with self.transaction() as conn, conn.cursor() as cur:
            if replace_source:
                cur.execute(
                    """
                    DELETE FROM mf.quality_flags
                    WHERE source = %s AND resolved_at IS NULL
                    """,
                    (replace_source,),
                )
                res.unchanged = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            if not materialised:
                return res
            self._create_staging(
                cur,
                "stg_flags",
                """
                amfi_scheme_code integer,
                nav_date         date,
                flag_type        text NOT NULL,
                severity         text NOT NULL,
                message          text NOT NULL,
                details          jsonb,
                source           text
                """,
            )
            self._copy_rows(
                cur,
                "stg_flags",
                (
                    "amfi_scheme_code", "nav_date", "flag_type",
                    "severity", "message", "details", "source",
                ),
                iter(materialised),
            )
            cur.execute(
                """
                INSERT INTO mf.quality_flags (
                    amfi_scheme_code, nav_date, flag_type, severity,
                    message, details, source
                )
                SELECT amfi_scheme_code, nav_date, flag_type, severity,
                       message, details, source
                FROM mf.stg_flags
                """
            )
            res.inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            self._drop(cur, "stg_flags")

        log.info(
            "quality_flags: inserted=%d (cleared %d prior open flags for source=%s)",
            res.inserted, res.unchanged, replace_source,
        )
        return res

    # -- reads ---------------------------------------------------------------

    def coverage(self) -> dict[str, Any]:
        """Scheme-level coverage summary from mf.v_coverage."""
        with self.connect().cursor() as cur:
            return cur.execute("SELECT * FROM mf.v_coverage").fetchone()

    def refresh_dataset_summary(
        self,
        reason: str,
        *,
        source_content_hash: Optional[str] = None,
    ) -> dict[str, Any]:
        """Refresh and return the exact singleton used by ``/api/stats``.

        The database function performs the expensive aggregate once after a
        governed write. Nested use participates in the caller's outer
        transaction, so a later integrity failure rolls the summary back with
        the data mutation.
        """
        if not reason or len(reason.strip()) > 100:
            raise ValueError("reason must contain 1..100 characters")
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM mf.refresh_dataset_summary(%s, %s)",
                (reason, source_content_hash),
            ).fetchone()
        if row is None:
            raise RuntimeError("dataset summary refresh returned no row")
        return row

    def partition_health(self) -> list[dict[str, Any]]:
        """nav_history partition sizes; nav_history_default should be empty."""
        with self.connect().cursor() as cur:
            return cur.execute("SELECT * FROM mf.v_partition_health").fetchall()

    def open_flags(self, limit: int = 200) -> list[dict[str, Any]]:
        """Unresolved quality flags, newest first."""
        with self.connect().cursor() as cur:
            return cur.execute(
                """
                SELECT flag_id, amfi_scheme_code, nav_date, flag_type, severity,
                       message, detected_at
                FROM mf.quality_flags
                WHERE resolved_at IS NULL
                ORDER BY detected_at DESC, flag_id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def nav_last_5y(
        self,
        *,
        scheme_code: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream the required 5-year window from mf.nav_last_5y.

        Uses a server-side (named) cursor so a multi-million-row export streams
        instead of materialising in client memory.
        """
        query = "SELECT * FROM mf.nav_last_5y"
        params: list[Any] = []
        if scheme_code is not None:
            query += " WHERE amfi_scheme_code = %s"
            params.append(scheme_code)
        query += " ORDER BY amfi_scheme_code, nav_date"
        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)

        conn = self.connect()
        with conn.cursor(name="nav_last_5y_stream") as cur:
            cur.itersize = 10_000
            cur.execute(query, params)
            yield from cur

    def nav_window(
        self,
        years: int = 5,
        asof: Optional[date] = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream mf.nav_window(years, asof) for reproducible historical cuts."""
        conn = self.connect()
        with conn.cursor(name="nav_window_stream") as cur:
            cur.itersize = 10_000
            cur.execute("SELECT * FROM mf.nav_window(%s, %s)", (years, asof))
            yield from cur

    def nav_span(self) -> dict[str, Any]:
        """Overall NAV date span and row count."""
        with self.connect().cursor() as cur:
            return cur.execute(
                """
                SELECT count(*)          AS rows,
                       min(nav_date)     AS first_nav_date,
                       max(nav_date)     AS last_nav_date,
                       count(DISTINCT amfi_scheme_code) AS schemes
                FROM mf.nav_history
                """
            ).fetchone()

    # -- checkpoints ---------------------------------------------------------

    def checkpoint_get(
        self, source: str, entity_kind: str, entity_key: str
    ) -> Optional[dict[str, Any]]:
        """Return the checkpoint row for (source, kind, key), or None."""
        with self.connect().cursor() as cur:
            return cur.execute(
                """
                SELECT source, entity_kind, entity_key, cursor_value, last_nav_date,
                       records_done, status, attempts, last_error, started_at, updated_at
                FROM mf.ingest_checkpoints
                WHERE source = %s AND entity_kind = %s AND entity_key = %s
                """,
                (source, entity_kind, entity_key),
            ).fetchone()

    def checkpoint_should_run(self, source: str, entity_kind: str, entity_key: str) -> bool:
        """True unless the checkpoint is already DONE. Resumable-run gate."""
        row = self.checkpoint_get(source, entity_kind, entity_key)
        return row is None or row["status"] != "DONE"

    def checkpoint_start(self, source: str, entity_kind: str, entity_key: str) -> None:
        """Mark a unit of work IN_PROGRESS (idempotent upsert, bumps attempts)."""
        with self.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mf.ingest_checkpoints
                    (source, entity_kind, entity_key, status, attempts, started_at, updated_at)
                VALUES (%s, %s, %s, 'IN_PROGRESS', 1, now(), now())
                ON CONFLICT (source, entity_kind, entity_key) DO UPDATE SET
                    status     = 'IN_PROGRESS',
                    attempts   = mf.ingest_checkpoints.attempts + 1,
                    started_at = now(),
                    updated_at = now()
                """,
                (source, entity_kind, entity_key),
            )

    def checkpoint_register(
        self, source: str, entity_kind: str, entity_key: str,
        cursor_value: Optional[str] = None,
    ) -> None:
        """Register a PENDING work item (a work queue) without starting it.

        Discovery phases use this to enqueue fetch work; the fetch phase then
        drains PENDING items, so a long crawl is fully resumable from the
        checkpoint table alone.
        """
        with self.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mf.ingest_checkpoints
                    (source, entity_kind, entity_key, cursor_value, status, updated_at)
                VALUES (%s, %s, %s, %s, 'PENDING', now())
                ON CONFLICT (source, entity_kind, entity_key) DO NOTHING
                """,
                (source, entity_kind, entity_key, cursor_value),
            )

    def checkpoint_done(self, source: str, entity_kind: str, entity_key: str,
                        records_done: int, cursor_value: Optional[str] = None) -> None:
        """Mark a unit of work DONE with its record count."""
        with self.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mf.ingest_checkpoints
                SET status = 'DONE', records_done = %s, cursor_value = %s,
                    last_error = NULL, updated_at = now()
                WHERE source = %s AND entity_kind = %s AND entity_key = %s
                """,
                (records_done, cursor_value, source, entity_kind, entity_key),
            )

    def checkpoint_failed(self, source: str, entity_kind: str, entity_key: str,
                          error: str) -> None:
        """Mark a unit of work FAILED with the error; a re-run will retry it."""
        with self.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mf.ingest_checkpoints
                SET status = 'FAILED', last_error = %s, updated_at = now()
                WHERE source = %s AND entity_kind = %s AND entity_key = %s
                """,
                (error, source, entity_kind, entity_key),
            )

    def pending_checkpoints(
        self, source: str, entity_kind: str
    ) -> list[dict[str, Any]]:

        """All non-DONE checkpoints for a source+kind, in key order."""
        with self.connect().cursor() as cur:
            return cur.execute(
                """
                SELECT entity_key, status, attempts, last_error
                FROM mf.ingest_checkpoints
                WHERE source = %s AND entity_kind = %s AND status <> 'DONE'
                ORDER BY entity_key
                """,
                (source, entity_kind),
            ).fetchall()

    # -- reference sets ------------------------------------------------------

    def in_scope_codes(self, *, include_defunct: bool = False) -> set[int]:
        """The in-scope Regular Plan scheme codes currently in mf.funds.

        The 5-year NAV window is delivered for this set. History rows for codes
        outside it are skipped during backfill.
        """
        sql = "SELECT amfi_scheme_code FROM mf.funds WHERE in_scope"
        if not include_defunct:
            sql += " AND NOT is_defunct"
        with self.connect().cursor() as cur:
            return {int(r["amfi_scheme_code"]) for r in cur.execute(sql).fetchall()}

    def fund_codes(self) -> set[int]:
        """Every scheme code currently in mf.funds (any plan, any scope)."""
        with self.connect().cursor() as cur:
            return {int(r["amfi_scheme_code"]) for r in cur.execute(
                "SELECT amfi_scheme_code FROM mf.funds").fetchall()}

    def update_amc_info(
        self,
        amfi_amc_name: str,
        values: dict[str, Any],
    ) -> int:
        """Update Groww-provided metadata on an existing AMC (never inserts).

        The AMC set is owned by AMFI; Groww's ``amc_info`` enriches the
        AMFI-registered row in place, keyed on the exact AMFI header string.
        Unknown keys are ignored. Returns the number of rows affected (0 if the
        AMC does not exist, which should not happen for in-scope funds).
        """
        allowed = (
            "amc_aum", "amc_rank", "amc_launch_date", "amc_address",
            "amc_description", "amc_sponsor", "amc_source", "amc_fetched_at",
        )
        keys = [k for k in allowed if values.get(k) is not None]
        if not keys:
            return 0
        sets = ", ".join(f"{k} = %s" for k in keys)
        with self.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE mf.amcs SET {sets}, updated_at = now() "
                f"WHERE amfi_amc_name = %s",
                tuple(values[k] for k in keys) + (amfi_amc_name,),
            )
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def replace_holdings(
        self,
        amfi_scheme_code: int,
        rows: Sequence[Sequence[Any]],
        *,
        only_if_empty: bool = False,
    ) -> int:
        """Replace a fund's holdings snapshot atomically (delete + insert).

        Holdings are a current-snapshot, so a full replace per fund is correct
        and simpler than a diff. Returns the number of rows written.

        ``only_if_empty`` makes the write non-destructive: if the fund already
        has holdings rows the call is a no-op returning 0. ``fund_holdings`` has
        no source column, so a replace cannot tell whose snapshot it is about to
        delete — a secondary source must therefore pass this and may only populate
        funds nobody has written yet. The owning source (Scripbox) keeps the
        default replacing behaviour.
        """
        with self.transaction() as conn, conn.cursor() as cur:
            if only_if_empty:
                cur.execute(
                    "SELECT 1 FROM mf.fund_holdings "
                    "WHERE amfi_scheme_code = %s LIMIT 1",
                    (amfi_scheme_code,))
                if cur.fetchone() is not None:
                    return 0
            else:
                cur.execute(
                    "DELETE FROM mf.fund_holdings WHERE amfi_scheme_code = %s",
                    (amfi_scheme_code,))
            stmt = (
                "INSERT INTO mf.fund_holdings (amfi_scheme_code, portfolio_date, "
                "holding_rank, company_name, sector_name, nature_name, market_value, "
                "weight_pct, rating) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")
            count = 0
            for row in rows:
                cur.execute(stmt, (amfi_scheme_code, *row))
                count += 1
        return count

    # -- source_metadata -----------------------------------------------------

    def record_fetch(self, meta: dict[str, Any]) -> int:
        """Insert one mf.source_metadata row. Returns the fetch_id."""
        cols = (
            "source", "endpoint", "entity_kind", "entity_key", "http_status",
            "content_type", "content_hash", "content_bytes", "acquisition",
            "wayback_ts", "user_agent", "request_url", "records_in", "records_ok",
            "records_quarantined", "duration_ms", "error",
        )
        present = [c for c in cols if meta.get(c) is not None]
        placeholders = ", ".join(["%s"] * len(present))
        with self.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO mf.source_metadata ({', '.join(present)}) "
                f"VALUES ({placeholders}) RETURNING fetch_id",
                tuple(meta[c] for c in present),
            )
            return int(cur.fetchone()["fetch_id"])
