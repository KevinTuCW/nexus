"""The zero-touch assertion.

The four incumbent tenants are integrated without modifying their repos.
That is not a nicety — it is the acceptance criterion, because the usual
reason an internal platform never gets adopted is "port your service first".
So it has to be checkable: every tenant checkout must have an empty
`git status --porcelain` before and after a conformance run.

The subtle part is UNVERIFIABLE. A checkout that is missing, or is not a git
repo, must never read as CLEAN. If it did, the zero-touch claim would go
green on a machine where it was never actually checked — the exact shape of
a gate that passes by not running.
"""

import subprocess
from enum import Enum
from pathlib import Path


class RepoStatus(str, Enum):
    CLEAN = "clean"
    DIRTY = "dirty"
    UNVERIFIABLE = "unverifiable"


def working_tree_status(repo_path: Path) -> RepoStatus:
    """CLEAN only when the checkout exists, is a git repo, and is untouched.

    Untracked files count as DIRTY: dropping an adapter file into a tenant
    repo is still touching it, even though `git diff` stays empty.
    """
    if not repo_path.is_dir():
        return RepoStatus.UNVERIFIABLE
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return RepoStatus.UNVERIFIABLE
    return RepoStatus.CLEAN if not result.stdout.strip() else RepoStatus.DIRTY
