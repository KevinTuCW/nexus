import pytest

from nexus.money import Price, format_usd, nanousd_per_token


def test_published_price_converts_exactly():
    # $0.60 per 1M tokens = 600 nano-USD per token, exactly.
    assert nanousd_per_token("0.60") == 600
    assert nanousd_per_token("2.50") == 2500
    assert nanousd_per_token("11.00") == 11000


def test_price_finer_than_nano_usd_is_refused_not_rounded():
    # Silently rounding a price is how a ledger balances against itself
    # while being wrong. Refuse instead.
    with pytest.raises(ValueError):
        nanousd_per_token("0.0001")


def test_float_input_is_refused():
    # 0.6 * 1000 is not exactly 600.0 in binary floating point on every
    # value we might meet; the table is hand-authored, so demand strings.
    with pytest.raises(TypeError):
        nanousd_per_token(0.60)  # type: ignore[arg-type]


def test_cents_would_have_been_useless():
    # Documents *why* the unit is nano-USD: one token of a $0.60/1M model
    # costs 0.00006 cents. In integer cents every call costs 0, the ledger
    # balances perfectly, and gate G2 measures nothing.
    ONE_CENT_IN_NANO = 10_000_000
    assert nanousd_per_token("0.60") * 1 < ONE_CENT_IN_NANO


def test_format_usd_is_readable():
    assert format_usd(1_500_000_000) == "$1.500000"
    assert format_usd(600) == "$0.000001"


def test_price_is_frozen():
    p = Price(prompt=600, completion=2200, cache_write=750, cache_read=60)
    with pytest.raises(Exception):
        p.prompt = 1  # type: ignore[misc]
