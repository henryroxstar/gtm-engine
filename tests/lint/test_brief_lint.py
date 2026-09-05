"""Prove each brief-lint rule fires on the defect it was written for, and stays quiet otherwise.

Every sample below is a paraphrase of something that actually shipped to a reader in the
2026-07-29 market-intelligence brief. The false-negative tests matter as much as the
positive ones: a linter that flags "4 sources, strong" — four independent companies backing
one claim — teaches writers to route around it, and a gate people route around is worse
than no gate, because it looks like coverage.

Samples name no real person or company (§R9): companies are `Acme` and `Initech`.
"""

from __future__ import annotations

import pytest

from gtm_core import brief_lint as bl


def _tiers(text: str, surface: str = bl.READER) -> list[str]:
    return [f.tier for f in bl.lint(text, surface)]


def _rules(text: str, surface: str = bl.READER) -> list[str]:
    return [f.rule for f in bl.lint(text, surface)]


# ── T1 · identifiers ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sample",
    [
        "<p>Source: <code>enterprise_filings</code></p>",  # a real leak, in a real table
        "<p>Computed by gtm_core.voc.delta from last week's file.</p>",
        "<p>Run <code>uv run python -m gtm_core.voc.collect</code> to refresh.</p>",
        "<p>The registry lives in competitors.toml.</p>",
        "<p>Untrusted input, handled per §R5.</p>",
        "<p>Each one requires settled_by before it can be quoted.</p>",
        "<p>The claim carries verified: false.</p>",
        "<p>Only BREADTH_ELIGIBLE_SPEAKERS may back a demand claim.</p>",
        "<p>Coverage came back pull-failed this week.</p>",
    ],
)
def test_t1_fires_on_repo_vocabulary(sample: str):
    assert "T1" in _tiers(sample), sample


def test_t1_names_the_replacement_for_a_source_id():
    findings = bl.lint("<p><code>enterprise_filings</code></p>", bl.READER)
    fixes = [f.fix for f in findings if f.rule == "source id"]
    assert fixes and "SEC filings" in fixes[0], fixes


def test_t1_is_silent_on_the_internal_record():
    """The markdown brief is allowed its mechanics — that is the whole surface split."""
    sample = "Computed by gtm_core.voc.delta over signals-2026-07-29.json."
    assert "T1" not in _tiers(sample, bl.RECORD)


def test_t1_ignores_markup_attributes():
    """`id="s4c"` is not prose, and a linter that says so is unusable."""
    sample = (
        '<section id="s4c" class="wrap_inner" data-audience="all"><p>Buyer language.</p></section>'
    )
    assert "T1" not in _tiers(sample)


# ── T2 · vocabulary ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sample",
    [
        "<p>Seven speakers, one of which counts as demand.</p>",
        "<p>Breadth is the number of independent filers.</p>",
        "<p>The funding lane found nothing this week.</p>",
        "<p>We harvested eleven pages.</p>",
        "<p>The figure does not reproduce.</p>",
        "<p>Deduped against the existing corpus.</p>",
        "<p>Provenance is external-financial.</p>",
        "<p>The watermark did not advance.</p>",
        "<p>The default is fail-OPEN.</p>",
        "<p>An issue-to-issue delta, never a recollection.</p>",
        "<p>Its disposition is open.</p>",
    ],
)
def test_t2_fires_on_house_jargon(sample: str):
    assert "T2" in _tiers(sample), sample


def test_t2_always_supplies_a_replacement():
    """A ban with no alternative just moves the problem to the next writer."""
    findings = [f for f in bl.lint("<p>Seven speakers.</p>", bl.READER) if f.tier == "T2"]
    assert findings
    assert all(f.fix.strip() for f in findings)


def test_t2_spares_quoted_material():
    """If a customer said it, the brief must be able to print it."""
    sample = "<blockquote><p>We cannot fetch an agent's identity.</p></blockquote>"
    assert "T2" not in _tiers(sample)


def test_t2_gates_on_our_tool_names():
    """ "Two Firecrawl credits" told a reader nothing, and there is always a better phrasing."""
    findings = [f for f in bl.lint("<p>Two Firecrawl credits.</p>", bl.READER) if f.tier == "T2"]
    assert findings
    assert all(f.severity == bl.ERROR for f in findings if f.rule == "internal tool name")


