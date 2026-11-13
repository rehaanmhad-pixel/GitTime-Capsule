"""Reads Git history from a local repository.

Safety notes:
- We only run `git` with a fixed list of arguments (never through a shell).
- We never execute any code from the analyzed repository.
- The path must be an existing folder that contains a .git entry.
"""

import os
import subprocess
from collections import Counter
from pathlib import Path


class RepoError(Exception):
    """A problem we can show to the user (bad path, empty repo, etc.)."""


def resolve_repo(path_str):
    if not path_str or not path_str.strip():
        raise RepoError("Please enter a repository path.")
    path = Path(path_str.strip()).expanduser().resolve()
    if not path.is_dir():
        raise RepoError(f"Not a folder: {path}")
    if not (path / ".git").exists():
        raise RepoError("That folder is not the root of a Git repository (no .git inside).")
    return path


def run_git(repo, args):
    cmd = [
        "git",
        "-c", "core.fsmonitor=false",
        "-c", "core.quotepath=false",
        "-C", str(repo),
    ] + args
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
        )
    except FileNotFoundError:
        raise RepoError("Git is not installed or not on your PATH.")
    except subprocess.TimeoutExpired:
        raise RepoError("Git took too long to respond.")
    if result.returncode != 0:
        raise RepoError(f"Git error: {result.stderr.strip() or 'unknown error'}")
    return result.stdout


def analyze(path_str):
    repo = resolve_repo(path_str)

    # Output looks like:
    #   @@Alice
    #   src/a.py
    #   src/b.py
    #
    #   @@Bob
    #   README.md
    output = run_git(repo, ["log", "--no-renames", "--name-only", "--pretty=format:@@%an"])

    total_commits = 0
    authors = Counter()
    file_changes = Counter()

    for line in output.splitlines():
        if line.startswith("@@"):
            total_commits += 1
            authors[line[2:]] += 1
        elif line.strip():
            file_changes[line.strip()] += 1

    if total_commits == 0:
        raise RepoError("This repository has no commits yet.")

    return {
        "path": str(repo),
        "total_commits": total_commits,
        "total_contributors": len(authors),
        "contributors": [
            {"name": name, "commits": count} for name, count in authors.most_common(10)
        ],
        "top_files": [
            {"path": p, "changes": count} for p, count in file_changes.most_common(15)
        ],
    }