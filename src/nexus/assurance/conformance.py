"""Gate G3: run each tenant's own gate, unchanged, and report what happened.

**Subprocess, never import.** The four incumbent repos have their own
virtualenvs and dependency sets; importing them is impossible in the general
case and violates zero-touch in every case. wuwork goes down the same
subprocess path even though it sits in this repo — the mechanism being
validated has to be the one that will later face the incumbents, or it will
quietly encode assumptions that only hold for a native tenant.

**The command comes from the policy.** Measured in P2a: the incumbents use
`make gate`, `make eval`, `make eval` and `make test` respectively. A runner
with one hardcoded command works for exactly one tenant and fails the other
three in a way that looks like *their* gates are broken.

**Only the environment is injected.** Rewriting a tenant's command would be
an edit to how that tenant is built, which is the thing this whole project
claims not to need.

Three outcomes are kept apart, because collapsing any two of them produces a
runner that lies:

  - *failed* — the gate ran and said no.
  - *unverifiable* — the checkout is missing or the command could not be
    started, so nothing ran. This is not a pass. A check that did not run
    must never read as a check that passed; same rule as
    `nexus.assurance.isolation`.
  - *metrics unavailable* — the gate ran and returned an exit code, but its
    stdout was not the JSON we hoped for. That is a tenant changing its
    output format, not a tenant getting worse. Reporting it as a quality
    regression sends someone hunting a bug that does not exist; reporting a
    real failure as a format change lets a red gate hide.
"""

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from nexus.registry.tenants import TenantPolicy

REPO_ROOT = Path(__file__).resolve().parents[3]

#: A native tenant runs with `cwd = REPO_ROOT`, so a gate command that
#: invokes this repo's own test suite would re-enter these tests, which call
#: this function, which runs the command again. It does not bite today —
#: wuwork declares `make wuwork-eval`, and `aura`'s `make test` runs in
#: aura's own checkout — but it is one policy edit away from an infinite
#: fork bomb, and it was found by trying it. Recorded rather than guarded:
#: a general "does this command re-enter us" check is not something that can
#: be written honestly, and a comment that names the hazard is worth more
#: than a heuristic that half-detects it.


@dataclass(frozen=True)
class GateOutcome:
    tenant: str
    command: str
    exit_code: int | None
    passed: bool
    unverifiable: bool = False
    metrics_unavailable: bool = False
    metrics: dict = field(default_factory=dict)
    output_tail: str = ""


def _parse_metrics(stdout: str) -> dict | None:
    """Tenants print one JSON object on stdout. Tolerate surrounding noise.

    Scans from the last line backwards: `make` echoes the command it is
    about to run, and other tooling adds its own chatter, so the gate's own
    output is what comes last.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def run_tenant_gate(
    policy: TenantPolicy,
    env_overrides: dict[str, str],
    timeout_s: int = 900,
) -> GateOutcome:
    """Run one tenant's declared gate command in its own checkout."""
    cwd = policy.repo_path if policy.repo_path is not None else REPO_ROOT
    if not Path(cwd).is_dir():
        return GateOutcome(
            tenant=policy.tenant,
            command=policy.gate_command,
            exit_code=None,
            passed=False,
            unverifiable=True,
            output_tail=f"checkout not found at {cwd}",
        )

    env = {**os.environ, **env_overrides}
    try:
        proc = subprocess.run(
            shlex.split(policy.gate_command),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return GateOutcome(
            tenant=policy.tenant,
            command=policy.gate_command,
            exit_code=None,
            passed=False,
            unverifiable=True,
            output_tail=str(exc),
        )

    metrics = _parse_metrics(proc.stdout)
    tail = (proc.stdout + proc.stderr)[-2000:]
    return GateOutcome(
        tenant=policy.tenant,
        command=policy.gate_command,
        exit_code=proc.returncode,
        passed=proc.returncode == 0,
        metrics_unavailable=metrics is None,
        metrics=metrics or {},
        output_tail=tail,
    )