def test_t2_leaves_the_markets_own_words_alone():
    """`control plane` is what buyers call it. Ours to use, not ours to invent."""
    sample = "<p>Initech shipped an agent control plane with a management plane beside it.</p>"
    assert not _tiers(sample)


# ── T3 · counts ───────────────────────────────────────────────────────────────────────


def test_t3_catches_a_stale_speaker_count():
    """ "Seven speakers" in the hero paragraph, after the eighth was added the same day."""
    assert "T3" in _tiers("Across seven speakers and one demand signal.", bl.RECORD)


def test_t3_catches_a_stale_source_total_in_ratio_form():
    assert "T3" in _tiers("11 of 15 sources present.", bl.RECORD)


def test_t3_accepts_the_live_count():
    total = sorted(bl.expected_counts()["sources"])[0]
    assert "T3" not in _tiers(f"All {total} sources read.", bl.RECORD)


def test_t3_does_not_touch_an_evidence_count():
    """THE false positive to avoid: four companies backing one claim is not our inventory."""
    sample = (
        "<p>Agent access-control risk disclosed by named filers — <b>4 sources, strong</b>. "
        "But all four are inside one channel.</p>"
    )
    assert "T3" not in _tiers(sample)


def test_t3_ignores_a_section_number_that_precedes_a_title():
    """`08 Sources key` is a heading. It has never been a claim about anything."""
    assert "T3" not in _tiers('<a href="#s8"><span class="rn">08</span> Sources key</a>')


def test_t3_runs_on_both_surfaces():
    """A wrong number is a defect in the internal record too — it is what gets quoted."""
    sample = "Across seven speakers."
    assert "T3" in _tiers(sample, bl.RECORD)
    assert "T3" in _tiers(sample, bl.READER)


def test_t3_expectations_are_derived_not_transcribed():
    """The counts must come from the code that owns them, or they go stale exactly as before."""
    from gtm_core.voc import watermark

    assert bl.expected_counts()["lanes"] == {len(watermark.POLICIES)}


# ── T4 · cross-references ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sample",
    [
        '<p>See <a href="#s4b">§04b</a>, <a href="#s4e">§04e</a>.</p>',
        "<p>Covered in §02 and §03.</p>",
        "<p>See §08.</p>",
    ],
)
def test_t4_fires_on_a_reference_that_names_nothing(sample: str):
    assert "T4" in _tiers(sample), sample


def test_t4_accepts_a_named_reference():
    assert "T4" not in _tiers('<p>See <a href="#s4c">§04c Regulatory clock</a>.</p>')


# ── T5 · figures ──────────────────────────────────────────────────────────────────────


def test_t5_flags_a_figure_with_nothing_to_follow():
    findings = bl.lint("<p>The largest penalty was $48.6M.</p>", bl.READER)
    assert any(f.tier == "T5" for f in findings)


def test_t5_is_advisory_never_a_gate_by_default():
    findings = [f for f in bl.lint("<p>Up 36.2% this quarter.</p>", bl.READER) if f.tier == "T5"]
    assert findings and all(f.severity == bl.WARN for f in findings)


def test_t5_accepts_a_figure_with_a_link():
    sample = '<p>The largest penalty was $48.6M (<a href="https://acme.example/x">source</a>).</p>'
    assert "T5" not in _tiers(sample)


def test_t5_takes_the_whole_table_row_as_the_unit():
    """In a table the source lives one cell over, and that is close enough for a reader."""
    sample = (
        "<table><tr><td>Acme</td><td>$30M</td>"
        '<td><a href="https://acme.example/pr">announcement</a></td></tr></table>'
    )
    assert "T5" not in _tiers(sample)


# ── T6 · structure ────────────────────────────────────────────────────────────────────


def test_t6_rejects_a_script():
    assert "T6" in _tiers("<p>x</p><script>alert(1)</script>")


def test_t6_rejects_an_external_asset():
    assert "T6" in _tiers('<link href="https://cdn.example.com/a.css" rel="stylesheet">')


def test_t6_catches_a_dangling_anchor():
    assert "dangling anchor" in _rules('<a href="#nowhere">go</a>')


def test_t6_catches_a_duplicate_id():
    assert "duplicate id" in _rules('<section id="s9"></section><section id="s9"></section>')


def test_t6_catches_an_unbalanced_div():
    assert "unbalanced <div>" in _rules("<div><div></div>")


