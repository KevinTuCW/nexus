"""The four gates, and the only thing in this project that can fail delivery.

Each gate is a pure function taking evidence and returning a list of
violation descriptions. Empty means pass. They deliberately share no
judgement logic: a helper used by two gates is a single edit that can "fix"
both, and these four exist partly to hold each other honest.

The governing rule, inherited from medscope and stated in the README:

    Any gate that can fail the delivery must have a failure mode that has
    been demonstrated.

`--fail-demo <gate>` is that demonstration, turned into a command anyone can
run rather than a claim in a commit message. It injects the exact failure
each gate is meant to catch.

Missing evidence is never a violation. Rows written before Phase 3b carry no
model chain, and a gate that fired on them would be reporting its own blind
spot as the tenant's fault. What to do about evidence that *should* exist
and does not is G3's question, not the other three gates'.
"""

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from nexus.assurance.baseline import compare
from nexus.config import get_settings
from nexus.ledger.book import Entry, UpstreamCharge, reconcile
from nexus.ledger.usage import Usage
from nexus.registry.families import family_of
from nexus.registry.tenants import TenantPolicy, load_policies, substitution_allowed


def check_g4_fallback_is_never_silent(rows: list[Entry]) -> list[str]:
    """G4: a fallback leaves a trace in the ledger, not only in the response.

    A response body is read once, by one caller, at one moment. The ledger
    is what anyone asks afterwards. A fallback that appears in the first and
    not the second is silent exactly where it matters.

    `fallback_from` is checked for agreement with the chain rather than for
    mere presence: a marker naming a model that was never routed to means
    the bookkeeping is wrong, and wrong bookkeeping is the thing this gate
    exists to make loud.
    """
    violations = []
    for row in rows:
        if row.routed_model is None:
            continue
        if row.model == row.routed_model:
            continue
        if row.fallback_from is None:
            violations.append(
                f"G4 call {row.call_id}: routed to {row.routed_model}, served "
                f"by {row.model}, no fallback_from recorded"
            )
        elif row.fallback_from != row.routed_model:
            violations.append(
                f"G4 call {row.call_id}: fallback_from says "
                f"{row.fallback_from} but routing chose {row.routed_model}"
            )
    return violations


def check_g1_diversity_is_never_collapsed(
    rows: list[Entry], policies: dict[str, TenantPolicy]
) -> list[str]:
    """G1: routing never substituted a model the tenant did not permit.

    This is the after-the-fact half of gate G1. `policy.diversity.guard`
    already refuses such a substitution on the request path; this checks the
    books independently, and the two are deliberately not the same code.
    The request-path guard is a single line in a handler — one edit removes
    it, and nothing else in the system would notice. This one would.

    Permission is re-derived from the tenant's policy rather than from the
    weight family. `zai/glm-4.6 -> zai/glm-4.7` stays inside one family and
    a family-only check would wave it through, but shopscout permits no
    substitution at all. The rule was never "same family"; it was "what the
    tenant declared".
    """
    violations = []
    for row in rows:
        if row.requested_model is None or row.routed_model is None:
            continue
        if row.routed_model == row.requested_model:
            continue
        policy = policies.get(row.tenant)
        if policy is None:
            violations.append(
                f"G1 call {row.call_id}: tenant '{row.tenant}' has no policy, "
                "so this substitution cannot be judged -- refusing to treat "
                "a missing policy as permission"
            )
            continue
        target = family_of(row.routed_model)
        if not substitution_allowed(policy, row.requested_model, target):
            violations.append(
                f"G1 call {row.call_id}: tenant '{row.tenant}' asked for "
                f"{row.requested_model}, routing chose {row.routed_model} "
                f"(family '{target}'), which the policy does not permit"
            )
    return violations


