"""Outreach pack linter — regression tests (rules 2026-07-16).

The linter exists because a cold-email pack can pass word-level lint while violating
the drafting spec (unhedged gaps, mail-merge stems, "teardown" CTA, high template
share). These tests pin the checks that catch that. Tenant-specific lists (case-study
names, banned stems) are file-driven and empty by default, so tests that exercise
those two rules pass explicit fixtures.
"""

from __future__ import annotations

import pytest
from outreach_pack_linter import (
    _GOOD,
    _PERSONA_RULES,
    _SEAT_RULES,
    NGRAM_N,
    RULES_VERSION,
    UNRESOLVED_SENTINEL,
    _artifact_count,
    _hedge_ngram_whitelist,
    _ngrams,
    _norm_tokens,
    _subject_shape,
    detect_format,
    lint_body_homogeneity,
    lint_formatted_pack,
    lint_pack,
    lint_subject_homogeneity,
    main,
    parse_draft_outreach_pack,
    parse_pack,
    parse_prospect_pack,
    persona_of,
    seat_of,
)


def _errors(text: str, **kwargs) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in lint_pack(text, **kwargs):
        if v.level == "ERROR":
            out.setdefault(v.rule, []).append(v.detail)
    return out


def test_good_block_passes() -> None:
    assert _errors(_GOOD) == {}


def test_missing_rules_version_fails_closed() -> None:
    text = _GOOD.replace(f"Rules-Version: {RULES_VERSION}\n\n", "")
    assert "rules-version-missing" in _errors(text)


def test_stale_rules_version_fails_closed() -> None:
    text = _GOOD.replace(RULES_VERSION, "2026-07-14")
    assert "rules-version-stale" in _errors(text)


def test_mail_merge_skeleton_is_rejected() -> None:
    stem = "share one service account per task"
    body = (
        "Hi Jane,\n\n"
        "Acme is shipping agents past the demo. My hunch, tell me if you've got this "
        "covered: those agents likely share one service account per task, so when one "
        "delegates to another, no one can tell which agent acted. "
        "Worth me sending the teardown?\n\nAlex"
    )
    text = (
        f"Rules-Version: {RULES_VERSION}\n\n"
        "### 1. Jane Doe · CEO, Acme\n**To:** jane@acme.com\n**Subject:** shipping agents\n\n"
        f"{body}\n\n---\n"
    )
    errs = _errors(text, banned_stems=(stem,))
    assert "banned-stem" in errs
    # cta-teardown was retired 2026-08-17: banning one word rejected a specific, producible
    # "one-page teardown" while waving through any other unproducible artifact. The gate is now
    # the profile's gift-artifacts list, tested below.
    assert "cta-teardown" not in errs


def test_offer_must_name_an_artifact_the_profile_can_produce() -> None:
    arts = ("one-pager", "teardown", "recorded demo")
    bad = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Want the benchmark report on how peers compare here?",
    )
    assert "cta-unstaged-artifact" in _errors(bad, gift_artifacts=arts)
    assert "cta-unstaged-artifact" not in _errors(_GOOD, gift_artifacts=arts)


def test_a_producible_teardown_is_allowed() -> None:
    """The word itself was never the problem; an unproducible artifact was."""
    text = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Want the one-page teardown of how that clears the review?",
    )
    assert "cta-unstaged-artifact" not in _errors(text, gift_artifacts=("teardown",))


def test_pure_interest_ask_needs_no_artifact() -> None:
    """ "Is X on your radar this quarter?" gates nothing and is a legitimate cold CTA."""
    text = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Is per-agent identity even on your radar this quarter?",
    )
    assert "cta-unstaged-artifact" not in _errors(text, gift_artifacts=("one-pager",))


def test_unhedged_gap_fails() -> None:
    text = _GOOD.replace("My bet on the open piece:", "The open piece:")
    assert "hedge-missing" in _errors(text)