def test_t6_catches_a_section_missing_from_the_contents():
    sample = (
        '<nav><a href="#s1">One</a></nav>'
        '<section id="s1"></section><section id="glossary"></section>'
    )
    assert "section missing from contents" in _rules(sample)


def test_t6_is_quiet_when_the_contents_are_complete():
    sample = (
        '<nav><a href="#s1">One</a><a href="#glossary">Glossary</a></nav>'
        '<section id="s1"><p>a</p></section><section id="glossary"><p>b</p></section>'
    )
    assert "T6" not in _tiers(sample)


# ── T7 · density ──────────────────────────────────────────────────────────────────────


def test_t7_flags_a_long_sentence():
    sentence = "<p>" + " ".join(["word"] * 60) + ".</p>"
    assert "long sentence" in _rules(sentence)


def test_t7_flags_bold_overload():
    sample = "<p>" + "".join(f"<b>thing {n}</b> " for n in range(6)) + "matters.</p>"
    assert "bold overload" in _rules(sample)


def test_t7_leaves_tables_alone():
    """A comparison grid is dense on purpose."""
    cells = "".join(f"<td>{' '.join(['word'] * 30)}</td>" for _ in range(6))
    assert "T7" not in _tiers(f"<table><tr>{cells}</tr></table>")


def test_t7_measures_prose_inside_plain_divs():
    """Most of this brief's copy lives in callout divs, not <p> tags."""
    sample = '<div class="note">' + " ".join(["word"] * 150) + ".</div>"
    assert "long paragraph" in _rules(sample)


def test_t7_never_gates_on_its_own():
    findings = bl.lint("<p>" + " ".join(["word"] * 60) + ".</p>", bl.READER)
    assert all(f.severity == bl.WARN for f in findings if f.tier == "T7")


# ── suppression ───────────────────────────────────────────────────────────────────────


def test_a_named_suppression_silences_one_tier_on_one_line():
    sample = "<p>Seven speakers.</p> <!-- lint-ok T2: quoting the spec verbatim -->"
    assert "T2" not in _tiers(sample)


def test_a_suppression_on_the_line_above_also_counts():
    sample = "<!-- lint-ok T2: quoting the spec verbatim -->\n<p>Seven speakers.</p>"
    assert "T2" not in _tiers(sample)


def test_a_suppression_does_not_silence_other_tiers():
    sample = "<p>Seven speakers, see §02.</p> <!-- lint-ok T2: reason -->"
    tiers = _tiers(sample)
    assert "T2" not in tiers
    assert "T4" in tiers


def test_a_bare_suppression_marker_is_ignored():
    """An unexplained suppression is how a gate quietly becomes decorative."""
    sample = "<p>Seven speakers.</p> <!-- lint-ok T2 -->"
    assert "T2" in _tiers(sample)


# ── surface + exit code ───────────────────────────────────────────────────────────────


def test_surface_is_inferred_from_the_suffix():
    from pathlib import Path

    assert bl.infer_surface(Path("brief.html")) == bl.READER
    assert bl.infer_surface(Path("brief.md")) == bl.RECORD


def test_clean_reader_copy_passes_every_tier():
    sample = (
        '<nav><a href="#s1">What buyers say</a></nav>'
        '<section id="s1" data-audience="all">'
        "<p>Four card issuers named agent-initiated payments as a risk in their annual "
        'filings (<a href="https://acme.example/10k">Acme</a>, '
        '<a href="https://initech.example/10k">Initech</a>).</p>'
        "</section>"
    )
    assert bl.lint(sample, bl.READER) == []


def test_main_exits_nonzero_on_an_error(tmp_path, capsys):
    path = tmp_path / "brief.html"
    path.write_text("<p><code>enterprise_filings</code></p>", encoding="utf-8")
    assert bl.main([str(path)]) == 1
    assert "enterprise_filings" in capsys.readouterr().out


def test_main_exits_zero_on_warnings_only(tmp_path):
    path = tmp_path / "brief.html"
    path.write_text("<p>Up 36.2% this quarter.</p>", encoding="utf-8")
    assert bl.main([str(path)]) == 0


def test_strict_turns_warnings_into_failures(tmp_path):
    path = tmp_path / "brief.html"
    path.write_text("<p>Up 36.2% this quarter.</p>", encoding="utf-8")
    assert bl.main([str(path), "--strict"]) == 1