def check_g2_attribution_error_is_zero(
    rows: list[Entry], charges: list[UpstreamCharge]
) -> list[str]:
    """G2: the ledger agrees with what providers said they charged.

    The one gate that reuses an existing judgement — `reconcile()` — and the
    reuse is deliberate. `reconcile` is not a helper that happens to be
    useful here; it *is* the definition of this gate, including the rule
    that `aborted` rows are a lower bound rather than a measurement.
    Reimplementing it would not buy independence, only a second copy free to
    drift from the first, and a gate whose definition exists in two places
    is a gate with two definitions.

    What this does not buy, and the README says so: independence from the
    provider. `charges` is derived from the provider's own response, so a
    provider that under-reports is reproduced faithfully. The genuinely
    independent source would be its billing API, which is not integrated.
    """
    return [f"G2 {d.kind}: {d.detail}" for d in reconcile(rows, charges)]


def check_g3_tenant_gates_have_not_regressed(
    current: dict[str, dict], baselines_dir: Path
) -> list[str]:
    """G3: integrating with nexus did not make any tenant worse.

    Two arms, and they prove different things.

    The **offline arm** runs wuwork's gate, which never crosses the gateway.
    It proves wuwork still works; it cannot prove that integration changed
    nothing, because nothing about it goes through nexus. Reporting it as
    the whole of G3 would hang the gate's name on a check that cannot reach
    the gate's subject.

    The **live arm** points a real tenant's gate at nexus and compares
    against the baseline captured before integration. That is the actual
    claim. It needs real credentials and real time, so it is marked `live`
    and skipped without them — never silently passed.

    A tenant with no baseline is reported, not waved through. Silence is the
    cheapest way to make this gate green: stop producing a number and the
    comparison has nothing left to complain about.
    """
    violations = []
    baselines = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(baselines_dir.glob("*.json"))
    }
    for tenant, metrics in current.items():
        base = baselines.get(tenant)
        if base is None:
            violations.append(
                f"G3 tenant '{tenant}' has no baseline; this is not a pass, "
                "it is an unanswered question"
            )
            continue
        for regression in compare(base, metrics):
            violations.append(f"G3 tenant '{tenant}': {regression.detail}")
    for tenant in baselines:
        if tenant not in current:
            violations.append(
                f"G3 tenant '{tenant}' has a baseline but produced no current "
                "run; a gate that stopped running has not started passing"
            )
    return violations


def _demo_row(gate: str) -> Entry:
    """One row carrying exactly the failure `gate` is meant to catch."""
    base = Entry(
        entry_id="demo", call_id="demo", tenant="wuwork", workload="default",
        trace_root=None, span_id="demo", parent_span_id=None,
        model="zai/glm-4.6", family="glm",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=1, status="ok", ts=datetime.now(timezone.utc),
        requested_model="zai/glm-4.6", routed_model="zai/glm-4.6",
    )
    if gate == "g4":
        # Served by something routing never chose, with nothing saying why.
        return replace(base, model="zai/glm-4.7")
    if gate == "g1":
        # A pinned juror served from another lab: the Phase 2b failure,
        # reproducible on demand.
        return replace(
            base,
            tenant="shopscout",
            routed_model="siliconflow/Qwen/Qwen3-8B",
            model="siliconflow/Qwen/Qwen3-8B",
        )
    raise SystemExit(f"no failure demo defined for gate '{gate}'")


def _demo_charge() -> UpstreamCharge:
    """A provider charge the books have never heard of.

    G2's failure lives on the other side of the comparison from G1's and
    G4's, so it cannot be expressed as a ledger row. That asymmetry is the
    gate's point: G2 is the only one that looks outside nexus for its
    evidence.
    """
    return UpstreamCharge(call_id="demo-unbooked", model="zai/glm-4.6", cost_nanousd=6000)


def _demo_g3_baselines_dir() -> str:
    """A throwaway baselines directory for the g3 failure demo.

    Not `baselines/`: this demo must not depend on the real baseline
    numbers staying put, and must not get quietly "fixed" by someone
    updating a real baseline file rather than the code that regressed.
    """
    tmp_dir = tempfile.mkdtemp(prefix="nexus-g3-demo-")
    Path(tmp_dir, "demo-tenant.json").write_text(
        json.dumps({"refusal_correctness": 1.0}), encoding="utf-8"
    )
    return tmp_dir