def test_template_share_ceiling_across_emails() -> None:
    shared = (
        "the same six gram sentence repeated verbatim across many emails proves the "
        "skeleton survived, and a second shared clause rides along here too. My hunch, "
        "tell me if you've got this covered: something structural. "
        "Acme2000 shipped Widget3000 last May. Want the one-pager?"
    )
    blocks = []
    for i, (first, dom) in enumerate(
        [("Ann", "a.com"), ("Bob", "b.com"), ("Cat", "c.com"), ("Dan", "d.com")], start=1
    ):
        blocks.append(
            f"### {i}. {first} X · CEO, {dom}\n**To:** {first.lower()}@{dom}\n"
            f"**Subject:** subject {'one two three four'.split()[i - 1]}\n\n"
            f"Hi {first},\n\n{shared}\n\nAlex\n\n---\n"
        )
    text = f"Rules-Version: {RULES_VERSION}\n\n" + "\n".join(blocks)
    assert "template-share" in _errors(text)


def test_same_company_clone_pair_rejected() -> None:
    body = (
        "Your four agents off the Series C put autonomous actors on regulated flows. "
        "My read, tell me if you've got this covered: the Activant2025 raise and the "
        "KYC4 rollout leave attribution open once agents delegate. Zest9 proved the "
        "shape with examiner-grade trails. Want the one-pager plus a short demo?"
    )
    text = (
        f"Rules-Version: {RULES_VERSION}\n\n"
        "### 1. Ann One · CISO, Acme\n**To:** ann@acme.com\n**Subject:** your four agents\n\n"
        f"Hi Ann,\n\n{body}\n\nAlex\n\n---\n\n"
        "### 2. Bob Two · GC, Acme\n**To:** bob@acme.com\n**Subject:** your four agents\n\n"
        f"Hi Bob,\n\n{body}\n\nAlex\n\n---\n"
    )
    errs = _errors(text)
    assert "same-company-subject" in errs
    assert "same-company-overlap" in errs


def test_time_ask_token_is_word_boundary_not_substring() -> None:
    """The 2026-08-12 Hanover Insurance false positive: "book a" matched inside
    "excess and surplus book and workbench" because "book a" is literally a substring
    of "book and". A real cold-open with that phrase must not trip time-ask."""
    body = (
        "Your excess and surplus book and workbench modernization reads like a trust bet. "
        "My hunch, tell me if you've got this covered: none of those agents prove which one "
        "acted. Want the one-pager plus a short recording?"
    )
    text = (
        f"Rules-Version: {RULES_VERSION}\n\n"
        "### 1. Robin Vega · CISO, Acme Surety\n**To:** robin@acmesurety.example\n"
        "**Subject:** book and workbench\n\n"
        f"Hi Robin,\n\n{body}\n\nAlex\n\n---\n"
    )
    assert "time-ask" not in _errors(text)


def test_time_ask_token_still_fires_on_a_real_ask() -> None:
    body = (
        "Your excess and surplus book modernization reads like a trust bet. My hunch, "
        "tell me if you've got this covered: none of those agents prove which one acted. "
        "Worth grabbing 15 minutes this week?"
    )
    text = (
        f"Rules-Version: {RULES_VERSION}\n\n"
        "### 1. Robin Vega · CISO, Acme Surety\n**To:** robin@acmesurety.example\n"
        "**Subject:** book modernization\n\n"
        f"Hi Robin,\n\n{body}\n\nAlex\n\n---\n"
    )
    assert "time-ask" in _errors(text)


def test_parse_pack_shape() -> None:
    version, blocks = parse_pack(_GOOD)
    assert version == RULES_VERSION
    assert len(blocks) == 1
    assert blocks[0].first == "Dana"
    assert blocks[0].to == "dana@acmerobotics.dev"


# --------------------------------------------------------------------------- CTA bundling
#
# The 2026-08-17 audit: 22 of 50 packs asked for two artifacts in one question, because
# voice.md's own worked examples all bundled ("want the one-pager + a short recorded demo?").
# The failure mode of a naive fix is the opposite — counting "a short demo recording" as two.


def test_artifact_count_treats_multiword_artifact_names_as_one() -> None:
    for single in (
        "Want the one-pager?",
        "Want a short recorded demo of that?",
        "Want the demo recording?",
        "Should I send the short write-up?",
        "Would a recorded walkthrough be useful?",
        "Want it?",
    ):
        assert _artifact_count(single) <= 1, single


def test_artifact_count_flags_a_genuine_bundle() -> None:
    for bundled in (
        "Would the one-pager and a short recorded demo be useful?",
        "Want the one-pager + a demo?",
        "Want the one-pager plus a short demo recording?",
    ):
        assert _artifact_count(bundled) > 1, bundled


