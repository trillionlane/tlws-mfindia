"""Independent policy revisions preserve registered failed candidates exactly."""
from mfdataindia.enrichment import book_sources, _registered_book_policy_v6
from mfdataindia.enrichment import current_facts_store as current
from mfdataindia.review.policy import fingerprint


def test_accepted_prior_policies_and_registered_candidate_are_immutable():
    assert fingerprint(book_sources.current_policy(2)) == (
        "151093a8798bb41803315b73bca944bb292adc661701173225173958d3b17302"
    )
    legacy = _registered_book_policy_v6.policy()
    assert fingerprint(legacy) == (
        "e8ce5b4ef4bca2ed59901a5df109d7ac191bf176e1a24e9a35cda92e2458c566"
    )
    assert legacy["book_source_configuration_hash"] == (
        "4a0ccc6fc01bacef173bc2631d5122a78d6dabc922c0259d7f83e0b1e8405d09"
    )
    revised = book_sources.current_policy(3)
    assert revised["version"] == 7 and revised["source_registry_version"] == 3
    assert revised["previous_policy_hash"] == fingerprint(book_sources.current_policy(2))
    assert revised["supersedes_unaccepted_policy_hash"] == fingerprint(legacy)
    policies = book_sources.current_policies()
    assert [p["version"] for p in policies][:4] == [4, 5, 6, 7]
    assert len({(p["id"], p["version"]) for p in policies}) == len(policies)
    assert current.known_policy(fingerprint(legacy)) == legacy
    assert current.known_policy(fingerprint(revised)) == revised


def test_legacy_policy_returns_independent_copies():
    document = _registered_book_policy_v6.policy()
    document["version"] = 99
    assert _registered_book_policy_v6.policy()["version"] == 6
