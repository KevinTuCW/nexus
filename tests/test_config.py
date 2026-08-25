from nexus.config import Settings


def test_settings_read_defaults_not_this_machines_dotenv():
    # Asserts the *exact* default on purpose. `> 0` would pass just as
    # happily on a value leaked in from a developer's real .env, making it a
    # check that cannot fail for the reason it exists. This is the standing
    # version of the manual leak test: on a machine whose .env sets
    # UPSTREAM_TIMEOUT_S to anything but 60, this goes red.
    s = Settings()
    assert s.upstream_timeout_s == 60
    assert s.default_currency_unit == "nanousd"


def test_env_overrides_a_field(monkeypatch):
    monkeypatch.setenv("UPSTREAM_TIMEOUT_S", "42")
    assert Settings().upstream_timeout_s == 42
