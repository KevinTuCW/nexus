"""wuwork's own gate: retrieval accuracy and refusal correctness.

Runs entirely offline and calls no model. That is a requirement, not a
convenience: this gate is the first thing the conformance runner is pointed
at, and a baseline whose numbers move between runs cannot detect a
regression — it can only produce arguments about whether one happened.

Two interface decisions follow directly from zero-touch integration, where
the runner may inject environment variables but may not rewrite the command
a tenant declares:

  - thresholds are read from the environment, not from CLI flags;
  - the machine-readable result goes to stdout unconditionally, with human
    notes on stderr, because the runner cannot pass a `--json` switch.

Thresholds are expressed as "how many golden cases may regress before this
goes red", not as a bare fraction. A fraction invites a number that looks
reasonable and means nothing; a case count is a decision someone made.
"""

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from tenants.wuwork.qa import MIN_QUERY_BIGRAM_HITS, _lexical_hits
from tenants.wuwork.retrieve import Corpus

GOLDEN = Path(__file__).resolve().parent / "golden.json"

#: How many answerable cases may fall over before the gate fires. One: a
#: single flipped case is worth knowing about, but demanding a permanent
#: perfect score makes the gate brittle enough that someone eventually
#: raises the threshold instead of fixing the corpus.
RETRIEVAL_SLACK_CASES = 1

#: Zero. The two refusal cases exist because an internal assistant that
#: answers HR questions from a model's own priors is the failure mode staff
#: are most likely to trust. "Only one of them regressed" is not a comfort.
REFUSAL_SLACK_CASES = 0


@dataclass(frozen=True)
class Result:
    retrieval_accuracy: float
    refusal_correctness: float
    n_cases: int


def _grounded(question: str, corpus: Corpus):
    """Top hit, or None when lexical overlap is too thin to answer from."""
    top = corpus.search(question, top_k=1)
    if not top:
        return None
    if _lexical_hits(question, top[0].text) < MIN_QUERY_BIGRAM_HITS:
        return None
    return top[0]


def run_eval() -> Result:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    corpus = Corpus.load()
    answerable = [c for c in cases if c["expect_doc"] is not None]
    refusals = [c for c in cases if c["expect_doc"] is None]

    hits = sum(
        1
        for case in answerable
        if (g := _grounded(case["question"], corpus)) is not None
        and g.doc_id == case["expect_doc"]
    )
    correct_refusals = sum(
        1 for case in refusals if _grounded(case["question"], corpus) is None
    )

    return Result(
        retrieval_accuracy=hits / len(answerable) if answerable else 0.0,
        refusal_correctness=(
            correct_refusals / len(refusals) if refusals else 1.0
        ),
        n_cases=len(cases),
    )


def main() -> int:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    n_answerable = sum(1 for c in cases if c["expect_doc"] is not None)
    n_refusals = sum(1 for c in cases if c["expect_doc"] is None)

    default_retrieval = (n_answerable - RETRIEVAL_SLACK_CASES) / n_answerable
    default_refusal = (
        (n_refusals - REFUSAL_SLACK_CASES) / n_refusals if n_refusals else 1.0
    )
    min_retrieval = float(os.environ.get("WUWORK_MIN_RETRIEVAL", default_retrieval))
    min_refusal = float(os.environ.get("WUWORK_MIN_REFUSAL", default_refusal))

    result = run_eval()
    print(json.dumps(asdict(result), ensure_ascii=False))

    failures = []
    if result.retrieval_accuracy < min_retrieval:
        failures.append(
            f"retrieval_accuracy {result.retrieval_accuracy:.3f} < {min_retrieval:.3f}"
        )
    if result.refusal_correctness < min_refusal:
        failures.append(
            f"refusal_correctness {result.refusal_correctness:.3f} < {min_refusal:.3f}"
        )
    for line in failures:
        print(f"GATE FAILED: {line}", file=sys.stderr)
    if not failures:
        print(f"GATE PASSED over {result.n_cases} cases", file=sys.stderr)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
