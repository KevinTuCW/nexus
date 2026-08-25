"""Money as integer nano-USD (1e-9 USD).

Cents cannot represent LLM pricing. One token of a $0.60-per-million model
costs 0.00006 cents, which is 0 in integer cents. Every per-call cost would
round to zero, the ledger would reconcile against itself perfectly, and
gate G2 would be green while measuring nothing — the most dangerous shape a
gate can take.

Floats are not an option either: the ledger sums hundreds of thousands of
rows, and binary floating point drift is exactly the kind of error G2 claims
to have driven to zero.

nano-USD is exact for every price we use. Published prices are quoted in USD
per million tokens, so `usd_per_million * 1000` is the nano-USD price per
token, and for all real price sheets that product is an integer. Prices
finer than that are refused rather than rounded. int64 tops out near 9.2e9
USD, which is not a ceiling this project will meet.
"""

from dataclasses import dataclass
from decimal import Decimal

NANO_PER_USD = 1_000_000_000


def nanousd_per_token(usd_per_million: str) -> int:
    """Convert a published '$ per 1M tokens' price to nano-USD per token.

    Takes a string on purpose: `Decimal(0.60)` inherits the float's binary
    approximation, while `Decimal("0.60")` is exact. The price table is
    hand-authored from vendor pages, so a string is what we have anyway.
    """
    if not isinstance(usd_per_million, str):
        raise TypeError(
            "pass the published price as a string, e.g. nanousd_per_token('0.60'); "
            "a float carries binary drift into the price table"
        )
    value = Decimal(usd_per_million) * 1000
    if value != value.to_integral_value():
        raise ValueError(
            f"price ${usd_per_million}/1M is finer than one nano-USD per token; "
            "refusing to round — a rounded price makes the ledger self-consistent and wrong"
        )
    return int(value)


@dataclass(frozen=True)
class Price:
    """Per-token prices in nano-USD, one field per billable token kind.

    Cache write and cache read are separate fields, not a discount applied
    to `prompt`: vendors price them independently, and folding them in makes
    the two indistinguishable in the ledger.
    """

    prompt: int
    completion: int
    cache_write: int = 0
    cache_read: int = 0


def format_usd(nanousd: int) -> str:
    """Render nano-USD for humans. Six decimals — enough to see one token."""
    return f"${nanousd / NANO_PER_USD:.6f}"
