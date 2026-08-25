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

**Running a tenant's own gate is not free of side effects, and this module
used to imply it was.** Pointing the runner at helpmate's `make gate`
rewrote `eval/report.md` — a git-tracked file, changed by helpmate's eval
doing exactly what an eval does. The zero-touch claim this project makes is
narrower and true: integrating costs no edit to a tenant's code. It was
never a claim that running a tenant's tests has no side effects, and no
test suite anywhere has that property. So the runner now refuses to start
against a checkout that is already dirty (it cannot tell its own changes
from someone else's), restores the tracked files it disturbed once the gate
has run, and lists — without deleting — any untracked files the gate
created. A checkout it cannot put back to clean is reported unverifiable:
a checkout we could not restore is one we can no longer make claims about.
"""

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from nexus.assurance.isolation import RepoStatus, working_tree_status
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
    #: What the runner found and put back after the gate left a genuine
    #: tenant checkout dirty. Tracked paths are restored via
    #: `git checkout -- .` and appear here as-is; untracked paths the gate
    #: created are listed too, suffixed to make clear they were left alone --
    #: deleting a file we did not create is a bigger risk than leaving it.
    restored: tuple[str, ...] = ()


def _parse_metrics(stdout: str) -> dict | None:
    """Find the last JSON object on stdout. Tolerate surrounding noise.

    Scans backwards from each `{` and lets the decoder consume as much as it
    needs, so an object spanning many lines parses. `make` echoes the
    command it is about to run and other tooling adds its own chatter, so
    the gate's own output is what comes last.

    The first version read one line at a time and therefore understood only
    compact output. It looked correct against shopscout and silently
    reported "no metrics" for wealthwise, whose gate pretty-prints — a
    tenant doing something completely ordinary. A runner that accepts one
    formatting style is a runner that mislabels formatting as absence, and
    absence is what it would then have compared baselines against.
    """
    decoder = json.JSONDecoder()
    for idx in range(len(stdout) - 1, -1, -1):
        if stdout[idx] != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stdout[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


def _git_status_lines(cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _restore_checkout(cwd: Path) -> tuple[tuple[str, ...], bool]:
    """Put back what the gate changed in a tracked file, and report the rest.

    `git checkout -- .` restores every tracked path the gate modified or
    deleted; it never touches untracked paths, because a file the gate
    created might be worth keeping and deleting something we did not create
    is the bigger risk. Returns every path worth reporting -- restored and
    merely noted -- and whether the tracked half of the tree actually came
    back clean.
    """
    before = _git_status_lines(cwd)
    tracked = [line[3:] for line in before if not line.startswith("??")]
    untracked = [line[3:] for line in before if line.startswith("??")]
    if tracked:
        subprocess.run(["git", "checkout", "--", "."], cwd=cwd, capture_output=True, text=True)

    after = _git_status_lines(cwd)
    still_dirty_tracked = [line for line in after if not line.startswith("??")]

    report = tuple(tracked) + tuple(f"{p} (untracked, left in place)" for p in untracked)
    return report, not still_dirty_tracked


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

    # The isolation dance below only applies to a genuine tenant checkout
    # living outside this repo. wuwork's cwd is REPO_ROOT itself -- treating
    # this repo's own working tree as something to snapshot and restore would
    # fight the very commits this project's own development makes here. The
    # zero-touch contract was always about the four incumbent checkouts, not
    # about nexus's own repo policing its own uncommitted work.
    guard_isolation = policy.repo_path is not None

    if guard_isolation and working_tree_status(Path(cwd)) == RepoStatus.DIRTY:
        return GateOutcome(
            tenant=policy.tenant,
            command=policy.gate_command,
            exit_code=None,
            passed=False,
            unverifiable=True,
            output_tail=(
                f"checkout at {cwd} is already dirty before the run; refusing "
                "to start -- we cannot tell our changes from someone else's, "
                "and restoring afterwards would destroy their work"
            ),
        )

    env = {**os.environ, **env_overrides}
    # Run the gate as if the tenant's virtualenv were activated. helpmate's
    # Makefile says `python -m eval.run_eval` with a bare interpreter name,
    # which resolves only inside an activated venv; the others spell out
    # `.venv/bin/python`. Without this the runner reports "gate failed" for
    # helpmate when nothing about helpmate is failing -- the first real
    # incumbent it was pointed at, and it got the diagnosis wrong.
    #
    # Prepending to PATH is environment injection, which the zero-touch
    # contract allows. Editing the tenant's Makefile to spell out an
    # interpreter is exactly what it forbids.
    venv_bin = Path(cwd) / ".venv" / "bin"
    if venv_bin.is_dir():
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(venv_bin.parent)
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

    restored: tuple[str, ...] = ()
    if guard_isolation and working_tree_status(Path(cwd)) == RepoStatus.DIRTY:
        restored, ok = _restore_checkout(Path(cwd))
        if not ok:
            return GateOutcome(
                tenant=policy.tenant,
                command=policy.gate_command,
                exit_code=proc.returncode,
                passed=False,
                unverifiable=True,
                metrics_unavailable=metrics is None,
                metrics=metrics or {},
                output_tail=tail + "\ncould not restore the checkout to clean after the gate ran",
                restored=restored,
            )

    return GateOutcome(
        tenant=policy.tenant,
        command=policy.gate_command,
        exit_code=proc.returncode,
        passed=proc.returncode == 0,
        metrics_unavailable=metrics is None,
        metrics=metrics or {},
        output_tail=tail,
        restored=restored,
    )