def test_a_missing_file_is_a_failure_not_a_pass(tmp_path):
    """Silently passing a path that does not exist is how a gate stops running."""
    assert bl.main([str(tmp_path / "gone.html")]) == 1


# ── rules that were wrong the first time ──────────────────────────────────────────────


def test_t4_accepts_a_title_that_starts_with_a_stopword():
    """`Where` is a stopword and also how a real section title starts.

    The first version of T4 keyed on the word alone and flagged "§03 Where BD is pointed",
    which would have forced the section to be renamed to satisfy the linter. Capitalisation
    is the tell.
    """
    assert "T4" not in _tiers('<p>See <a href="#s3">§03 Where BD is pointed</a>.</p>')
    assert "T4" in _tiers("<p>Covered where it belongs, see §03 where relevant.</p>")


def test_t4_flags_a_reference_that_ends_a_sentence():
    """The next sentence's first word is not this reference's title."""
    assert "T4" in _tiers("<p>See §04c. The market is loud about this.</p>")


def test_t3_knows_voices_is_the_reader_facing_word_for_speakers():
    """The glossary said "five voices" while T3 only understood "speakers"."""
    assert "T3" in _tiers("<p>Which of the five voices a passage belongs to.</p>")


def test_style_and_comments_are_not_prose():
    """`/* speaker series */` in a stylesheet is code no reader sees."""
    assert not _tiers("<style>/* speaker series — reused across chips */</style><p>Hello.</p>")
    assert not _tiers("<!-- breadth-ineligible marker --><p>Hello.</p>")


def test_a_wrapped_phrase_is_still_checked():
    """Hard-wrapped markdown split `the five\\nspeakers` across lines and T3 missed it."""
    assert "T3" in _tiers(
        "presents the same content with the five\nspeakers kept distinct", bl.RECORD
    )


def test_t4_accepts_a_ref_that_describes_itself_without_its_title():
    """ "§3 — what the market says and does" tells the reader what it points at."""
    assert "T4" not in _tiers("<p>Customer voice (§3 — what the market says and does).</p>")
    assert "T4" in _tiers("<p>Customer voice (§3 — see).</p>")


def test_t3_sees_through_an_adjective():
    """ "all nine external lanes are current" was stale and the rule could not see it."""
    assert "T3" in _tiers("**all nine** external\nlanes are `current`.", bl.RECORD)


def test_t7_does_not_double_count_a_container():
    """Five short numbered items in one div is not a 232-word paragraph."""
    items = "".join(f"<p>{n}. " + " ".join(["word"] * 30) + ".</p>" for n in range(5))
    assert "long paragraph" not in _rules(f'<div class="note">{items}</div>')


def test_t7_leaves_a_verbatim_quotation_alone():
    """A filing's own 100-word sentence is the source's choice, not the writer's."""
    quote = " ".join(["word"] * 70)
    assert "long sentence" not in _rules(f'<div class="ev">“{quote}.”</div>')


# ── T8 · audience routing ─────────────────────────────────────────────────────────────


def test_t8_requires_an_audience_on_every_section():
    assert "T8" in _tiers("<section id='s1'><p>Hello.</p></section>")


def test_t8_rejects_an_unknown_audience():
    assert "T8" in _tiers('<section data-audience="everyone"><p>Hi.</p></section>')


def test_t8_operator_sections_must_come_last():
    sample = (
        '<section data-audience="operator"><p>build log</p></section>'
        '<section data-audience="all"><p>findings</p></section>'
    )
    findings = [f for f in bl.lint(sample, bl.READER) if f.tier == "T8"]
    assert any("before reader" in f.rule for f in findings)


def test_the_operator_appendix_is_exempt_from_reader_register():
    """The one place mechanics are allowed — that is what makes banning them elsewhere fair."""
    sample = (
        '<section data-audience="operator" class="appx">'
        "<p>Re-run the pull; the watermark did not advance. See competitors.toml.</p>"
        "</section>"
    )
    tiers = _tiers(sample)
    assert "T1" not in tiers and "T2" not in tiers


def test_the_same_mechanics_outside_the_operator_appendix_still_fail():
    sample = (
        '<section data-audience="all">'
        "<p>Re-run the pull; the watermark did not advance. See competitors.toml.</p>"
        "</section>"
    )
    tiers = _tiers(sample)
    assert "T1" in tiers and "T2" in tiers


# ── T9 · takeaway-first ───────────────────────────────────────────────────────────────


