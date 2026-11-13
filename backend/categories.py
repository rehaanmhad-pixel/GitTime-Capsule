"""Sorts files into broad groups so the dashboard can filter them.

Groups: source, tests, docs, config, other.
These are guesses from the file name and folder only (we don't read the code),
so they can be wrong for unusual project layouts.
"""

import posixpath

TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e"}
DOC_DIRS = {"doc", "docs", "documentation"}
DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
DOC_NAMES = {
    "license", "licence", "copying", "authors", "contributors", "changelog",
    "changes", "history", "news", "readme", "notice",
}
CONFIG_EXTS = {".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".lock", ".xml", ".properties"}
CONFIG_NAMES = {"makefile", "dockerfile", "cmakelists.txt", "manifest.in", "tox.ini"}
SOURCE_LANGUAGES = {
    "Python", "Java", "JavaScript", "TypeScript", "C", "C/C++ header",
    "C++", "C#", "Go", "Rust", "HTML", "CSS", "Shell",
}


def categorize(path, language):
    if language == "Binary":
        return "other"

    parts = path.lower().split("/")
    name = parts[-1]
    stem, ext = posixpath.splitext(name)
    folders = set(parts[:-1])

    if (
        folders & TEST_DIRS
        or stem.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
        or stem == "conftest"
    ):
        return "tests"

    if (
        ext in CONFIG_EXTS
        or name in CONFIG_NAMES
        or name.startswith(".")
        or name.startswith("requirements")
        or ".github" in folders
    ):
        return "config"

    if folders & DOC_DIRS or ext in DOC_EXTS or stem in DOC_NAMES:
        return "docs"

    if language in SOURCE_LANGUAGES:
        return "source"

    return "other"