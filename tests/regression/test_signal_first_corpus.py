"""T0.1: the committed corpus is exactly what its builder writes, and stays fictional."""

from __future__ import annotations

import sys
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "signal_first"
sys.path.insert(0, str(FIX))
import build_corpus  # noqa: E402


def test_committed_bytes_equal_the_builders_output():
    built = build_corpus.build()
    committed = {
        str(p.relative_to(build_corpus.OUT)): p.read_bytes()
        for p in build_corpus.OUT.rglob("*")
        if p.is_file()
    }
    assert sorted(built) == sorted(committed)
    assert [k for k in built if built[k] != committed[k]] == []


def test_identities_are_fictional_and_unique():
    rows = build_corpus.accounts()
    assert len({r["email"] for r in rows}) == len(rows) == build_corpus.N
    assert all(r["email"].endswith(".example") for r in rows)
    assert all(r["company_domain"].endswith(".example") for r in rows)


def test_the_builder_does_not_import_the_constants_a_golden_must_not_follow():
    import ast

    tree = ast.parse((FIX / "build_corpus.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not imported & {"RECORD_COLUMNS", "MASTER_COLS"}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in modules if m and "columns" in m}
