from nexus.routing_log import RoutingEvent, RoutingLog


def test_an_accepted_route_is_recorded():
    log = RoutingLog(capacity=10)
    log.record("wuwork", "zai/glm-4.6", "zai/glm-4.6", vetoed=False, reason="pinned")
    (e,) = log.events()
    assert isinstance(e, RoutingEvent)
    assert e.vetoed is False


def test_a_vetoed_substitution_is_recorded_with_its_reason():
    # The whole point. A veto that leaves no trace is a gate that only
    # exists in a log nobody reads.
    log = RoutingLog(capacity=10)
    log.record("shopscout", "zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B",
               vetoed=True, reason="policy does not permit qwen3")
    (e,) = log.events()
    assert e.vetoed is True
    assert "qwen3" in e.reason


def test_the_log_is_bounded():
    # An unbounded in-memory log is a slow leak that looks like a feature
    # until the process is old enough.
    log = RoutingLog(capacity=3)
    for i in range(10):
        log.record("wuwork", f"m{i}", f"m{i}", vetoed=False, reason="")
    assert len(log.events()) == 3


def test_the_oldest_events_are_the_ones_dropped():
    log = RoutingLog(capacity=2)
    for i in range(3):
        log.record("wuwork", f"m{i}", f"m{i}", vetoed=False, reason="")
    assert [e.requested for e in log.events()] == ["m1", "m2"]


def test_vetoes_survive_eviction_pressure():
    # Vetoes are rare and are the only reason to open this panel; accepted
    # routes are constant and dull. A single ring buffer would drop exactly
    # the interesting ones and leave a tidy record of everything that went
    # right.
    log = RoutingLog(capacity=3)
    log.record("shopscout", "a", "b", vetoed=True, reason="veto")
    for i in range(10):
        log.record("wuwork", f"m{i}", f"m{i}", vetoed=False, reason="")
    assert any(e.vetoed for e in log.events())