def load_ledger_rows(ledger_json: str | None) -> list[Entry]:
    """The rows G1 and G4 will judge.

    `--ledger-json` wins when given; otherwise the persisted ledger is used
    if `DATABASE_URL` points at one. An in-memory ledger is not consulted,
    because there is nothing in it: it died with the gateway process that
    wrote it, and this command is a different one.

    This function is the fix for the defect that mattered most in this
    repository. `main()` used to build `rows = []`, hand the empty list to
    three gates, and print `passed` three times -- while the rows those gates
    exist to audit sat in Postgres, unread. Every rule here says that is not
    a pass. G3 already refuses to call a tenant green because it stopped
    producing a number; the other three were doing precisely that, on every
    invocation, and the README quoted the output as proof of four green
    gates.
    """
    if ledger_json:
        raw = json.loads(Path(ledger_json).read_text(encoding="utf-8"))
        return [_entry_from_dict(r) for r in raw]

    # Through Settings, not `os.environ`. The gateway resolves DATABASE_URL
    # via pydantic-settings, which also reads `.env`; reading the raw
    # environment here would have the eval and the gateway disagree about
    # which ledger exists -- and the eval would report "no evidence" over a
    # database that was sitting right there, full of the rows it was asked
    # to audit. Found by running it.
    dsn = get_settings().database_url.strip()
    if not dsn:
        return []
    from nexus.ledger.pg import PgLedger

    return PgLedger(dsn).entries()


def _entry_from_dict(raw: dict) -> Entry:
    """Rehydrate one ledger row from JSON.

    Kept alongside the loader rather than on `Entry` itself: this is an
    evidence-import format for the gates, not a second serialisation of the
    row shape that the gateway or the database would ever write.
    """
    ts = raw["ts"]
    return Entry(
        entry_id=raw["entry_id"],
        call_id=raw["call_id"],
        tenant=raw["tenant"],
        workload=raw["workload"],
        trace_root=raw.get("trace_root"),
        span_id=raw["span_id"],
        parent_span_id=raw.get("parent_span_id"),
        model=raw["model"],
        family=raw["family"],
        usage=Usage(
            prompt_tokens=raw.get("prompt_tokens", 0),
            completion_tokens=raw.get("completion_tokens", 0),
            cache_write_tokens=raw.get("cache_write_tokens", 0),
            cache_read_tokens=raw.get("cache_read_tokens", 0),
        ),
        cost_nanousd=raw["cost_nanousd"],
        status=raw["status"],
        ts=datetime.fromisoformat(ts) if isinstance(ts, str) else ts,
        fallback_from=raw.get("fallback_from"),
        requested_model=raw.get("requested_model"),
        routed_model=raw.get("routed_model"),
    )


def load_charges(charges_json: str | None) -> list[UpstreamCharge]:
    """What the providers said they charged. G2's other side.

    There is no default source, and that absence is the honest state of the
    project: nexus does not integrate any provider's billing API, so once the
    gateway process that made the calls has exited, nothing here knows what
    was charged. Supplying an empty list and letting `reconcile` run would
    turn "we do not know what the provider charged" into "the provider
    charged nothing", and every row in the ledger would be reported as an
    orphan -- a red gate that means the opposite of what it says.
    """
    if not charges_json:
        return []
    raw = json.loads(Path(charges_json).read_text(encoding="utf-8"))
    return [
        UpstreamCharge(
            call_id=c["call_id"], model=c["model"], cost_nanousd=c["cost_nanousd"]
        )
        for c in raw
    ]


