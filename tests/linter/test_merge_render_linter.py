"""Merge-render linter — regression tests (rules 2026-07-28).

The gap this linter closes, in one sentence: on 2026-07-28 the outreach copy linter
passed 334 prospect rows with **zero errors across 1,002 renders** while 9 of those rows
would have sent "Hi 🍦," or "agents at Canopy GBS | SAP Consulting | AI & Automation |
move...". The copy gate reads finished text; a sequence is templates plus a CSV, and
nothing was linting the combination.

Each defect class below is a value that was actually in ``ready-to-load.csv`` that day.
"""

from __future__ import annotations

import pytest
from merge_render_linter import (
    DEFAULT_FIELD_LABELS,
    RULES_VERSION,
    Touch,
    Violation,
    _is_data_borne,
    capacity_note,
    lint_article_collision,
    lint_empty_merge_tags,
    lint_hedge_stem,
    lint_merge_render,
    lint_merge_tags,
    lint_possessive,
    lint_same_company_divergence,
    lint_signal_relevance,
    lint_touch_personalisation,
    lint_unused_signal_columns,
    parse_spec,
    render,
)

SPEC = """
**Step 1 — Day 1** · Subject: `your agents in production`
> Hi {{First Name}},
>
> Once agents at {{Company}} move from retrieving data to acting on it, identity becomes
> the question an auditor asks first. Tell me if you've got this covered: your
> logs capture which account touched a record, but not which agent held the authority to
> act, and that gap widens the moment one agent hands work to another. An SME-operations
> platform running eight agents per user swapped one shared audit-log identity for a
> cryptographic DID per agent. Want that mapped to {{Company}}'s stack, plus a 90-second
> recording?
>
> Henry

Some prose about the touch that is not part of the body.

**Step 2 — Day 4** (same thread, no subject)
> Hi {{First Name}},
>
> Following the note on agent authority. Teams we work with hit this when an agent first
> acts on regulated data rather than reading it, because that is when an auditor asks who
> authorized the action instead of who accessed the record. My hunch: {{Company}} already
> logs the what, and the open piece is portable proof of the who. A regulated-FI
> compliance team held 100 percent audit compliance that way. Want their before-and-after,
> mapped to {{Company}}'s agent path?
>
> Henry
"""

GOOD_ROW = {
    "first": "Chris",
    "last": "Renner",
    "email": "chris@cascade.example",
    "company": "Cascade",
    "company_domain": "cascade.example",
    "title": "CISO",
}


def _row(**kw) -> dict:
    return {**GOOD_ROW, **kw}


def _errors(rows, touches=None, **kw) -> dict[str, int]:
    touches = touches if touches is not None else parse_spec(SPEC)
    violations, _ = lint_merge_render(touches, rows, signoff="Henry", **kw)
    out: dict[str, int] = {}
    for v in violations:
        if v.level == "ERROR":
            out[v.rule] = out.get(v.rule, 0) + 1
    return out


# --- spec parsing --------------------------------------------------------


def test_parse_spec_reads_touches_subjects_and_days():
    touches = parse_spec(SPEC)
    assert [t.number for t in touches] == [1, 2]
    assert [t.day for t in touches] == [1, 4]
    assert touches[0].subject == "your agents in production"
    assert touches[1].subject == ""  # same-thread follow-up


def test_parse_spec_body_excludes_surrounding_prose():
    body = parse_spec(SPEC)[0].body
    assert body.startswith("Hi {{First Name}},")
    assert body.endswith("Henry")
    assert "Some prose about the touch" not in body


def test_a_spec_with_no_touches_is_an_error_not_a_silent_pass():
    assert "parse" in _errors([_row()], touches=[])


# --- rendering -----------------------------------------------------------


def test_render_substitutes_mapped_tags():
    out = render("Hi {{First Name}}, about {{Company}}.", _row())
    assert out == "Hi Chris, about Cascade."


def test_unmapped_tag_is_left_in_place_so_the_copy_linter_reports_it():
    # An unresolved tag reaching a prospect is the exact failure this linter exists for,
    # so it must survive rendering rather than being silently blanked.
    assert "{{Nope}}" in render("Hi {{Nope}},", _row())


# --- the real 2026-07-28 defects -----------------------------------------