def test_bundled_cta_is_rejected() -> None:
    text = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Want the one-pager and a short recorded demo of per-run attribution?",
    )
    assert "cta-bundled" in _errors(text)


def test_short_call_is_a_time_ask() -> None:
    """ "Worth a short call?" shipped in the 2026-07-19 run: every listed token assumed
    the word "quick" or a calendar noun, so an adjective-wearing meeting ask slipped past."""
    text = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Worth a short call?",
    )
    assert "time-ask" in _errors(text)


# --------------------------------------------------------------------------- subject shapes


def test_subject_shape_collapses_possessive_templates() -> None:
    for subject in (
        "deepagent's missing primitive",
        "cursor 2.0's missing primitive",
        "the agent mesh's missing primitive",
        "ai labs' missing primitive",
    ):
        assert _subject_shape(subject) == "missing primitive", subject


def test_subject_template_share_fires_only_past_the_ceiling() -> None:
    subjects = [
        ("a.md", "deepagent's missing primitive"),
        ("b.md", "gravity's missing primitive"),
        ("c.md", "fusion's missing primitive"),
        ("d.md", "langgraph's missing primitive"),
        ("e.md", "who decided the merchant"),
    ]
    rules = {v.rule for v in lint_subject_homogeneity(subjects, ceiling=3)}
    assert rules == {"subject-template-share"}
    assert not lint_subject_homogeneity(subjects[:3] + subjects[4:], ceiling=3)


# --------------------------------------------------------------------------- new pack formats

_DRAFT_OUTREACH = f"""# Cold email — Acme Robotics — 2026-07-03

Rules-Version: {RULES_VERSION}

- **To:** Dana Rivera, Head of Platform — dana@acmerobotics.dev
- **Why-now:** Series B and 12 agents in production (2026-06-01)

---

**Subject:** agent audit trail

Hi Dana,

Shipping autonomous agents into production right after the Series B puts 12 agents calling internal tools directly, with every run logged. My bet on the open piece: those logs prove what ran, not which agent held the credential once one hands off to another. EU AI Act Article 12 lands that on your customers by 2027, and today each of them rebuilds the answer per deployment. The primitives for that are already standardised, so it lands as configuration rather than a build. Want the one-pager on per-agent identity at the tool boundary?

Alex

---

**Word count:** 95
"""

_PROSPECT_PACK = f"""# Outreach Pack — Acme Robotics — 2026-07-19

Rules-Version: {RULES_VERSION}

**Tier:** A | **Score:** 9/12 | **Segment:** Enterprise | **Market:** United States
**Primary persona:** Dana Rivera — Head of Platform

---
## LinkedIn DM (≤ 280 chars)
> Dana — the Series B and 12 agents in production read like a trust bet. Worth a look?

---
## Email (touch 1)
**Subject:** agent audit trail
> Hi Dana,
>
> Shipping autonomous agents into production right after the Series B puts 12 agents calling internal tools directly, with every run logged. My bet on the open piece: those logs prove what ran, not which agent held the credential once one hands off to another. EU AI Act Article 12 lands that on your customers by 2027, and today each of them rebuilds the answer per deployment. The primitives for that are already standardised, so it lands as configuration rather than a build. Want the one-pager on per-agent identity at the tool boundary?
>
> Alex

**Word count:** 95 / 100

---
## Sequence — 4 touches / 2 channels over ~12 days
- **Touch 1 — Day 0 — LinkedIn:** connection request using the DM above.
- **Touch 2 — Day 2 — Email:** the email above.
- **Touch 3 — Day 5 — Email:** different-angle insight, same signal.
- **Touch 4 — Day 12 — LinkedIn:** gift the artifact anyway, then park.
"""


