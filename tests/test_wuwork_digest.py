from tenants.wuwork.digest import Digest, build_digest


class _FakeUsage:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.seen = None

    def get_usage(self, tenants):
        self.seen = tenants
        if self.status != 200:
            raise PermissionError(f"HTTP {self.status}")
        return self.payload


class _FakeClient:
    def __init__(self, reply="集团各业务线本日 AI 支出平稳。"):
        self.reply = reply
        self.seen = None

    def chat(self, messages, model=None):
        self.seen = messages
        return self.reply


def _payload():
    return {
        "by_tenant": {"helpmate": 6000, "shopscout": 2000},
        "currency_unit": "nanousd",
        "n_rows": 5,
    }


def test_digest_summarises_every_requested_line():
    d = build_digest(_FakeUsage(_payload()), _FakeClient(),
                     tenants=("helpmate", "shopscout"))
    assert isinstance(d, Digest)
    assert d.summary
    assert d.by_tenant == {"helpmate": 6000, "shopscout": 2000}


def test_the_figures_reach_the_model():
    # A digest that retrieves numbers and then asks the model to write about
    # nothing is a press release.
    client = _FakeClient()
    build_digest(_FakeUsage(_payload()), client, tenants=("helpmate", "shopscout"))
    joined = " ".join(m["content"] for m in client.seen)
    assert "helpmate" in joined
    # An amount only the payload could have supplied -- the tenant names are
    # also in the request, so asserting on those alone would pass even if
    # the figures never arrived.
    assert "0.000006" in joined


def test_an_unauthorised_digest_says_so_rather_than_shrinking():
    # The dangerous failure is a digest that silently covers one business
    # line and is still titled "group". Refusing is the cheaper outcome.
    d = build_digest(_FakeUsage({}, status=403), _FakeClient(),
                     tenants=("helpmate",))
    assert d.summary == ""
    assert d.refused is True
    assert d.by_tenant == {}


def test_a_refused_digest_never_calls_the_model():
    # Nothing to report means nothing to ask. Calling anyway bills the
    # tenant for an answer that must be thrown away.
    client = _FakeClient()
    build_digest(_FakeUsage({}, status=403), client, tenants=("helpmate",))
    assert client.seen is None


def test_amounts_are_not_reformatted_on_the_way_through():
    # A digest that disagrees with the ledger it came from is worse than no
    # digest: someone will reconcile the two and trust the wrong one.
    d = build_digest(_FakeUsage(_payload()), _FakeClient(),
                     tenants=("helpmate", "shopscout"))
    assert d.by_tenant["helpmate"] == 6000


def test_every_requested_tenant_is_asked_for():
    # If the digest quietly asked for fewer tenants than it was told to,
    # the 403-on-partial-authorisation rule in /v1/usage would never fire
    # and the shrinking it prevents would happen one layer up instead.
    usage = _FakeUsage(_payload())
    build_digest(usage, _FakeClient(), tenants=("helpmate", "shopscout", "aura"))
    assert usage.seen == ("helpmate", "shopscout", "aura")
