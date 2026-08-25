import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from nexus.eval import check_g4_fallback_is_never_silent
from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage

ROOT = Path(__file__).resolve().parent.parent


def _row(call_id, requested, routed, served, fallback_from=None):
    return Entry(
        entry_id=f"e-{call_id}", call_id=call_id, tenant="wuwork",
        workload="default", trace_root=None, span_id=f"s-{call_id}",
        parent_span_id=None, model=served, family="glm",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=1, status="ok",
        ts=datetime(2026, 8, 25, tzinfo=timezone.utc),
        fallback_from=fallback_from,
        requested_model=requested, routed_model=routed,
    )


def _env():
    return {"PYTHONPATH": f"{ROOT}/src:{ROOT}", "PATH": "/usr/bin:/bin"}


def test_no_fallback_is_clean():
    rows = [_row("c-1", "zai/glm-4.6", "zai/glm-4.6", "zai/glm-4.6")]
    assert check_g4_fallback_is_never_silent(rows) == []


def test_a_recorded_fallback_is_clean():
    rows = [_row("c-1", "zai/glm-4.6", "zai/glm-4.6", "zai/glm-4.7",
                 fallback_from="zai/glm-4.6")]
    assert check_g4_fallback_is_never_silent(rows) == []


def test_a_served_model_nobody_recorded_a_fallback_for_is_a_violation():
    # The shape of a silent fallback in the books. The response body is read
    # once, by one caller, at one moment; the ledger is what everyone asks
    # afterwards -- during an incident, during a bill review, during an
    # argument about why an answer changed.
    rows = [_row("c-1", "zai/glm-4.6", "zai/glm-4.6", "zai/glm-4.7")]
    v = check_g4_fallback_is_never_silent(rows)
    assert v and "c-1" in v[0]


def test_a_fallback_marker_that_disagrees_with_the_chain_is_a_violation():
    # fallback_from is a consistency check, not a checkbox. A marker naming
    # a model that was never routed to says the bookkeeping is wrong, and
    # wrong bookkeeping is what G4 exists to make loud.
    rows = [_row("c-1", "zai/glm-4.6", "zai/glm-4.6", "zai/glm-4.7",
                 fallback_from="siliconflow/Qwen/Qwen3-8B")]
    v = check_g4_fallback_is_never_silent(rows)
    assert v and "c-1" in v[0]


def test_rows_without_the_chain_are_skipped_not_failed():
    # Pre-3b rows carry no routed_model. Missing evidence is not a
    # violation; conflating the two would make the gate fire on history it
    # was never able to judge.
    rows = [_row("c-1", None, None, "zai/glm-4.7")]
    assert check_g4_fallback_is_never_silent(rows) == []


def test_eval_exits_two_when_a_gate_fails():
    # The exit code is the whole delivery contract. Everything else this
    # module prints is reporting.
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval", "--fail-demo", "g4"],
        cwd=ROOT, capture_output=True, text=True, env=_env(),
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "G4" in proc.stdout + proc.stderr


def test_eval_exits_zero_when_every_gate_passes():
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval"],
        cwd=ROOT, capture_output=True, text=True, env=_env(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
