import ast
from pathlib import Path

import pytest

from nexus.state import State, get_state

SRC = Path(__file__).resolve().parent.parent / "src" / "nexus"


def test_get_state_is_cached(monkeypatch, policies_dir):
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    try:
        assert get_state() is get_state()
    finally:
        get_state.cache_clear()


def test_state_carries_the_four_wirings(monkeypatch, policies_dir):
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    try:
        s = get_state()
        assert isinstance(s, State)
        assert set(s.policies) == {
            "helpmate", "shopscout", "wealthwise", "aura", "wuwork"
        }
        assert s.ledger.entries() == []
        assert s.upstream.charges() == []
    finally:
        get_state.cache_clear()


def test_no_module_imports_the_app_to_reach_state():
    # The Phase 1 workaround was a function-local `from nexus.app import
    # get_state` inside the request handler, to dodge a cycle. Routing,
    # diversity and fallback all need state too, so that workaround was one
    # task away from being copy-pasted three more times. This test makes the
    # cycle structurally impossible to reintroduce rather than trusting
    # reviewers to notice it.
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "app.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "nexus.app"
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("nexus.app"):
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == [], f"these modules import nexus.app: {offenders}"


def test_default_upstream_is_the_fake_one(monkeypatch, policies_dir):
    from nexus.upstream import FakeUpstream

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    try:
        assert isinstance(get_state().upstream, FakeUpstream)
    finally:
        get_state.cache_clear()


def test_litellm_upstream_is_opt_in(monkeypatch, policies_dir):
    # Opt-in, never the default. A test run or a fresh clone that silently
    # reached real providers would bill someone real money for a `make test`.
    from nexus.upstream_litellm import LiteLLMUpstream

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("UPSTREAM", "litellm")
    get_state.cache_clear()
    try:
        assert isinstance(get_state().upstream, LiteLLMUpstream)
    finally:
        get_state.cache_clear()


def test_unknown_upstream_name_is_refused(monkeypatch, policies_dir):
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("UPSTREAM", "typo")
    get_state.cache_clear()
    try:
        with pytest.raises(ValueError):
            get_state()
    finally:
        get_state.cache_clear()


def test_state_policies_come_from_the_effective_layer():
    # Asserts the binding. A State that composed nothing would read declared
    # values, and a gateway running on declared values is one on which
    # overrides do not exist.
    #
    # `load_policies` is also imported now -- deliberately, for `declared`,
    # which the console needs in order to show what was tightened. So the
    # claim is no longer "load_policies is absent" but "policies is the
    # composed one and declared is kept beside it".
    import inspect

    import nexus.state as st

    assert hasattr(st, "load_effective_policies")
    source = inspect.getsource(st.get_state)
    assert "load_effective_policies(settings.policies_dir" in source
    assert "policies = load_policies(" not in source


def test_state_keeps_the_declared_policies_beside_the_effective_ones(
    monkeypatch, policies_dir
):
    # With no overrides the two are equal, and that equality is Phase 4a's
    # completion criterion holding at the assembly point rather than only in
    # the composition unit test.
    from nexus.state import get_state

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("DATABASE_URL", "")
    get_state.cache_clear()
    try:
        state = get_state()
        assert state.declared and state.policies == state.declared
    finally:
        get_state.cache_clear()
