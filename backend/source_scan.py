"""Reads the files of the latest version straight from Git's object store.

We never open files from the working folder, so symlinks inside an untrusted
repository can't lead us to files outside it.
"""

from gitutil import run_git, run_git_bytes
from languages import LINK_EXTRACTORS, PathIndex, classify

MAX_FILE_BYTES = 512 * 1024   # bigger files are listed but not read
READ_CHUNK = 100
MAX_LINKS_PER_FILE = 200


def list_tracked(repo):
    """Return [(path, mode, sha)] for every file in the latest version."""
    output = run_git(repo, ["ls-files", "-s", "-z"])
    entries = []
    for record in output.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not path:
            continue
        entries.append((path, parts[0], parts[1]))
    return entries


def blob_sizes(repo, shas):
    if not shas:
        return {}
    data = run_git_bytes(repo, ["cat-file", "--batch-check"],
                         ("\n".join(shas) + "\n").encode())
    sizes = {}
    for line in data.decode("utf-8", errors="replace").split("\n"):
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            try:
                sizes[parts[0]] = int(parts[2])
            except ValueError:
                pass
    return sizes


def read_blobs(repo, shas):
    result = {}
    for i in range(0, len(shas), READ_CHUNK):
        chunk = shas[i:i + READ_CHUNK]
        data = run_git_bytes(repo, ["cat-file", "--batch"],
                             ("\n".join(chunk) + "\n").encode())
        pos = 0
        for sha in chunk:
            newline = data.find(b"\n", pos)
            if newline == -1:
                break
            header = data[pos:newline].split()
            pos = newline + 1
            if len(header) == 3 and header[1] == b"blob":
                size = int(header[2])
                result[sha] = data[pos:pos + size]
                pos += size + 1
    return result


def scan_sources(repo):
    """Return (files, edges).

    files: {path: {language, level, size, lines, code_lines, note}}
    edges: [(importing_file, imported_file)] - only links we could verify
    """
    entries = list_tracked(repo)
    index = PathIndex(p for p, _, _ in entries)

    regular = [(p, sha) for p, mode, sha in entries if mode in ("100644", "100755")]
    sizes = blob_sizes(repo, list(dict.fromkeys(sha for _, sha in regular)))
    to_read = list(dict.fromkeys(
        sha for _, sha in regular if sizes.get(sha, MAX_FILE_BYTES + 1) <= MAX_FILE_BYTES
    ))
    blobs = read_blobs(repo, to_read)

    files = {}
    texts = {}
    for path, mode, sha in entries:
        language, level = classify(path)
        info = {"path": path, "language": language, "level": level,
                "size": 0, "lines": None, "code_lines": None, "note": ""}

        if mode == "120000":
            info.update(language="Symlink", level="basic", note="symlink (not followed)")
        elif mode == "160000":
            info.update(language="Submodule", level="basic", note="submodule (not scanned)")
        else:
            size = sizes.get(sha, 0)
            info["size"] = size
            data = blobs.get(sha)
            if data is None:
                info["level"] = "basic"
                info["note"] = "too large to analyze" if size > MAX_FILE_BYTES else "could not be read"
            elif b"\0" in data[:8000]:
                info.update(language="Binary", level="binary")
            else:
                text = data.decode("utf-8", errors="replace")
                lines = text.splitlines()
                info["lines"] = len(lines)
                info["code_lines"] = sum(1 for line in lines if line.strip())
                if level == "deep":
                    texts[path] = text
        files[path] = info

    edges = []
    for path, text in texts.items():
        extractor = LINK_EXTRACTORS.get(files[path]["language"])
        if extractor is None:
            continue
        try:
            targets = sorted(extractor(path, text, index))[:MAX_LINKS_PER_FILE]
        except Exception:
            continue  # one odd file must not break the whole analysis
        for target in targets:
            edges.append((path, target))

    return files, edges