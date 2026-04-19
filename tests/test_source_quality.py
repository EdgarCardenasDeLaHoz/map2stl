"""
Source-quality guards that catch encoding, syntax, and DOM-ownership bugs
before they manifest at runtime.

These tests scan the actual source files and flag problems that are hard
to detect in unit tests but easy to catch with static analysis:

1. Non-ASCII in Python log/print statements (breaks Windows cp1252 consoles)
2. Vue templates that place reactive children inside elements whose innerHTML
   is overwritten by vanilla JS (causes Vue insertBefore-on-null errors)
"""
import ast
import os
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent          # strm2stl/
_APP = _ROOT / "app"
_PY_APP = _APP / "server"
# session/ is excluded — its print() output goes to Jupyter notebooks which
# handle UTF-8 fine, not to the Windows console.
_VUE_DIR = _APP / "client" / "static" / "js" / "vue" / "components"
_JS_MODULES = _APP / "client" / "static" / "js" / "modules"
_JS_TOP = _APP / "client" / "static" / "js"

# ── helpers ────────────────────────────────────────────────────────────────


def _py_files():
    """All Python files under app/server/ (console-facing code)."""
    if _PY_APP.is_dir():
        yield from _PY_APP.rglob("*.py")


def _vue_files():
    """All .vue component files."""
    if _VUE_DIR.is_dir():
        yield from _VUE_DIR.rglob("*.vue")


def _js_module_files():
    """All vanilla JS module files (not Vue, not workers)."""
    for f in _JS_TOP.glob("*.js"):
        yield f
    if _JS_MODULES.is_dir():
        yield from _JS_MODULES.rglob("*.js")


# ---------------------------------------------------------------------------
# 1. Non-ASCII characters in Python log / print statements
# ---------------------------------------------------------------------------
# On Windows the default console encoding is cp1252 which cannot represent
# Unicode arrows, em-dashes, etc.  Log and print output must be plain ASCII.

_NON_ASCII_RE = re.compile(r"[^\x00-\x7E]")


class _LogCallVisitor(ast.NodeVisitor):
    """Walk the AST and collect string literals inside logging / print calls."""

    _LOG_NAMES = {"info", "warning", "error", "debug", "critical", "exception"}

    def __init__(self):
        self.issues: list[tuple[int, str, str]] = []  # (line, char, context)

    def _check_node(self, node: ast.AST, context: str) -> None:
        """Recursively check every string literal inside *node*."""
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                m = _NON_ASCII_RE.search(child.value)
                if m:
                    self.issues.append(
                        (child.lineno, m.group(), context)
                    )
            # f-string values are JoinedStr → walk into them
            if isinstance(child, ast.JoinedStr):
                for v in child.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        m = _NON_ASCII_RE.search(v.value)
                        if m:
                            self.issues.append(
                                (child.lineno, m.group(), context)
                            )

    def visit_Call(self, node: ast.Call):  # noqa: N802 – ast convention
        # logger.info(...), log.warning(...), print(...)
        func = node.func
        is_log = (
            isinstance(func, ast.Attribute)
            and func.attr in self._LOG_NAMES
        )
        is_print = isinstance(func, ast.Name) and func.id == "print"
        if is_log or is_print:
            ctx = func.attr if isinstance(func, ast.Attribute) else "print"
            for arg in node.args:
                self._check_node(arg, ctx)
            for kw in node.keywords:
                self._check_node(kw.value, ctx)
        self.generic_visit(node)


