"""Transactional storage operations for the private association-tag API."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from mfdataindia.api.association_schemas import AssociationTagMutation

SOURCE = "trillion-insights"


class FamilyNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class VersionConflictError(Exception):
    current_version: int


class IdempotencyConflictError(Exception):
    pass


def _tags(conn: Connection, tlws_mf_id: str) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT value, tag_type AS type, source
          FROM mf.fund_family_association_tags
         WHERE tlws_mf_id = %s
         ORDER BY tag_type, value, source
        """,
        (tlws_mf_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_state(conn: Connection, tlws_mf_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT family.tlws_mf_id,
               COALESCE(state.version, 0) AS version,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'value', tag.value,
                           'type', tag.tag_type,
                           'source', tag.source
                       )
                       ORDER BY tag.tag_type, tag.value, tag.source
                   ) FILTER (WHERE tag.tlws_mf_id IS NOT NULL),
                   '[]'::jsonb
               ) AS tags
          FROM mf.fund_family AS family
          LEFT JOIN mf.fund_family_association_state AS state
            ON state.tlws_mf_id = family.tlws_mf_id
          LEFT JOIN mf.fund_family_association_tags AS tag
            ON tag.tlws_mf_id = family.tlws_mf_id
         WHERE family.tlws_mf_id = %s
         GROUP BY family.tlws_mf_id, state.version
        """,
        (tlws_mf_id,),
    ).fetchone()
    if row is None:
        raise FamilyNotFoundError(tlws_mf_id)
    return {
        "tlws_mf_id": tlws_mf_id,
        "version": int(row["version"]),
        "tags": list(row["tags"]),
    }


def _request_hash(tlws_mf_id: str, request: AssociationTagMutation) -> str:
    canonical = {
        "tlws_mf_id": tlws_mf_id,
        "expected_version": request.expected_version,
        "tags": sorted(
            (tag.model_dump(mode="json") for tag in request.tags),
            key=lambda tag: (tag["type"], tag["value"], tag["source"]),
        ),
    }
    encoded = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def add_tags(
    conn: Connection,
    tlws_mf_id: str,
    request: AssociationTagMutation,
    *,
    idempotency_key: str,
) -> tuple[dict[str, Any], int]:
    """Add normalized tags atomically with optimistic concurrency and replay."""
    request_hash = _request_hash(tlws_mf_id, request)
    with conn.transaction():
        # Serialize this caller/key across all families. Without this lock, two
        # concurrent first uses could both mutate before one loses the ledger PK.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{SOURCE}:{idempotency_key}",),
        )
        replay = conn.execute(
            """
            SELECT request_hash, response_status, response_body
              FROM mf.association_tag_idempotency
             WHERE source = %s AND idempotency_key = %s
            """,
            (SOURCE, idempotency_key),
        ).fetchone()
        if replay:
            if replay["request_hash"] != request_hash:
                raise IdempotencyConflictError(idempotency_key)
            return dict(replay["response_body"]), int(replay["response_status"])

        family = conn.execute(
            "SELECT 1 AS present FROM mf.fund_family WHERE tlws_mf_id = %s",
            (tlws_mf_id,),
        ).fetchone()
        if family is None:
            raise FamilyNotFoundError(tlws_mf_id)

        conn.execute(
            """
            INSERT INTO mf.fund_family_association_state (tlws_mf_id)
            VALUES (%s) ON CONFLICT (tlws_mf_id) DO NOTHING
            """,
            (tlws_mf_id,),
        )
        state = conn.execute(
            """
            SELECT version FROM mf.fund_family_association_state
             WHERE tlws_mf_id = %s FOR UPDATE
            """,
            (tlws_mf_id,),
        ).fetchone()
        current_version = int(state["version"])
        if request.expected_version != current_version:
            raise VersionConflictError(current_version)

        added: list[dict[str, str]] = []
        for tag in sorted(request.tags, key=lambda item: (item.type, item.value, item.source)):
            row = conn.execute(
                """
                INSERT INTO mf.fund_family_association_tags
                    (tlws_mf_id, value, tag_type, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tlws_mf_id, tag_type, value, source) DO NOTHING
                RETURNING value, tag_type AS type, source
                """,
                (tlws_mf_id, tag.value, tag.type, tag.source),
            ).fetchone()
            if row:
                added.append(dict(row))

        new_version = current_version
        if added:
            new_version = current_version + 1
            conn.execute(
                """
                UPDATE mf.fund_family_association_state
                   SET version = %s, updated_at = now()
                 WHERE tlws_mf_id = %s
                """,
                (new_version, tlws_mf_id),
            )

        response = {
            "tlws_mf_id": tlws_mf_id,
            "previous_version": current_version,
            "version": new_version,
            "added": added,
            "tags": _tags(conn, tlws_mf_id),
        }
        status = 201 if added else 200
        conn.execute(
            """
            INSERT INTO mf.association_tag_idempotency
                (source, idempotency_key, request_hash, response_status, response_body)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (SOURCE, idempotency_key, request_hash, status, json.dumps(response)),
        )
        return response, status
