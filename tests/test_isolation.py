import subprocess

import pytest

from nexus.assurance.isolation import RepoStatus, working_tree_status


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def clean_repo(tmp_path):
    repo = tmp_path / "tenant"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_untouched_repo_reads_clean(clean_repo):
    assert working_tree_status(clean_repo) == RepoStatus.CLEAN


def test_modified_repo_reads_dirty(clean_repo):
    (clean_repo / "README.md").write_text("changed\n")
    assert working_tree_status(clean_repo) == RepoStatus.DIRTY


def test_untracked_file_also_counts_as_dirty(clean_repo):
    # Integrating by dropping an adapter file into the tenant repo is still
    # touching it, even though `git diff` stays empty.
    (clean_repo / "nexus_patch.py").write_text("# oops\n")
    assert working_tree_status(clean_repo) == RepoStatus.DIRTY


def test_missing_repo_is_unverifiable_not_clean(tmp_path):
    # The important one. A checkout that isn't there must never read as
    # "clean" -- that would turn the zero-touch claim green on a machine
    # where it was never checked at all.
    assert working_tree_status(tmp_path / "nope") == RepoStatus.UNVERIFIABLE


def test_non_git_directory_is_unverifiable_not_clean(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    # Precondition, stated rather than assumed. This test only means
    # anything if tmp_path is genuinely outside every git worktree; on a
    # runner whose temp dir sits inside a checkout, `git status` would
    # succeed and report on *that* repo, and the assertion below would go
    # green for a reason unrelated to what it claims to test. Asserting the
    # precondition makes such an environment fail loudly instead.
    enclosing = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=plain,
        capture_output=True,
        text=True,
    )
    assert enclosing.returncode != 0, (
        f"tmp_path {plain} sits inside a git worktree; this test cannot "
        "distinguish UNVERIFIABLE from a real repo status here"
    )
    assert working_tree_status(plain) == RepoStatus.UNVERIFIABLE
