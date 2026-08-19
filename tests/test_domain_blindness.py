"""Domain blindness.

Two independent defences, because they catch different failures:

  1. DEPENDENCY DIRECTION -- core imports no adapter. This is the invariant.
  2. VOCABULARY GREP      -- a cheap smoke test. Catches domain nouns leaking
                             into identifiers and docstrings, which import
                             rules never see.

The grep is not architecture. Do not mistake it for architecture.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "hanish" / "core"

# Nouns from every host we have or plan. If one of these appears in core,
# the substrate has quietly become a tool for one application.
DOMAIN_NOUNS = [
    "houdini", "usd", "solaris", "karma", "node", "cook", "render",
    "shader", "frame", "sim", "vex", "commit", "git", "github", "build",
    "test_suite", "pipeline", "workflow", "runner",
]


def _core_files():
    return [p for p in CORE.glob("*.py") if p.name != "__init__.py"]


def test_core_imports_no_adapter():
    """The invariant. Dependency flows one way: adapters import core."""
    for path in _core_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert "adapter" not in name, f"{path.name} imports {name}"


def test_core_has_no_third_party_dependencies():
    """The core depends on the standard library, its own types, and nothing
    else. A substrate with a dependency tree is a substrate that rots."""
    stdlib_ok = {
        "json", "os", "uuid", "hashlib", "dataclasses", "datetime", "enum",
        "typing", "pathlib", "__future__", "collections", "itertools",
    }
    for path in _core_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] in stdlib_ok, f"{path.name}: {a.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    root = node.module.split(".")[0]
                    assert root in stdlib_ok, f"{path.name}: {node.module}"


def _executable_source(path: Path) -> str:
    """Source with comments and docstrings removed.

    Prose that MENTIONS a domain noun is harmless -- this very test file is
    full of them. An identifier or a runtime string literal containing one is
    a leak. Only the second kind is grepped.
    """
    src = path.read_text()
    tree = ast.parse(src)

    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                target = node.body[0]
                for ln in range(target.lineno, (target.end_lineno or target.lineno) + 1):
                    docstring_lines.add(ln)

    kept = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in docstring_lines:
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept).lower()


def test_core_contains_no_domain_vocabulary():
    """Smoke test over executable source only. Cheap, and it catches what
    import rules cannot: a domain noun baked into an identifier or a value."""
    offenders = []
    for path in _core_files():
        text = _executable_source(path)
        for noun in DOMAIN_NOUNS:
            if re.search(rf"\b{re.escape(noun)}\b", text):
                offenders.append(f"{path.name}: {noun!r}")
    assert not offenders, "domain vocabulary in core: " + "; ".join(offenders)


def test_the_vocabulary_grep_actually_catches_a_leak(tmp_path):
    """A test that never fails is not a test. Prove the grep bites."""
    leaky = tmp_path / "leaky.py"
    leaky.write_text('"""A docstring mentioning commit is fine."""\n'
                     'def cook_duration():\n'
                     '    return 1\n')
    text = _executable_source(leaky)
    assert "commit" not in text          # docstring stripped
    assert "cook" in text                # identifier caught
