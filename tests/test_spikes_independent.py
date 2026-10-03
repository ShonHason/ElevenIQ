"""
Spikes must never import eleveniq (plan 005 M4b2).

The verification spikes re-derive reference figures from raw StatsBomb JSON so
that the pipeline is checked against something it did not produce; a spike that
imports eleveniq checks the code against itself. The measurement and
before/after spikes follow the same rule.

The check parses every spikes/*.py with ast, so comments and strings that merely
mention eleveniq do not count, but an import at any depth (inside a function too)
or a dynamic import of an "eleveniq..." module name does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SPIKES = Path(__file__).resolve().parents[1] / "spikes"
_PACKAGE = "eleveniq"


def _is_package(name: str | None) -> bool:
    return name is not None and (name == _PACKAGE or name.startswith(_PACKAGE + "."))


def eleveniq_imports(source: str) -> list[int]:
    """Line numbers of every eleveniq import in `source`."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(_is_package(a.name) for a in node.names):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _is_package(node.module):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if (name in ("import_module", "__import__") and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and _is_package(node.args[0].value)):
                lines.append(node.lineno)
    return sorted(lines)


_SPIKE_FILES = sorted(_SPIKES.glob("*.py"))


def test_there_are_spikes_to_check():
    assert len(_SPIKE_FILES) >= 5


@pytest.mark.parametrize("path", _SPIKE_FILES, ids=[p.name for p in _SPIKE_FILES])
def test_spike_does_not_import_eleveniq(path):
    lines = eleveniq_imports(path.read_text())
    assert not lines, f"{path.name} imports eleveniq at line(s) {lines}"


class TestDetector:
    """The check must be able to fail: each planted import is found."""

    @pytest.mark.parametrize("source", [
        "import eleveniq\n",
        "import eleveniq.metrics.positions as p\n",
        "from eleveniq.config import MIN_MINUTES_THRESHOLD\n",
        "def f():\n    from eleveniq.pipeline.build_player_match import _process_match\n",
        "import importlib\nm = importlib.import_module('eleveniq.metrics')\n",
        "m = __import__('eleveniq')\n",
    ])
    def test_planted_import_is_found(self, source):
        assert eleveniq_imports(source)

    @pytest.mark.parametrize("source", [
        "# never imports eleveniq\n",
        "'''Reads raw JSON; no eleveniq imports.'''\n",
        "import eleveniq_tools\n",
        "from . import eleveniq\n",
    ])
    def test_mentions_and_other_packages_are_not_imports(self, source):
        assert not eleveniq_imports(source)