def test_clean_rows_pass():
    assert _errors([_row()]) == {}


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        ({"first": "\U0001f366"}, "first-name-unrenderable"),
        ({"first": ""}, "first-name-empty"),
        ({"company": "DevTrial | We Build Tests"}, "company-headline"),
        ({"company": "Westvale Land Title Insurance Company®"}, "company-trademark-glyph"),
        ({"company": "MediPath, Inc."}, "company-trailing-period"),
        ({"company": "All About You! Collaborative"}, "company-sentence-punct"),
        ({"email": "info@cascade.example"}, "email-role-address"),
    ],
)
def test_merge_field_defects_are_errors(kw, rule):
    assert rule in _errors([_row(**kw)])


def test_row_level_defects_are_reported_once_not_once_per_touch():
    # Three touches must not triple-count one bad company name.
    assert _errors([_row(company="A | B")])["company-headline"] == 1


# --- merge tags ----------------------------------------------------------


def test_unknown_merge_tag_is_caught_before_the_provider_400s():
    # The real bug: "Company Domain Name" was emitted where Saleshandy's label is
    # "Company Domain", and the first import sub-batch 400'd.
    bad = Touch(1, 1, "subject", "Hi {{First Name}},\n\n{{Company Domain Name}}\n\nHenry")
    rules = {v.rule for v in lint_merge_tags([bad], DEFAULT_FIELD_LABELS) if v.level == "ERROR"}
    assert "unknown-merge-tag" in rules


def test_valid_tag_with_no_csv_column_warns_but_does_not_block():
    t = Touch(1, 1, "subject", "Hi {{First Name}},\n\n{{Industry}}\n\nHenry")
    violations = lint_merge_tags([t], DEFAULT_FIELD_LABELS)
    assert [v.level for v in violations] == ["WARN"]
    assert violations[0].rule == "merge-tag-not-in-csv"


def test_known_tags_are_clean():
    assert lint_merge_tags(parse_spec(SPEC), DEFAULT_FIELD_LABELS) == []


# --- copy rules still apply to every render ------------------------------


def test_copy_violations_in_a_render_are_reported_per_touch():
    # A banned word introduced via the template must fail on every touch it appears in.
    t = Touch(1, 1, "subject here", "Hi {{First Name}},\n\nLet's circle back.\n\nHenry")
    errs = _errors([_row()], touches=[t])
    assert "banned-word" in errs


def test_stats_count_every_render():
    _, stats = lint_merge_render(parse_spec(SPEC), [_row(), _row(email="b@x.com")], signoff="Henry")
    assert stats == {"rows": 2, "touches": 2, "renders": 4}


def test_rules_version_is_pinned():
    assert RULES_VERSION == "2026-08-20"


# --- possessive against sibilant company names ---------------------------


def _possessive_touch() -> Touch:
    return Touch(1, 1, "subject", "Hi {{First Name}},\n\nAbout {{Company}}'s stack.\n\nHenry")


def test_possessive_against_sibilant_company_is_flagged():
    v = lint_possessive([_possessive_touch()], [_row(company="Gears & Vectors")])
    assert [x.rule for x in v] == ["possessive-sibilant"]
    assert "1/1 rows" in v[0].detail


def test_possessive_is_reported_once_per_touch_not_once_per_row():
    # 66 identical row warnings would bury the single template line that needs changing.
    rows = [_row(company="Acme Solutions", email=f"a{i}@x.com") for i in range(20)]
    v = lint_possessive([_possessive_touch()], rows)
    assert len(v) == 1
    assert "20/20 rows" in v[0].detail


def test_possessive_clean_when_no_company_ends_in_a_sibilant():
    assert lint_possessive([_possessive_touch()], [_row(company="Cascade")]) == []


def test_possessive_clean_when_the_template_avoids_the_construction():
    rephrased = Touch(1, 1, "subject", "Hi {{First Name}},\n\nThe stack at {{Company}}.\n\nHenry")
    assert lint_possessive([rephrased], [_row(company="Gears & Vectors")]) == []


def test_possessive_never_blocks():
    errs = _errors([_row(company="Gears & Vectors")], touches=[_possessive_touch()])
    assert "possessive-sibilant" not in errs


# --- article collision ---------------------------------------------------


def _article_touch() -> Touch:
    return Touch(1, 1, "subject", "Hi {{First Name}},\n\nAbout the {{Company}} stack.\n\nHenry")


def test_the_before_company_tag_is_flagged_for_companies_carrying_an_article():
    # Regression: fixing the possessive ({{Company}}'s -> the {{Company}}) introduced this.
    v = lint_article_collision([_article_touch()], [_row(company="The Meridian Group")])
    assert [x.rule for x in v] == ["article-collision"]
    assert "the The Meridian Group" in v[0].detail


