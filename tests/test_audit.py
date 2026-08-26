from datetime import datetime

from nexus.audit import AuditRecord, InMemoryAudit


def test_a_record_names_who_read_what_and_when():
    a = InMemoryAudit()
    a.record_cross_tenant_read("wuwork", ("helpmate", "shopscout"))
    (r,) = a.records()
    assert isinstance(r, AuditRecord)
    assert r.caller == "wuwork"
    assert r.targets == ("helpmate", "shopscout")
    assert isinstance(r.ts, datetime)


def test_a_record_carries_no_amounts():
    # Audit answers "who looked at what", not "store another copy of what
    # they saw". A figure in here is the same tenant data again, sitting
    # behind whatever access control the audit table has -- usually looser
    # than the original's, because audit tables get read by more people.
    a = InMemoryAudit()
    a.record_cross_tenant_read("wuwork", ("helpmate",))
    fields = set(vars(a.records()[0]))
    assert "cost_nanousd" not in fields
    assert "amount" not in fields


def test_a_denied_read_is_recorded_too():
    # The attempts that were refused are the ones an investigation starts
    # from. Recording only successes produces an audit trail in which
    # nothing was ever refused.
    a = InMemoryAudit()
    a.record_cross_tenant_denial("shopscout", ("wealthwise",))
    (r,) = a.records()
    assert r.caller == "shopscout"
    assert r.denied is True


def test_records_are_returned_in_order():
    a = InMemoryAudit()
    a.record_cross_tenant_read("wuwork", ("helpmate",))
    a.record_cross_tenant_read("wuwork", ("aura",))
    assert [r.targets for r in a.records()] == [("helpmate",), ("aura",)]
