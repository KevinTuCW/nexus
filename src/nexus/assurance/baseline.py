"""Comparing a tenant's current metrics against its pre-integration baseline.

Gate G3 says integration must not make a tenant worse. Turning that into a
check requires deciding how much movement is noise, and the answer is not
the same for every number.

**Soft metrics get a tolerance band.** LLM-backed figures move a little
between runs. A gate that fires on every wobble is muted within a week, and
a muted gate is worse than no gate: everyone still believes something is
watching.

**Hard metrics get zero.** `compliance_pass_rate` is the shape of
shopscout's 51/51 and wealthwise's 64/64; `critical_recall` is medscope's
"no missed critical finding". For these, "down by only one" is precisely the
regression the tenant exists to prevent, and a tolerance band exists only to
swallow it.

**A metric that disappeared is a regression.** Otherwise the cheapest way to
turn a gate green is to stop reporting the number it checks — and that is
not hypothetical, it is what happens when someone tidies up an eval's
output.

**Every metric here is higher-is-better.** That is an assumption, not a law,
and it is the reason `phi_leaks` is deliberately absent from the hard set
even though it belongs there on the merits: with 0 as its perfect score, a
rise from 0 to 3 would be scored as an improvement by the comparison below.
Adding direction handling before any tenant reports such a metric would be
speculative; adding the metric without it would be wrong. A test enforces
that the two stay in step.
"""

from dataclasses import dataclass

#: Absolute, not relative. A relative band on a metric that lives near 1.0
#: is far tighter than the same band near 0.5, and these thresholds are
#: easier to reason about when they mean one thing everywhere.
SOFT_TOLERANCE = 0.05

#: Metrics whose whole purpose is to be exactly met. Tolerance is zero.
#: Higher-is-better only — see the module docstring.
HARD_METRICS = frozenset(
    {
        "compliance_pass_rate",
        "critical_recall",
        "suitability_pass_rate",
        "refusal_correctness",
    }
)


@dataclass(frozen=True)
class Regression:
    metric: str
    baseline: float | None
    current: float | None
    detail: str


def compare(baseline: dict, current: dict) -> list[Regression]:
    """Every way `current` is worse than `baseline`. Empty means no regression."""
    problems: list[Regression] = []
    for metric, base_value in baseline.items():
        if not isinstance(base_value, (int, float)) or isinstance(base_value, bool):
            continue  # captured_at and other non-numeric bookkeeping
        if metric not in current:
            problems.append(
                Regression(
                    metric=metric,
                    baseline=base_value,
                    current=None,
                    detail=(
                        f"'{metric}' is missing from the current run; a metric "
                        "that stopped being reported has not stopped mattering"
                    ),
                )
            )
            continue
        now = current[metric]
        tolerance = 0.0 if metric in HARD_METRICS else SOFT_TOLERANCE
        if now < base_value - tolerance:
            problems.append(
                Regression(
                    metric=metric,
                    baseline=base_value,
                    current=now,
                    detail=(
                        f"'{metric}' {now} < baseline {base_value} "
                        f"(tolerance {tolerance})"
                    ),
                )
            )
    return problems