@pytest.mark.parametrize("company", ["The Meridian Group", "A Better Place", "The Regional Banker"])
def test_every_article_form_is_caught(company):
    assert lint_article_collision([_article_touch()], [_row(company=company)])


def test_no_collision_for_ordinary_company_names():
    assert lint_article_collision([_article_touch()], [_row(company="Cascade")]) == []


def test_the_at_form_is_correct_for_articles_and_sibilants_alike():
    # "the stack at {{Company}}" is the one phrasing safe for both collision classes.
    safe = Touch(1, 1, "s", "Hi {{First Name}},\n\nThe stack at {{Company}}.\n\nHenry")
    rows = [_row(company="The Meridian Group"), _row(company="Gears & Vectors", email="b@x.com")]
    assert lint_article_collision([safe], rows) == []
    assert lint_possessive([safe], rows) == []


# --- unused signal columns -----------------------------------------------


def test_populated_why_now_that_no_touch_renders_is_flagged():
    # Long enough to survive the reducer once its trailing date stamp is dropped — a bare
    # "Series C" is under SIGNAL_MIN_CHARS and reduces to nothing, which is a different rule.
    rows = [
        _row(why_now="Series C led by Founders Fund 2026-07-29"),
        _row(email="b@x.com", why_now=""),
    ]
    v = lint_unused_signal_columns(parse_spec(SPEC), rows)
    assert [x.rule for x in v] == ["unused-signal-column"]
    assert "1/2 rows" in v[0].detail


def test_no_finding_when_the_column_is_empty_across_the_list():
    assert lint_unused_signal_columns(parse_spec(SPEC), [_row(why_now="")]) == []


def test_no_finding_when_a_touch_actually_renders_the_signal():
    uses = Touch(1, 1, "s", "Hi {{First Name}},\n\nSaw {{Why Now}}.\n\nHenry")
    assert lint_unused_signal_columns([uses], [_row(why_now="Series C")]) == []


# --- empty merge tags (the reason the pool has to be split) --------------


def _signal_touch() -> Touch:
    return Touch(
        1,
        1,
        "subject here",
        "Hi {{First Name}},\n\nSaw the news out of {{Company}}: {{Why Now}}. My read, tell me if "
        "you've got this covered: logs capture which account touched a record in 2026, not which "
        "agent held authority. Should I send the one-pager?\n\nHenry",
    )


def test_a_tag_blank_for_some_rows_is_an_error():
    # Saleshandy substitutes an empty string, so this ships "Saw the news out of Acme: ."
    rows = [_row(signal_clause="Series C 2026-07-29"), _row(email="b@x.com", signal_clause="")]
    v = lint_empty_merge_tags([_signal_touch()], rows)
    assert [x.rule for x in v] == ["empty-merge-tag"]
    assert [x.level for x in v] == ["ERROR"]
    assert "1/2 rows" in v[0].detail


def test_no_finding_when_every_row_fills_the_tag():
    rows = [_row(signal_clause="Series C"), _row(email="b@x.com", signal_clause="Launch")]
    assert lint_empty_merge_tags([_signal_touch()], rows) == []


def test_a_tag_the_copy_does_not_render_is_not_checked():
    plain = Touch(1, 1, "s", "Hi {{First Name}},\n\nAbout {{Company}}.\n\nHenry")
    assert lint_empty_merge_tags([plain], [_row(signal_clause="")]) == []


def test_signal_copy_against_an_unsplit_list_fails_closed():
    # The end-to-end guard: pointing signal-led copy at the whole pool must not pass.
    errs = _errors([_row(), _row(email="b@x.com")], touches=[_signal_touch()])
    assert "empty-merge-tag" in errs


# --- unused signal columns counts only USABLE signal ---------------------


def test_unused_signal_counts_only_rows_that_reduce_to_a_clause():
    rows = [
        _row(why_now="Agent Control Layer launch (2026-06-09)"),  # usable
        _row(email="b@x.com", why_now="machine learning (intent score 81)"),  # intent label
        _row(email="c@x.com", why_now="No dated funding round confirmed"),  # no signal
    ]
    v = lint_unused_signal_columns(parse_spec(SPEC), rows)
    assert len(v) == 1
    assert "1/3 rows" in v[0].detail


