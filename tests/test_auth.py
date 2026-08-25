import pytest

from nexus.ingress.auth import AuthError, authenticate, build_key_index
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