def _fmt_errors(text: str, fmt: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in lint_formatted_pack(text, fmt)[0]:
        if v.level == "ERROR":
            out.setdefault(v.rule, []).append(v.detail)
    return out


def test_format_autodetection() -> None:
    assert detect_format(_GOOD) == "tier-a-manual"
    assert detect_format(_PROSPECT_PACK) == "prospect-pack"
    assert detect_format(_DRAFT_OUTREACH) == "draft-outreach"


def test_clean_packs_in_both_new_formats_pass() -> None:
    assert _fmt_errors(_DRAFT_OUTREACH, "draft-outreach") == {}
    assert _fmt_errors(_PROSPECT_PACK, "prospect-pack") == {}


def test_parsers_extract_the_touch_one_email_only() -> None:
    _, blocks, meta = parse_draft_outreach_pack(_DRAFT_OUTREACH)
    assert blocks[0].first == "Dana" and blocks[0].subject == "agent audit trail"
    assert meta.stated_words == 95

    _, blocks, meta = parse_prospect_pack(_PROSPECT_PACK)
    assert blocks[0].first == "Dana" and blocks[0].body.endswith("Alex")
    assert meta.declared_touches == 4 and meta.enumerated_touches == 4
    assert meta.touch_channels[0] == "LinkedIn"


def test_attachment_on_cold_first_touch_is_rejected() -> None:
    """Every one of 50 batch-1 drafts attached a teaser PDF to touch 1, against a rule
    that was already documented — nothing mechanical was enforcing it."""
    with_header = _DRAFT_OUTREACH.replace(
        "- **Why-now:**", "- **Attach:** `teaser-acme.pdf`\n- **Why-now:**"
    )
    assert "attachment-on-touch1" in _fmt_errors(with_header, "draft-outreach")

    in_body = _DRAFT_OUTREACH.replace(
        "Want the one-pager on", "I put together a short read (attached). Want the one-pager on"
    )
    assert "attachment-on-touch1" in _fmt_errors(in_body, "draft-outreach")


def test_self_reported_word_count_must_match_the_body() -> None:
    text = _DRAFT_OUTREACH.replace("**Word count:** 95", "**Word count:** 60")
    assert "word-count-mismatch" in _fmt_errors(text, "draft-outreach")


def test_word_count_tolerance_allows_a_small_drift() -> None:
    text = _DRAFT_OUTREACH.replace("**Word count:** 95", "**Word count:** 97")
    assert "word-count-mismatch" not in _fmt_errors(text, "draft-outreach")


def test_touch_count_over_the_ladder_ceiling_is_rejected() -> None:
    text = _PROSPECT_PACK.replace("## Sequence — 4 touches", "## Sequence — 5 touches")
    assert "touch-count" in _fmt_errors(text, "prospect-pack")


def test_declared_and_enumerated_touch_counts_must_agree() -> None:
    text = _PROSPECT_PACK.replace(
        "- **Touch 4 — Day 12 — LinkedIn:** gift the artifact anyway, then park.\n", ""
    )
    assert "touch-count" in _fmt_errors(text, "prospect-pack")


def test_email_before_linkedin_warns_on_channel_order() -> None:
    text = _PROSPECT_PACK.replace(
        "- **Touch 1 — Day 0 — LinkedIn:** connection request using the DM above.",
        "- **Touch 1 — Day 0 — Email:** the email above.",
    )
    rules = {v.rule for v in lint_formatted_pack(text, "prospect-pack")[0] if v.level == "WARN"}
    assert "channel-order" in rules


# --------------------------------------------------------------------------- unresolved contact
#
# 28 of 50 packs shipped a literal `{{First Name}}` greeting. A pack with no resolved contact
# is a known-incomplete draft; one check should say so instead of a dozen downstream findings.


def test_merge_tag_in_a_hand_sent_greeting_is_an_error() -> None:
    text = _PROSPECT_PACK.replace("> Hi Dana,", "> Hi {{First Name}},")
    assert "placeholder" in _fmt_errors(text, "prospect-pack")


def test_unresolved_sentinel_reports_once_and_does_not_cascade() -> None:
    """The sentinel must not cascade into NAME-derived errors (greeting mismatch, bad-name).

    It originally asserted the sentinel produced *exactly one* violation full stop, which also
    suppressed every content rule — the fail-open hole that left 105 of 390 packs unchecked.
    The no-cascade intent is preserved; the content-masking side effect is not.
    """
    text = _PROSPECT_PACK.replace("> Hi Dana,", f"> Hi {UNRESOLVED_SENTINEL},")
    violations = lint_formatted_pack(text, "prospect-pack")[0]
    unresolved = [v for v in violations if v.rule == "unresolved-contact"]
    # ERROR since 2026-08-27: as a WARN this exited 0, so a pack whose greeting still read
    # "Hi <unresolved>," linted green and reported as sendable. The rule's own message
    # already said the pack could not send.
    assert len(unresolved) == 1 and unresolved[0].level == "ERROR"
    assert not {"greeting", "greeting-not-a-name"} & {v.rule for v in violations}


def test_honorific_is_not_a_first_name() -> None:
    """A persona of "Dr Hongjuan Liu" greets Hongjuan; requiring "Hi Dr," would be wrong."""
    text = _PROSPECT_PACK.replace(
        "**Primary persona:** Dana Rivera — Head of Platform",
        "**Primary persona:** Dr Dana Rivera — Head of Platform",
    )
    _, blocks, _ = parse_prospect_pack(text)
    assert blocks[0].first == "Dana"


def test_body_template_share_catches_a_batch_wide_phrase() -> None:
    """46 of 50 packs opened the gap with the same hedge wording in the 2026-07-19 run.
    Every one was individually legal, so only a cross-file check can see it."""
    shared = "my read tell me if this is already handled the agents"
    bodies = [(f"{i}.md", f"Hi Dana, {shared} do something at company{i}.") for i in range(5)]
    rules = {v.rule for v in lint_body_homogeneity(bodies, ceiling=3)}
    assert rules == {"body-template-share"}
    assert not lint_body_homogeneity(bodies[:3], ceiling=3)


def test_shared_phrase_exempts_product_vocabulary_only() -> None:
    """A 390-account run describes ONE product; the pitch recurring is consistency, not a
    pasted template. Declared product/case-study phrasing is exempt from the 6-gram ceiling."""
    pitch = "acme gateway gives each agent a verifiable identity"
    # Realistic shape: the SAME pitch sentence, wrapped in prose genuinely written per account,
    # so the pitch is the only thing that repeats. Note the surrounding words must differ too —
    # a 6-gram straddling the phrase boundary contains the author's own words and is NOT exempt,
    # which is the point: only the pitch is shared vocabulary, its framing is still personalisation.
    lead = [
        "after the Series B",
        "once the pilot closed",
        "before the audit lands",
        "as the rollout widened",
        "while procurement stalled",
    ]
    tail = [
        "across claims.",
        "inside underwriting.",
        "for the payments team.",
        "on the trading desk.",
        "throughout onboarding.",
    ]
    bodies = [(f"{i}.md", f"Hi Dana, {lead[i]} {pitch} {tail[i]}") for i in range(5)]
    assert lint_body_homogeneity(bodies, ceiling=3), "unexempted product pitch should still trip"
    assert not lint_body_homogeneity(bodies, ceiling=3, shared_phrases=(pitch,))


def test_shared_phrase_does_not_wave_through_a_templated_sentence() -> None:
    """The exemption is per-6-gram, not per-file: a 6-gram is skipped only when it falls wholly
    inside a declared phrase. A pasted sentence that merely name-drops the product still trips,
    otherwise declaring one phrase would silently disable the gate around it."""
    pitch = "acme gateway gives each agent a verifiable identity"
    template = f"we think {pitch} is the missing primitive in your stack today"
    bodies = [(f"{i}.md", f"Hi Dana, {template}") for i in range(5)]
    assert lint_body_homogeneity(bodies, ceiling=3, shared_phrases=(pitch,)), (
        "the templated words AROUND the exempt phrase must still be caught"
    )


def test_persona_lead_mismatch_fires_on_a_founder_led_with_audit_pain() -> None:
    """A 2026-08-17 re-draft sent auditor/examiner framing to eight CEOs. The seat sets the
    lead pain (voice.md persona-axis); attribution is the CISO's row, not a founder's."""
    text = _GOOD.replace(
        "### 1. Dana Rivera · Head of Platform, Acme Robotics",
        "### 1. Dana Rivera · Co-Founder & CEO, Acme Robotics",
    ).replace("rebuilds the answer per deployment", "cannot answer the auditor")
    assert "persona-lead-mismatch" in _errors(text)


def test_persona_lead_ok_when_the_seat_owns_that_pain() -> None:
    text = _GOOD.replace(
        "### 1. Dana Rivera · Head of Platform, Acme Robotics",
        "### 1. Dana Rivera · CISO, Acme Robotics",
    )
    assert "persona-lead-mismatch" not in _errors(text)


def test_unrecognised_title_never_fires_persona_mismatch() -> None:
    """Silence is the safe default: an unknown seat says nothing about the lead pain."""
    text = _GOOD.replace(
        "### 1. Dana Rivera · Head of Platform, Acme Robotics",
        "### 1. Dana Rivera · Regional Lead, Acme Robotics",
    ).replace("rebuilds the answer per deployment", "cannot answer the auditor")
    assert "persona-lead-mismatch" not in _errors(text)


def test_bare_artifact_ask_is_an_error() -> None:
    """ "Want the one-pager?" makes the reader reconstruct the value from the paragraph above."""
    text = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?", "Want the one-pager?"
    )
    assert "cta-unanchored" in _errors(text)


