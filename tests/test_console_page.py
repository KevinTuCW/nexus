import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.state import get_state


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


H = {"Authorization": "Bearer sk-wuwork"}


def test_the_page_is_served(client):
    r = client.get("/console")
    assert r.status_code == 200
    assert "nexus" in r.text


def test_the_page_shows_which_upstream_is_in_use(client):
    # A console pointed at the fake upstream looks exactly like one pointed
    # at real providers. Without this banner the page is a machine for
    # producing convincing screenshots of nothing.
    assert "UPSTREAM = " in client.get("/console").text


def test_the_mode_endpoint_reports_the_configured_upstream(client):
    body = client.get("/console/mode", headers=H).json()
    assert body["upstream"] == "fake"


def test_the_mode_endpoint_requires_authentication(client):
    assert client.get("/console/mode").status_code == 401


def test_the_page_explains_what_not_covered_means(client):
    # The grey cell has to say why it is grey. "not_covered" on its own
    # reads as a minor status; the tooltip is where it says nobody checked.
    #
    # Asserts the tooltip binding as well as the words. The first version
    # checked only that the sentence appeared somewhere in the page, so
    # renaming `title=` to anything else left the explanation in the source
    # and invisible on screen -- and the test green. Found by trying exactly
    # that.
    page = client.get("/console").text
    assert "没人检查过" in page
    assert 'title="${' in page


def test_amounts_are_rendered_at_nano_precision(client):
    # toFixed(6). One token of a $0.60/1M model costs 0.0000006 USD, so
    # cents would render every honest figure as 0.00.
    assert "toFixed(6)" in client.get("/console").text


def test_the_page_states_which_tenants_it_covers(client):
    # A view silently missing a business line looks exactly like a group in
    # which that line spent nothing. The panels are now scoped to the
    # caller's authorisation, so the page has to say what the scope was --
    # otherwise scoping trades a leak for a quieter kind of wrong number.
    #
    # Asserts the binding, not just the word: the string "scope" appearing
    # somewhere in a script is satisfied by a comment. This checks that the
    # element exists and that something writes the payload's field into it.
    page = client.get("/console").text
    assert 'id="scope"' in page
    assert "d.scope.join" in page


def test_the_banner_does_not_claim_real_providers_by_default(client):
    # The banner used to read `upstream === "fake" ? warn : "real providers"`,
    # so every value it did not recognise -- including the `undefined` an
    # unauthenticated page gets -- rendered as "reaching real providers" over
    # a gateway running UPSTREAM=fake. A safety banner has to fail closed:
    # "reaching real providers" may only be reached from a known real
    # upstream, never from an else branch.
    page = client.get("/console").text
    assert "REAL.includes(m.upstream)" in page
    assert "无法确认这是真上游还是假上游" in page


def test_the_banner_reports_a_failed_mode_lookup(client):
    # An unreachable or refused /console/mode must leave the banner saying so,
    # not silently keeping whatever it last claimed.
    page = client.get("/console").text
    assert "UPSTREAM = 未知" in page


def test_panels_distinguish_no_rows_from_no_answer(client):
    # `r.json()` parses a 401 body happily, so the old one-line `get` turned
    # every refused panel into an empty table -- indistinguishable from a
    # tenant that spent nothing, which is the exact misreading the scope line
    # elsewhere on this page exists to prevent.
    page = client.get("/console").text
    assert "if (!r.ok)" in page
    assert 'fail("costs"' in page
    assert 'fail("quota"' in page


def test_the_page_says_a_key_is_required_when_none_was_given(client):
    # Opening /console with no ?key= is the overwhelmingly common way to land
    # on a blank screen. The page has to name that cause and the fix rather
    # than leaving it to be found in devtools.
    page = client.get("/console").text
    assert "/console?key=" in page
    assert "NEXUS_KEY_" in page


def test_the_scope_line_retracts_when_costs_fail(client):
    # Left reading "scope: …" above a failed panel, the line is still making
    # a claim about which tenants the screen covers.
    assert "scope: 未知" in client.get("/console").text


def test_the_page_explains_an_over_budget_tenant(client):
    # `over_budget` next to a number is a status; the tooltip is where it
    # says traffic is being refused right now. Same shape of assertion as
    # the not_covered one above, and for the same reason.
    page = client.get("/console").text
    assert "正在被 429 拒绝" in page
    assert 'r.state === "over_budget"' in page
