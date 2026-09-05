"""Contract: the readability gate stays wired to the skill, and its vocabulary stays current.

`tests/lint/test_brief_lint.py` proves the rules work. This proves they are still connected
to something — the two ways a gate like this dies:

1. **It stops running.** The linter lives in `gtm_core/`, but the artifact it judges lives
   under gitignored `content/`, so CI never sees a brief. The only thing that makes it run
   is the instruction in the skill body. Delete that line and the gate is still green,
   still tested, and never executed again.
2. **Its vocabulary goes stale.** T1 bans the collector's source ids and T3 checks the
   collector's counts. Both are derived at run time for exactly this reason — the counts in
   the shipped brief went stale three separate ways ("ten live sources", "10 / 10 present",
   "11 of 15 present") while every gate stayed green.

The template's own prose is checked here too. "The five speakers kept visually distinct"
sat in `body_template.md` after the eighth speaker landed, which is how a stale count
reaches a reader: not by someone mistyping it, but by the instructions saying it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import brief_lint as bl

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "plugin" / "skills" / "market-intelligence"
HARVEST = REPO / "plugin" / "skills" / "market-harvest"
BODY = SKILL / "body_template.md"


# Every authored instruction file behind the two market skills. Globbed rather than listed:
# a hardcoded list is how the fourth file (references/market-intel-template.html, which
# carried "SEVEN SPEAKERS" in a comment) stayed unchecked. SKILL.md is excluded — it is
# generated from body_template.md by codegen, so a finding there is a finding here.
def _authored_templates() -> list[Path]:
    out: list[Path] = []
    for skill in (SKILL, HARVEST):
        out += [p for p in sorted(skill.rglob("*.md")) if p.name != "SKILL.md"]
        out += sorted(skill.rglob("*.html"))
    return out


def test_every_source_id_has_a_human_readable_name():
    """T1's ban and its fix are the same lookup — a new source arrives with both."""
    from gtm_core.voc import collect as voc

    names = bl.source_names()
    with_ids = {s["id"] for s in _manifest_sources()}
    assert set(names) == with_ids, "source_names() drifted from the collector"
    assert voc.CUSTOMER_VOICE  # the collector is the authority, not a copy here


def _manifest_sources() -> list[dict]:
    import tempfile
    from datetime import date

    from gtm_core.voc import collect as voc

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = voc.collect(root / "content", root / "profiles", "acme", today=date(2026, 1, 1))
    return manifest["sources"]


def test_no_suggested_replacement_would_itself_fail_the_linter():
    """The fix a writer is handed must be usable copy.

    If a source's label were `own_product_watch`, T1 would ban the id and then suggest the
    id, and the writer would be stuck in a loop with no way out.
    """
    offenders = []
    for source_id, label in bl.source_names().items():
        if bl.lint(f"<p>{label}</p>", bl.READER):
            offenders.append((source_id, label))
    assert not offenders, (
        f"these source labels are not usable as reader copy: {offenders}. "
        "The label in gtm_core/voc/collect.py is what the linter tells a writer to use, "
        "so it has to survive the linter's own rules."
    )


def test_the_skill_tells_the_run_to_lint_the_html():
    """THE regression guard. Nothing else makes this gate execute."""
    body = BODY.read_text(encoding="utf-8")
    assert "gtm_core.brief_lint" in body, (
        "body_template.md no longer tells the run to lint the HTML companion. The artifact "
        "is under gitignored content/, so no CI job will ever check it — this instruction is "
        "the only thing that makes the readability gate run at all."
    )


def test_the_skill_states_the_surface_split():
    """A gate whose rule is not written down gets argued with instead of followed."""
    body = BODY.read_text(encoding="utf-8")
    lowered = body.lower()
    assert "reader surface" in lowered
    assert "internal record" in lowered


@pytest.mark.parametrize("path", _authored_templates(), ids=lambda p: p.name)
def test_the_instructions_carry_no_stale_counts(path: Path):
    """T3 over the templates themselves, at the record surface.

    The reader-surface tiers are deliberately NOT applied: these files are instructions to
    the model and legitimately name modules, speakers and fields. Only the counts have to
    be true, because a count in the instructions becomes a count in the brief.
    """
    findings = [f for f in bl.lint(path.read_text(encoding="utf-8"), bl.RECORD) if f.tier == "T3"]
    assert not findings, [f"{path.name}:{f.line}: “{f.excerpt}” → {f.fix}" for f in findings]


# The exact strings that reached a reader in the 2026-07-29 brief. Each one is a defect
# class the user named in review; keeping them here means a future rewrite of the rules
# cannot quietly stop catching them.
SHIPPED_DEFECTS = (
    "<p>Source: <code>enterprise_filings</code></p>",
    "<p>Computed by gtm_core.voc.delta from signals-2026-07-29.json — a diff, never a "
    "recollection.</p>",
    "<p>Across seven speakers and sixteen sources.</p>",
    "<p>Neither figure reproduces.</p>",
    "<p>The web lane runs at its 20/20 daily cap.</p>",
    "<p>Two Firecrawl credits, one news search.</p>",
    "<p>Coverage came back pull-failed for two lanes.</p>",
    "<p>The default posture is fail-OPEN.</p>",
    "<p>Registry lives in competitors.toml.</p>",
    "<p>See §04c and §04d.</p>",
)


@pytest.mark.parametrize("sample", SHIPPED_DEFECTS)
def test_each_defect_that_shipped_is_now_caught(sample: str):
    findings = bl.lint(sample, bl.READER)
    assert any(f.severity == bl.ERROR for f in findings), sample