def test_no_unused_signal_finding_when_nothing_is_usable():
    rows = [_row(why_now="machine learning & artificial intelligence (intent score 81)")]
    assert lint_unused_signal_columns(parse_spec(SPEC), rows) == []


# --- same-company divergence ---------------------------------------------


def test_two_contacts_at_one_company_getting_identical_copy_is_flagged():
    rows = [
        _row(email="a@cascade.example", first="Ann"),
        _row(email="b@cascade.example", first="Ben"),
    ]
    # The template varies only by first name, so the *bodies* differ — no finding.
    varied = Touch(1, 1, "s", "Hi {{First Name}},\n\nAbout {{Company}}.\n\nHenry")
    assert lint_same_company_divergence([varied], rows) == []

    # A body that never mentions the prospect renders byte-identical for both.
    same = Touch(1, 1, "s", "Hi there,\n\nAbout {{Company}}.\n\nHenry")
    v = lint_same_company_divergence([same], rows)
    assert [x.rule for x in v] == ["same-company-identical-copy"]
    assert "2 contacts" in v[0].detail


def test_single_contact_per_company_is_never_flagged():
    rows = [_row(email="a@cascade.example"), _row(email="b@relay.example", company="Relay")]
    same = Touch(1, 1, "s", "Hi there,\n\nAbout {{Company}}.\n\nHenry")
    assert lint_same_company_divergence([same], rows) == []


# --- severity follows what the copy renders ------------------------------


def test_last_name_defect_is_advisory_when_the_copy_does_not_use_the_tag():
    errs = _errors([_row(last="Reece \U0001f947✨")])
    assert "last-name-symbols" not in errs


def test_last_name_defect_becomes_an_error_when_the_copy_renders_it():
    uses_last = Touch(1, 1, "subject", "Hi {{First Name}} {{Last Name}},\n\nAbout you.\n\nHenry")
    errs = _errors([_row(last="Reece \U0001f947✨")], touches=[uses_last])
    assert "last-name-symbols" in errs


def test_title_headline_becomes_an_error_when_the_copy_renders_it():
    uses_title = Touch(1, 1, "subject", "Hi {{First Name}},\n\nAs {{Job Title}}.\n\nHenry")
    assert "title-headline" not in _errors([_row(title="VP | Compliance")])
    assert "title-headline" in _errors([_row(title="VP | Compliance")], touches=[uses_title])


# --- template-wide collapse ----------------------------------------------


def _warns(rows, touches) -> list:
    violations, _ = lint_merge_render(touches, rows, signoff="Henry")
    return [v for v in violations if v.level == "WARN"]


def test_a_finding_on_every_render_collapses_to_one_template_wide_line():
    # cta-unanchored depends only on the template, so it fires on all N renders with the
    # same message. 334 identical lines bury the per-row findings that actually differ.
    t = Touch(
        1,
        1,
        "subject here",
        "Hi {{First Name}},\n\nMy read, tell me if you've got this covered: {{Company}} logs "
        "which account touched a record in 2026, not which agent held authority. Teams hit "
        "this when an agent acts rather than reads. Should I send the one-pager?\n\nHenry",
    )
    rows = [_row(email=f"a{i}@x.com", company="Cascade") for i in range(5)]
    # Severity-agnostic on purpose: this pins the COLLAPSE behaviour, not the rule's level
    # (cta-unanchored was promoted WARN -> ERROR on 2026-08-17).
    violations, _ = lint_merge_render([t], rows, signoff="Henry")
    hits = [v for v in violations if v.rule == "cta-unanchored"]
    assert len(hits) == 1
    assert "all 5 renders" in hits[0].email
    assert "template-wide" in hits[0].detail


def test_a_finding_on_only_some_renders_stays_itemized():
    # word-count varies with company-name length — genuinely per-row, so nothing collapses
    # and nothing is hidden.
    #
    # Severity-agnostic (like the collapse test above), and deliberately so since 2026-08-21:
    # the soft band moved 70-95 -> 65-97 to stop firing one word either side of real copy, so
    # a name that used to land in the WARN shoulder now lands over the hard cap instead. What
    # this test pins is the ITEMIZATION, never which level word-count happens to report at.
    touches = parse_spec(SPEC)
    rows = [
        _row(email="short@x.com", company="Relay"),  # 88 words — inside the band
        _row(
            email="long@x.com",
            company="Universal Property and Casualty Insurance Company of North America",
        ),  # 104 words
    ]
    violations, _ = lint_merge_render(touches, rows, signoff="Henry")
    hits = [v for v in violations if v.rule == "word-count"]
    assert hits, "expected the long company name to push a render outside the band"
    assert all("all " not in v.email for v in hits)
    assert any("long@x.com" in v.email for v in hits)
    assert not any("short@x.com" in v.email for v in hits)


