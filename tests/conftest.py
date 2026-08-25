"""Shared pytest fixtures for nexus.

`_isolated_settings` clears every variable `Settings` would read by
**enumerating `Settings.model_fields`** rather than matching a prefix.
Settings deliberately has no `env_prefix`, so there is no prefix to match;
and deriving the list from the model means a field added later is isolated
automatically. A hand-maintained list silently stops covering new settings,
and the failure mode of that is a test run quietly picking up a real
credential from this machine's .env — which is exactly what happened in the
sibling `aura` project.
"""

import os
from pathlib import Path

import pytest

from nexus.config import Settings

#: Third-party conventions that aren't nexus settings but still steer SDKs.
_ISOLATED_PREFIXES = ("OPENAI_", "LANGFUSE_", "NEXUS_")


def _settings_env_names() -> set[str]:
    names = set(Settings.model_fields)
    # Provider key variables are named in providers.ENDPOINTS as *values*,
    # not as Settings fields, so neither the field-name sweep above nor the
    # prefix sweep below would clear them -- a developer's real GLM or
    # SiliconFlow key would stay visible to the whole suite. Deriving them
    # from the registry keeps a newly added provider isolated automatically,
    # for the same reason the field list is derived rather than typed out.
    from nexus.providers import ENDPOINTS

    names |= {e.api_key_env for e in ENDPOINTS.values()}
    return {n.upper() for n in names} | {n.lower() for n in names}


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, request):
    if request.node.get_closest_marker("live"):
        # Live tests exist precisely to use real credentials. Isolating them
        # would strip the key this fixture was extended to hide, and they
        # would fail for a reason unrelated to what they test -- which is
        # exactly what happened the first time `make test-live` ran.
        #
        # Safe because reaching them is opt-in twice over: the marker plus a
        # separate make target. A test without the marker still cannot see a
        # provider key, which is the property that matters.
        return
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    managed = _settings_env_names()
    for key in list(os.environ):
        if key in managed or key.startswith(_ISOLATED_PREFIXES):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def policies_dir() -> Path:
    """The real policies/ directory, resolved from this file's location.

    Not `Path("policies")`: that depends on the pytest working directory and
    would pass locally while failing in CI.
    """
    return Path(__file__).resolve().parent.parent / "policies"