def _scan_py_file_for_log_encoding(path: Path) -> list[str]:
    """Return human-readable issue strings for non-ASCII in log/print calls."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return [f"  Could not parse {path}"]

    visitor = _LogCallVisitor()
    visitor.visit(tree)
    rel = path.relative_to(_ROOT)
    return [
        f"  {rel}:{line} non-ASCII {char!r} in {ctx}() call"
        for line, char, ctx in visitor.issues
    ]


@pytest.mark.parametrize("py_file", list(_py_files()), ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_non_ascii_in_log_statements(py_file):
    """Log/print strings must be pure ASCII (Windows cp1252 safety)."""
    issues = _scan_py_file_for_log_encoding(py_file)
    assert not issues, (
        "Non-ASCII characters in log/print calls will crash on Windows "
        "cp1252 consoles:\n" + "\n".join(issues)
    )


# ---------------------------------------------------------------------------
# 2. Vue/JS DOM ownership conflicts
# ---------------------------------------------------------------------------
# When a Vue template places reactive children (v-if, v-for, v-show) inside
# an element whose `id` is overwritten via `.innerHTML` in vanilla JS, Vue's
# virtual-DOM references go stale → TypeError: Cannot read properties of null
# (reading 'insertBefore').
#
# This test detects that pattern statically.

# Reactive directive patterns that make Vue "own" child DOM nodes
_VUE_DIRECTIVE_RE = re.compile(
    r"""\bv-(?:if|else-if|else|for|show)\b"""
)

# Extracts id="..." from an HTML open tag
_ID_RE = re.compile(r"""\bid=["']([^"']+)["']""")

# Matches `.innerHTML` assignment patterns in JS:  getElementById('foo').innerHTML  or  el.innerHTML
_INNERHTML_TARGET_RE = re.compile(
    r"""getElementById\(\s*['"](\w+)['"]\s*\)[\s\S]{0,20}\.innerHTML"""
)


def _extract_template(vue_source: str) -> str:
    """Return the <template> block from a Vue SFC."""
    m = re.search(r"<template\b[^>]*>(.*?)</template>", vue_source, re.DOTALL)
    return m.group(1) if m else ""


def _find_reactive_children_in_id_elements(template: str) -> dict[str, list[int]]:
    """
    Return {element_id: [line_numbers]} for elements with an `id=` whose
    *children* contain a Vue reactive directive.
    """
    conflicts: dict[str, list[int]] = {}
    lines = template.split("\n")

    # Simple depth tracker: find opening tags with id=, track nesting,
    # flag if any child line contains a reactive directive.
    current_id: str | None = None
    depth = 0

    for lineno_0, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue

        if current_id is not None:
            # Inside a tracked id element
            if _VUE_DIRECTIVE_RE.search(stripped):
                conflicts.setdefault(current_id, []).append(lineno_0)

            # Track nesting: count opening and self-closing tags
            opens = len(re.findall(r"<\w+", stripped))
            closes = len(re.findall(r"</\w+", stripped))
            self_closes = len(re.findall(r"/>", stripped))
            depth += opens - closes - self_closes
            if depth <= 0:
                current_id = None
                depth = 0
        else:
            # Look for an element with an id= attribute
            id_match = _ID_RE.search(stripped)
            if id_match:
                current_id = id_match.group(1)
                depth = 1
                # If the tag self-closes on this line, don't track it
                if "/>" in stripped:
                    current_id = None
                    depth = 0

    return conflicts


def _collect_innerhtml_targets() -> set[str]:
    """Scan vanilla JS modules for element IDs used with .innerHTML."""
    targets: set[str] = set()
    for js_file in _js_module_files():
        try:
            source = js_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _INNERHTML_TARGET_RE.finditer(source):
            targets.add(m.group(1))
    return targets


# Build the innerHTML target set once at collection time
_INNERHTML_TARGETS = _collect_innerhtml_targets()


def _check_vue_file_for_dom_conflicts(path: Path) -> list[str]:
    """Return issue strings for Vue components with reactive children
    inside innerHTML-targeted elements."""
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [f"  Could not read {path}"]

    template = _extract_template(source)
    if not template:
        return []

    conflicts = _find_reactive_children_in_id_elements(template)
    issues = []
    rel = path.relative_to(_ROOT)
    for eid, line_numbers in conflicts.items():
        if eid in _INNERHTML_TARGETS:
            lines_str = ", ".join(str(ln) for ln in line_numbers)
            issues.append(
                f"  {rel}: #{eid} has Vue reactive children (lines {lines_str}) "
                f"but vanilla JS overwrites its innerHTML"
            )
    return issues


@pytest.mark.parametrize(
    "vue_file",
    list(_vue_files()),
    ids=lambda p: str(p.relative_to(_ROOT)),
)
def test_no_vue_reactive_children_in_innerhtml_targets(vue_file):
    """Vue must not place v-if/v-for/v-show children inside elements
    whose innerHTML is overwritten by vanilla JS modules."""
    issues = _check_vue_file_for_dom_conflicts(vue_file)
    assert not issues, (
        "DOM ownership conflict: Vue reactive children inside innerHTML targets "
        "will cause insertBefore-on-null errors:\n" + "\n".join(issues)
    )