def test_collapse_reports_variant_messages_when_a_rule_fires_on_every_row():
    # Same rule, same level, slightly DIFFERENT message per row — still one template problem.
    #
    # Driven by word-count rather than specificity since 2026-08-21. specificity's WARN band
    # is now [MIN_ANCHORS, SOFT_ANCHORS) = [2, 3), a single reachable value, so it can only
    # ever emit one message and cannot exercise the variant-message path at all. word-count
    # reports the actual count, so two company names of different lengths give two messages
    # for one template defect — which is the case this collapse logic exists to handle.
    touches = parse_spec(SPEC)
    rows = [
        _row(
            email="a@x.com",
            company="Universal Property and Casualty Insurance of North America",
        ),  # 102 words
        _row(
            email="b@x.com",
            company="Universal Property and Casualty Insurance Company of North America",
        ),  # 104 words
    ]
    violations, _ = lint_merge_render([touches[0]], rows, signoff="Henry")
    hits = [v for v in violations if v.rule == "word-count"]
    assert len(hits) == 1, [f"{v.email}: {v.detail}" for v in hits]
    assert "all 2 renders" in hits[0].email


# --- capacity ------------------------------------------------------------


def test_capacity_note_reports_the_send_horizon():
    note = capacity_note({"rows": 334, "touches": 3, "renders": 1002}, daily_cap=30)
    assert "1002 emails at 30/day" in note
    assert "33 working days" in note
    assert "6.7 weeks" in note


def test_capacity_note_is_silent_without_a_cap():
    assert capacity_note({"rows": 1, "touches": 1, "renders": 1}, daily_cap=0) == ""


# --- signal relevance, touch personalisation, hedge stem (added 2026-08-19) -----
#
# All three come out of the 60-recipient spot check of the Run-500 send. Each catches a
# defect that every per-render rule passes: the copy is grammatical, the merge fields are
# clean, and the email still reads as generated.


def _signal_touch(number: int = 1) -> Touch:
    return Touch(
        number,
        1,
        "subject",
        "Hi {{First Name}},\n\n{{Why Now}}.\n\nAbout the agents at {{Company}}.\n\nHenry",
    )


def test_off_topic_signal_clause_is_an_error():
    # The clause and the body are about different things, so the two paragraphs visibly
    # do not connect — the single most detectable "this is a template" tell.
    rows = [
        _row(
            email="off@x.com",
            signal_clause="Relay advises fintech and wealth firms on their sale transactions",
        )
    ]
    v = lint_signal_relevance([_signal_touch()], rows)
    assert [x.rule for x in v if x.level == "ERROR"] == ["signal-off-topic"]


def test_on_topic_signal_clause_passes():
    rows = [_row(email="on@x.com", signal_clause="Relay ships an agentic AI claims assistant")]
    assert not [x for x in lint_signal_relevance([_signal_touch()], rows) if x.level == "ERROR"]


def test_standing_description_is_only_a_warning():
    # True and specific, just not a dated trigger. Advisory: suppressing these would cost
    # more rows than the campaign can afford.
    rows = [_row(email="static@x.com", signal_clause="Relay runs an agentic AI claims platform")]
    v = lint_signal_relevance([_signal_touch()], rows)
    assert not [x for x in v if x.level == "ERROR"]
    assert [x.rule for x in v] == ["signal-not-an-event"]


def test_signal_relevance_is_silent_when_the_copy_never_renders_the_clause():
    plain = Touch(1, 1, "subject", "Hi {{First Name}},\n\nAbout {{Company}}.\n\nHenry")
    rows = [_row(email="off@x.com", signal_clause="Relay makes industrial fasteners in Ohio")]
    assert lint_signal_relevance([plain], rows) == []


def test_touch_that_varies_only_by_company_name_is_an_error():
    # 453 recipients received a byte-identical step 2 on the 08-18 send; one forward
    # between two of them ends the campaign.
    boilerplate = Touch(
        3,
        9,
        "closing the loop",
        "Hi {{First Name}},\n\n" + "Filler words here. " * 20 + "\n\nAt {{Company}}.\n\nHenry",
    )
    rows = [
        _row(email=f"a{i}@x.com", signal_clause="Relay ships an agentic AI claims assistant")
        for i in range(3)
    ]
    v = lint_touch_personalisation([boilerplate], rows)
    assert [x.rule for x in v] == ["touch-not-personalised"]


