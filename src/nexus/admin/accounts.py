"""Control-plane accounts and sessions: username, password, server-side session.

This replaces the shared-key scheme outright. That scheme had one flaw that
no amount of care around it could fix: a browser address bar cannot set an
`Authorization` header, so the only way a person could open the console was
`/admin?key=…` -- and a secret in a query string reaches the access log, the
`Referer` of every outbound link, and browser history. It was written down as
a known weakness rather than fixed, and the fix is this file.

Three decisions worth stating, because each has a cheaper alternative that is
worse:

**Passwords are scrypt-hashed, not SHA-256.** A fast hash is the wrong
primitive for something a human chose: SHA-256 lets an attacker with the
table try billions of candidate passwords per second. scrypt is deliberately
slow and memory-hard. It is in the standard library, so this costs no
dependency.

**Sessions are server-side rows, not signed cookies.** A signed cookie
carrying its own claims stays valid until it expires no matter what the
server learns in the meantime -- you cannot revoke it, and "log this person
out now" is a thing control planes need. The cookie here is an opaque random
token; the table decides what it means, and a `revoked_at` ends it.

**Throttling is per account.** A global limiter would let one brute-force run
against one username lock every other administrator out of the console at
precisely the moment somebody is attacking it.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

#: scrypt parameters. n is the cost; raising it slows every login for
#: everyone, which is the point, but it also slows the tests.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

#: Consecutive failures before an account is frozen, and for how long.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)

#: How long a session lives. Short enough that an abandoned browser is not a
#: standing grant, long enough not to interrupt an afternoon of work.
SESSION_LIFETIME = timedelta(hours=12)

COOKIE_NAME = "nexus_admin_session"


class LoginFailed(Exception):
    """Wrong credentials, unknown account, locked, or disabled.

    One exception for all four on purpose. Distinguishing "no such user" from
    "wrong password" hands an attacker a way to enumerate valid usernames,
    and the person legitimately locked out gets the same sentence either way.
    """


@dataclass(frozen=True)
class Admin:
    """A named administrator and what they are allowed to do."""

    name: str
    role: str

    @property
    def may_write(self) -> bool:
        return self.role == "rw"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return `(hash_hex, salt_hex)`."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt), **_SCRYPT
    )
    return digest.hex(), salt


def token_digest(token: str) -> str:
    """Session tokens are high-entropy random, so a fast hash is right here.

    Unlike a password, there is nothing to guess: brute-forcing a 256-bit
    random token is not a strategy, so scrypt would buy nothing and cost a
    hash on every single request.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccountStore:
    """Accounts and sessions. `tables` is parameterised for the tests."""

    def __init__(
        self,
        dsn: str,
        accounts_table: str = "admin_account",
        sessions_table: str = "admin_session",
    ) -> None:
        self.dsn = dsn
        self.accounts_table = accounts_table
        self.sessions_table = sessions_table

    # --- accounts --------------------------------------------------------

    def create(self, username: str, password: str, role: str = "rw") -> None:
        if role not in {"rw", "ro"}:
            raise ValueError(f"role must be 'rw' or 'ro', not {role!r}")
        if len(password) < 12:
            # Not a policy theatre rule. This account can switch a tenant off
            # and mint credentials; a password someone can guess over lunch
            # is the weakest link in everything above it.
            raise ValueError("password must be at least 12 characters")
        digest, salt = hash_password(password)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"INSERT INTO {self.accounts_table} (username, password_hash, "
                "salt, role, created_at) VALUES (%s, %s, %s, %s, %s)",
                (username, digest, salt, role, datetime.now(timezone.utc)),
            )

    def set_password(self, username: str, password: str) -> None:
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        digest, salt = hash_password(password)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE {self.accounts_table} SET password_hash = %s, salt = %s, "
                "failed_attempts = 0, locked_until = NULL WHERE username = %s",
                (digest, salt, username),
            )

    def disable(self, username: str) -> None:
        """Disable an account and end its sessions in the same breath.

        Leaving live sessions behind would make "disabled" mean "cannot log
        in again", which is not what anyone reads it as.
        """
        now = datetime.now(timezone.utc)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE {self.accounts_table} SET disabled_at = %s WHERE username = %s",
                (now, username),
            )
            conn.execute(
                f"UPDATE {self.sessions_table} SET revoked_at = %s "
                "WHERE username = %s AND revoked_at IS NULL",
                (now, username),
            )

    def list_accounts(self) -> list[dict]:
        """Never returns a hash or a salt."""
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT username, role, created_at, last_login_at, "
                f"failed_attempts, locked_until, disabled_at "
                f"FROM {self.accounts_table} ORDER BY username"
            ).fetchall()
        return [
            {
                "username": r[0], "role": r[1], "created_at": r[2],
                "last_login_at": r[3], "failed_attempts": r[4],
                "locked_until": r[5],
                "state": "disabled" if r[6] else (
                    "locked"
                    if r[5] and r[5] > datetime.now(timezone.utc)
                    else "active"
                ),
            }
            for r in rows
        ]

    def count(self) -> int:
        with psycopg.connect(self.dsn) as conn:
            (n,) = conn.execute(
                f"SELECT count(*) FROM {self.accounts_table} WHERE disabled_at IS NULL"
            ).fetchone()
        return n

    # --- login -----------------------------------------------------------

    def login(self, username: str, password: str) -> tuple[str, Admin]:
        """Verify credentials and open a session. Returns `(token, admin)`.

        The token is returned once and stored only as a digest, the same rule
        tenant credentials follow.
        """
        now = datetime.now(timezone.utc)

        # Read, decide, then write -- each in its own transaction, and the
        # raise always outside the `with`. `psycopg.connect()` as a context
        # manager rolls back when the block exits with an exception, so
        # incrementing the failure count and *then* raising inside the same
        # block silently undoes the increment: the account would never lock,
        # no matter how many attempts it took. Found by the lockout test.
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT password_hash, salt, role, locked_until, disabled_at "
                f"FROM {self.accounts_table} WHERE username = %s",
                (username,),
            ).fetchone()

        if row is None:
            # Spend the work anyway. Returning immediately for an unknown
            # username makes login timing a username oracle.
            hash_password(password)
            raise LoginFailed("用户名或密码不正确")

        digest, salt, role, locked_until, disabled_at = row
        if disabled_at is not None or (
            locked_until is not None and locked_until > now
        ):
            raise LoginFailed("用户名或密码不正确")

        candidate, _ = hash_password(password, salt)
        if not hmac.compare_digest(candidate, digest):
            with psycopg.connect(self.dsn) as conn:
                attempts = conn.execute(
                    f"UPDATE {self.accounts_table} SET failed_attempts = "
                    "failed_attempts + 1 WHERE username = %s RETURNING failed_attempts",
                    (username,),
                ).fetchone()[0]
                if attempts >= MAX_FAILED_ATTEMPTS:
                    conn.execute(
                        f"UPDATE {self.accounts_table} SET locked_until = %s, "
                        "failed_attempts = 0 WHERE username = %s",
                        (now + LOCKOUT, username),
                    )
            raise LoginFailed("用户名或密码不正确")

        token = secrets.token_urlsafe(32)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"INSERT INTO {self.sessions_table} (token_sha256, username, "
                "created_at, expires_at) VALUES (%s, %s, %s, %s)",
                (token_digest(token), username, now, now + SESSION_LIFETIME),
            )
            conn.execute(
                f"UPDATE {self.accounts_table} SET last_login_at = %s, "
                "failed_attempts = 0, locked_until = NULL WHERE username = %s",
                (now, username),
            )
        return token, Admin(name=username, role=role)

    def resolve(self, token: str) -> Admin | None:
        """The administrator a session token names, or None."""
        if not token:
            return None
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT a.username, a.role FROM {self.sessions_table} s "
                f"JOIN {self.accounts_table} a ON a.username = s.username "
                "WHERE s.token_sha256 = %s AND s.revoked_at IS NULL "
                "AND s.expires_at > %s AND a.disabled_at IS NULL",
                (token_digest(token), datetime.now(timezone.utc)),
            ).fetchone()
        return Admin(name=row[0], role=row[1]) if row else None

    def logout(self, token: str) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE {self.sessions_table} SET revoked_at = %s "
                "WHERE token_sha256 = %s AND revoked_at IS NULL",
                (datetime.now(timezone.utc), token_digest(token)),
            )
