import pytest

from nexus.policy.quota import Decision, check_budget


def test_within_budget_is_allowed():
    d = check_budget(spent_today=100, incoming_estimate=50, budget=1000)
    assert d.allowed is True


def test_exceeding_budget_is_denied_not_warned():
    d = check_budget(spent_today=990, incoming_estimate=50, budget=1000)
    assert d.allowed is False
    assert "budget" in d.reason


def test_exactly_at_budget_is_allowed():
    d = check_budget(spent_today=950, incoming_estimate=50, budget=1000)
    assert d.allowed is True


def test_zero_budget_blocks_everything():
    # A tenant configured with no budget is switched off, not unlimited.
    # Treating 0 as "unmetered" is the classic off-by-semantics bug, and its
    # failure mode is that the least-configured tenant spends the most.
    d = check_budget(spent_today=0, incoming_estimate=1, budget=0)
    assert d.allowed is False


def test_decision_cannot_be_mutated_after_the_fact():
    d = check_budget(spent_today=0, incoming_estimate=1, budget=10)
    assert isinstance(d, Decision)
    with pytest.raises(Exception):
        d.allowed = False  # type: ignore[misc]
