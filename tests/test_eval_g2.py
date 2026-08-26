import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from nexus.eval import check_g2_attribution_error_is_zero
from nexus.ledger.book import Entry, UpstreamCharge
from nexus.ledger.usage import Usage

ROOT = Path(__file__).resolve().parent.parent


def _row(call_id, cost, status="ok"):
    return Entry(
        entry_id=f"e-{call_id}", call_id=call_id, tenant="wuwork",
        workload="default", trace_root=None, span_id=f"s-{call_id}",
        parent_span_id=None, model="zai/glm-4.6", family="glm",
        usage=Usage(prompt_tokens=10, completion_tokens=2),
        cost_nanousd=cost, status=status,
        ts=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )


def _charge(call_id, cost):
    return UpstreamCharge(call_id=call_id, model="zai/glm-4.6", cost_nanousd=cost)


def test_a_balanced_ledger_is_clean():
    assert check_g2_attribution_error_is_zero(
        [_row("c-1", 6000)], [_charge("c-1", 6000)]
    ) == []


def test_an_amount_mismatch_is_a_violation():
    v = check_g2_attribution_error_is_zero([_row("c-1", 5999)], [_charge("c-1", 6000)])
    assert v and "c-1" in v[0]


def test_a_charge_with_no_ledger_row_is_a_violation():
    # The aborted-stream bug in its observable form: the provider billed for
    # a call the books have never heard of.
    assert check_g2_attribution_error_is_zero([], [_charge("c-9", 6000)]) != []


def test_a_ledger_row_with_no_charge_is_a_violation():
    # The mirror image. Billing a tenant for a call the provider never
    # charged for is the same defect pointing the other way.
    assert check_g2_attribution_error_is_zero([_row("c-1", 6000)], []) != []


def test_an_aborted_row_is_judged_as_a_lower_bound():
    # Measured in Phase 2b: an abandoned stream carries no usage frame, so
    # the ledger books the deltas it saw, and a delta is not a token.
    # Demanding equality would fail the gate on correct behaviour.
    rows = [replace(_row("c-1", 5000), status="aborted")]
    assert check_g2_attribution_error_is_zero(rows, [_charge("c-1", 6000)]) == []


def test_an_aborted_row_billing_more_than_the_upstream_is_still_a_violation():
    # A bound is a bound. "We approximated" is not a licence to approximate
    # upwards.
    rows = [replace(_row("c-1", 7000), status="aborted")]
    assert check_g2_attribution_error_is_zero(rows, [_charge("c-1", 6000)]) != []


def test_every_violation_names_the_gate():
    # The report is read by someone who did not write it. A line that says
    # only "amount_mismatch" leaves them looking for which gate produced it.
    v = check_g2_attribution_error_is_zero([_row("c-1", 5999)], [_charge("c-1", 6000)])
    assert v[0].startswith("G2 ")


def test_fail_demo_g2_exits_two():
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval", "--fail-demo", "g2"],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": f"{ROOT}/src:{ROOT}", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "G2" in proc.stdout + proc.stderr


def test_fail_demo_g2_does_not_trip_the_other_gates():
    # Each demo must exercise exactly its own gate. A demo that trips three
    # gates proves nothing about which one is working.
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval", "--fail-demo", "g2"],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": f"{ROOT}/src:{ROOT}", "PATH": "/usr/bin:/bin"},
    )
    combined = proc.stdout + proc.stderr
    # Asserted as "did not fire", not as "passed". A demo that supplies only
    # a provider charge gives G1 and G4 no rows, and reporting that absence
    # as a pass is the defect `no evidence` was introduced to remove -- so
    # the old form of this assertion would now be pinning it back in.
    assert "G1: FAILED" not in combined
    assert "G4: FAILED" not in combined
    assert "G2: FAILED" in combined
