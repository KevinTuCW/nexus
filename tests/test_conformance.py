from dataclasses import replace
from pathlib import Path

import pytest

from nexus.assurance.conformance import GateOutcome, run_tenant_gate
from nexus.registry.tenants import load_policies


@pytest.fixture
def policies(policies_dir):
    return load_policies(policies_dir)


def test_running_wuworks_own_gate_succeeds(policies):
    outcome = run_tenant_gate(policies["wuwork"], env_overrides={})
    assert isinstance(outcome, GateOutcome)
    assert outcome.passed is True
    assert outcome.exit_code == 0
    assert outcome.metrics["retrieval_accuracy"] > 0


def test_a_failing_gate_is_reported_not_raised(policies):
    # The runner's job is to report, not to decide. Raising here would make
    # one tenant's red gate abort the run before the others are reached.
    outcome = run_tenant_gate(
        policies["wuwork"], env_overrides={"WUWORK_MIN_RETRIEVAL": "1.01"}
    )
    assert outcome.passed is False
    assert outcome.exit_code == 2


def test_the_runner_uses_the_command_the_policy_declares(policies):
    # The four incumbent repos do not share an entrypoint -- measured in
    # P2a: `make gate`, `make eval`, `make eval`, `make test`. A runner with
    # one hardcoded command works for exactly one tenant and fails the rest
    # in a way that looks like their gates are broken.
    assert policies["wuwork"].gate_command == "make wuwork-eval"
    outcome = run_tenant_gate(policies["wuwork"], env_overrides={})
    assert outcome.command == "make wuwork-eval"
    # ...and the declared command is the one that actually ran. `command`
    # alone is copied straight off the policy, so it reports what we meant
    # to run rather than what we ran -- hardcoding a different command
    # leaves that assertion perfectly green. The fingerprint below can only
    # come from wuwork's own gate.
    assert outcome.metrics.get("n_cases") == 12
    assert "retrieval_accuracy" in outcome.metrics


def test_a_missing_repo_is_unverifiable_not_passing(policies):
    # Same rule as assurance/isolation.py: a check that did not run must
    # never read as a check that passed.
    broken = replace(policies["helpmate"], repo_path=Path("/nonexistent/repo"))
    outcome = run_tenant_gate(broken, env_overrides={})
    assert outcome.passed is False
    assert outcome.unverifiable is True


def test_env_overrides_reach_the_subprocess(policies):
    # Injecting the environment is the only lever the runner has: rewriting
    # a tenant's command would be an edit to how that tenant is built.
    outcome = run_tenant_gate(
        policies["wuwork"], env_overrides={"WUWORK_MIN_RETRIEVAL": "0.0"}
    )
    assert outcome.passed is True


def test_unparseable_output_is_flagged_apart_from_failure(policies):
    # A tenant that changed its output format has not become worse. Merging
    # the two would send someone hunting a quality regression that never
    # happened -- and would also let a genuinely failing gate hide behind
    # "probably just a format change".
    echo_only = replace(policies["wuwork"], gate_command="echo not-json")
    outcome = run_tenant_gate(echo_only, env_overrides={})
    assert outcome.passed is True
    assert outcome.metrics_unavailable is True
    assert outcome.metrics == {}
