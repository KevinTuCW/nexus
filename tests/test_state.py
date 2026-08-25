import ast
from pathlib import Path

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
