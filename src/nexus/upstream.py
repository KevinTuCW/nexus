"""Upstream providers.

Phase 1 ships only `FakeUpstream`. It is not a placeholder for the real
adapter — it is what makes the whole metering and reconciliation path
testable offline and deterministically, and it stays in the test suite after
LiteLLM lands in P2.

It also records what it "charged", which is what reconciliation compares the
ledger against. A fake that did not do this would let the ledger be checked
only against itself.
"""

from dataclasses import dataclass
from typing import Protocol

from nexus.ledger.book import UpstreamCharge
from nexus.ledger.usage import Usage, cost_nanousd
from nexus.money import Price, nanousd_per_token

#: Hand-authored from vendor price pages. Prices are per million tokens, as
#: published; `nanousd_per_token` refuses anything finer than one nano-USD
#: per token rather than rounding it away.
PRICES: dict[str, Price] = {
    "zai/glm-4.6": Price(
        prompt=nanousd_per_token("0.60"),
        completion=nanousd_per_token("2.20"),
    ),
    "zai/glm-4.7": Price(
        prompt=nanousd_per_token("0.60"),
        completion=nanousd_per_token("2.20"),
    ),
    "siliconflow/Qwen/Qwen3-8B": Price(
        prompt=nanousd_per_token("0.06"),
        completion=nanousd_per_token("0.06"),
    ),
    "siliconflow/Qwen/Qwen3-235B-A22B": Price(
        prompt=nanousd_per_token("0.35"),
        completion=nanousd_per_token("1.40"),
    ),
    "dashscope/qwen3-235b-a22b": Price(
        prompt=nanousd_per_token("0.35"),
        completion=nanousd_per_token("1.40"),
    ),
    "siliconflow/deepseek-ai/DeepSeek-V3": Price(
        prompt=nanousd_per_token("0.27"),
        completion=nanousd_per_token("1.10"),
    ),
}


class UnpricedModel(Exception):
    """We have no price for this model, so we will not serve it.

    Serving it would write a zero-cost row that reconciles perfectly against
    a zero-cost charge — a ledger that is exactly wrong and entirely
    self-consistent.
    """


class UpstreamUnavailable(Exception):
    """The provider could not serve this call. Fallback territory."""


@dataclass(frozen=True)
class Completion:
    content: str
    usage: Usage


class Upstream(Protocol):
    """What the gateway needs from a provider.

    Declared so `state.py` can hold an upstream without importing a concrete
    one, and so the LiteLLM adapter arriving in P2b has a shape to satisfy
    rather than a class to subclass.

    `charges()` is part of the interface, not an artefact of the fake: gate
    G2 reconciles the ledger against what the provider says it charged, and
    an upstream that cannot report that turns reconciliation into the ledger
    checking itself.
    """

    def complete(self, call_id: str, model: str, messages: list[dict]) -> Completion: ...

    def charges(self) -> list[UpstreamCharge]: ...


class FakeUpstream:
    """Deterministic stand-in provider that also books what it charged.

    `fail_models` exists so the fallback path is testable without a network
    partition. A fallback path that has only ever run in production is a
    path nobody has read.
    """

    def __init__(self, fail_models: frozenset[str] = frozenset()) -> None:
        self._charges: list[UpstreamCharge] = []
        self._fail_models = fail_models

    def complete(self, call_id: str, model: str, messages: list[dict]) -> Completion:
        price = PRICES.get(model)
        if price is None:
            raise UnpricedModel(model)
        if model in self._fail_models:
            raise UpstreamUnavailable(model)
        prompt_tokens = sum(len(m.get("content", "")) for m in messages) or 1
        usage = Usage(prompt_tokens=prompt_tokens, completion_tokens=8)
        self._charges.append(
            UpstreamCharge(
                call_id=call_id,
                model=model,
                cost_nanousd=cost_nanousd(usage, price),
            )
        )
        return Completion(content=f"[fake:{model}] ack", usage=usage)

    def charges(self) -> list[UpstreamCharge]:
        return list(self._charges)