def test_ask_naming_its_outcome_passes() -> None:
    text = _GOOD.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Want the teardown of how that clears the review?",
    )
    assert "cta-unanchored" not in _errors(text)


def test_touch1_heading_tolerates_a_recipient_suffix() -> None:
    """Supplement packs write `## Email (touch 1, to Gregory Wright)`. Splitting on the bare
    literal left two real packs unparsed — and so never linted at all, while the `parse` error
    made it look like the FILE was malformed rather than the linter blind."""
    text = _PROSPECT_PACK.replace("## Email (touch 1)", "## Email (touch 1, to Dana Rivera)")
    _, blocks, _ = parse_prospect_pack(text)
    assert blocks, "a recipient suffix on the heading must still parse"
    assert blocks[0].subject == "agent audit trail"


def test_addendum_without_an_email_is_not_a_parse_error() -> None:
    """A `-b.md` contact-resolution addendum records a resolved persona for a pack that lives in
    another file; it carries no email. That is not a malformed pack."""
    addendum = (
        "# Outreach Pack — Acme — 2026-07-19 — Contact Resolution Addendum\n\n"
        f"Rules-Version: {RULES_VERSION}\n\n"
        "## Resolved contact — Head of Platform row\nDana Rivera, dana@acme.dev\n"
    )
    violations, blocks = lint_formatted_pack(addendum, "prospect-pack", signoff="Henry")
    assert not blocks
    assert not [v for v in violations if v.level == "ERROR"]
    assert {v.rule for v in violations} == {"not-an-outreach-pack"}


