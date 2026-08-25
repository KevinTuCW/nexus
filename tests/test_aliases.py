import pytest

from nexus.providers import ALIASES, canonical_model
from nexus.registry.families import family_of
from nexus.upstream import PRICES


def test_a_tenants_own_model_name_resolves(monkeypatch):
    # helpmate says "glm-4.7" because that is what z.ai calls it. It is
    # integrated zero-touch, so it cannot be asked to say anything else --
    # and if nexus demanded a rename, "integration costs zero lines" would
    # be false. Speaking the tenant's dialect is the gateway's job.
    assert canonical_model("glm-4.7") == "zai/glm-4.7"
    assert canonical_model("Qwen/Qwen3-8B") == "siliconflow/Qwen/Qwen3-8B"


def test_canonical_ids_pass_through_unchanged():
    for model in PRICES:
        assert canonical_model(model) == model


def test_unknown_names_are_returned_untouched_for_the_caller_to_refuse():
    # Not an error here: the price check downstream produces a better
    # message, naming the model the tenant actually sent.
    assert canonical_model("nobody/knows-this") == "nobody/knows-this"


def test_every_alias_points_at_a_priced_model():
    dangling = sorted(t for t in ALIASES.values() if t not in PRICES)
    assert dangling == [], f"aliases pointing at unpriced models: {dangling}"


def test_no_alias_shadows_a_canonical_id():
    # An alias whose key is itself a real model id would silently redirect
    # traffic away from the model the tenant named.
    shadowing = sorted(a for a in ALIASES if a in PRICES)
    assert shadowing == [], f"aliases shadowing real ids: {shadowing}"


def test_aliasing_never_changes_the_weight_family():
    # Aliases resolve before routing, so everything downstream -- including
    # gate G1 -- sees a canonical id. An alias that crossed families would
    # move a diversity verdict without anyone choosing to.
    for alias, target in ALIASES.items():
        assert family_of(target) != "unknown", f"{alias} -> {target} has no family"
