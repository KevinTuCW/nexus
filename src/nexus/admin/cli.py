"""Account management from the shell. `python -m nexus.admin.cli`.

Accounts are created here rather than from an environment variable, because
a bootstrap password in `.env` is a plaintext credential in a file that gets
copied, shared and occasionally committed. `getpass` keeps it out of the
shell history too.

There is no web endpoint for creating the first account. A control plane
that will mint its own first administrator over HTTP has a window, however
short, in which anyone can be that administrator.
"""

import argparse
import getpass
import sys

from nexus.admin.accounts import AccountStore
from nexus.config import get_settings


def _store() -> AccountStore:
    dsn = get_settings().database_url
    if not dsn:
        sys.exit(
            "DATABASE_URL is empty. Control-plane accounts live in Postgres; "
            "there is nowhere to put one.\n"
            "Try: set -a; . ./.env; set +a"
        )
    return AccountStore(dsn)


def main() -> int:
    parser = argparse.ArgumentParser(prog="nexus.admin.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="create an administrator")
    add.add_argument("username")
    add.add_argument("--role", choices=["rw", "ro"], default="rw")

    passwd = sub.add_parser("passwd", help="change a password")
    passwd.add_argument("username")

    disable = sub.add_parser("disable", help="disable an account and end its sessions")
    disable.add_argument("username")

    sub.add_parser("list", help="list accounts (never shows a hash)")

    args = parser.parse_args()
    store = _store()

    if args.cmd == "list":
        rows = store.list_accounts()
        if not rows:
            print("no accounts. create one with: make admin-add USER=<name>")
            return 0
        for r in rows:
            last = r["last_login_at"].strftime("%Y-%m-%d %H:%M") if r["last_login_at"] else "never"
            print(f"{r['username']:<16} {r['role']:<3} {r['state']:<9} last login: {last}")
        return 0

    if args.cmd in {"add", "passwd"}:
        password = getpass.getpass("password: ")
        if password != getpass.getpass("repeat: "):
            sys.exit("passwords do not match")
        try:
            if args.cmd == "add":
                store.create(args.username, password, args.role)
                print(f"created '{args.username}' ({args.role})")
            else:
                store.set_password(args.username, password)
                print(f"password changed for '{args.username}'")
        except ValueError as exc:
            sys.exit(str(exc))
        except Exception as exc:  # duplicate username, missing account, ...
            sys.exit(f"failed: {exc}")
        return 0

    if args.cmd == "disable":
        store.disable(args.username)
        print(f"disabled '{args.username}' and ended its sessions")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
