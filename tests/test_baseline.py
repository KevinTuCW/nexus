from nexus.assurance.baseline import HARD_METRICS, Regression, compare


def test_matching_metrics_report_no_regression():
    assert compare({"retrieval_accuracy": 0.9}, {"retrieval_accuracy": 0.9}) == []


def test_a_soft_metric_within_tolerance_is_allowed():
    # LLM-backed numbers move a little between runs. A gate that fires on
    # every wobble gets muted within a week, and a muted gate is worse than
    # no gate because everyone still believes it is watching.
    assert compare({"retrieval_accuracy": 0.90}, {"retrieval_accuracy": 0.88}) == []


def test_a_soft_metric_beyond_tolerance_is_a_regression():
    problems = compare({"retrieval_accuracy": 0.90}, {"retrieval_accuracy": 0.70})
    assert [p.metric for p in problems] == ["retrieval_accuracy"]
    assert isinstance(problems[0], Regression)


def test_hard_metrics_have_zero_tolerance():
    # compliance_pass_rate is the shape of shopscout's 51/51 and
    # wealthwise's 64/64. "Down by only one" is exactly the regression these
    # tenants exist to prevent, and a tolerance band would swallow it.
    assert "compliance_pass_rate" in HARD_METRICS
    problems = compare({"compliance_pass_rate": 1.0}, {"compliance_pass_rate": 0.999})
    assert [p.metric for p in problems] == ["compliance_pass_rate"]


def test_improvements_are_never_regressions():
    assert compare({"retrieval_accuracy": 0.80}, {"retrieval_accuracy": 0.95}) == []


def test_a_metric_that_vanished_is_a_regression_not_a_pass():
    # A tenant that stops reporting a metric must not thereby stop being
    # measured by it -- that is the cheapest possible way to make a gate
    # green.
    problems = compare({"retrieval_accuracy": 0.9}, {})
    assert [p.metric for p in problems] == ["retrieval_accuracy"]
    assert "missing" in problems[0].detail


def test_non_numeric_baseline_fields_are_ignored():
    # baselines/*.json carry a captured_at stamp. Comparing a timestamp
    # against itself with a tolerance is meaningless, and crashing on it
    # would make the file unable to record when it was taken.
    assert compare(
        {"captured_at": "2026-08-25T12:00:00Z", "retrieval_accuracy": 0.9},
        {"retrieval_accuracy": 0.9},
    ) == []


def test_no_lower_is_better_metric_is_in_the_hard_set():
    # `compare` treats every metric as higher-is-better. A metric like
    # phi_leaks, where 0 is perfect, would be judged "improved" as it rose
    # from 0 to 3. None are registered today; this test is what stops one
    # being added without the direction handling that would make it correct.
    lower_is_better_names = {"phi_leaks", "error_rate", "latency_p95_ms", "cost_nanousd"}
    assert HARD_METRICS & lower_is_better_names == set()