#: A gate's verdict. `NO_EVIDENCE` is a first-class third outcome, not a
#: flavour of pass: a gate that judged nothing has not judged anything, and
#: the single cheapest way to make any gate in this file green is to stop
#: feeding it.
PASSED, FAILED, NO_EVIDENCE = "passed", "FAILED", "no evidence"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fail-demo",
        choices=["g1", "g2", "g3", "g4"],
        help="inject the failure this gate is meant to catch, and prove it fires",
    )
    ap.add_argument(
        "--ledger-json",
        help="ledger rows for G1/G4 to judge; defaults to DATABASE_URL if set",
    )
    ap.add_argument(
        "--charges-json",
        help="what the providers charged, for G2 to reconcile the ledger against",
    )
    ap.add_argument(
        "--require-evidence",
        action="store_true",
        help="exit 2 when a gate had nothing to judge, not only when one fails",
    )
    args = ap.parse_args()

    rows: list[Entry] = []
    charges: list[UpstreamCharge] = []
    current: dict[str, dict] = {}
    conformance_ran = False
    demo_baselines_dir = None

    if args.fail_demo == "g2":
        charges.append(_demo_charge())
    elif args.fail_demo == "g3":
        # A hard metric slipping below a synthetic baseline, in a directory
        # this demo controls -- see _demo_g3_baselines_dir.
        demo_baselines_dir = _demo_g3_baselines_dir()
        current = {"demo-tenant": {"refusal_correctness": 0.5}}
        conformance_ran = True
    elif args.fail_demo:
        row = _demo_row(args.fail_demo)
        rows.append(row)
        # A charge that matches the demo row exactly, so the failure this
        # demo is meant to catch is the only one that fires -- not G2's,
        # which is about the ledger disagreeing with the provider, not
        # about the substance of this row.
        charges.append(
            UpstreamCharge(call_id=row.call_id, model=row.model, cost_nanousd=row.cost_nanousd)
        )
    else:
        rows = load_ledger_rows(args.ledger_json)
        charges = load_charges(args.charges_json)

    policies = load_policies(Path(__file__).resolve().parents[2] / "policies")

    print(
        f"evidence: {len(rows)} ledger row(s), {len(charges)} upstream charge(s)"
        + (
            ""
            if rows or charges
            else " -- nothing to judge. Point --ledger-json at exported rows, "
            "or set DATABASE_URL to the ledger the gateway writes."
        ),
        file=sys.stderr,
    )

    # (verdict, violations). A gate with no evidence never reaches its check
    # function: running a check over an empty list and reporting the empty
    # result as a pass is the whole defect this structure exists to remove.
    results: dict[str, tuple[str, list[str]]] = {}
    results["G1"] = _judge(
        bool(rows), lambda: check_g1_diversity_is_never_collapsed(rows, policies)
    )
    results["G2"] = _judge(
        bool(charges), lambda: check_g2_attribution_error_is_zero(rows, charges)
    )
    results["G4"] = _judge(bool(rows), lambda: check_g4_fallback_is_never_silent(rows))
    results["G3"] = _judge(
        conformance_ran,
        lambda: check_g3_tenant_gates_have_not_regressed(
            current, Path(demo_baselines_dir)
        ),
    )
    if demo_baselines_dir:
        shutil.rmtree(demo_baselines_dir, ignore_errors=True)

    failed = False
    starved = False
    for name in ("G1", "G2", "G3", "G4"):
        verdict, violations = results[name]
        if verdict == FAILED:
            failed = True
            print(f"{name}: FAILED ({len(violations)})", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
        elif verdict == NO_EVIDENCE:
            starved = True
            print(f"{name}: no evidence -- {_STARVED_WHY[name]}", file=sys.stderr)
        else:
            print(f"{name}: passed", file=sys.stderr)

    if starved and not args.require_evidence:
        print(
            "\nSome gates judged nothing. That is not a pass; it is an "
            "unanswered question. Run with --require-evidence to make it "
            "fail the delivery.",
            file=sys.stderr,
        )
    return 2 if failed or (starved and args.require_evidence) else 0


#: Why a gate had nothing to judge, in the terms of what to do about it.
#: A bare "no evidence" tells a reader that something is missing without
#: telling them which of four different things it is.
_STARVED_WHY = {
    "G1": "no ledger rows (--ledger-json / DATABASE_URL)",
    "G2": "no provider charges to reconcile against (--charges-json); "
    "nexus integrates no billing API, so this is the project's stated gap",
    "G3": "no conformance run in this invocation",
    "G4": "no ledger rows (--ledger-json / DATABASE_URL)",
}


def _judge(has_evidence: bool, check) -> tuple[str, list[str]]:
    if not has_evidence:
        return NO_EVIDENCE, []
    violations = check()
    return (FAILED, violations) if violations else (PASSED, [])


if __name__ == "__main__":
    raise SystemExit(main())
