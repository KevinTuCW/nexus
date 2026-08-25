import pytest

from nexus.providers import ENDPOINTS, Endpoint, endpoint_for
from nexus.registry.families import FAMILIES, family_of
from nexus.upstream import PRICES


def test_every_priced_model_has_an_endpoint():
    # A model that can be billed but not reached is a routing candidate the
    # gateway will pick and then fail on.
    missing = sorted(set(PRICES) - set(ENDPOINTS))
    assert missing == [], f"priced but unreachable: {missing}"


def test_every_endpoint_has_a_price():
    missing = sorted(set(ENDPOINTS) - set(PRICES))
    assert missing == [], f"reachable but unpriced: {missing}"


def test_endpoint_lookup_returns_transport_details():
    e = endpoint_for("zai/glm-4.6")
    assert isinstance(e, Endpoint)
    assert e.api_base
    assert e.api_key_env
    assert e.litellm_model


def test_unknown_model_has_no_endpoint():
    with pytest.raises(KeyError):
        endpoint_for("some/never-registered")


def test_two_hostings_of_one_checkpoint_keep_one_family():
    # The whole point of keeping ids and transport apart. These two entries
    # reach different vendors -- different api_base, different key -- and
    # must still be one weight family, because that is what gate G1 counts.
    assert (
        endpoint_for("siliconflow/Qwen/Qwen3-235B-A22B").api_base
        != endpoint_for("dashscope/qwen3-235b-a22b").api_base
    )
    assert family_of("siliconflow/Qwen/Qwen3-235B-A22B") == family_of(
        "dashscope/qwen3-235b-a22b"
    )


def test_transport_details_are_absent_from_the_family_registry():
    # Structural guarantee: if api_base ever leaked into FAMILIES, changing
    # a hosting provider would silently change a diversity verdict.
    for record in FAMILIES.values():
        assert "http" not in record.family
        assert "://" not in record.basis