def test_t9_flags_a_long_callout_that_leads_with_method():
    body = "Method: 65 tasks, five domains, ten fictional companies, " + " ".join(["detail"] * 60)
    sample = f'<section data-audience="all"><div class="note teal">{body}.</div></section>'
    assert "callout buries its takeaway" in _rules(sample)


def test_t9_accepts_a_callout_that_opens_with_a_bold_takeaway():
    body = "<b>Agents cannot follow rules yet.</b> " + " ".join(["detail"] * 60)
    sample = f'<section data-audience="all"><div class="note teal">{body}.</div></section>'
    assert "callout buries its takeaway" not in _rules(sample)


def test_t9_leaves_short_callouts_alone():
    sample = (
        '<section data-audience="all"><div class="note">No bold here, but short.</div></section>'
    )
    assert "callout buries its takeaway" not in _rules(sample)


def test_t9_caveat_block_may_not_precede_the_findings():
    sample = (
        '<section data-audience="all"><div class="note crit">'
        '<span class="cl">Before quoting any number</span>five caveats</div></section>'
        '<section data-audience="all" id="appendix" class="appx"><p>ref</p></section>'
    )
    assert "caveats before content" in _rules(sample)


def test_t9_a_pointer_to_the_caveats_is_fine():
    """The pointer is the desired state — flagging the phrase would ban the fix."""
    sample = (
        '<section data-audience="all"><p>Before quoting any number externally, '
        'see <a href="#appendix">Appendix A</a>.</p></section>'
        '<section data-audience="all" id="appendix" class="appx">'
        '<div class="note"><span class="cl">Before quoting any number</span>caveats</div></section>'
    )
    assert "caveats before content" not in _rules(sample)


def test_t9_actions_section_must_be_split_by_team():
    sample = (
        '<section data-audience="all"><h2>What this means for each team</h2>'
        "<h3>Marketing</h3><p>a</p><h3>Sales</h3><p>b</p></section>"
    )
    assert "actions not split by team" in _rules(sample)


def test_t9_a_link_to_the_actions_section_does_not_trigger_the_shape_check():
    sample = (
        '<section data-audience="all"><p>See <a href="#s9">What this means for each team</a>.</p>'
        "</section>"
    )
    assert "actions not split by team" not in _rules(sample)


# --------------------------------------------------------------------------------------
# T10 — render integrity. Both defects below shipped in the 2026-08-11 brief and were
# found by a human reading the published page, which is the wrong last line of defence.
# --------------------------------------------------------------------------------------


def test_t10_flags_a_class_the_stylesheet_never_defines():
    """The unstyled-section bug: markup named a container class that did not exist."""
    sample = '<style>.tbl{width:100%}</style><section data-audience="all"><div class="ship">x</div></section>'
    assert "class-not-styled" in _rules(sample)


def test_t10_quiet_when_every_class_is_styled():
    sample = '<style>.ship{display:grid}</style><section data-audience="all"><div class="ship">x</div></section>'
    assert "class-not-styled" not in _rules(sample)


def test_t10_flags_an_inline_span_whose_vertical_margin_is_dropped():
    """The "88 companies" bug: <b>8</b><span class=sub>8 companies</span> rendered glued,
    because margin-top does not apply to an inline box. The source looks correctly separated,
    which is exactly why a reader found it before a gate did."""
    sample = (
        "<style>.sub{margin-top:12px; color:#888}</style>"
        '<section data-audience="all"><td><b>8</b><span class="sub">8 companies</span></td></section>'
    )
    assert "glued-inline" in _rules(sample)


def test_t10_quiet_once_the_span_is_given_a_block_display():
    """Scoping the rule (`td .sub`) is the actual fix — the check must accept it."""
    sample = (
        "<style>.sub{margin-top:12px} td .sub{display:block; margin-top:3px}</style>"
        '<section data-audience="all"><td><b>8</b><span class="sub">8 companies</span></td></section>'
    )
    assert "glued-inline" not in _rules(sample)


def test_t10_quiet_for_an_inline_class_that_declares_no_vertical_margin():
    """An inline badge with only horizontal spacing renders fine glued — not a defect."""
    sample = (
        "<style>.spk{margin-right:6px; color:#333}</style>"
        '<section data-audience="all"><p>x<span class="spk">customer</span></p></section>'
    )
    assert "glued-inline" not in _rules(sample)


