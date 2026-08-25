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

import pytest

from nexus.config import Settings

#: Third-party conventions that aren't nexus settings but still steer SDKs.
_ISOLATED_PREFIXES = ("OPENAI_", "LANGFUSE_", "NEXUS_")


def _settings_env_names() -> set[str]:
    names = set(Settings.model_fields)
    return {n.upper() for n in names} | {n.lower() for n in names}


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    managed = _settings_env_names()
    for key in list(os.environ):
        if key in managed or key.startswith(_ISOLATED_PREFIXES):
            monkeypatch.delenv(key, raising=False)
