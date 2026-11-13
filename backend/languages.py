"""What languages we recognise and how we find imports between files.

Analysis levels:
  deep    - imports are extracted and resolved to files inside the repo
  metrics - language recognised, line metrics only (no dependency links yet)
  basic   - other text file: line metrics + Git history
  binary  - not a text file: Git history only

A dependency link is only reported when the imported file actually exists in
the repository. Anything we cannot resolve for certain is left out.
"""

import ast
import posixpath
import re
import warnings

LANGUAGE_BY_EXT = {
    ".py": "Python",
    ".java": "Java",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".c": "C", ".h": "C/C++ header",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
}

BASIC_TYPES = {
    ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML",
    ".cfg": "Config", ".ini": "Config",
    ".html": "HTML", ".css": "CSS", ".sh": "Shell",
}

DEEP_LANGUAGES = {
    "Python", "Java", "JavaScript", "TypeScript",
    "C", "C++", "C/C++ header", "Rust",
}
METRICS_LANGUAGES = {"C#", "Go"}


def language_level(language):
    if language in DEEP_LANGUAGES:
        return "deep"
    if language in METRICS_LANGUAGES:
        return "metrics"
    if language == "Binary":
        return "binary"
    return "basic"


def classify(path):
    """Return (language, level) based on the file extension."""
    ext = posixpath.splitext(path)[1].lower()
    language = LANGUAGE_BY_EXT.get(ext) or BASIC_TYPES.get(ext) or "Other"
    return language, language_level(language)


class PathIndex:
    """Lets us ask: does this file exist in the repo? Is a path suffix unique?"""

    def __init__(self, paths):
        self.paths = set(paths)
        self._suffixes = {}
        for p in self.paths:
            parts = p.split("/")
            for i in range(len(parts)):
                self._suffixes.setdefault("/".join(parts[i:]), []).append(p)

    def exists(self, path):
        return path in self.paths

    def unique_suffix(self, suffix):
        matches = self._suffixes.get(suffix, [])
        return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------- Python

PY_ROOTS = ("", "src", "lib")  # where top-level packages are searched for


def _py_resolve(base, dotted, index):
    stem = posixpath.join(base, *dotted.split("."))
    for candidate in (stem + ".py", stem + "/__init__.py"):
        if index.exists(candidate):
            return candidate
    return None


def _py_absolute(module, names, index, found):
    for root in PY_ROOTS:
        hit = _py_resolve(root, module, index)
        if hit:
            found.add(hit)
            for name in names:
                sub = _py_resolve(root, module + "." + name, index)
                if sub:
                    found.add(sub)
            return


def python_links(path, text, index):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(text)  # parses only; never runs the code
        except Exception:
            return set()
    found = set()
    here = posixpath.dirname(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _py_absolute(alias.name, [], index, found)
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names if a.name != "*"]
            if node.level == 0:
                if node.module:
                    _py_absolute(node.module, names, index, found)
            else:
                base = here
                for _ in range(node.level - 1):
                    base = posixpath.dirname(base)
                if node.module:
                    hit = _py_resolve(base, node.module, index)
                    if hit:
                        found.add(hit)
                    for name in names:
                        sub = _py_resolve(base, node.module + "." + name, index)
                        if sub:
                            found.add(sub)
                else:
                    for name in names:
                        sub = _py_resolve(base, name, index)
                        if sub:
                            found.add(sub)
    found.discard(path)
    return found


# ------------------------------------------------- JavaScript / TypeScript

JS_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"]([^'"\n]+)['"]"""
)
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def js_links(path, text, index):
    found = set()
    here = posixpath.dirname(path)
    for spec in JS_IMPORT_RE.findall(text):
        if not spec.startswith("."):
            continue  # package imports (react, lodash...) are external
        base = posixpath.normpath(posixpath.join(here, spec))
        if base.startswith(".."):
            continue
        candidates = [base]
        candidates += [base + e for e in JS_EXTS]
        candidates += [base + "/index" + e for e in JS_EXTS]
        root, ext = posixpath.splitext(base)
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            candidates += [root + ".ts", root + ".tsx"]  # TS imports written as .js
        for c in candidates:
            if index.exists(c):
                found.add(c)
                break
    found.discard(path)
    return found


# ------------------------------------------------------------------ Java

JAVA_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?([\w.]+?)(\.\*)?\s*;", re.MULTILINE
)


def java_links(path, text, index):
    found = set()
    for name, wildcard in JAVA_IMPORT_RE.findall(text):
        if wildcard:
            continue  # "import a.b.*" does not say which file
        parts = name.split(".")
        for drop in (0, 1, 2):  # allow nested classes / static members
            current = parts[: len(parts) - drop]
            if len(current) < 2:
                break
            hit = index.unique_suffix("/".join(current) + ".java")
            if hit:
                found.add(hit)
                break
    found.discard(path)
    return found


# ------------------------------------------------------------------ C / C++

C_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"\n]+)"', re.MULTILINE)


def c_links(path, text, index):
    found = set()
    here = posixpath.dirname(path)
    for spec in C_INCLUDE_RE.findall(text):
        if spec.startswith("/"):
            continue
        near = posixpath.normpath(posixpath.join(here, spec))
        if not near.startswith("..") and index.exists(near):
            found.add(near)
            continue
        hit = index.unique_suffix(posixpath.normpath(spec))
        if hit:
            found.add(hit)
    found.discard(path)
    return found


# ------------------------------------------------------------------ Rust

RUST_MOD_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.MULTILINE
)


def rust_links(path, text, index):
    found = set()
    here = posixpath.dirname(path)
    stem = posixpath.splitext(posixpath.basename(path))[0]
    base = here if stem in ("mod", "lib", "main") else posixpath.join(here, stem)
    for name in RUST_MOD_RE.findall(text):
        for candidate in (
            posixpath.join(base, name + ".rs"),
            posixpath.join(base, name, "mod.rs"),
        ):
            if index.exists(candidate):
                found.add(candidate)
                break
    found.discard(path)
    return found


LINK_EXTRACTORS = {
    "Python": python_links,
    "JavaScript": js_links,
    "TypeScript": js_links,
    "Java": java_links,
    "C": c_links,
    "C++": c_links,
    "C/C++ header": c_links,
    "Rust": rust_links,
}