def test_t10_applies_inside_the_operator_appendix_too():
    """Markup that does not render is a defect everywhere; T10 is not audience-filtered."""
    sample = (
        "<style>.tbl{width:100%}</style>"
        '<section data-audience="all"><p>a</p></section>'
        '<section data-audience="operator"><div class="oops">x</div></section>'
    )
    assert "class-not-styled" in _rules(sample)


# --------------------------------------------------------------------------------------
# T9 headline + T11 internal consistency. Every case below is a stale dependent: a fact
# changed in one place and the sentences depending on it did not. All three shipped in one
# 2026-08-11 session; a human caught two of them.
# --------------------------------------------------------------------------------------


def _h1(headline: str) -> str:
    return f'<style>.x{{color:#000}}</style><section data-audience="all"><h1>{headline}</h1><p class="x">a</p></section>'


def test_t9_headline_about_our_method_is_rejected():
    """The real rejected headline: a report on our search hygiene, not on the market."""
    sample = _h1(
        "We finally asked buyers the right question and three of them had already answered it"
    )
    assert "headline-about-method" in _rules(sample)


def test_t9_headline_stating_a_market_fact_is_fine():
    sample = _h1("This category's newest products all stop at the company boundary")
    assert "headline-about-method" not in _rules(sample)


def test_t9_headline_may_say_we_when_it_is_not_about_method():
    """Banning first person outright would ban legitimate positioning headlines."""
    sample = _h1("Two rivals now ship a product with our flagship's name")
    assert "headline-about-method" not in _rules(sample)


def _two_tables(count_a: str, count_b: str) -> str:
    row = "<tr><td>Agent identity tooling is immature</td><td><b>%s</b></td><td>strong</td></tr>"
    return (
        "<style>.x{color:#000}</style>"
        f'<section data-audience="all" id="s2"><p class="x">a</p><table><tbody>{row % count_a}</tbody></table></section>'
        f'<section data-audience="all" id="ev"><p class="x">b</p><table><tbody>{row % count_b}</tbody></table></section>'
    )


def test_t11_same_claim_with_two_different_counts_is_an_error():
    """Updating §02 to 6 and leaving the appendix at 5 is the exact defect."""
    assert "count-disagrees-across-tables" in _rules(_two_tables("6", "5"))


def test_t11_quiet_when_both_tables_agree():
    assert "count-disagrees-across-tables" not in _rules(_two_tables("6", "6"))


def test_t11_flags_a_sentence_asserting_what_another_section_lacks():
    """ "our central bet is the one thing §02 has no customer evidence for" — true when written,
    false one issue later, and unverifiable by machine. Flagged for a human re-read."""
    sample = (
        '<style>.x{color:#000}</style><section data-audience="all" id="s2"><p class="x">a</p></section>'
        '<section data-audience="all" id="ev"><p class="x">our central bet is the one thing '
        '<a href="#s2">&sect;02 What buyers say</a> has no customer evidence for.</p></section>'
    )
    assert "cross-section-assertion" in _rules(sample)


def test_t11_an_ordinary_cross_reference_is_not_flagged():
    """A plain pointer must stay quiet, and so must a row that merely ends in an evidence link
    with a negation elsewhere in the sentence — that looseness produced 20 junk warnings."""
    sample = (
        '<style>.x{color:#000}</style><section data-audience="all" id="s2"><p class="x">a</p></section>'
        '<section data-audience="all" id="ev"><p class="x">A mention is not demand. '
        'See <a href="#s2">&sect;02 What buyers say</a> for the counts.</p></section>'
    )
    assert "cross-section-assertion" not in _rules(sample)


# --- T12 template residue ------------------------------------------------------------
# The 2026-08-18 brief shipped with the browser tab reading "{{Voice of the Customer}}",
# the template's own build instructions still at the top of the file, and a CSS comment
# announcing a placeholder accent above the tenant's real brand colour. Replacing the body
# leaves all three untouched, and a reader found each of them.

_T12_STYLE = "<style>.ok{color:red}</style>"


def _t12(body: str) -> list[str]:
    return [f.rule for f in bl.lint(_T12_STYLE + body, bl.READER) if f.tier == "T12"]


def test_t12_flags_unreplaced_placeholder_in_the_head():
    body = '<title>{{Voice of the Customer}} · {{MONTH YEAR}}</title><section data-audience="all"><p class="ok">x</p></section>'
    assert "unreplaced-placeholder" in _t12(body)