def test_declared_email_section_that_fails_to_parse_is_still_an_error() -> None:
    """The addendum carve-out must not swallow a real parse failure: if a document declares a
    touch-1 email, failing to read it means unlinted copy would ship."""
    broken = (
        "# Outreach Pack — Acme — 2026-07-19\n\n"
        f"Rules-Version: {RULES_VERSION}\n\n"
        "## Email (touch 1)\n(the subject line and body are missing)\n"
    )
    violations, blocks = lint_formatted_pack(broken, "prospect-pack", signoff="Henry")
    assert not blocks
    assert "parse" in {v.rule for v in violations if v.level == "ERROR"}


def test_touch1_heading_tolerates_a_trailing_annotation() -> None:
    """`## Email (touch 1) — formal/standard register`. Anchoring the heading regex to end of
    line was STRICTER than the literal substring match it replaced, and silently un-linted a
    pack that had previously parsed."""
    text = _PROSPECT_PACK.replace(
        "## Email (touch 1)", "## Email (touch 1) — formal/standard register"
    )
    _, blocks, _ = parse_prospect_pack(text)
    assert blocks, "a register annotation after the heading must still parse"
    assert blocks[0].subject == "agent audit trail"


def test_greeting_name_must_be_a_name_not_a_broken_persona_field() -> None:
    """The `greeting` rule compares the greeting to the persona field it was derived from, so a
    broken persona validates its own broken greeting. A 2026-07-19 pack whose persona read
    "*name unconfirmed to publish*" produced "Hi *name," and passed. Thirteen packs were one
    send from that. This rule is the independent witness."""
    text = _PROSPECT_PACK.replace(
        "**Primary persona:** Dana Rivera — Head of Platform",
        "**Primary persona:** *name unconfirmed to publish standard*",
    ).replace("Hi Dana,", "Hi *name,")
    violations, _ = lint_formatted_pack(text, "prospect-pack", signoff="Alex")
    rules = {v.rule for v in violations if v.level == "ERROR"}
    assert "greeting-not-a-name" in rules
    assert "greeting" not in rules, "the greeting itself is self-consistent — that is the trap"


