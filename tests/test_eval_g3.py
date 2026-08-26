import json
import subprocess
import sys
from pathlib import Path

from nexus.eval import check_g3_tenant_gates_have_not_regressed

ROOT = Path(__file__).resolve().parent.parent


def _env():
    return {"PYTHONPATH": f"{ROOT}/src:{ROOT}", "PATH": "/usr/bin:/bin"}


def test_matching_metrics_are_clean(tmp_path):
    (tmp_path / "wuwork.json").write_text(
        json.dumps({"retrieval_accuracy": 1.0, "refusal_correctness": 1.0}),
        encoding="utf-8",
    )
    current = {"wuwork": {"retrieval_accuracy": 1.0, "refusal_correctness": 1.0}}
    assert check_g3_tenant_gates_have_not_regressed(current, tmp_path) == []


def test_a_hard_metric_slipping_is_a_violation(tmp_path):
    (tmp_path / "wuwork.json").write_text(
        json.dumps({"refusal_correctness": 1.0}), encoding="utf-8"
    )
    current = {"wuwork": {"refusal_correctness": 0.999}}
    assert check_g3_tenant_gates_have_not_regressed(current, tmp_path) != []


def test_a_soft_metric_within_tolerance_is_clean(tmp_path):
    (tmp_path / "wuwork.json").write_text(
        json.dumps({"retrieval_accuracy": 0.90}), encoding="utf-8"
    )
    current = {"wuwork": {"retrieval_accuracy": 0.88}}
    assert check_g3_tenant_gates_have_not_regressed(current, tmp_path) == []


def test_a_tenant_with_no_baseline_is_reported_not_passed(tmp_path):
    # aura emits no metrics and helpmate's gate exceeds a conformance
    # budget, so neither has one. Silence must not read as health -- that is
    # the cheapest way to make this gate green: stop producing a baseline.
    v = check_g3_tenant_gates_have_not_regressed({"aura": {}}, tmp_path)
    assert v and "aura" in v[0]


def test_a_baseline_with_no_current_run_is_a_violation(tmp_path):
    # The mirror image: a gate that stopped running has not started passing.
    (tmp_path / "wuwork.json").write_text(
        json.dumps({"retrieval_accuracy": 1.0}), encoding="utf-8"
    )
    v = check_g3_tenant_gates_have_not_regressed({}, tmp_path)
    assert v and "wuwork" in v[0]


def test_non_numeric_baseline_fields_do_not_break_it(tmp_path):
    # baselines carry captured_at and a note. A gate that crashed on them
    # would force the files to stop recording when they were taken.
    (tmp_path / "wuwork.json").write_text(
        json.dumps({
            "captured_at": "2026-08-25T12:00:00Z",
            "note": "before integration",
            "retrieval_accuracy": 1.0,
        }),
        encoding="utf-8",
    )
    current = {"wuwork": {"retrieval_accuracy": 1.0}}
    assert check_g3_tenant_gates_have_not_regressed(current, tmp_path) == []


def test_fail_demo_g3_exits_two():
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval", "--fail-demo", "g3"],
        cwd=ROOT, capture_output=True, text=True, env=_env(),
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "G3" in proc.stdout + proc.stderr


def test_fail_demo_g3_does_not_trip_the_other_gates():
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.eval", "--fail-demo", "g3"],
        cwd=ROOT, capture_output=True, text=True, env=_env(),
    )
    combined = proc.stdout + proc.stderr
    # "did not fire", not "passed" -- see the same assertion in
    # tests/test_eval_g2.py for why the difference matters.
    assert "G1: FAILED" not in combined
    assert "G2: FAILED" not in combined
    assert "G4: FAILED" not in combined
    assert "G3: FAILED" in combined
