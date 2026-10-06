"""Single source of truth for the PostgreSQL DSN.

Every script in this repo used to carry its own hardcoded default, and every one
of them said ``port=5433 dbname=postgres`` -- the PGlite dev instance. That
instance ran *alongside* the real PostgreSQL on 5432, so a script started without
``MFDATAINDIA_DSN`` connected successfully, did its work, printed a success
report... and had written to a database nothing reads.

That is the worst failure mode available here: the run looks completely healthy
and the live data never changes. So there is no default any more.

PGlite has since been shut down (``db/server.mjs``, data under ``data/pglite``),
so 5433 refuses connections and the silent-write failure is no longer reachable.
The guard stays anyway: bringing PGlite back is one command, and every stale
default would return with it.

``resolve_dsn()`` requires the target to be stated -- either ``MFDATAINDIA_DSN``
or an explicit ``--dsn`` -- and refuses a DSN aimed at the stale PGlite port
unless the caller passes ``allow_pglite=True`` because it genuinely means it (the
PGlite -> PostgreSQL migration script is the only such caller).
"""

from __future__ import annotations

import os
import re
from typing import Optional

__all__ = ["PGLITE_PORT", "DsnError", "resolve_dsn", "dsn_port", "describe_dsn"]

#: The PGlite dev instance. Now shut down, but one command from being back --
#: and never the live database either way.
PGLITE_PORT = 5433

_ENV_VAR = "MFDATAINDIA_DSN"

_KV_PORT = re.compile(r"\bport\s*=\s*(\d+)")


class DsnError(RuntimeError):
    """Raised when the target database is missing or looks like the stale one."""


def dsn_port(dsn: str) -> Optional[int]:
    """Best-effort port extraction from either DSN spelling.

    Handles both ``postgresql://user:pw@host:5432/db`` and the keyword form
    ``host=127.0.0.1 port=5432 dbname=x``. Returns None when unstated, which
    means the libpq default (5432) will apply.
    """
    m = _KV_PORT.search(dsn)
    if m:
        return int(m.group(1))
    m = re.search(r"@(?:\[[^\]]+\]|[^/]+):(\d+)/", dsn)
    return int(m.group(1)) if m else None


def _dbname(dsn: str) -> Optional[str]:
    m = re.search(r"\bdbname\s*=\s*(\S+)", dsn)
    if m:
        return m.group(1)
    m = re.search(r"@(?:\[[^\]]+\]|[^/]+)/([^?]+)", dsn)
    return m.group(1) if m else None


def describe_dsn(dsn: str) -> str:
    """A short, safe-to-print label for logs (never includes credentials)."""
    return f"port={dsn_port(dsn) or 5432} dbname={_dbname(dsn) or '?'}"


def resolve_dsn(explicit: Optional[str] = None, *,
                allow_pglite: bool = False, purpose: str = "") -> str:
    """Return the DSN to use, or raise rather than guess.

    Precedence: ``explicit`` (a ``--dsn`` argument) then ``MFDATAINDIA_DSN``.
    Refuses the stale PGlite port unless ``allow_pglite`` is set.
    """
    dsn = explicit or os.environ.get(_ENV_VAR)
    what = purpose or "this command"
    if not dsn:
        raise DsnError(
            f"No database specified for {what}. Refusing to guess: the PGlite dev "
            f"instance on port {PGLITE_PORT} is not the live database, and a run "
            f"against it succeeds silently while the live data is untouched.\n"
            f"  export {_ENV_VAR}='postgresql://postgres:secret@localhost:5432/mfdataindia'\n"
            f"  or pass --dsn explicitly.")
    port = dsn_port(dsn)
    if port == PGLITE_PORT and not allow_pglite:
        raise DsnError(
            f"{what} was pointed at port {PGLITE_PORT} ({describe_dsn(dsn)}), the "
            f"stale PGlite dev instance -- not the live database on 5432. "
            f"Set {_ENV_VAR} to the PostgreSQL DSN. If you really mean PGlite, "
            f"pass allow_pglite=True / --allow-pglite.")
    return dsn
