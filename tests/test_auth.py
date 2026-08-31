import pytest

from nexus.ingress.auth import (
    AuthError,
    authenticate,
    build_key_index,
    key_digest,
)
from nexus.registry.tenants import load_policies


@pytest.fixture
def index(policies_dir, monkeypatch):
    monkeypatch.setenv("NEXUS_KEY_SHOPSCOUT", "sk-shopscout-xyz")
    monkeypatch.setenv("NEXUS_KEY_HELPMATE", "sk-helpmate-abc")
    return build_key_index(load_policies(policies_dir))


def test_key_maps_to_its_tenant(index):
    assert authenticate("Bearer sk-shopscout-xyz", index) == "shopscout"


def test_raw_key_without_bearer_prefix_also_works(index):
    assert authenticate("sk-helpmate-abc", index) == "helpmate"


def test_unknown_key_is_rejected(index):
    with pytest.raises(AuthError):
        authenticate("Bearer sk-nobody", index)


def test_empty_header_is_rejected(index):
    with pytest.raises(AuthError):
        authenticate("", index)


def test_tenants_without_a_configured_key_are_not_reachable(index, policies_dir):
    # wealthwise/aura/wuwork have no key set in this fixture. An unset key
    # must not become a blank key that any empty header matches -- that is
    # how "every tenant is reachable by sending nothing" happens.
    with pytest.raises(AuthError):
        authenticate("Bearer ", index)
    assert "wealthwise" not in index.values()


def test_duplicate_keys_across_tenants_are_refused(policies_dir, monkeypatch):
    # Two tenants sharing a key makes attribution ambiguous, and the ledger
    # would silently bill one tenant for the other's traffic.
    monkeypatch.setenv("NEXUS_KEY_SHOPSCOUT", "sk-same")
    monkeypatch.setenv("NEXUS_KEY_HELPMATE", "sk-same")
    with pytest.raises(ValueError):
        build_key_index(load_policies(policies_dir))


def test_the_index_holds_no_plaintext(policies_dir, monkeypatch):
    # Before Phase 4a this process held every tenant's key in the clear for
    # its whole lifetime. A heap dump, a crash report or an attached debugger
    # handed all five over in one step.
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-secret")
    index = build_key_index(load_policies(policies_dir))
    assert "sk-secret" not in index
    assert index[key_digest("sk-secret")] == "wuwork"


def test_a_key_shared_across_the_two_sources_is_refused(policies_dir, monkeypatch):
    # A bootstrap key colliding with an issued one is exactly as ambiguous as
    # two bootstrap keys colliding, so it fails the same way: at startup.
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-shared")
    with pytest.raises(ValueError, match="share an API key"):
        build_key_index(
            load_policies(policies_dir),
            stored={key_digest("sk-shared"): "helpmate"},
        )


def test_the_same_key_from_both_sources_for_one_tenant_is_not_a_collision(
    policies_dir, monkeypatch
):
    # Not ambiguous: both name wuwork, so attribution has one answer.
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-same")
    index = build_key_index(
        load_policies(policies_dir), stored={key_digest("sk-same"): "wuwork"}
    )
    assert authenticate("Bearer sk-same", index) == "wuwork"


def test_an_issued_credential_authenticates_like_a_bootstrap_one(policies_dir):
    # The store hands back plaintext; the index only ever saw the digest.
    index = build_key_index(
        load_policies(policies_dir), stored={key_digest("nx-issued"): "aura"}
    )
    assert authenticate("Bearer nx-issued", index) == "aura"
