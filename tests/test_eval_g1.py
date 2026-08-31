import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from nexus.eval import check_g1_diversity_is_never_collapsed
from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage
from nexus.registry.effective import Override, compose
from nexus.registry.tenants import load_policies

ROOT = Path(__file__).resolve().parent.parent


def _row(tenant, requested, routed, call_id="c-1"):
    return Entry(
        entry_id=f"e-{call_id}", call_id=call_id, tenant=tenant,
        workload="jury", trace_root=None, span_id=f"s-{call_id}",
        parent_span_id=None, model=routed, family="glm",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=1, status="ok",
        ts=datetime(2026, 8, 25, tzinfo=timezone.utc),
        requested_model=requested, routed_model=routed,
    )


def test_an_unsubstituted_row_is_clean(policies_dir):
    pol = load_policies(policies_dir)
    rows = [_row("shopscout", "zai/glm-4.6", "zai/glm-4.6")]
    assert check_g1_diversity_is_never_collapsed(rows, pol) == []


def test_a_permitted_substitution_is_clean(policies_dir):
    # helpmate permits its router model to be served by any qwen3.
    pol = load_policies(policies_dir)
    rows = [_row("helpmate", "siliconflow/Qwen/Qwen3-8B",
                 "siliconflow/Qwen/Qwen3-235B-A22B")]
    assert check_g1_diversity_is_never_collapsed(rows, pol) == []


def test_swapping_a_pinned_juror_is_a_violation(policies_dir):
    # The exact shape measured in Phase 2b: unpinning shopscout's jury
    # collapsed three labs into three copies of one model, cut the bill 91%,
    # and produced no error of any kind. This is that event, seen from the
    # books afterwards.
    pol = load_policies(policies_dir)
    rows = [_row("shopscout", "zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B")]
    v = check_g1_diversity_is_never_collapsed(rows, pol)
    assert v and "zai/glm-4.6" in v[0]


def test_the_gate_checks_the_policy_not_merely_the_family(policies_dir):
    # glm-4.6 -> glm-4.7 stays inside one weight family, so a family-only
    # check would wave it through. shopscout permits no substitution at all,
    # and "same family" was never the rule -- the tenant's declaration was.
    pol = load_policies(policies_dir)
    rows = [_row("shopscout", "zai/glm-4.6", "zai/glm-4.7")]
    assert check_g1_diversity_is_never_collapsed(rows, pol) != []


def test_rows_without_the_chain_are_skipped(policies_dir):
    pol = load_policies(policies_dir)
    rows = [replace(_row("shopscout", "zai/glm-4.6", "zai/glm-4.6"),
                    requested_model=None, routed_model=None)]
    assert check_g1_diversity_is_never_collapsed(rows, pol) == []


def test_an_unknown_tenant_is_a_violation_not_a_skip(policies_dir):
    # A ledger row naming a tenant with no policy cannot be judged, and
    # cannot be waved through either: that would make "delete the policy
    # file" a way to pass G1.
    pol = load_policies(policies_dir)
    rows = [_row("ghost-tenant", "zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B")]
    v = check_g1_diversity_is_never_collapsed(rows, pol)
    assert v and "ghost-tenant" in v[0]


def test_fail_demo_g1_exits_two():
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval", "--fail-demo", "g1"],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": f"{ROOT}/src:{ROOT}", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "G1" in proc.stdout + proc.stderr


def _qwen_substitution_row() -> Entry:
    # wuwork asks for Qwen3-8B and routing serves another model of the same
    # family. The declared policy permits this (substitutable_to: [qwen3]),
    # so the row is compliant as it stands.
    return Entry(
        entry_id="e1", call_id="c1", tenant="wuwork", workload="default",
        trace_root=None, span_id="s1", parent_span_id=None,
        model="dashscope/qwen3-235b-a22b", family="qwen3",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=1, status="ok", ts=datetime.now(timezone.utc),
        requested_model="siliconflow/Qwen/Qwen3-8B",
        routed_model="dashscope/qwen3-235b-a22b",
    )


def test_g1_permits_the_substitution_the_declared_policy_allows(policies_dir):
    assert check_g1_diversity_is_never_collapsed(
        [_qwen_substitution_row()], load_policies(policies_dir)) == []


def test_g1_judges_by_the_effective_policy_not_the_declared_one(policies_dir):
    # Same row, same gate. The only difference is that the policy was
    # tightened, and the gate has to follow -- otherwise a tightening made in
    # the control plane is invisible to all four gates.
    declared = load_policies(policies_dir)
    effective = dict(declared)
    effective["wuwork"] = compose(declared["wuwork"], [Override(
        tenant="wuwork", field="substitutable_to",
        removed_value="qwen3", model="siliconflow/Qwen/Qwen3-8B")])

    violations = check_g1_diversity_is_never_collapsed(
        [_qwen_substitution_row()], effective)

    assert len(violations) == 1
    assert "wuwork" in violations[0]


def test_eval_loads_policies_through_the_effective_layer():
    import nexus.eval as ev

    assert hasattr(ev, "load_effective_policies")
    assert not hasattr(ev, "load_policies")
