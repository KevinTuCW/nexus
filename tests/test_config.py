from nexus.config import Settings


def test_defaults_do_not_reach_the_network():
    s = Settings()
    assert s.upstream_timeout_s > 0
    assert s.default_currency_unit == "nanousd"


def test_env_overrides_a_field(monkeypatch):
    monkeypatch.setenv("UPSTREAM_TIMEOUT_S", "42")
    assert Settings().upstream_timeout_s == 42
