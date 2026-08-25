"""Phase 1 must be provably offline."""

import sys


def test_phase1_imports_no_llm_sdk():
    # Importing the app must not pull in a provider SDK. If it does, an
    # accidental live call becomes possible in a suite that is supposed to
    # be hermetic.
    for module in ("openai", "litellm", "langfuse"):
        sys.modules.pop(module, None)
    import nexus.app  # noqa: F401

    assert "openai" not in sys.modules
    assert "litellm" not in sys.modules
    assert "langfuse" not in sys.modules
