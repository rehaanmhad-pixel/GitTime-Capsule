"""Reads Git history from a local repository.

Safety notes:
- We only run `git` with a fixed list of arguments (never through a shell).
- We never execute any code from the analyzed repository.
- The path must be an existing folder that contains a .git entry.
"""

import math
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Hotspot score weights (must add up to 1.0)
W_FREQUENCY = 0.40
W_CHURN = 0.35
W_RECENT = 0.25
RECENT_DAYS = 90


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


def parse_history(output):
    """Turn `git log --numstat` output into a list of commit dicts.

    Header lines start with the \\x1e character and have fields separated by
    \\x1f. Every other non-empty line is "added<TAB>deleted<TAB>path".
    NOTE: we split on "\\n" only. str.splitlines() would also split on \\x1e.
    """
    commits = []
    current = None
    for line in output.split("\n"):
        if line.startswith("\x1e"):
            parts = line[1:].split("\x1f", 3)
            if len(parts) < 4:
                current = None
                continue
            try:
                ts = int(parts[2])
            except ValueError:
                current = None
                continue
            current = {"author": parts[1], "ts": ts, "subject": parts[3], "files": []}
            commits.append(current)
        elif line.strip() and current is not None:
            pieces = line.split("\t", 2)
            if len(pieces) != 3:
                continue
            added_s, deleted_s, path = pieces
            if added_s == "-" or deleted_s == "-":
                added = deleted = 0  # binary file: git gives no line counts
            else:
                try:
                    added, deleted = int(added_s), int(deleted_s)
                except ValueError:
                    continue
            current["files"].append((path, added, deleted))
    return commits


def date_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def month_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")


def compute_hotspots(commits, current_files, latest_ts):
    cutoff = latest_ts - RECENT_DAYS * 86400
    stats = {}

    for commit in commits:
        for path, added, deleted in commit["files"]:
            if path not in current_files:
                continue  # file no longer exists at HEAD
            s = stats.setdefault(path, {
                "commits": 0, "added": 0, "deleted": 0, "recent": 0,
                "authors": Counter(),
            })
            s["commits"] += 1
            s["added"] += added
            s["deleted"] += deleted
            s["authors"][commit["author"]] += 1
            if commit["ts"] >= cutoff:
                s["recent"] += 1

    if not stats:
        return []

    max_commits = max(s["commits"] for s in stats.values())
    max_churn = max(s["added"] + s["deleted"] for s in stats.values())
    max_recent = max(s["recent"] for s in stats.values())

    results = []
    for path, s in stats.items():
        churn = s["added"] + s["deleted"]
        frequency_n = s["commits"] / max_commits
        churn_n = math.log1p(churn) / math.log1p(max_churn) if max_churn > 0 else 0.0
        recent_n = s["recent"] / max_recent if max_recent > 0 else 0.0
        score = 100 * (W_FREQUENCY * frequency_n + W_CHURN * churn_n + W_RECENT * recent_n)
        top_author, top_count = s["authors"].most_common(1)[0]
        results.append({
            "path": path,
            "score": round(score, 1),
            "commits": s["commits"],
            "added": s["added"],
            "deleted": s["deleted"],
            "churn": churn,
            "recent_commits": s["recent"],
            "authors": len(s["authors"]),
            "top_author": top_author,
            "top_author_share": round(top_count / s["commits"], 2),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def analyze(path_str):
    repo = resolve_repo(path_str)

    history = run_git(repo, [
        "log", "--no-renames", "--numstat",
        "--pretty=format:%x1e%H%x1f%an%x1f%at%x1f%s",
    ])
    commits = parse_history(history)
    if not commits:
        raise RepoError("This repository has no commits yet.")

    tracked = run_git(repo, ["ls-files", "-z"])
    current_files = set(p for p in tracked.split("\0") if p)

    latest_ts = max(c["ts"] for c in commits)
    first_ts = min(c["ts"] for c in commits)

    contributors = {}
    timeline = {}
    total_added = total_deleted = 0

    for c in commits:
        added = sum(f[1] for f in c["files"])
        deleted = sum(f[2] for f in c["files"])
        total_added += added
        total_deleted += deleted

        person = contributors.setdefault(c["author"], {"commits": 0, "added": 0, "deleted": 0})
        person["commits"] += 1
        person["added"] += added
        person["deleted"] += deleted

        bucket = timeline.setdefault(month_str(c["ts"]), {"commits": 0, "added": 0, "deleted": 0})
        bucket["commits"] += 1
        bucket["added"] += added
        bucket["deleted"] += deleted

    top_contributors = sorted(contributors.items(), key=lambda kv: kv[1]["commits"], reverse=True)[:10]
    hotspots = compute_hotspots(commits, current_files, latest_ts)

    return {
        "overview": {
            "path": str(repo),
            "total_commits": len(commits),
            "total_contributors": len(contributors),
            "first_commit": date_str(first_ts),
            "last_commit": date_str(latest_ts),
            "files_at_head": len(current_files),
            "lines_added": total_added,
            "lines_deleted": total_deleted,
        },
        "contributors": [{"name": n, **v} for n, v in top_contributors],
        "timeline": [{"month": m, **v} for m, v in sorted(timeline.items())],
        "hotspots": hotspots[:20],
        "scoring": {
            "frequency_weight": W_FREQUENCY,
            "churn_weight": W_CHURN,
            "recent_weight": W_RECENT,
            "recent_days": RECENT_DAYS,
        },
    }