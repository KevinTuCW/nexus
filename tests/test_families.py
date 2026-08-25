import pytest

from nexus.registry.families import (
    UNKNOWN_FAMILY,
    distinct_families,
    family_of,
    FAMILIES,
)


def test_same_open_weights_on_two_vendors_are_one_family():
    # This is the whole point of gate G1. shopscout's jury reaches three
    # different labs on purpose. If nexus grouped by vendor, three
    # platforms hosting the same Qwen3 checkpoint would look like three
    # families, the jury would quietly become homogeneous, and the books
    # would show full compliance.
    assert family_of("siliconflow/Qwen/Qwen3-235B-A22B") == "qwen3"
    assert family_of("dashscope/qwen3-235b-a22b") == "qwen3"
    assert distinct_families(
        ["siliconflow/Qwen/Qwen3-235B-A22B", "dashscope/qwen3-235b-a22b"]
    ) == 1


def test_sizes_of_one_checkpoint_line_share_a_family():
    assert family_of("siliconflow/Qwen/Qwen3-8B") == "qwen3"
    assert family_of("siliconflow/Qwen/Qwen3-235B-A22B") == "qwen3"


def test_different_labs_are_different_families():
    assert distinct_families(["zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B"]) == 2


def test_unknown_model_is_unknown_not_a_guess():
    # Guessing a family for an unrecognised model is how G1 becomes a fake
    # gate: a wrong guess can make two identical models look distinct.
    assert family_of("some-vendor/brand-new-model-v9") == UNKNOWN_FAMILY


def test_unknown_families_never_count_as_distinct():
    # Two unknowns are not evidence of two families.
    assert distinct_families(["a/unknown-one", "b/unknown-two"]) == 0


def test_every_record_states_its_basis():
    # A family table nobody can audit is a table nobody will maintain.
    for model, record in FAMILIES.items():
        assert record.basis.strip(), f"{model} has no stated basis"
