"""Analyzes a local Git repository: history, hotspots, languages, dependencies.

Safety notes:
- We only run `git` with a fixed list of arguments (never through a shell).
- We never execute any code from the analyzed repository.
- File contents come from Git's object store, not the working folder.
"""

import math
from collections import Counter
from datetime import datetime, timezone

from gitutil import RepoError, resolve_repo, run_git
from languages import language_level
from source_scan import scan_sources

# Hotspot score weights (must add up to 1.0)
W_FREQUENCY = 0.35
W_CHURN = 0.30
W_RECENT = 0.20
W_SIZE = 0.15
RECENT_DAYS = 90


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


def compute_hotspots(commits, files, latest_ts, imports_out, imported_by):
    cutoff = latest_ts - RECENT_DAYS * 86400
    stats = {}

    for commit in commits:
        for path, added, deleted in commit["files"]:
            if path not in files:
                continue  # file no longer exists at the latest version
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
    max_loc = max((files[p]["code_lines"] or 0) for p in stats)

    results = []
    for path, s in stats.items():
        info = files[path]
        churn = s["added"] + s["deleted"]
        loc = info["code_lines"] or 0

        frequency_n = s["commits"] / max_commits
        churn_n = math.log1p(churn) / math.log1p(max_churn) if max_churn > 0 else 0.0
        recent_n = s["recent"] / max_recent if max_recent > 0 else 0.0
        size_n = math.log1p(loc) / math.log1p(max_loc) if max_loc > 0 else 0.0

        score = 100 * (W_FREQUENCY * frequency_n + W_CHURN * churn_n
                       + W_RECENT * recent_n + W_SIZE * size_n)
        top_author, top_count = s["authors"].most_common(1)[0]
        results.append({
            "path": path,
            "score": round(score, 1),
            "language": info["language"],
            "level": info["level"],
            "code_lines": info["code_lines"],
            "commits": s["commits"],
            "added": s["added"],
            "deleted": s["deleted"],
            "churn": churn,
            "recent_commits": s["recent"],
            "authors": len(s["authors"]),
            "top_author": top_author,
            "top_author_share": round(top_count / s["commits"], 2),
            "imports": imports_out.get(path, 0),
            "imported_by": imported_by.get(path, 0),
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

    files, edges = scan_sources(repo)
    imports_out = Counter(src for src, _ in edges)
    imported_by = Counter(dst for _, dst in edges)

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
    hotspots = compute_hotspots(commits, files, latest_ts, imports_out, imported_by)

    languages = {}
    for info in files.values():
        entry = languages.setdefault(info["language"], {
            "language": info["language"],
            "level": language_level(info["language"]),
            "files": 0,
            "code_lines": 0,
        })
        entry["files"] += 1
        entry["code_lines"] += info["code_lines"] or 0
    language_list = sorted(languages.values(), key=lambda e: (-e["code_lines"], -e["files"]))

    return {
        "overview": {
            "path": str(repo),
            "total_commits": len(commits),
            "total_contributors": len(contributors),
            "first_commit": date_str(first_ts),
            "last_commit": date_str(latest_ts),
            "files_at_head": len(files),
            "lines_added": total_added,
            "lines_deleted": total_deleted,
        },
        "contributors": [{"name": n, **v} for n, v in top_contributors],
        "timeline": [{"month": m, **v} for m, v in sorted(timeline.items())],
        "hotspots": hotspots[:20],
        "languages": language_list,
        "analysis_levels": dict(Counter(info["level"] for info in files.values())),
        "dependencies": {
            "links": len(edges),
            "files_with_links": len(set(imports_out) | set(imported_by)),
            "most_imported": [
                {"path": p, "imported_by": n} for p, n in imported_by.most_common(10)
            ],
        },
        "scoring": {
            "frequency_weight": W_FREQUENCY,
            "churn_weight": W_CHURN,
            "recent_weight": W_RECENT,
            "size_weight": W_SIZE,
            "recent_days": RECENT_DAYS,
        },
    }