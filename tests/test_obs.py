import pytest

from nexus.obs import SAFE_ATTRS, span, tracing_enabled


def test_tracing_is_off_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing_enabled() is False


def test_span_is_a_no_op_when_tracing_is_off(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with span("route", tenant="shopscout") as s:
        assert s is None


def test_span_never_breaks_the_request_when_tracing_fails(monkeypatch):
    # Observability is not allowed to take the gateway down. A tracing
    # backend that is slow, unreachable or misconfigured must cost a trace,
    # never an answer.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    def _boom(*a, **kw):
        raise RuntimeError("langfuse exploded")

    monkeypatch.setattr("nexus.obs._start_span", _boom)
    with span("route", tenant="shopscout") as s:
        assert s is None  # degraded, but the block still runs


def test_the_safe_list_does_not_name_message_content():
    # Checks the list.
    assert "messages" not in SAFE_ATTRS
    assert "content" not in SAFE_ATTRS


def test_attributes_outside_the_safe_list_are_dropped(monkeypatch):
    # Checks the filter. The two fail independently, and it is the filter
    # that stands between a prompt and a third-party system -- a correct
    # list with no filtering behind it protects nothing.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    sent = {}

    def _capture(name, attrs):
        sent.update(attrs)

        class _H:
            @staticmethod
            def end():
                pass

        return _H()

    monkeypatch.setattr("nexus.obs._start_span", _capture)
    with span("route", tenant="shopscout", messages=[{"content": "secret"}]):
        pass
    assert sent == {"tenant": "shopscout"}


def test_a_failing_end_does_not_break_the_request(monkeypatch):
    # The span is closed in a `finally`; if flushing the trace throws, the
    # request has already succeeded and must not be retroactively failed.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    class _H:
        @staticmethod
        def end():
            raise RuntimeError("flush failed")

    monkeypatch.setattr("nexus.obs._start_span", lambda name, attrs: _H())
    with span("route", tenant="shopscout"):
        pass  # must not raise
