"""Actual ACL validators with controlled catalogues, not native DB evidence."""

from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import store
from mfdataindia.review import reader_access_profile as profiles
from test_broad_reader_policy import state

GENERATOR = "mfdata_verified_generator"


class EnrichmentCatalogue:
    def __init__(self, *, broad, principal=GENERATOR, session=None, database="mfdataindia"):
        self.broad = broad
        self.identity = dict(database=database, principal=principal, session=session or principal)
        self.statements = []

    def execute(self, query, params=None):
        self.statements.append((query, params))
        if query == "SELECT current_user AS name":
            value = {"name": self.identity["principal"]}
        elif "current_database() AS database" in query:
            value = self.identity
        elif "has_column_privilege" in query:
            value = {"allowed": True}
        elif "SELECT nspowner" in query:
            value = {"nspowner": 42, "owner": "mfdata_admin"}
        elif "SELECT c.relname" in query:
            value = [dict(relname=name, relowner=42, relkind="r")
                     for name in ("blobs", "documents", "mappings")]
        elif "SELECT t.tgname" in query:
            value = [dict(tgname=name, tgenabled="O") for name in
                     ("document_sealed", "mapping_sealed", "blob_immutable",
                      "document_immutable", "mapping_immutable")]
        elif "SELECT p.proname" in query:
            value = [dict(proname=name, prosecdef=True, proowner=42)
                     for name in ("seal_document", "seal_mapping")]
        elif "WITH objects AS" in query:
            value = ([dict(kind="table", name="blobs", owner=42,
                           grantee="mfdata_verified_reader", permission="SELECT")]
                     if self.broad else [])
        elif "has_schema_privilege" in query:
            value = {"allowed": False}
        elif "AS rewrite" in query:
            value = {"rewrite": False, "append": True}
        else:
            pytest.fail("Unexpected validator query")
        return SimpleNamespace(fetchone=lambda: value, fetchall=lambda: value)


def test_actual_generator_guard_accepts_independently_verified_broad_reader(monkeypatch):
    monkeypatch.setattr(store, "installed_policy", lambda conn: None)
    monkeypatch.setattr(profiles, "_catalogue", lambda conn: profiles.prepare(state())["after"])
    store.verify_acl(EnrichmentCatalogue(broad=False))
    conn = EnrichmentCatalogue(broad=True)
    store.verify_acl(conn)  # The generator still uses its original restricted default.
    assert all(query.lstrip().startswith(("SELECT", "WITH")) for query, _ in conn.statements)
    with pytest.raises(profiles.ReaderAccessError, match="^READER_PROFILE_IDENTITY_MISMATCH$"):
        store.verify_acl(conn, reader_profile=profiles.BROAD)


@pytest.mark.parametrize("caller", [GENERATOR, "mfdata_verified_admin", "mfdata_admin"])
def test_other_callers_verify_the_whole_reader_policy_without_adopting_it(monkeypatch, caller):
    calls = []
    complete = profiles.prepare(state())["after"]
    monkeypatch.setattr(profiles, "_catalogue", lambda conn: calls.append(conn) or complete)
    conn = EnrichmentCatalogue(broad=True, principal=caller)
    assert profiles.verified_reader_select(conn, "mfdata_verified_reader")
    assert calls == [conn]
    assert not profiles.verified_reader_select(conn, caller)
    assert calls == [conn]
    with pytest.raises(profiles.ReaderAccessError, match="^READER_PROFILE_IDENTITY_MISMATCH$"):
        profiles.verify_selection(conn, profiles.BROAD)


@pytest.mark.parametrize("change", ["missing_select", "write", "grant_option", "membership",
                                    "ddl", "function"])
def test_extra_reader_select_does_not_excuse_incomplete_or_unsafe_policy(monkeypatch, change):
    value = profiles.prepare(state())["after"]
    reader = next(r["oid"] for r in value["roles"] if r["rolname"] == "mfdata_verified_reader")
    if change == "missing_select":
        value["relations"][0]["capabilities"][0] = False
        value["relations"][0]["acl"] = [a for a in value["relations"][0]["acl"] if a[1] != reader]
    elif change == "write":
        value["columns"][0]["capabilities"][1] = True
    elif change == "grant_option":
        next(a for a in value["namespaces"][0]["acl"] if a[1] == reader)[3] = True
    elif change == "membership":
        value["memberships"].append(dict(roleid=16587, member=reader, grantor=16587,
                                          admin_option=False, inherit_option=False, set_option=True))
    elif change == "ddl":
        value["namespaces"][0]["create_allowed"] = True
    else:
        value["functions"].append(dict(prosecdef=True, execute_allowed=True))
    monkeypatch.setattr(profiles, "_catalogue", lambda conn: value)
    with pytest.raises(profiles.ReaderAccessError):
        profiles.verified_reader_select(EnrichmentCatalogue(broad=True), "mfdata_verified_reader")


@pytest.mark.parametrize("identity", [dict(principal="untrusted"), dict(principal="postgres"),
    dict(principal=GENERATOR, session="mfdata_admin"), dict(database="unrelated")])
def test_caller_identity_is_verified_before_independent_policy_inspection(monkeypatch, identity):
    monkeypatch.setattr(profiles, "_catalogue", lambda conn: pytest.fail("untrusted inspection"))
    with pytest.raises(profiles.ReaderAccessError, match="^READER_PROFILE_IDENTITY_MISMATCH$"):
        profiles.verified_reader_select(EnrichmentCatalogue(broad=True, **identity), "mfdata_verified_reader")


def test_restricted_reader_cannot_self_adopt_from_grant_atoms(monkeypatch):
    monkeypatch.setattr(profiles, "_catalogue", lambda conn: pytest.fail("restricted reader promotion"))
    assert not profiles.verified_reader_select(
        EnrichmentCatalogue(broad=True, principal="mfdata_verified_reader"), "mfdata_verified_reader")
