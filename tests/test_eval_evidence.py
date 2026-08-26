"""The gates have to judge something.

`python -m nexus.eval` built `rows = []`, handed the empty list to G1, G2 and
G4, and printed `passed` three times. Every rule in this repository says that
is not a pass: G3 already refuses to treat a tenant that produced no run as
green, on the grounds that "a gate that stopped running has not started
passing". The other three were doing exactly that, every invocation, and the
README quoted the output as evidence of four green gates.

Meanwhile the evidence existed. With `DATABASE_URL` set, the ledger those
gates are supposed to audit is sitting in Postgres, and the eval never opened
it.

These tests are about the *verdict vocabulary*: `no evidence` must be a
distinct third outcome, printed as such, and never counted as a pass.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(*args, env_extra=None):
    import os

    env = {**os.environ, "PYTHONPATH": f"{REPO / 'src'}:{REPO}", "DATABASE_URL": ""}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "nexus.eval", *args],
        cwd=REPO, capture_output=True, text=True, env=env,
    )


def _row(**over):
    row = {
        "entry_id": "e1", "call_id": "c1", "tenant": "wuwork",
        "workload": "default", "trace_root": None, "span_id": "c1",
        "parent_span_id": None, "model": "zai/glm-4.6", "family": "glm",
        "prompt_tokens": 10, "completion_tokens": 2,
        "cache_write_tokens": 0, "cache_read_tokens": 0,
        "cost_nanousd": 10400, "status": "ok",
        "ts": datetime.now(timezone.utc).isoformat(),
        "fallback_from": None,
        "requested_model": "zai/glm-4.6", "routed_model": "zai/glm-4.6",
    }
    row.update(over)
    return row


def test_an_empty_ledger_is_not_a_pass():
    proc = _run()
    assert "passed" not in proc.stderr.split("G1:")[1].split("\n")[0], proc.stderr
    assert "no evidence" in proc.stderr


def test_no_evidence_still_exits_zero_by_default():
    # Nothing failed, so a red delivery would be a lie in the other
    # direction. The verdict is honest; the exit code stays 0.
    assert _run().returncode == 0


def test_require_evidence_turns_silence_into_a_failure():
    # What the delivery pipeline runs. "We shipped without judging anything"
    # is precisely the outcome that must not be quietly green.
    proc = _run("--require-evidence")
    assert proc.returncode == 2, proc.stderr


def test_gates_judge_rows_handed_to_them(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([_row()]), encoding="utf-8")
    proc = _run("--ledger-json", str(ledger))
    assert "G1: passed" in proc.stderr, proc.stderr
    assert "1 ledger row" in proc.stderr


def test_a_real_violation_in_a_supplied_ledger_fails_the_run(tmp_path):
    # shopscout pins every model it uses; this row says routing moved one.
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps([
            _row(
                tenant="shopscout",
                requested_model="zai/glm-4.6",
                routed_model="siliconflow/Qwen/Qwen3-8B",
                model="siliconflow/Qwen/Qwen3-8B",
            )
        ]),
        encoding="utf-8",
    )
    proc = _run("--ledger-json", str(ledger), "--require-evidence")
    assert proc.returncode == 2
    assert "G1: FAILED" in proc.stderr


def test_g2_without_a_charge_feed_reports_no_evidence(tmp_path):
    # G2 compares two sides. Handed rows and no provider charges, the old
    # code would have called every row an orphan_entry -- turning "we have
    # no idea what the provider charged" into "the provider charged nothing",
    # which is the same mistake in the opposite direction.
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([_row()]), encoding="utf-8")
    proc = _run("--ledger-json", str(ledger))
    g2_line = [ln for ln in proc.stderr.splitlines() if ln.startswith("G2:")][0]
    assert "no evidence" in g2_line, proc.stderr


def test_g2_judges_rows_against_a_supplied_charge_feed(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([_row()]), encoding="utf-8")
    charges = tmp_path / "charges.json"
    charges.write_text(
        json.dumps([{"call_id": "c1", "model": "zai/glm-4.6", "cost_nanousd": 999}]),
        encoding="utf-8",
    )
    proc = _run("--ledger-json", str(ledger), "--charges-json", str(charges))
    assert "G2: FAILED" in proc.stderr, proc.stderr
    assert "amount_mismatch" in proc.stderr


def test_the_failure_demos_still_fail(tmp_path):
    for gate in ("g1", "g2", "g3", "g4"):
        proc = _run("--fail-demo", gate)
        assert proc.returncode == 2, f"{gate}: {proc.stderr}"
        assert f"{gate.upper()}: FAILED" in proc.stderr, proc.stderr
