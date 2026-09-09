"""The typed-count gate, plus a negative control for it.

A lint test that only ever asserts "the tree is clean" is indistinguishable from a lint that
matches nothing — the failure this repo has already had twice (an inert V11 check, a linter
regex that matched zero packs). So the suite proves the scanner FIRES as well as passing.
"""

from __future__ import annotations

import textwrap

import rendered_prose_check as rp


def test_the_rendered_prose_is_free_of_typed_counts():
    bad = [f for f in rp.findings() if f"{f[0]}:{f[1]}" not in rp.load_allowlist()]
    assert not bad, "typed counts in rendered prose:\n" + "\n".join(
        f"  {rel}:{line}: {num!r} — …{ctx}…" for rel, line, num, ctx in bad
    )


def test_every_allowlist_entry_carries_a_dated_reason():
    """An entry with no reason is the defect wearing the gate's own clothes."""
    for key, reason in rp.load_allowlist().items():
        assert reason, f"{key}: allowlisted with no reason"
        assert reason[:10].count("-") == 2, f"{key}: reason must start with a YYYY-MM-DD date"


def _scan(src: str, tmp_path, monkeypatch):
    pkg = tmp_path / "gtm_core" / "email_campaign_dashboard"
    pkg.mkdir(parents=True)
    (pkg / "views_x.py").write_text(textwrap.dedent(src), encoding="utf-8")
    return rp.findings(tmp_path)


def test_it_fires_on_a_count_typed_into_a_sentence(tmp_path, monkeypatch):
    hits = _scan(
        """
        def v(m):
            return "<p>7 of the 18 recipients hold a seat the matrix has no row for</p>"
    """,
        tmp_path,
        monkeypatch,
    )
    assert {h[2] for h in hits} == {"7", "18"}


def test_it_stays_quiet_on_a_derived_count(tmp_path, monkeypatch):
    assert not _scan(
        """
        def v(m):
            return f"<p>{a} of the {b} recipients hold a seat the matrix has no row for</p>"
    """,
        tmp_path,
        monkeypatch,
    )


def test_it_stays_quiet_on_history_rationale_and_labels(tmp_path, monkeypatch):
    """A docstring records the past; `rule 9` and `2026-09-04` name things rather than count."""
    assert not _scan(
        '''
        def v(m):
            """On 2026-09-04 the tiles read 0 of 990 emails, 51 people, 3 sequences."""
            return "<p>voice.md rule 9 and rule 10 are in tension; see touch 1 and §2.1.2</p>"
    ''',
        tmp_path,
        monkeypatch,
    )


def test_it_stays_quiet_on_inline_css_and_bare_arguments(tmp_path, monkeypatch):
    assert not _scan(
        """
        def v(m):
            pad = "0"
            return "<div style='margin:8px 0 0;padding:12px 14px'>" + f"{x:.2%}".rstrip(pad)
    """,
        tmp_path,
        monkeypatch,
    )