def test_accented_and_hyphenated_first_names_are_accepted() -> None:
    """An ASCII-only name rule would reject Joaquín and Josué — real recipients in this batch.
    Breaking real names to catch placeholders would be a worse bug than the one being fixed."""
    for name in ("Joaquín", "Josué", "Jean-Luc", "O'Brien", "Åsa"):
        text = _PROSPECT_PACK.replace(
            "**Primary persona:** Dana Rivera — Head of Platform",
            f"**Primary persona:** {name} Rivera — Head of Platform",
        ).replace("Hi Dana,", f"Hi {name},")
        violations, _ = lint_formatted_pack(text, "prospect-pack", signoff="Alex")
        assert "greeting-not-a-name" not in {v.rule for v in violations}, name


def test_unresolved_sentinel_is_not_flagged_as_a_bad_name() -> None:
    """The sentinel is the CORRECT way to say "not resolved yet" — it already warns separately."""
    text = _PROSPECT_PACK.replace(
        "**Primary persona:** Dana Rivera — Head of Platform",
        f"**Primary persona:** {UNRESOLVED_SENTINEL} — Head of Platform",
    ).replace("Hi Dana,", f"Hi {UNRESOLVED_SENTINEL},")
    violations, _ = lint_formatted_pack(text, "prospect-pack", signoff="Alex")
    assert "greeting-not-a-name" not in {v.rule for v in violations}


def test_unresolved_contact_still_lints_the_body() -> None:
    """The sentinel suppresses the NAME rules, never the CONTENT rules. Short-circuiting the
    whole rule set left 105 of 390 packs in the 2026-07-19 run unchecked — fail-open, with the
    defects surfacing only once someone filled the name in and nobody re-linted."""
    text = _PROSPECT_PACK.replace(
        "**Primary persona:** Dana Rivera — Head of Platform",
        f"**Primary persona:** {UNRESOLVED_SENTINEL} — Head of Platform",
    ).replace("Hi Dana,", f"Hi {UNRESOLVED_SENTINEL},")
    # break the body in a way that has nothing to do with the name
    text = text.replace(
        "Want the one-pager on per-agent identity at the tool boundary?",
        "Worth grabbing 15 minutes this week?",
    )
    violations, _ = lint_formatted_pack(text, "prospect-pack", signoff="Alex")
    rules = {v.rule for v in violations}
    assert "unresolved-contact" in rules, "the sentinel must still be reported"
    assert "time-ask" in {v.rule for v in violations if v.level == "ERROR"}, (
        "a body defect must still be caught while the contact is unresolved"
    )


def test_unresolved_contact_suppresses_only_the_name_rules() -> None:
    """The sentinel IS the correct greeting until a name lands — it must not read as a defect."""
    text = _PROSPECT_PACK.replace(
        "**Primary persona:** Dana Rivera — Head of Platform",
        f"**Primary persona:** {UNRESOLVED_SENTINEL} — Head of Platform",
    ).replace("Hi Dana,", f"Hi {UNRESOLVED_SENTINEL},")
    violations, _ = lint_formatted_pack(text, "prospect-pack", signoff="Alex")
    rules = {v.rule for v in violations if v.level == "ERROR"}
    assert "greeting" not in rules
    assert "greeting-not-a-name" not in rules


# --- the persona axis (H3: one vocabulary, two views) --------------------------------


@pytest.mark.parametrize(
    ("title", "persona", "seat"),
    [
        ("Chief Information Security Officer", "ciso", "security"),
        ("Vice President & Chief Information Security Officer", "ciso", "security"),
        ("Chief Data & Analytics Officer", "data-compliance", "security"),
        ("Chief Risk Officer", "compliance", "security"),
        ("Chief Technology Officer", "cto", "cto"),
        ("Chief Information Officer", "cloud-architect", "architect"),
        ("Enterprise Architect", "cloud-architect", "architect"),
        ("Chief AI Officer", "ai-platform", "ai-platform"),
        ("Head of AI Platform", "ai-platform", "ai-platform"),
        ("Chief Product Officer", "cpo", "product"),
        ("CEO", "ceo", "ceo"),
        ("President and CEO", "ceo", "ceo"),
        ("President", "ceo", "ceo"),
    ],
)
def test_persona_and_seat_resolve_together(title, persona, seat):
    assert persona_of(title) == persona
    assert seat_of(title) == seat


