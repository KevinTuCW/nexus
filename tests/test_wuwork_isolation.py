import ast
from pathlib import Path

import pytest

WUWORK = Path(__file__).resolve().parent.parent / "tenants" / "wuwork"


def test_wuwork_never_imports_nexus_internals():
    # wuwork lives in this repo for convenience, but it is a *tenant*: the
    # number this project reports as "cost to onboard a new business line"
    # only means something if wuwork reached the gateway the way an outside
    # team would. One `from nexus.ledger import ...` and the figure silently
    # becomes "how fast can you write code inside the same codebase", which
    # is a different and much less interesting question.
    offenders = []
    for path in WUWORK.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "nexus"
            ):
                offenders.append(f"{path.name}:{node.lineno} from {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("nexus"):
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
    assert offenders == [], f"wuwork imports nexus internals: {offenders}"


def test_wuwork_talks_to_a_configured_base_url():
    from tenants.wuwork.config import WuworkSettings

    s = WuworkSettings(nexus_base_url="http://example/v1", nexus_api_key="k")
    assert s.nexus_base_url == "http://example/v1"


def test_wuwork_refuses_to_start_without_a_key():
    # Same rule the gateway applies to itself: a blank credential must not
    # become a silent anonymous call that fails at the far end and looks
    # like an outage.
    from tenants.wuwork.config import WuworkSettings
    from tenants.wuwork.client import NexusClient

    with pytest.raises(RuntimeError) as exc:
        NexusClient(WuworkSettings(nexus_base_url="http://example/v1", nexus_api_key=""))
    assert "NEXUS_API_KEY" in str(exc.value)
