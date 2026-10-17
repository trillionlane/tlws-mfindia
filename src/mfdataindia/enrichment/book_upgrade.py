"""Owner-only versioned source authority; no source/publication activation here."""

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import book_sources, book_store, current_facts_store as current
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation_structure
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def install(conn, root):
    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS name,current_user=session_user AS direct"
        ).fetchone()
        if who["name"] not in ("postgres", "mfdata_admin") or not who["direct"]:
            raise ValueError("direct source-upgrade owner required")
        acquire_nav_data_operation_lock(conn)
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        prior = book_store.installed_configuration(conn)
        old_version = prior["document"]["registry"]["version"]
        target = book_sources.CURRENT_VERSION
        if old_version == target:
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        if old_version != target - 1 or target not in book_store.MIGRATIONS:
            raise ValueError("one reviewed sequential source upgrade required")
        graph = dict(current.configuration(conn))
        if graph["policy_hash"] != fingerprint(
            book_sources.current_policy(old_version)
        ) or not current.projection_state(conn):
            raise ValueError("accepted previous active book graph required before source upgrade")
        before = capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        inventory = book_store.capture(conn)
        function = conn.execute(
            "SELECT pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace AND proname='seal_input'"
        ).fetchone()["definition"]
        body = {
            "format": "official-amc-book-upgrade-v1",
            "mode": "upgrade",
            "prior_book_configuration": dict(prior),
            "prior_seal_function": function,
            "prior_current_configuration_hash": fingerprint(graph),
            "target_version": target,
            "target_configuration_hash": fingerprint(book_sources.configuration(target)),
            "to_generation": prior["generation"] + 1,
            "source_rows_changed": 0,
        }
        intent = {**body, "transition_id": fingerprint(body)}
        book_store.append_exact(conn, "transitions", "transition_id", intent, {})
        raw = book_store.migration_source(root, version=target)
        if raw.count("\nBEGIN;\n") != 1 or not raw.rstrip().endswith("COMMIT;"):
            raise ValueError("reviewed transactional source upgrade required")
        conn.execute(raw.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        document = book_sources.configuration(target)
        conn.execute(
            "INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)",
            (intent["to_generation"], fingerprint(document), Jsonb(document)),
        )
        policy = book_sources.current_policy(target)
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(policy),
            policy,
            {"policy_id": policy["id"], "policy_version": policy["version"]},
        )
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        after = book_store.capture(conn)
        for key in ("configuration", "sources", "batches", "runs", "transitions"):
            if any(after[key].get(k) != v for k, v in inventory[key].items()):
                raise ValueError("prior immutable book evidence changed during upgrade")
        if dict(current.configuration(conn)) != graph:
            raise ValueError("source installation must not activate current facts")
        compare_preservation_structure(
            before, capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        )
        return {
            "status": "UPGRADED",
            "upgrade_id": intent["transition_id"],
            "configuration_hash": fingerprint(document),
            "source_rows_changed": 0,
        }


def prior_upgrade(conn, target_version):
    """The exact latest transition into an installed authority, not a candidate lookup."""
    cfg = book_store.installed_configuration(conn)
    if cfg["document"]["registry"]["version"] != target_version:
        raise ValueError("expected installed source authority required")
    row = conn.execute(
        "SELECT document FROM mf_book_facts.transitions WHERE document->>'mode'='upgrade' AND (document->>'to_generation')::integer=%s",
        (cfg["generation"],),
    ).fetchone()
    if not row or row["document"]["target_version"] != target_version:
        raise ValueError("sealed prior source-upgrade intent required")
    return row["document"]


def restore(conn, intent):
    """Called inside graph recovery; old body/config restored, new rows retained."""
    who = conn.execute("SELECT session_user AS name,current_user=session_user AS direct").fetchone()
    if who["name"] not in ("postgres", "mfdata_admin") or not who["direct"]:
        raise ValueError("direct source-recovery owner required")
    body = {k: v for k, v in intent.items() if k != "transition_id"}
    if fingerprint(body) != intent.get("transition_id") or intent.get("mode") != "upgrade":
        raise ValueError("sealed source recovery intent differs")
    prior = intent["prior_book_configuration"]
    version = prior["document"]["registry"]["version"]
    if prior["document"] != book_sources.configuration(version):
        raise ValueError("known historical source authority required")
    expected = book_store.migration_source(version=version).split("AS $$", 1)[1].split("$$;", 1)[0]
    if (
        intent["prior_seal_function"].split("AS $function$", 1)[-1].split("$function$", 1)[0]
        != expected
    ):
        raise ValueError("sealed prior executable body differs")
    conn.execute(intent["prior_seal_function"])
    generation = conn.execute(
        "SELECT max(generation)+1 AS generation FROM mf_book_facts.configuration"
    ).fetchone()["generation"]
    conn.execute(
        "INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)",
        (generation, prior["configuration_hash"], Jsonb(prior["document"])),
    )
    book_store.verify_acl(conn, principal="mfdata_verified_reader")


def recover_unactivated(conn, upgrade_id):
    """A failed source/staging gate can restore sources without an activation ID."""
    from mfdataindia.enrichment.monthly_projection import owner

    with conn.transaction():
        owner(conn)
        row = conn.execute(
            "SELECT document FROM mf_book_facts.transitions WHERE transition_id=%s", (upgrade_id,)
        ).fetchone()
        if not row or row["document"].get("mode") != "upgrade":
            raise ValueError("sealed source-upgrade recovery intent required")
        intent = row["document"]
        # Recovery must not revalidate the failed candidate against the running
        # code's newest source specification. Use the sealed prior intent and
        # exact generation/current-graph fence, then verify the restored prior.
        cfg = conn.execute(
            "SELECT generation,configuration_hash,document FROM mf_book_facts.configuration ORDER BY generation DESC LIMIT 1"
        ).fetchone()
        prior = intent["prior_book_configuration"]
        if fingerprint({k: v for k, v in intent.items() if k != "transition_id"}) != upgrade_id:
            raise ValueError("source-upgrade intent seal differs")
        if (
            fingerprint(dict(current.configuration(conn)))
            != intent["prior_current_configuration_hash"]
        ):
            raise ValueError("active/later graph requires its own graph recovery")
        if cfg["document"] == prior["document"]:
            book_store.verify_acl(conn, principal="mfdata_verified_reader")
            return {"status": "ALREADY_RECOVERED", "source_rows_changed": 0}
        if cfg["generation"] != intent["to_generation"]:
            raise ValueError("stale source intent or active overlay requires graph recovery first")
        restore(conn, intent)
        body = {
            "mode": "recover_source_upgrade",
            "upgrade_id": upgrade_id,
            "to_generation": book_store.installed_configuration(conn)["generation"],
            "source_rows_changed": 0,
        }
        doc = {**body, "transition_id": fingerprint(body)}
        book_store.append_exact(conn, "transitions", "transition_id", doc, {})
        return {"status": "RECOVERED", "source_rows_changed": 0}
