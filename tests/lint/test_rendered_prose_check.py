"""The typed-count gate, plus a negative control for it.

A lint test that only ever asserts "the tree is clean" is indistinguishable from a lint that
matches nothing — the failure this repo has already had twice (an inert V11 check, a linter
regex that matched zero packs). So the suite proves the scanner FIRES as well as passing.
"""

from __future__ import annotations

import textwrap

import pytest
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


def _tree(tmp_path):
    """Every SCANNED entry, empty — a missing one is a failure, not a pass (see below)."""
    for entry in rp.SCANNED:
        path = tmp_path / entry
        if path.suffix == ".py":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    return tmp_path / "gtm_core" / "email_campaign_dashboard"


def _scan(src: str, tmp_path, monkeypatch):
    (_tree(tmp_path) / "views_x.py").write_text(textwrap.dedent(src), encoding="utf-8")
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


def test_it_fires_on_a_count_written_as_a_word(tmp_path, monkeypatch):
    """PS20 P1.7 Rule A: "Five things change" sat over a six-row table. A word is a count too."""
    hits = _scan(
        """
        def v(m):
            return "<p>Five things change from person to person across a dozen groups</p>"
    """,
        tmp_path,
        monkeypatch,
    )
    assert {h[2] for h in hits} == {"Five", "dozen"}


def test_it_stays_quiet_on_hyphenated_forms_and_determiners(tmp_path, monkeypatch):
    """ "one" and "half" are deliberately not counts (PRD P1.7): they are determiners far more
    often than claims. A hyphenated compound names a kind of thing rather than counting one."""
    assert not _scan(
        """
        def v(m):
            return "<p>a two-step sequence reaches one person, and half the list waits</p>"
    """,
        tmp_path,
        monkeypatch,
    )


def test_it_scans_the_experiment_block_in_campaigns_dashboard(tmp_path, monkeypatch):
    """The manifest's experiment notes render from `campaigns_dashboard._experiment_block`,
    outside the package — a typed count there reached the page with no gate watching it."""
    _tree(tmp_path)
    mod = tmp_path / "gtm_core" / "campaigns_dashboard.py"
    mod.write_text(
        textwrap.dedent(
            """
            def _experiment_block(x):
                return "<h3>The five questions we set out to answer</h3>"
            """
        ),
        encoding="utf-8",
    )
    assert [(h[0], h[2]) for h in rp.findings(tmp_path)] == [
        ("gtm_core/campaigns_dashboard.py", "five")
    ]


def test_a_number_word_inside_another_word_is_not_a_count(tmp_path, monkeypatch):
    """ "often" holds "ten", "twofold" holds "two" — neither counts anything."""
    assert not _scan(
        """
        def v(m):
            return "<p>We often see a twofold spread in the weighted results</p>"
    """,
        tmp_path,
        monkeypatch,
    )


def test_a_missing_scanned_entry_fails_loudly(tmp_path):
    """A rename must not pass with nothing scanned: the gate would read as clean forever."""
    _tree(tmp_path)
    (tmp_path / "gtm_core" / "campaigns_dashboard.py").unlink()
    with pytest.raises(FileNotFoundError, match="campaigns_dashboard.py"):
        rp.findings(tmp_path)
