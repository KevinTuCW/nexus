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
and does not is G3's question, not this module's.
"""

import argparse
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fail-demo",
        choices=["g1", "g2", "g4"],
        help="inject the failure this gate is meant to catch, and prove it fires",
    )
    args = ap.parse_args()

    rows: list[Entry] = []
    charges: list[UpstreamCharge] = []
    if args.fail_demo == "g2":
        charges.append(_demo_charge())
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

    policies = load_policies(Path(__file__).resolve().parents[2] / "policies")

    results = {
        "G1": check_g1_diversity_is_never_collapsed(rows, policies),
        "G2": check_g2_attribution_error_is_zero(rows, charges),
        "G4": check_g4_fallback_is_never_silent(rows),
    }

    failed = False
    for name, violations in results.items():
        if violations:
            failed = True
            print(f"{name}: FAILED ({len(violations)})", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
        else:
            print(f"{name}: passed", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