def test_same_thread_followup_is_exempt_from_the_personalisation_floor():
    # Its personalisation lives in the quoted thread above it; repeating the clause
    # verbatim three days later is its own tell.
    in_thread = Touch(
        2,
        4,
        "",
        "Hi {{First Name}},\n\n" + "Filler words here. " * 20 + "\n\nAt {{Company}}.\n\nHenry",
    )
    rows = [
        _row(email=f"a{i}@x.com", signal_clause="Relay ships an agentic AI claims assistant")
        for i in range(3)
    ]
    assert lint_touch_personalisation([in_thread], rows) == []


def test_touch_carrying_the_clause_is_personalised_enough():
    rows = [
        _row(email=f"a{i}@x.com", signal_clause="Relay ships an agentic AI claims assistant")
        for i in range(3)
    ]
    assert lint_touch_personalisation([_signal_touch(3)], rows) == []


def test_personalisation_rule_is_silent_when_no_row_carries_usable_signal():
    # A generic sequence has nothing to personalise with; failing it would block a
    # legitimate send rather than improve one.
    boilerplate = Touch(2, 4, "", "Hi {{First Name}},\n\nAt {{Company}}.\n\nHenry")
    assert lint_touch_personalisation([boilerplate], [_row(email="a@x.com")]) == []


def test_repeated_hedge_stem_across_touches_is_an_error():
    touches = [
        Touch(1, 1, "s", "Hi {{First Name}},\n\nMy read, and correct me if wrong: a.\n\nHenry"),
        Touch(2, 4, "", "Hi {{First Name}},\n\nMy hunch, and tell me: b.\n\nHenry"),
        Touch(3, 9, "s", "Hi {{First Name}},\n\nMy bet, and tell me: c.\n\nHenry"),
    ]
    v = lint_hedge_stem([(f"step {t.number}", t.body) for t in touches])
    assert [x.rule for x in v] == ["hedge-stem-repeat"]
    assert "3 touches" in v[0].detail


def test_one_hedge_stem_per_sequence_is_allowed():
    touches = [
        Touch(1, 1, "s", "Hi {{First Name}},\n\nMy read, and correct me if wrong: a.\n\nHenry"),
        Touch(2, 4, "", "Hi {{First Name}},\n\nYou may well have this covered.\n\nHenry"),
    ]
    assert lint_hedge_stem([(f"step {t.number}", t.body) for t in touches]) == []


def test_persona_mismatch_sourced_from_merged_data_is_not_the_copys_fault():
    # A recipient whose own signal clause contains "attribution" ("...CreatorOS for
    # creator commerce attribution") tripped the seat-lead rule on copy that led on
    # nothing of the sort.
    v = Violation("ERROR", "a@x.com", "persona-lead-mismatch", "exec seat led on (attribution) x")
    assert _is_data_borne(v, "sparklink raises a series b to expand creatoros for attribution")
    assert not _is_data_borne(v, "relay ships an agentic ai claims assistant")


def test_data_borne_filter_never_suppresses_a_mechanical_rule():
    v = Violation("ERROR", "a@x.com", "word-count", "104 words (hard 50-99)")
    assert not _is_data_borne(v, "104 words")


def test_a_digit_inside_the_company_name_is_not_a_stray_digit():
    # The no-digits contract exists to keep a date or metric out of an opener. Written as
    # a blanket ban it also excluded every company whose NAME carries a digit — K9 Risk
    # Solutions and B2Bnow both had a verified trigger and no sendable clause.
    rows = [
        _row(
            email="k9@x.com",
            company="K9 Risk Solutions",
            signal_clause="K9 Risk Solutions added EverSure AI powered merchant risk products",
        )
    ]
    v = lint_signal_relevance([_signal_touch()], rows)
    assert not [x for x in v if x.rule == "signal-stray-digit"]


def test_a_date_or_metric_in_the_clause_is_still_an_error():
    rows = [
        _row(
            email="k9@x.com",
            company="K9 Risk Solutions",
            signal_clause="K9 Risk Solutions raised 45 million for its AI merchant risk products",
        )
    ]
    v = lint_signal_relevance([_signal_touch()], rows)
    hits = [x for x in v if x.rule == "signal-stray-digit"]
    assert len(hits) == 1 and "45" in hits[0].detail
