import os

import pytest

from nexus.ingress.auth import authenticate, build_key_index, key_digest
from nexus.registry.tenants import load_policies

DSN = os.environ.get("DATABASE_URL", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not DSN, reason="no DATABASE_URL in the shell; tenant_key store skipped"
    ),
]

#: Its own table, created `LIKE tenant_key INCLUDING ALL` rather than from a
#: second copy of the DDL. Derived from the real table it cannot drift from
#: it -- including the UNIQUE index on key_sha256, which one of these tests
#: is specifically about. And revoking rows in a test must never reach the
#: table the running gateway authenticates against.
TEST_TABLE = "tenant_key_pgtest"


@pytest.fixture
def store():
    import psycopg

    from nexus.admin.store import TenantKeyStore

    with psycopg.connect(DSN) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
        conn.execute(
            f"CREATE TABLE {TEST_TABLE} (LIKE tenant_key INCLUDING ALL)"
        )
    yield TenantKeyStore(DSN, table=TEST_TABLE)
    with psycopg.connect(DSN) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")


def test_an_issued_key_authenticates(store, policies_dir):
    _, plaintext = store.issue("wuwork", "test", "kevin")
    index = build_key_index(load_policies(policies_dir), store.active_digests())
    assert authenticate(f"Bearer {plaintext}", index) == "wuwork"


def test_the_table_never_holds_the_plaintext(store):
    import psycopg

    _, plaintext = store.issue("wuwork", "test", "kevin")
    with psycopg.connect(DSN) as conn:
        row = conn.execute(f"SELECT * FROM {TEST_TABLE}").fetchone()
    assert plaintext not in str(row)
    assert key_digest(plaintext) in str(row)


def test_a_revoked_key_stops_authenticating_but_its_row_survives(store, policies_dir):
    import psycopg

    key_id, plaintext = store.issue("wuwork", "test", "kevin")
    store.revoke(key_id, "kevin")

    index = build_key_index(load_policies(policies_dir), store.active_digests())
    from nexus.ingress.auth import AuthError

    with pytest.raises(AuthError):
        authenticate(f"Bearer {plaintext}", index)

    # The row stays: deleting it would delete the answer to "who made that
    # call in March" while the ledger still holds what it cost.
    with psycopg.connect(DSN) as conn:
        (n,) = conn.execute(
            f"SELECT count(*) FROM {TEST_TABLE} WHERE key_id = %s", (key_id,)
        ).fetchone()
    assert n == 1


def test_rotation_has_no_gap(store, policies_dir):
    # The whole point of allowing a tenant more than one live key: issue the
    # new one, let the tenant cut over, then revoke the old one. At no point
    # is the tenant unable to authenticate.
    policies = load_policies(policies_dir)
    old_id, old = store.issue("wuwork", "old", "kevin")

    _, new = store.issue("wuwork", "new", "kevin")
    index = build_key_index(policies, store.active_digests())
    assert authenticate(f"Bearer {old}", index) == "wuwork"
    assert authenticate(f"Bearer {new}", index) == "wuwork"

    store.revoke(old_id, "kevin")
    index = build_key_index(policies, store.active_digests())
    from nexus.ingress.auth import AuthError

    with pytest.raises(AuthError):
        authenticate(f"Bearer {old}", index)
    assert authenticate(f"Bearer {new}", index) == "wuwork"


def test_the_console_listing_exposes_neither_key_nor_digest(store):
    _, plaintext = store.issue("wuwork", "prod", "kevin")
    rows = store.list_for_console("wuwork")
    assert len(rows) == 1
    assert rows[0]["key_prefix"] == plaintext[:8]
    assert rows[0]["state"] == "active"
    blob = str(rows[0])
    assert plaintext not in blob
    # The digest is not the key, but it is a verifier for one, and guessing
    # offline against it is cheaper than against an endpoint that logs.
    assert key_digest(plaintext) not in blob