@pytest.mark.parametrize(
    "title",
    [
        "Chief Technology Officer and Executive Vice President",
        "Senior Vice President, Chief Technology Officer",
        "Executive Vice President & Chief Technology Officer",
    ],
)
def test_a_vice_president_who_is_really_a_cto_is_seated_as_a_cto(title):
    """The bare "president" cue swept 28 CTOs into the exec bucket (found 2026-08-20).

    Order fixes the ones carrying a seat marker; ``_ANTI_CUES`` catches the rest. 70 of
    the 123 live titles containing "president" are VICE-presidents.
    """
    assert persona_of(title) == "cto"
    assert seat_of(title) == "cto"


def test_a_bare_vice_president_with_no_seat_marker_stays_unresolved():
    """Fail-quiet beats guessing: an EVP of nothing-we-recognise is not a CEO."""
    assert persona_of("Executive Vice President, Mission Engagement") is None
    assert seat_of("Executive Vice President, Mission Engagement") is None


@pytest.mark.parametrize("title", ["FinOps Lead", "Head of Partnerships", "Head of Ecosystem"])
def test_seatless_personas_resolve_but_claim_no_seat(title):
    """finops and partnership are recognised so they can never be invisible, but hold 0
    and 1 recipients in the whole pool — so no copy is owed and no seat is claimed."""
    assert persona_of(title) is not None
    assert seat_of(title) is None


@pytest.mark.parametrize("title", ["", "Barista", "Chief Happiness Officer"])
def test_an_unrecognised_title_says_nothing(title):
    assert persona_of(title) is None
    assert seat_of(title) is None


def test_every_seat_persona_is_a_real_persona_and_has_stakes_vocabulary():
    """The two views must stay joinable: a seat naming a persona the resolver cannot
    produce would be a bucket nothing ever lands in, and lint_persona_lead would raise
    on a seat with no stakes words."""
    known = {p for p, _ in _PERSONA_RULES}
    for seat, personas, stakes in _SEAT_RULES:
        assert personas, f"{seat} covers no persona"
        assert stakes, f"{seat} has no own-stakes vocabulary"
        for persona in personas:
            assert persona in known, f"{seat} claims unknown persona {persona!r}"


def test_no_persona_is_claimed_by_two_seats():
    seen: set[str] = set()
    for _seat, personas, _ in _SEAT_RULES:
        for persona in personas:
            assert persona not in seen, f"{persona!r} is claimed by two seats"
            seen.add(persona)


# --- an easy out is not a hedge --------------------------------------------- #
# "No reply needed" releases the reader from replying; it does not qualify the claim
# it follows. Treating it as a hedge cue let a body making a hard, unqualified
# assertion satisfy `hedge-missing` by appending a closing courtesy.


def test_no_reply_needed_does_not_satisfy_the_hedge_gate() -> None:
    text = _GOOD.replace("My bet on the open piece:", "The open piece:")
    text = text.replace("Worth a look?", "Worth a look? No reply needed.")
    assert "hedge-missing" in _errors(text), "an easy out passed as a hedge on a hard claim"


def test_a_real_hedge_still_satisfies_the_gate() -> None:
    assert "hedge-missing" not in _errors(_GOOD)


def test_no_reply_needed_is_still_exempt_from_homogeneity_dedup() -> None:
    """It stays whitelisted where it belongs: shared closing phrasing is not templating."""
    wl = _hedge_ngram_whitelist()
    toks = _norm_tokens("no reply needed")
    assert _ngrams(toks, NGRAM_N) <= wl


# --- the WARN tier is budgeted (B9) ----------------------------------------- #
# `main` returned `1 if errors else 0`, so any number of warnings exited green.
# A wall of warnings reads as "noisy but fine" and the findings that mattered ship
# with the batch — 388 of them once did exactly that.


def _run_main(tmp_path, text, extra=()):
    pack = tmp_path / "pack.md"
    pack.write_text(text, encoding="utf-8")
    return main([str(pack), "--format", "prospect-pack", *extra])


def test_an_unresolved_greeting_makes_the_pack_fail(tmp_path):
    text = _PROSPECT_PACK.replace("> Hi Dana,", f"> Hi {UNRESOLVED_SENTINEL},")
    assert _run_main(tmp_path, text) == 1, "a pack that cannot send exited green"


def test_a_clean_pack_still_passes(tmp_path):
    """Positive control — the gate must not fail everything."""
    assert _run_main(tmp_path, _PROSPECT_PACK) == 0
