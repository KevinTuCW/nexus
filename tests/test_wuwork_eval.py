import json
import os
import subprocess
import sys
from pathlib import Path

from tenants.wuwork.eval import Result, run_eval

ROOT = Path(__file__).resolve().parent.parent


def _run(extra_env: dict[str, str]):
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/src:{ROOT}", **extra_env}
    return subprocess.run(
        [sys.executable, "-m", "tenants.wuwork.eval"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_eval_reports_retrieval_accuracy_and_refusal_rate():
    r = run_eval()
    assert isinstance(r, Result)
    assert 0.0 <= r.retrieval_accuracy <= 1.0
    assert 0.0 <= r.refusal_correctness <= 1.0
    assert r.n_cases == 12


def test_eval_is_deterministic():
    # The whole point of the offline embedder. A baseline that moves between
    # runs cannot detect a regression -- it can only produce arguments.
    assert run_eval() == run_eval()


def test_eval_exits_nonzero_when_a_threshold_is_missed():
    # The gate has to be able to fail, and the threshold has to be reachable
    # through the environment: the conformance runner may not edit the
    # command a tenant declares.
    proc = _run({"WUWORK_MIN_RETRIEVAL": "1.01"})
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_eval_prints_machine_readable_json_on_stdout_by_default():
    # No --json flag. The runner cannot add one, so the machine-readable
    # form has to be what the gate already emits.
    proc = _run({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert "retrieval_accuracy" in payload


def test_human_notes_go_to_stderr_so_stdout_stays_parseable():
    proc = _run({})
    json.loads(proc.stdout)  # must not raise
    assert proc.stderr.strip()