def test_t12_flags_the_templates_own_build_instructions():
    body = '<!-- generic HTML companion template. Copy this file, then: --><section data-audience="all"><p class="ok">x</p></section>'
    assert "template-scaffolding" in _t12(body)


def test_t12_flags_a_stale_placeholder_accent_comment():
    body = '<section data-audience="all"><p class="ok">/* PLACEHOLDER ACCENT (neutral slate-blue) */</p></section>'
    assert "template-scaffolding" in _t12(body)


def test_t12_silent_on_a_finished_document():
    body = '<title>Acme · Market Intelligence · August 2026</title><section data-audience="all"><p class="ok">x</p></section>'
    assert _t12(body) == []


# --- T13 component contracts ---------------------------------------------------------
# ".dseg" is a bar TRACK whose coloured fill is an inner <i>. A brief shipped a five-row
# chart of empty outlines with every class spelled correctly, so T10 passed. The check is
# derived from the stylesheet, and deliberately ignores children that only carry typography
# — a callout that happens to contain no list is correct, not a defect.

_T13_STYLE = (
    "<style>"
    ".dseg{display:flex}.dseg i{display:block;height:100%}"
    ".gloss{border-top:1px solid}.gloss dt{font-weight:600}"
    ".gloss dd{margin:0;font-size:13px;line-height:1.55}"
    ".clist{list-style:none}.clist li{font-size:13px;line-height:1.55}"
    ".note{padding:14px}.note li{margin:0 0 9px;line-height:1.58}"
    ".ok{color:red}</style>"
)


def _t13(body: str) -> list[str]:
    text = _T13_STYLE + f'<section data-audience="all">{body}</section>'
    return [f.rule for f in bl.lint(text, bl.READER) if f.tier == "T13"]


def test_t13_flags_a_bar_track_with_no_fill():
    assert "styled-child-missing" in _t13('<span class="dseg" style="width:52%"></span>')


def test_t13_accepts_a_bar_track_with_its_fill():
    assert _t13('<span class="dseg"><i style="width:52%"></i></span>') == []


def test_t13_flags_a_glossary_that_is_not_a_definition_list():
    body = '<div class="gloss"><div><b>term</b><span>definition</span></div></div>'
    assert "list-container-not-a-list" in _t13(body)


def test_t13_accepts_a_real_definition_list():
    body = '<dl class="gloss"><div><dt>term</dt><dd>definition</dd></div></dl>'
    assert _t13(body) == []


def test_t13_flags_a_list_container_holding_prose():
    assert "list-container-not-a-list" in _t13('<div class="clist"><p>prose</p></div>')


def test_t13_accepts_a_list_container_holding_a_list():
    assert _t13('<div class="clist"><ul><li>x</li></ul></div>') == []


def test_t13_does_not_flag_a_callout_that_simply_has_no_list():
    """The regression that made the first cut of T13 unusable.

    ".note li" only sets typography, so a callout without a list is correct — and the
    naive check fired on 14 of them in one prior brief against a single true positive.
    The property test must also not treat "line-height" as the "height" of a painting
    rule, which is what reintroduced the false positives once before.
    """
    assert _t13('<div class="note"><p>Just a callout, no list.</p></div>') == []


def test_t14_ignores_header_rows():
    """A <th> row names the columns; it makes no claim, so it has nothing to cite.

    Flagging it made the tier fire on every well-formed table in the four gated sections —
    noise that trains the reader to ignore a real finding.
    """
    html = (
        '<h1>x</h1><section id="s2"><h2>What buyers say</h2>'
        "<table><tr><th>Claim</th><th>Sources</th></tr>"
        '<tr><td><a href="#ev-b1">cited</a></td><td>20</td></tr></table></section>'
    )
    findings = bl.t14_evidence_linkage(html, bl._flatten(html), bl._line_index(html))
    assert findings == [], "header row and cited row should both pass"


def test_t14_still_catches_an_uncited_body_row():
    html = (
        '<h1>x</h1><section id="s4b"><h2>Competitor moves</h2>'
        "<table><tr><th>Vendor</th><th>Event</th></tr>"
        "<tr><td>Okta</td><td>no movement in window</td></tr></table></section>"
    )
    findings = bl.t14_evidence_linkage(html, bl._flatten(html), bl._line_index(html))
    assert len(findings) == 1
    assert findings[0].tier == "T14"
