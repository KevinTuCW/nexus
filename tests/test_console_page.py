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
