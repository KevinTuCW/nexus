"""Point a tenant at a running nexus and report what happened.

Not a test: it needs real credentials and a real network, and its output is
meant to be read and pasted into an integration note. What it must never do
is write anything into the tenant's checkout — the whole claim under
examination is that integration costs zero lines there, so the script
verifies that claim before and after rather than assuming it.

The tenant command is supplied on the command line because each tenant's
minimal call path differs; determining it means reading that repo's Makefile
and .env.example, which is reading, not writing.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

TENANT_REPOS = {
    name: Path.home() / "ai_projects" / name
    for name in ("helpmate", "shopscout", "wealthwise", "aura")
}


def working_trees() -> dict[str, str]:
    out = {}
    for name, path in TENANT_REPOS.items():
        if not (path / ".git").is_dir():
            out[name] = "UNVERIFIABLE"
            continue
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        out[name] = "CLEAN" if r.returncode == 0 and not r.stdout.strip() else "DIRTY"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, choices=sorted(TENANT_REPOS))
    ap.add_argument("--nexus-url", default="http://127.0.0.1:8000")
    ap.add_argument("--nexus-key", required=True)
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        help="EXTRA=VALUE injected into the tenant process; {NEXUS} is "
        "substituted with --nexus-url. Repeatable.",
    )
    ap.add_argument(
        "command", nargs=argparse.REMAINDER, help="the tenant command, after --"
    )
    args = ap.parse_args()

    before = working_trees()
    print(f"[before] {before}")

    env = dict(os.environ)
    # Every tenant here reads bare-named settings with no env_prefix, so the
    # base_url fields can be overridden from outside. That is the entire
    # integration mechanism -- no code, no adapter, no patch.
    for pair in args.env:
        key, _, value = pair.partition("=")
        env[key] = value.replace("{NEXUS}", args.nexus_url)
    # Provider keys are supplied by nexus, not by the tenant: pointing a
    # tenant at the gateway should also stop it holding provider
    # credentials. The tenant still needs *a* key to put in the header, and
    # that key is its nexus identity.
    for var in ("GLM_API_KEY", "SILICONFLOW_API_KEY", "OPENAI_API_KEY"):
        env[var] = args.nexus_key

    cmd = [c for c in args.command if c != "--"]
    print(f"[run] {cmd}")
    result = subprocess.run(cmd, cwd=TENANT_REPOS[args.tenant], env=env)
    print(f"[exit] {result.returncode}")

    after = working_trees()
    print(f"[after ] {after}")

    dirty = {k: v for k, v in after.items() if v != "CLEAN"}
    if dirty:
        # UNVERIFIABLE counts as a failure here for the same reason it does
        # in assurance/isolation.py: a check that did not run must not read
        # as a check that passed.
        print(f"ZERO-TOUCH VIOLATED OR UNVERIFIABLE: {dirty}", file=sys.stderr)
        return 2
    print("zero-touch holds: every tenant checkout unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
