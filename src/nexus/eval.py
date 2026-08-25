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

from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage


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
    raise SystemExit(f"no failure demo defined for gate '{gate}'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fail-demo",
        choices=["g4"],
        help="inject the failure this gate is meant to catch, and prove it fires",
    )
    args = ap.parse_args()

    rows: list[Entry] = []
    if args.fail_demo:
        rows.append(_demo_row(args.fail_demo))

    results = {"G4": check_g4_fallback_is_never_silent(rows)}

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
