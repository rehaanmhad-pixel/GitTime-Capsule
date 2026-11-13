"""Small, safe helpers for running Git against a local repository."""

import os
import subprocess
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


def _run(repo, args, stdin_bytes=None):
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
            input=stdin_bytes,
            capture_output=True,
            timeout=120,
            env=env,
        )
    except FileNotFoundError:
        raise RepoError("Git is not installed or not on your PATH.")
    except subprocess.TimeoutExpired:
        raise RepoError("Git took too long to respond.")
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RepoError(f"Git error: {message or 'unknown error'}")
    return result.stdout


def run_git(repo, args):
    """Run git and return its output as text."""
    return _run(repo, args).decode("utf-8", errors="replace")


def run_git_bytes(repo, args, stdin_bytes=None):
    """Run git and return raw bytes (used for reading file contents)."""
    return _run(repo, args, stdin_bytes)