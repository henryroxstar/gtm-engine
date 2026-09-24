"""Mutation suite — one crafted bad input per rule, proving every rule in
``RULE_CATALOGUE`` actually fires (email-eval-calibration PRD, P0.5).

The gap this file closes: before 2026-08-20, only 25 of ``RULE_CATALOGUE``'s (then
mis-documented) 43 entries had ANY assertion anywhere in the test suite that they fire on
a bad input. A rule with no negative control is indistinguishable from a dead rule — both
produce silence on clean input, and this pipeline had never told the two apart. Worse: the
catalogue itself was wrong. 5 documented entries ("company", "signoff", "word-count-
mismatch", "same-company-subject", "same-company-overlap") can never fire from this
module's entry point at all, and 26 rules this module fires on every real run — mechanical
``lint_email`` hygiene checks plus most of ``check_row``'s field defects — were entirely
undocumented. See ``merge_render_linter.RULE_CATALOGUE``'s header comment for the full
accounting; this file is the mutation half of that fix.

Every test below crafts the SMALLEST possible deviation from a known-clean baseline that
triggers exactly one rule, then asserts that rule name appears in ``lint_merge_render``'s
violations (any level — WARN is still proof of life; only ``account_integrity``-style
gates need to distinguish ERROR from WARN, and that is not this suite's job). The final
test, ``test_every_catalogue_rule_has_a_mutation``, is the completeness backstop: it fails
loudly the moment a 67th rule is added to either call path without a matching mutation
here, which is the same "admission test" principle the calibration PRD applies to new
lint rules generally.

2026-08-20 (hook-coverage PRD, H2): 64 -> 66 with ``hook-cell-missing`` and
``hook-cell-unknown``. **Both retired 2026-09-24 (FR3)** into ``angle-missing`` /
``angle-unknown``; the entry is kept because the lesson below outlived the two rules, but
neither id ships any more. At the time they were opt-in behind ``hook_matrix``, so they shipped
with three extra
controls beyond the two mutations — the real-cell case, the no-matrix off-state, and the
unsupported-matrix-shape WARN. An opt-in rule needs its off-state pinned as much as its
firing state: ``cta-unstaged-artifact`` was a correct rule nobody had opted into.

2026-08-26 (cold-email craft evidence, docs/cold-email-craft-evidence.md 5.5): 75 -> 77 with
``sentence-length`` and ``question-count``. Both are readability rules the catalogue had no
cover for at all — `word-count` gates how LONG a body is and says nothing about whether it can
be skimmed, and `cta-question` inspects only the final sentence and so never counted the
others. Thresholds were calibrated against 103 real first touches parsed out of ``content/``
rather than set at the published ideal; see the constants in ``outreach_pack_linter``. Note
``question-count`` fires on nothing in that corpus (observed max is 2 of a permitted 3) and is
registered as a regression guard, not as a catch — a rule that admits this is more useful than
one that implies a defect it never found.
"""

from __future__ import annotations

import pytest
from outreach import (
    _DATA_BORNE_ELIGIBLE,
    RULE_CATALOGUE,
    Touch,
    lint_merge_render,
    parse_spec,
)

# --------------------------------------------------------------------------- fixtures

GOOD_ROW = {
    "first": "Chris",
    "last": "Renner",
    "email": "chris@cascade.example",
    "company": "Cascade",
    "company_domain": "cascade.example",
    "title": "CISO",
    # On-topic (mentions "agent"), well-formed length, no event verb (a standing
    # description) and no stray digit — deliberately the WEAKEST legal clause, so
    # `signal-not-an-event` fires in the baseline itself. That is fine: it is a WARN,
    # every other test still passes cleanly around it, and it keeps this fixture from
    # silently depending on the event-verb list too.
    "signal_clause": "Cascade sits on the BRIGHTPATH agent-runtime-security working group",
}


def _row(**kw) -> dict:
    return {**GOOD_ROW, **kw}


# Two touches: step 1 opens a new thread (has a subject, renders {{Why Now}}), step 2 is
# a same-thread follow-up (no subject — exempt from the personalisation floor). Both bodies
# carry >= SOFT_ANCHORS (4) real mid-sentence anchors and sit inside the 70-95 soft word
# band, verified against the actual `_anchors()`/word-count logic while drafting, then
# re-checked by `test_baseline_is_clean_of_every_construction_and_substance_rule` below —
# word/anchor counts are exactly the kind of thing that drifts silently under hand-editing.
def _blockquote(text: str) -> str:
    """Prefix every line of a plain multi-paragraph string with the markdown blockquote
    marker ``parse_spec`` requires — a blank line becomes a bare ``>``, matching the shipped
    sequence specs' own convention. Building a body as one interpolated string (as this file
    originally did for GOOD_BODY_1/2) silently truncates after line 1: ``parse_spec`` stops
    reading a touch's body at the first line that does not start with ``>``, and only line 1
    of an f-string-interpolated multi-line value ever gets that prefix."""
    return "\n".join(f"> {ln}" if ln else ">" for ln in text.strip("\n").split("\n"))


GOOD_BODY_1 = (
    "Hi {{First Name}},\n\n"
    "{{Why Now}}. Once agents at {{Company}} move from retrieving data inside Epic to "
    "acting on it, identity becomes the question an auditor asks first. Tell me if you've "
    "got this covered: your logs capture which account touched a record, not which agent "
    "held the authority to act.\n\n"
    "Would the one-pager on how another team mapped that to {{Company}}'s agent path be "
    "useful?\n\n"
    "Henry"
)
GOOD_BODY_2 = (
    "Hi {{First Name}},\n\n"
    "Following the note on agent authority at {{Company}}. My hunch: the open piece is "
    "portable proof of the who, not the what, and Okta alone will not settle it for an "
    "examiner walking the estate in 2026. A regulated-FI compliance team held their audit "
    "trail together the same way, mapping every agent handoff back to a single owner "
    "before anything shipped to production.\n\n"
    "Want their before-and-after, mapped to {{Company}}'s agent path?\n\n"
    "Henry"
)

GOOD_SPEC = f"""
**Step 1 — Day 1** · Subject: `identity in production`
{_blockquote(GOOD_BODY_1)}

**Step 2 — Day 4** (same thread, no subject)
{_blockquote(GOOD_BODY_2)}
"""


def _spec_with_body_1(body: str) -> str:
    """``GOOD_SPEC`` with touch 1's body swapped — the smallest-deviation helper for the
    per-body rules that arrive through the imported ``lint_email``. Touch 2 is left untouched so
    a mutation cannot accidentally be proven by a second, unrelated render."""
    return f"""
**Step 1 — Day 1** · Subject: `identity in production`
{_blockquote(body)}

**Step 2 — Day 4** (same thread, no subject)
{_blockquote(GOOD_BODY_2)}
"""


def _touches(spec: str = GOOD_SPEC) -> list[Touch]:
    return parse_spec(spec)


def _fires(rule: str, touches: list[Touch], rows: list[dict], **kw) -> list:
    """Assert ``rule`` appears (any level) in a real ``lint_merge_render`` run; return the
    matching violations so a test can add a stronger assertion (e.g. the level) on top."""
    violations, _ = lint_merge_render(touches, rows, signoff="Henry", **kw)
    hits = [v for v in violations if v.rule == rule]
    assert hits, f"expected rule {rule!r} to fire; got rules {sorted({v.rule for v in violations})}"
    return hits


def test_baseline_is_clean_of_every_construction_and_substance_rule():
    """The shared GOOD_ROW/GOOD_SPEC fixture must not itself trip an ERROR — every mutation
    test below changes exactly one thing away from this baseline, and a baseline that is
    already broken would make every other test's "one crafted defect" claim false."""
    violations, stats = lint_merge_render(_touches(), [_row()], signoff="Henry")
    errors = [v for v in violations if v.level == "ERROR"]
    assert not errors, [str(v) for v in errors]
    assert stats["renders"] == 2


# --------------------------------------------------------------------------- relevance


def test_signal_off_topic():
    _fires(
        "signal-off-topic",
        _touches(),
        [_row(signal_clause="Opened a new office in Austin last month")],
    )


def test_signal_not_an_event():
    # Baseline's own signal_clause already lacks an event verb — this test just makes the point
    # explicit and independently verifiable rather than piggy-backing on the fixture.
    _fires(
        "signal-not-an-event",
        _touches(),
        [_row(signal_clause="Cascade sits on the BRIGHTPATH agent-runtime-security working group")],
    )


def test_signal_stray_digit():
    _fires(
        "signal-stray-digit",
        _touches(),
        [_row(signal_clause="Raised $45M for its agent platform in March 2026")],
    )


def test_persona_lead_mismatch():
    # Exec seat, security-only vocabulary, none of the exec seat's own words.
    rows = [
        # Deliberately no "rollout"/"adoption"/etc — an exec-seat own-word landing in the
        # RENDERED clause would neutralise the mismatch this test exists to prove, since
        # `own` vocabulary is checked against the full rendered body, merge-borne or not.
        _row(
            title="Chief Executive Officer",
            signal_clause="Piloting an agent framework across its claims handling",
        )
    ]
    spec = """
**Step 1 — Day 1** · Subject: `the audit question`
> Hi {{First Name}},
>
> {{Why Now}}. Once agents at {{Company}} act on records, an auditor and an examiner will
> ask for the attribution trail before anything else, and that is attributable to nobody
> today. Tell me if you've got this covered.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("persona-lead-mismatch", _touches(spec), rows)


# --------------------------------------------------------------------------- substance


def test_touch_not_personalised():
    # Step 1 opens a new thread but uses none of the row's own words beyond the greeting —
    # every recipient gets near-identical copy, while the row itself DOES carry usable
    # signal (so the rule doesn't bail out for lack of anything to personalise with).
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agent identity is becoming the question every auditor asks first, and most teams have
> not built the trail yet. It is worth having a plan before the review, not after it, and
> teams that wait usually regret it. My hunch is you have thought about this already.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires(
        "touch-not-personalised",
        _touches(spec),
        [_row(signal_clause="Standardised on Okta as its agent identity provider in 2026")],
    )


def test_signal_contradicts_pitch():
    _fires(
        "signal-contradicts-pitch",
        _touches(),
        [_row(signal_clause="Issues verified agent identities for every internal service")],
    )


def test_signal_agent_homonym():
    _fires(
        "signal-agent-homonym",
        _touches(),
        [_row(signal_clause="Licensed insurance agents now cover all fifty states nationwide")],
    )


def test_word_count():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Short note.
>
> Useful?
>
> Henry
"""
    _fires("word-count", _touches(spec), [_row()])


def test_sentence_length():
    """One runaway sentence, body still inside the legal word band.

    The defect this catches is invisible to `word-count`: a 96-word body is compliant whether
    it arrives as six skimmable sentences or as one 44-word wall plus filler. Only the second
    is unreadable in nine seconds, and only this rule can tell them apart.
    """
    spec = """
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{First Name}},
>
> {{Why Now}}. Tell me if you've got this covered: once agents at {{Company}} move from
> retrieving data inside the record system to acting on it under a shared service account
> that nobody can attribute afterwards, the identity question becomes the first thing any
> auditor asks about the whole estate. A regulated-FI compliance team hit that same wall.
>
> Would the one-pager on how they mapped it be useful?
>
> Henry
"""
    hits = _fires("sentence-length", _touches(spec), [_row()])
    assert any(h.level == "ERROR" for h in hits), [str(h) for h in hits]


def test_question_count():
    """Four questions in a body whose LAST sentence is still a question.

    Deliberately constructed so `cta-question` passes — that rule only ever inspects the final
    sentence, so a body can satisfy it while asking the reader to make four separate decisions.
    This is the gap; the mutation has to prove the two rules are not the same rule.
    """
    spec = """
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{First Name}},
>
> {{Why Now}}. Who owns agent authority at {{Company}} today? Is it the platform team or
> security? My hunch is the logs capture the account, not the agent that held the authority
> to act, so how are you attributing that now?
>
> Would the one-pager on how another regulated team mapped it be useful?
>
> Henry
"""
    touches = _touches(spec)
    hits = _fires("question-count", touches, [_row()])
    assert any(h.level == "ERROR" for h in hits), [str(h) for h in hits]
    violations, _ = lint_merge_render(touches, [_row()], signoff="Henry")
    assert not [v for v in violations if v.rule == "cta-question"], "cta-question must still pass"


# --------------------------------------------------------------------------- merge


def test_unknown_merge_tag():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> {{Favorite Color}} is a merge tag no provider defines.
>
> Useful?
>
> Henry
"""
    _fires("unknown-merge-tag", _touches(spec), [_row()])


def test_merge_tag_not_in_csv():
    # "LinkedIn" is a real DEFAULT_FIELD_LABELS entry with no TAG_TO_COLUMN mapping.
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Saw {{LinkedIn}} recently.
>
> Useful?
>
> Henry
"""
    _fires("merge-tag-not-in-csv", _touches(spec), [_row()])


def test_empty_merge_tag():
    _fires("empty-merge-tag", _touches(), [_row(company="")])


def test_unused_signal_column():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    # `lint_unused_signal_columns` deliberately reads the RAW "why_now" research column
    # (not "signal_clause", the already-reduced render-safe value) — it is asking "does
    # unreduced research exist that COULD have opened this touch", which is a different
    # question from "is the reduced clause good", so it needs its own row key.
    _fires(
        "unused-signal-column",
        _touches(spec),
        [_row(why_now="Standardised on Okta as its agent identity provider in 2026")],
    )


def test_first_name_unrenderable():
    _fires("first-name-unrenderable", _touches(), [_row(first="J0hn")])


def test_greeting():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hello there {{First Name}}.
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("greeting", _touches(spec), [_row()])


def test_greeting_not_a_name():
    # Passes _NAME_CHARS_RE (check_row) — letters only — but matches _NAME_RE's own
    # blocklist, so this fires without collateral from first-name-unrenderable.
    _fires("greeting-not-a-name", _touches(), [_row(first="Unconfirmed")])


def test_unresolved_contact():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi [NAME UNRESOLVED],
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> [NAME UNRESOLVED]
"""
    _fires("unresolved-contact", _touches(spec), [_row(first="[NAME UNRESOLVED]")])


def test_placeholder():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> [INSERT PROOF POINT HERE] is the reason {{Company}}'s agents need attribution before an
> auditor asks for it during the next regulated review cycle across the estate.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("placeholder", _touches(spec), [_row()])


def test_spintax():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> {Agents|Bots} at {{Company}} are moving from retrieving data to acting on it, and the
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("spintax", _touches(spec), [_row()])


# --------------------------------------------------------------------------- data


def test_last_name_initial():
    _fires("last-name-initial", _touches(), [_row(last="R")])


def test_last_name_symbols():
    _fires("last-name-symbols", _touches(), [_row(last="Renner\U0001f366")])


def test_last_name_credentials():
    _fires("last-name-credentials", _touches(), [_row(last="Renner, MD")])


def test_company_headline():
    _fires("company-headline", _touches(), [_row(company="Cascade | We Build Trust")])


def test_company_empty():
    _fires("company-empty", _touches(), [_row(company="")])


def test_company_placeholder():
    _fires("company-placeholder", _touches(), [_row(company="none")])


def test_company_trademark_glyph():
    _fires("company-trademark-glyph", _touches(), [_row(company="Cascade®")])


def test_company_sentence_punct():
    _fires("company-sentence-punct", _touches(), [_row(company="Cascade!")])


def test_company_trailing_period():
    _fires("company-trailing-period", _touches(), [_row(company="Cascade Corp.")])


def test_company_transaction_entity():
    _fires("company-transaction-entity", _touches(), [_row(company="Summitline Health Merger")])


def test_company_domain_junk():
    _fires("company-domain-junk", _touches(), [_row(company_domain="localhost")])


def test_first_name_empty():
    _fires("first-name-empty", _touches(), [_row(first="")])


def test_first_name_placeholder():
    _fires("first-name-placeholder", _touches(), [_row(first="team")])


def test_email_malformed():
    _fires("email-malformed", _touches(), [_row(email="not-an-email")])


def test_email_role_address():
    _fires("email-role-address", _touches(), [_row(email="info@cascade.example")])


def test_email_freemail():
    _fires("email-freemail", _touches(), [_row(email="chris@gmail.com")])


def test_title_headline():
    _fires("title-headline", _touches(), [_row(title="VP | Compliance")])


def test_title_bio_fragment():
    _fires("title-bio-fragment", _touches(), [_row(title="15+ years in fintech compliance")])


# --------------------------------------------------------------------------- grammar


def test_possessive_sibilant():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> About {{Company}}'s agents moving from retrieving data to acting on it, and the trail
> has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("possessive-sibilant", _touches(spec), [_row(company="Gears & Vectors")])


def test_article_collision():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Once agents at the {{Company}} move from retrieving data to acting on it, the trail has
> not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("article-collision", _touches(spec), [_row(company="The Summitline Health")])


# --------------------------------------------------------------------------- deliverability


def test_subject_length():
    spec = """
**Step 1 — Day 1** · Subject: `this subject line has six words`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("subject-length", _touches(spec), [_row()])


def test_subject_lowercase():
    spec = """
**Step 1 — Day 1** · Subject: `Identity Question`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("subject-lowercase", _touches(spec), [_row()])


def test_subject_placeholder():
    spec = """
**Step 1 — Day 1** · Subject: `[INSERT SUBJECT]`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("subject-placeholder", _touches(spec), [_row()])


def test_no_links():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it — details at
> https://example.com/agents and the audit trail has not caught up with that shift.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("no-links", _touches(spec), [_row()])


def test_em_dash():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} — the ones acting on regulated records — have not caught up with
> the audit trail across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("em-dash", _touches(spec), [_row()])


# --------------------------------------------------------------------------- brand


def test_sign_off():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager be useful?
>
> Thanks
"""
    _fires("sign-off", _touches(spec), [_row()])


def test_banned_word():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Wanted to reach out about agents at {{Company}} moving from retrieving data to acting
> on it, and the audit trail has not caught up with that shift this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("banned-word", _touches(spec), [_row()])


def test_banned_stem():
    # `_load_bans` lowercases every stem read from a profile file before it reaches
    # `lint_email`, and the check is a bare substring test with no case-folding of its
    # own — so the stem passed here must already be lowercase, matching the real call
    # path, and must be a phrase genuinely present in the rendered body.
    _fires(
        "banned-stem",
        _touches(),
        [_row()],
        banned_stems=("the question an auditor asks first",),
    )


def test_named_case_study():
    # `_load_bans` lowercases every case-study name read from a profile file, and the
    # check compares against a lowercased body with no case-folding of its own — so the
    # name passed here must already be lowercase, matching the real call path.
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, the way Cascade
> mapped its own agent-attribution trail, and that is one useful pattern this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("named-case-study", _touches(spec), [_row()], case_studies=("cascade",))


def test_time_ask():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Worth a quick call this week?
>
> Henry
"""
    _fires("time-ask", _touches(spec), [_row()])


# --------------------------------------------------------------------------- dedupe


def test_same_company_identical_copy():
    # Two DIFFERENT people (different last name/email) whose row renders every tag the
    # template actually uses ({{First Name}}, {{Company}}, {{Why Now}}) to the SAME value —
    # last name and email are never rendered into this spec's body, so this is the minimal
    # way two real, distinct contacts produce a byte-identical send.
    rows = [
        _row(first="Chris", last="Renner", email="chris@cascade.example"),
        _row(first="Chris", last="Oyelaran", email="dana@cascade.example"),
    ]
    _fires("same-company-identical-copy", _touches(), rows)


# --------------------------------------------------------------------------- risk


def test_email_domain_mismatch():
    _fires(
        "email-domain-mismatch",
        _touches(),
        [_row(email="chris@othercorp.example", company_domain="cascade.example")],
    )


# --------------------------------------------------------------------------- cosmetic


def test_first_name_initial():
    _fires("first-name-initial", _touches(), [_row(first="C")])


def test_company_allcaps():
    _fires("company-allcaps", _touches(), [_row(company="CASCADEWORKS")])


def test_company_long():
    _fires("company-long", _touches(), [_row(company="A" * 33)])


def test_company_legal_suffix():
    _fires("company-legal-suffix", _touches(), [_row(company="Cascade Inc")])


def test_company_parenthetical():
    _fires("company-parenthetical", _touches(), [_row(company="Cascade (formerly Vantos)")])


# --------------------------------------------------------------------------- completeness


# --- hook coverage (message axis) ----------------------------------------------------
#
# Both rules are OFF unless ``hook_matrix`` is passed — the same opt-in shape as
# ``gift_artifacts``, which is exactly how ``cta-unstaged-artifact`` sat inert for months.
# So each mutation below passes the matrix explicitly, and
# ``test_hook_cell_checks_are_silent_without_a_matrix`` pins the off-state so nobody
# "fixes" the opt-in into an always-on rule that breaks every pre-declaration spec.

#: A miniature persona x signal grid in one live tenant's shape. Invented personas and hooks —
#: the real matrix is a tenant asset and never belongs in a fixture (R9 in spirit: this is
#: the tenant's copy, not their people).
MINI_MATRIX = """---
source: manual
---
# Hook matrix — Northwind Systems

## Enterprise

| Signal → / Persona ↓ | M&A / consolidation | Compliance event |
|---|---|---|
| **CISO** | "One perimeter across two estates." | "Evidence a regulator can read." |
| **FinOps lead** | "Cost per agent, not one bill." | "Defensible AI spend." |
"""


def _spec_front(*lines: str) -> str:
    """A spec front block in the shipped shape: a fenced ``Key: value`` block."""
    body = "\n".join(("Campaign:   Test", "Profile:    northwind", "Sign-off:   Henry", *lines))
    return f"# Sequence spec — test\n\n```\n{body}\n```\n"


# --- signal cell (2026-08-23) ---------------------------------------------------------
#
# The row-level counterpart to the hook-cell pair above: those two prove a SPEC's declared
# cell exists in the matrix; these three prove the ROWS it was sent to actually hold that
# cell's signal. Measured 2026-08-23: `hook_cell` already had a campaign-wide ERROR
# (`cell-row-mismatch`) and was unpopulated on all 207 live send-ready rows — a correct gate,
# inert because nothing fed it. Same opt-in shape as the hook-cell pair (OFF unless
# `hook_matrix` is passed), so the same three extra controls apply: the real-signal case,
# the no-matrix off-state, and (here) the finding-budget check that an unrecorded column
# reports ONCE per list, not once per row.

_SPEC_DECLARING_COMPLIANCE = _spec_front("hook_cell:   CISO × Compliance event")

#: The three new rule names, precise — `v.rule.startswith("signal-")` would also catch the
#: unrelated pre-existing `signal-off-topic` / `signal-not-an-event` / `signal-stray-digit`
#: family (the baseline row's own `signal_clause` already trips `signal-not-an-event` by
#: design; see `GOOD_ROW`'s comment), which is not what a "did my new rules fire" assertion
#: should be checking.
_SIGNAL_CELL_RULES = {"signal-column-unknown", "signal-cell-mismatch", "signal-column-unrecorded"}


def test_signal_rules_are_not_data_borne_eligible():
    """These rules read CSV columns directly and never touch a rendered body — the
    `_DATA_BORNE_ELIGIBLE` suppression exists only for a copy rule that cannot tell the
    template's words from the row's, which does not apply here. Pinned so nobody later adds
    them to that frozenset by analogy with `persona-lead-mismatch`."""
    assert "signal-cell-mismatch" not in _DATA_BORNE_ELIGIBLE
    assert "signal-column-unknown" not in _DATA_BORNE_ELIGIBLE
    assert "signal-column-unrecorded" not in _DATA_BORNE_ELIGIBLE


# --- premise fit (2026-08-21) --------------------------------------------------------
#
# Opt-in behind `premise_vocab`, exactly like the hook-cell pair behind `hook_matrix`, so
# they ship with their OFF-state pinned as well as their firing state. `cta-unstaged-artifact`
# was a correct rule that nobody had opted into for months; an opt-in rule with no off-state
# test is that failure waiting to repeat.

#: A miniature tenant premise vocabulary. Carries no real tenant's terms — the shape is what
#: is under test, and the live vocabulary is profile data.
MINI_PREMISE = {
    "multi-framework": type(
        "P",
        (),
        {
            "key": "multi-framework",
            "claim": "runs agents on more than one framework",
            "min_distinct": 2,
            "terms": frozenset({"langgraph", "crewai", "bedrock"}),
            "attested_by": lambda self, text: {
                t for t in ("langgraph", "crewai", "bedrock") if t in (text or "").lower()
            },
            "met_by": lambda self, text: len(self.attested_by(text)) >= self.min_distinct,
        },
    )(),
    # An arity-1 premise whose term is an ordinary business word — the shape that makes
    # `premise-unsupported` a word-presence check. Mirrors the tenant's real
    # `cross-org-agents`, which lists "partner" and needs only one match.
    "cross-org": type(
        "P",
        (),
        {
            "key": "cross-org",
            "claim": "the recipient's agents call across an organisational boundary",
            "min_distinct": 1,
            "terms": frozenset({"partner", "third-party", "vendor"}),
            "attested_by": lambda self, text: {
                t for t in ("partner", "third-party", "vendor") if t in (text or "").lower()
            },
            "met_by": lambda self, text: len(self.attested_by(text)) >= self.min_distinct,
        },
    )(),
}


def test_premise_missing():
    """A spec that declares no premise: what the body needs the recipient's own facts to
    establish is undeclared, so no row can be checked against it."""
    hits = _fires(
        "premise-missing",
        _touches(),
        [_row()],
        spec_text=_spec_front("hook_cell:   CISO × Compliance event"),
        premise_vocab=MINI_PREMISE,
    )
    assert hits[0].level == "WARN"


def test_premise_unknown():
    """A premise id the profile's vocabulary does not define — an invented requirement
    wearing a declaration, the same shape as `hook-cell-unknown`."""
    hits = _fires(
        "premise-unknown",
        _touches(),
        [_row()],
        spec_text=_spec_front("premise:     runs-on-vibes"),
        premise_vocab=MINI_PREMISE,
    )
    assert hits[0].level == "ERROR"


def test_premise_unsupported():
    """The operator's dominant rejection class: the fact is true, specific and about the
    right company, and it attests ONE of something the body claims several of."""
    # One bad row among three good ones: below the saturation threshold, so the finding
    # stays ITEMIZED and names the row. (The aggregate form has its own test below — the two
    # shapes mean different things and both need pinning.)
    good = [
        _row(
            email=f"ok{i}@x.example",
            signal_evidence="Runs LangGraph for research and CrewAI for support.",
        )
        for i in range(3)
    ]
    bad = _row(
        email="thin@x.example",
        signal_evidence="Shipped a new assistant built on Bedrock this quarter.",
    )
    hits = _fires(
        "premise-unsupported",
        _touches(),
        [*good, bad],
        spec_text=_spec_front("premise:     multi-framework"),
        premise_vocab=MINI_PREMISE,
    )
    assert len(hits) == 1
    assert hits[0].level == "ERROR"
    assert hits[0].email == "thin@x.example"
    assert "needs 2 distinct" in hits[0].detail


def test_a_row_that_attests_the_premise_fires_nothing():
    """Positive control. Without it, the test above is equally consistent with a rule that
    fires on every row regardless of evidence — which is exactly what `specificity` at its
    old threshold was doing while looking like a working check."""
    row = _row(signal_evidence="Runs LangGraph for research and CrewAI for support.")
    violations, _ = lint_merge_render(
        _touches(),
        [row],
        signoff="Henry",
        spec_text=_spec_front("premise:     multi-framework"),
        premise_vocab=MINI_PREMISE,
    )
    assert not [v for v in violations if v.rule.startswith("premise-")]


def test_the_premise_rules_are_off_without_a_vocabulary():
    """The off-state. A profile shipping no premise-vocab.toml gets none of these rules,
    and must not get `premise-missing` either — an opt-in check that fires when it was never
    opted into is noise every tenant inherits."""
    violations, _ = lint_merge_render(
        _touches(),
        [_row()],
        signoff="Henry",
        spec_text=_spec_front("hook_cell:   CISO × Compliance event"),
    )
    assert not [v for v in violations if v.rule.startswith("premise-")]


def test_premise_unsupported_collapses_when_it_saturates():
    """Past half the list it reports ONE aggregate finding, not one per row.

    Measured at 56/61, 50/57, 54/71 and 17/18 on the lists this rule was written for. A wall
    of 177 identical-shaped ERRORs would bury the actual message, which is not about the rows
    at all: the ARGUMENT is asking this list to establish something it was never selected for.
    """
    rows = [
        _row(email=f"a{i}@x.example", signal_evidence="Shipped one assistant on Bedrock.")
        for i in range(10)
    ]
    hits = _fires(
        "premise-unsupported",
        _touches(),
        rows,
        spec_text=_spec_front("premise:     multi-framework"),
        premise_vocab=MINI_PREMISE,
    )
    assert len(hits) == 1
    assert hits[0].email == "SPEC"
    assert "SATURATED" in hits[0].detail


def test_premise_thin():
    """A premise that passes on one common word. Measured on the shipped enterprise-security
    list: 15/15 rows passed `cross-org-agents`, 9 on a single term and 6 of those on the bare
    word "partner" — an integrator *joining* a partner network, a hospital *selecting* a
    vendor. Neither is agents crossing an organisational boundary, which is the claim. The
    rows PASS; the point is that the pass stops reading as strong evidence."""
    rows = [
        _row(email=f"a{i}@x.example", signal_evidence="Named an integration partner this week.")
        for i in range(4)
    ]
    hits = _fires(
        "premise-thin",
        _touches(),
        rows,
        spec_text=_spec_front("premise:     cross-org"),
        premise_vocab=MINI_PREMISE,
    )
    assert hits[0].level == "WARN"
    assert hits[0].email == "SPEC"


def test_opener_undated():
    """GOOD_BODY_1's opener carries no year or month. Opt-in, so the flag is passed here —
    which is the point: the firing state is only reachable deliberately."""
    hits = _fires("opener-undated", _touches(), [GOOD_ROW], require_dated_opener=True)
    assert hits[0].level == "WARN" and hits[0].email == "SPEC"


def test_opener_undated_is_off_unless_asked():
    """The off-state, pinned as hard as the firing state. Zero of 596 live openers carry a
    date, so a rule that quietly defaulted to ON would fail the entire fleet — and
    `cta-unstaged-artifact` is the precedent in the other direction, a correct rule nobody
    had opted into."""
    violations, _ = lint_merge_render(_touches(), [GOOD_ROW], signoff="Henry")
    assert not [v for v in violations if v.rule == "opener-undated"]


def test_a_dated_opener_clears_the_warning():
    """And it must actually read the opener: a body whose date sits in a later paragraph is
    still undated where it counts."""
    dated = (
        "Hi {{First Name}},\n\n"
        "The BRIGHTPATH working group published its agent-identity draft in April 2026. Once agents "
        "at {{Company}} move from retrieving data inside Epic to acting on it, identity becomes "
        "the question an auditor asks first. Tell me if you've got this covered: your logs "
        "capture which account touched a record, not which agent held the authority to act.\n\n"
        "Would the one-pager on how another team mapped that to {{Company}}'s agent path be "
        "useful?\n\n"
        "Henry"
    )
    violations, _ = lint_merge_render(
        _touches(_spec_with_body_1(dated)),
        [GOOD_ROW],
        signoff="Henry",
        require_dated_opener=True,
    )
    assert not [v for v in violations if v.rule == "opener-undated"]


def test_thread_sentence_repeat():
    """The same sentence in two touches that land in ONE thread. GOOD_SPEC's step 2 carries
    no subject, so it is a same-thread follow-up — the mutation is to echo one of step 1's
    sentences there, and nothing else."""
    echoed = (
        "Hi {{First Name}},\n\n"
        # A short opener of its own, so the echoed sentence is not glued to the greeting by
        # `_sentences` — glued, its token set differs from step 1's and nothing repeats.
        "Following up on the note below.\n\n"
        "Once agents at {{Company}} move from retrieving data inside Epic to acting on it, "
        "identity becomes the question an auditor asks first.\n\n"
        "Want their before-and-after, mapped to {{Company}}'s agent path?\n\n"
        "Henry"
    )
    spec = f"""
**Step 1 — Day 1** · Subject: `identity in production`
{_blockquote(GOOD_BODY_1)}

**Step 2 — Day 4** (same thread, no subject)
{_blockquote(echoed)}
"""
    hits = _fires("thread-sentence-repeat", _touches(spec), [GOOD_ROW])
    assert hits[0].email == "SPEC", "a sequence-wide defect reported against one render"


def test_a_repeat_across_two_threads_is_not_a_repeat():
    """Off-state. The rule is about what the reader sees in ONE window: give step 2 its own
    subject and the same echo is a fresh thread, which is a different (legal) shape. Without
    this the rule could be `any sentence twice` and every test above would still pass."""
    echoed = (
        "Hi {{First Name}},\n\n"
        # A short opener of its own, so the echoed sentence is not glued to the greeting by
        # `_sentences` — glued, its token set differs from step 1's and nothing repeats.
        "Following up on the note below.\n\n"
        "Once agents at {{Company}} move from retrieving data inside Epic to acting on it, "
        "identity becomes the question an auditor asks first.\n\n"
        "Want their before-and-after, mapped to {{Company}}'s agent path?\n\n"
        "Henry"
    )
    spec = f"""
**Step 1 — Day 1** · Subject: `identity in production`
{_blockquote(GOOD_BODY_1)}

**Step 2 — Day 4** · Subject: `a second look`
{_blockquote(echoed)}
"""
    violations, _ = lint_merge_render(_touches(spec), [GOOD_ROW], signoff="Henry")
    assert not [v for v in violations if v.rule == "thread-sentence-repeat"]


def test_thread_reply_prefix():
    """A touch that OPENS a thread claiming to continue one."""
    spec = f"""
**Step 1 — Day 1** · Subject: `Re: identity in production`
{_blockquote(GOOD_BODY_1)}

**Step 2 — Day 4** (same thread, no subject)
{_blockquote(GOOD_BODY_2)}
"""
    hits = _fires("thread-reply-prefix", _touches(spec), [GOOD_ROW])
    assert hits[0].email == "SPEC"


# --------------------------------------------------------------------- row data (check_row)
#
# Four rules `gtm_core.merge_hygiene.check_row` raises that had no mutation here until
# 2026-09-24, because the pre-merge backstop AST-walked one linter file and never walked
# `check_row` at all. They were KEPT by PRD §3A (row data) and are now catalogued, so they
# need the mutation every catalogued rule owes. Each is checked only when the row CARRIES the
# column, so the control is the baseline row, which carries neither.


def test_score_not_numeric():
    _fires("score-not-numeric", _touches(), [_row(score="high")])


def test_score_out_of_range():
    # 53 is the top of the SPEND ranking that reached 749 published rows in the qualification
    # column — a real number from the wrong scale, which is the defect this rule exists for.
    _fires("score-out-of-range", _touches(), [_row(score="53")])


def test_segment_noncanonical():
    _fires("segment-noncanonical", _touches(), [_row(segment="Enterprise")])


def test_segment_unknown():
    _fires("segment-unknown", _touches(), [_row(segment="midmarket")])


def test_the_row_data_rules_are_silent_when_the_row_omits_the_column():
    """The negative control for all four at once: the baseline row carries neither column, so
    a missing optional column must not read as a defect. Without this the four mutations above
    would be satisfied by a rule that fired on every row."""
    violations, _ = lint_merge_render(_touches(), [_row()], signoff="Henry")
    assert not {
        "score-not-numeric",
        "score-out-of-range",
        "segment-noncanonical",
        "segment-unknown",
    } & {v.rule for v in violations}


def test_a_canonical_segment_and_an_in_range_score_are_clean():
    """...and the control that exercises the OTHER branch — the columns present and correct."""
    violations, _ = lint_merge_render(
        _touches(), [_row(segment="enterprise", score="7")], signoff="Henry"
    )
    assert not {
        "score-not-numeric",
        "score-out-of-range",
        "segment-noncanonical",
        "segment-unknown",
    } & {v.rule for v in violations}


# --------------------------------------------------------------- derivation (FR3, 2026-09-24)
#
# The three rules that replace the twenty-five retired ones. Every fixture below is FICTIONAL
# (§R9): the tenant slug, the claim ids, the proof statements and the seat vocabulary are all
# invented, because `tests/linter/` carries no tenant token. Real claims live only under
# `profiles/`.
#
# The registry is built on disk and loaded through `registry.load` — the SAME loader
# `resolve.angle_for` uses — rather than by constructing a `Registry` in memory. A hand-built
# Registry could hold a state the loader refuses (a `live` angle on an unverified claim), and a
# rule proven against an impossible state is proven against nothing. Test plan §7.

_FIX_PROFILE = "marlowe"

_FIX_VOCABULARY = """\
default_persona = "ciso"
segments = ["enterprise", "unspecified"]

[[persona]]
name = "ciso"
cues = ["ciso", "head of security"]

[[seat]]
name = "security"
personas = ["ciso"]
stakes = ["breach", "audit"]
"""

_FIX_PREMISE = """\
schema = 1

[premise.multi-framework]
claim = "the reader runs agents on more than one framework"
min_distinct = 1
terms = ["langgraph", "autogen"]
"""

_FIX_CLAIMS = """\
[[claim]]
id = "audit-signed"
group = "observability"
status = "verified"
statement = "Each audit entry is signed."
source = "knowledge/references/ledger-notes.md:12"
do_not_say = ["hash-chained", "tamper-proof"]

[[claim]]
id = "transport-pinned"
group = "transport"
status = "design-target"
statement = "Per-agent transport pinning is planned."
"""

_FIX_PROOF = """\
[[proof]]
id = "regulator-note-sg"
kind = "anchor"
market = "Singapore"
figure_kind = "none"
statement = "A verifiable identity per agent, tied to an accountable human."
source = "knowledge/guidance/regulator-notes.md:4"

[[proof]]
id = "regulator-note-us"
kind = "anchor"
market = "United States"
figure_kind = "none"
statement = "An agent acts only inside a scope a human granted."
source = "knowledge/guidance/regulator-notes.md:9"

[[proof]]
id = "reference-turnaround"
kind = "outcome"
figure_kind = "measured"
statement = "A reference check against a five-to-fifteen day baseline is reduced to minutes across 12 markets."
source = "knowledge/references/pilot-notes.md:3"

[[proof]]
id = "review-cycle-cut"
kind = "outcome"
figure_kind = "measured"
statement = "Audit review cycles fell 74% against a measured baseline."
source = "knowledge/references/pilot-notes.md:11"

[[proof]]
id = "legacy-review-saving"
kind = "outcome"
figure_kind = "disputed"
statement = "A 90% reduction in review time. RETRACTED - the figure appears in no primary record."

[[proof]]
id = "legacy-audit-coverage"
kind = "outcome"
figure_kind = "disputed"
statement = "100% audit coverage. RETRACTED - the figure appears in no primary record."
"""

_FIX_ANGLES = """\
[[angle]]
id = "sec-audit-sg"
seat = "security"
premise = "multi-framework"
claim = "audit-signed"
proof = "regulator-note-sg"
opener_kind = "account-event"
summary = "One chain of custody per agent action."
status = "draft"

[[angle]]
id = "sec-audit-us"
seat = "security"
premise = "multi-framework"
claim = "audit-signed"
proof = "regulator-note-us"
opener_kind = "account-event"
summary = "One granted scope per agent action."
status = "draft"

[[angle]]
id = "sec-transport"
seat = "security"
premise = "multi-framework"
claim = "transport-pinned"
proof = "regulator-note-sg"
opener_kind = "account-event"
summary = "One pinned channel per agent."
status = "draft"
"""


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    from gtm_core.messaging.registry import load

    root = tmp_path_factory.mktemp("profiles")
    knowledge = root / _FIX_PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    for name, text in (
        ("claims.toml", _FIX_CLAIMS),
        ("proof.toml", _FIX_PROOF),
        ("angles.toml", _FIX_ANGLES),
        ("role-vocabulary.toml", _FIX_VOCABULARY),
        ("premise-vocab.toml", _FIX_PREMISE),
    ):
        (knowledge / name).write_text(text, encoding="utf-8")
    return load(_FIX_PROFILE, profiles_root=root)


_FIX_SLOTS = """\
slot_signal: signal_evidence
slot_claim: audit-signed
slot_pain: security
slot_hedge: usually
slot_proof: regulator-note-sg
"""


#: The shared baseline, unmodified. It carries "in 2026", and until 2026-09-24 that year was a
#: figure to `proof-status` — so the year had to be stripped here or every derivation control
#: would fire on the fixture rather than on what the test changed, the "a baseline that is
#: already broken makes every other test's claim false" trap `test_baseline_is_clean_...`
#: exists for. A year is a date, not a magnitude; the strip is no longer needed, and
#: `_NOT_A_MAGNITUDE` pins that directly.
_DERIV_BODY_2 = GOOD_BODY_2


def _derived_spec(angle: str = "sec-audit-sg", *, slots: str = _FIX_SLOTS, body: str = "") -> str:
    """A spec in the MIGRATED shape: a fenced front block declaring one angle and five slots."""
    return f"""```
angle: {angle}
{slots}```

**Step 1 — Day 1** · Subject: `identity in production`
{_blockquote(body or GOOD_BODY_1)}

**Step 2 — Day 4** (same thread, no subject)
{_blockquote(_DERIV_BODY_2)}
"""


def _derivation(spec: str, registry, rows=None) -> list:
    violations, _ = lint_merge_render(
        _touches(spec),
        rows if rows is not None else [_row()],
        signoff="Henry",
        spec_text=spec,
        registry=registry,
    )
    return [v for v in violations if v.rule in {"claim-status", "proof-status", "slot-attribution"}]


def test_the_derivation_rules_are_off_without_a_registry():
    """The opt-in off-state, pinned like `--hook-matrix`'s was: an opt-in rule that nobody
    proves is OFF when unasked is a rule that silently changes every existing caller."""
    spec = _derived_spec(
        body=GOOD_BODY_1.replace("Once agents", "The hash-chained log means agents")
    )
    violations, _ = lint_merge_render(_touches(spec), [_row()], signoff="Henry", spec_text=spec)
    assert not [
        v for v in violations if v.rule in {"claim-status", "proof-status", "slot-attribution"}
    ]


def test_claim_status(registry):
    """A `do_not_say` phrase in the copy. This half needs no declared angle, so it is the
    branch that reaches unmigrated specs — which is why it is the named mutation."""
    body = GOOD_BODY_1.replace("Once agents", "The hash-chained trail means agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "claim-status"]
    assert hits, "a phrase the claim's own do_not_say list forbids did not fire"
    assert hits[0].level == "ERROR"
    assert "hash-chained" in hits[0].detail


def test_the_same_body_without_the_banned_phrase_is_clean(registry):
    """§R18's other half: the control differs from the mutation by the phrase and nothing else."""
    hits = [v for v in _derivation(_derived_spec(), registry) if v.rule == "claim-status"]
    assert not hits, [str(v) for v in hits]


def test_an_angle_on_an_unverified_claim_is_refused(registry):
    hits = [
        v for v in _derivation(_derived_spec("sec-transport"), registry) if v.rule == "claim-status"
    ]
    assert hits, "a design-target claim reached a body"
    assert "design-target" in hits[0].detail


def test_an_angle_on_a_verified_claim_is_not(registry):
    """Same spec, same body, the one angle swapped — the whole difference is the claim status."""
    hits = [
        v for v in _derivation(_derived_spec("sec-audit-sg"), registry) if v.rule == "claim-status"
    ]
    assert not hits, [str(v) for v in hits]


def test_proof_status(registry):
    """A figure with no `measured` proof behind it. ERROR because the spec declares an angle."""
    body = GOOD_BODY_1.replace("Once agents", "That cut review time 42% once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert hits, "an unsourced figure did not fire"
    assert hits[0].level == "ERROR"
    assert "42" in hits[0].detail


def test_an_unsourced_figure_is_only_a_warning_on_a_spec_that_predates_the_registry(registry):
    """The severity is migration-gated, and the gate is the `angle:` declaration. Measured
    2026-09-24: this branch fires on 27 of 47 live specs, all written before a figure needed a
    proof id, so ERROR-by-default would fail the fleet for a re-draft nobody can yet produce."""
    spec = f"""
**Step 1 — Day 1** · Subject: `identity in production`
{_blockquote(GOOD_BODY_1.replace("Once agents", "That cut review time 42% once agents"))}
"""
    hits = [v for v in _derivation(spec, registry) if v.rule == "proof-status"]
    assert hits and {v.level for v in hits} == {"WARN"}, [str(v) for v in hits]


def test_a_word_form_figure_with_a_measured_proof_is_clean(registry):
    """The primary-supported figure, in the shape the registry records it: no digit, so nothing
    to source. The control that keeps `proof-status` from convicting the one claim it backs."""
    body = GOOD_BODY_1.replace(
        "Once agents", "Reference checks went from five-to-fifteen days to minutes once agents"
    )
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert not hits, [str(v) for v in hits]


def test_a_figure_a_measured_proof_actually_carries_is_clean(registry):
    """...and the branch that proves the measured lookup WORKS rather than being unreachable:
    the same shape of sentence, quoting a number the measured proof itself states.

    The quoted number carries its UNIT on purpose. Until 2026-09-24 this test read "across 12
    markets" against a proof saying the same — which passed after the figure predicate was
    narrowed too, but for the wrong reason: neither side is a magnitude any more, so the
    measured lookup was never reached and the control proved nothing.
    """
    body = GOOD_BODY_1.replace("Once agents", "That cut review cycles 74% once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert not hits, [str(v) for v in hits]


def test_a_disputed_figure_is_named_as_disputed(registry):
    """A retracted figure is a different operator action from an unmeasured one, so it gets its
    own message — and stays ERROR even on a spec that declares no angle."""
    body = GOOD_BODY_1.replace("Once agents", "That was a 90% reduction once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert hits and hits[0].level == "ERROR"
    assert "disputed" in hits[0].detail and "legacy-review-saving" in hits[0].detail


def test_the_second_retracted_flagship_figure_is_named_as_disputed(registry):
    """The 100% sibling of the retraction above, in the shape the tenant records it.

    Both retracted flagship figures are the reason this rule exists, so both are pinned
    explicitly. Narrowing the figure predicate on 2026-09-24 is exactly the change that could
    have lost one of them while the other kept passing.
    """
    body = GOOD_BODY_1.replace("Once agents", "We showed 100% audit coverage once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert hits and hits[0].level == "ERROR"
    assert "disputed" in hits[0].detail and "legacy-audit-coverage" in hits[0].detail


def test_a_figure_that_is_only_a_SUBSTRING_of_a_disputed_one_is_not_called_disputed(registry):
    """`9%` is a substring of the disputed proof's `90%` and is not that figure.

    The first version asked ``figure in proof.statement`` and matched the live corpus's "10"
    inside a proof's "100%" and its "3" inside "35%", telling two specs their numbers came from
    a disputed proof they had never quoted. A figure gate that convicts the wrong number sends
    the operator looking for a figure that is not there, and they stop believing the rule.
    """
    body = GOOD_BODY_1.replace("Once agents", "That was a 9% saving once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert hits, "an unsourced figure did not fire at all — the control branch is dead"
    assert "disputed" not in hits[0].detail, hits[0].detail
    assert "no `measured` proof" in hits[0].detail


# ------------------------------------------------- the figure predicate (2026-09-24, §R18)
#
# `proof-status` shipped on 2026-09-24 asking `\d[\d,.]*` — ANY digit run — and its docstring
# said so: "Every digit in a body is a magnitude claim." It is not, and the live packs measured
# it that day: 49 of 66 warnings (74.2%) came from this one rule, over the saturation line the
# linter's own budget draws, with `1`, `1.5` and `110` as the exemplars; and one live pack was
# convicted at ERROR because "a 90-second demo recording?" was matched against the retracted
# `90%` proof. Both halves of that are the same mistake — a numeral with no unit carries no
# magnitude, and comparing numerals rather than magnitudes makes `90 seconds` and `90 percent`
# the same claim. The predicate below is the line drawn instead: a figure is a magnitude when
# it carries a UNIT or a comparative, and it is compared to a proof WITH that unit.
#
# These tests are written from real copy. They are the regression half of the fix; the two
# mutations at the end are the §R18 half, proving the narrowing did not make the rule inert.

#: Real prose from the live packs and from the exemplars the saturation guard printed. None of
#: these is a claim about magnitude, and none may reach `proof-status`.
_NOT_A_MAGNITUDE = (
    # The live ERROR false positive: a numeral, a hyphen, and a duration that is not a claim.
    "Want the one-pager and a 90-second demo recording?",
    # The live ERROR false positive on the OTHER disputed proof (`35%` incident rates): the
    # ROW's own funding research, pasted into a 1:1 body. A scale word is not a unit — it
    # needs a noun to mean anything, and the noun here is the prospect's Series D.
    "Their October raise was 35 million dollars.",
    # The three exemplars the saturation report printed, in prose.
    "We counted 1 link in that thread.",
    "The score sat at 1.5 on their own scale.",
    "The backlog held 110 of them.",
    # A count next to a plural noun. "agents" reads like a unit and is not one.
    "It runs 8 agents per user.",
    # A year. Until this fix the shared fixture had to have "in 2026" stripped out of it.
    "An examiner walking the estate in 2026 will ask.",
)


@pytest.mark.parametrize("sentence", _NOT_A_MAGNITUDE)
def test_a_number_with_no_unit_is_not_a_magnitude_claim(registry, sentence):
    body = GOOD_BODY_1.replace("Once agents", f"{sentence} Once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert not hits, [str(v) for v in hits]


#: ``(sentence, the normalised figure the refusal must name)``. A unit or a comparative: the
#: forms an outbound body actually writes a magnitude in.
_IS_A_MAGNITUDE = (
    ("We cut onboarding 40% last quarter.", "40%"),
    # The word form normalises onto the sign form, so a body cannot dodge the gate by
    # spelling out `percent` — and the refusal names one figure, not two.
    ("We cut onboarding 40 percent last quarter.", "40%"),
    ("Reviews run 1.5x faster now.", "1.5x"),
    ("It gives back $40k a year.", "$40k"),
    ("That is 3 hours back every week.", "3 hour"),
    ("It lands in 2 days rather than a quarter.", "2 day"),
)


@pytest.mark.parametrize(("sentence", "figure"), _IS_A_MAGNITUDE)
def test_a_number_that_carries_a_unit_still_convicts(registry, sentence, figure):
    body = GOOD_BODY_1.replace("Once agents", f"{sentence} Once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert hits, f"an unbacked magnitude escaped: {sentence!r}"
    assert hits[0].level == "ERROR"
    assert figure in hits[0].detail, hits[0].detail


def test_the_same_numeral_under_a_different_unit_is_a_different_figure(registry):
    """The disputed branch compares magnitudes, not numerals — the half that produced the live
    ERROR. `90 days` shares a numeral with the retracted `90%` and is not that claim, so it is
    reported as unmeasured (which it is) and never as the retraction (which it is not)."""
    body = GOOD_BODY_1.replace("Once agents", "That closed in 90 days once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert hits, "the control branch is dead — nothing fired at all"
    assert "disputed" not in hits[0].detail, hits[0].detail
    assert "'90 day'" in hits[0].detail, hits[0].detail


def test_narrowing_the_matcher_further_lets_a_true_positive_escape(registry, monkeypatch):
    """§R18 negative control: the unit table is load-bearing, not decoration.

    Two mutations, each aimed at a different claim the fix makes. Dropping the spelled-out
    `percent` alternative lets a real unbacked magnitude through while the sign form still
    convicts — so the alternative earns its place rather than being covered by `%`. Emptying
    the matcher lets the RETRACTED flagship figure through — so the whole rule rests on it,
    and a fix that had quietly made the matcher unable to fire would fail here.
    """
    import re as _re

    import outreach.rules_derivation as rd

    def _fires(sentence: str) -> list:
        body = GOOD_BODY_1.replace("Once agents", f"{sentence} Once agents")
        return [
            v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"
        ]

    assert _fires("We cut onboarding 40 percent last quarter.")
    assert _fires("That was a 90% reduction.")

    # Both mutations are of the SHIPPED pattern, not of a hand-written stand-in — a stand-in
    # can be weaker than the real thing in ways the real thing never was.
    sign_only = _re.compile(rd._FIGURE_RE.pattern.replace(rd._UNIT_ALT, "%"), rd._FIGURE_RE.flags)
    monkeypatch.setattr(rd, "_FIGURE_RE", sign_only)
    assert not _fires("We cut onboarding 40 percent last quarter."), "`percent` is not covered"
    assert _fires("That was a 90% reduction."), "the sign form must survive this mutation"

    never = _re.compile(rf"(?!)(?:{rd._FIGURE_RE.pattern})", rd._FIGURE_RE.flags)
    monkeypatch.setattr(rd, "_FIGURE_RE", never)
    assert not _fires("That was a 90% reduction."), "the retracted flagship figure escaped"


def test_a_merge_tag_label_is_not_a_figure_the_author_asserted(registry):
    """`{{2026 Plan}}` is a field reference, not a magnitude claim.

    Merge tags are stripped before figures are counted. Every shipped tag label happens to be
    digit-free, so the strip looks inert — until a tenant adds a field whose NAME carries one,
    at which point every body rendering it would be told to source a number nobody wrote.
    `unknown-merge-tag` is the rule that judges a bad label; this one must stay out of it.
    """
    body = GOOD_BODY_1.replace("Once agents", "{{2026 Plan}} means once agents")
    hits = [v for v in _derivation(_derived_spec(body=body), registry) if v.rule == "proof-status"]
    assert not hits, [str(v) for v in hits]


def test_an_anchor_for_another_market_is_refused(registry):
    """A US reader against an SG anchor, in a registry that HOLDS a US anchor. Mirrors
    `resolve._eligible_proof` from the other side."""
    rows = [_row(country="United States")]
    hits = [
        v
        for v in _derivation(_derived_spec("sec-audit-sg"), registry, rows)
        if v.rule == "proof-status"
    ]
    assert hits, "an SG anchor was pointed at a US reader"
    # Markets are compared through `email_compliance.normalize_market`, so both sides of the
    # message are the canonical form — asserting on the raw CSV spelling would pass only while
    # the canonicaliser happened to be an identity function.
    assert "united states" in hits[0].detail and "regulator-note-sg" in hits[0].detail


def test_the_readers_own_market_anchor_is_clean(registry):
    """Same rows, the angle swapped to the one anchored where the reader is."""
    rows = [_row(country="United States")]
    hits = [
        v
        for v in _derivation(_derived_spec("sec-audit-us"), registry, rows)
        if v.rule == "proof-status"
    ]
    assert not hits, [str(v) for v in hits]


def test_a_market_with_no_recorded_anchor_ships_the_no_anchor_shape(registry):
    """The deliberate asymmetry: where the registry records NO anchor for the reader's market,
    the absence was recorded on purpose and the offer ships without one. Borrowing Singapore's
    would be confidently wrong about the reader's own regulator."""
    rows = [_row(country="United Arab Emirates")]
    hits = [
        v
        for v in _derivation(_derived_spec("sec-audit-sg"), registry, rows)
        if v.rule == "proof-status"
    ]
    assert not hits, [str(v) for v in hits]


def test_slot_attribution(registry):
    """A slot with no source id."""
    slots = (
        "\n".join(ln for ln in _FIX_SLOTS.splitlines() if not ln.startswith("slot_hedge")) + "\n"
    )
    hits = [
        v for v in _derivation(_derived_spec(slots=slots), registry) if v.rule == "slot-attribution"
    ]
    assert hits and hits[0].level == "ERROR"
    assert "'hedge'" in hits[0].detail


def test_all_five_slots_sourced_is_clean(registry):
    hits = [v for v in _derivation(_derived_spec(), registry) if v.rule == "slot-attribution"]
    assert not hits, [str(v) for v in hits]


def test_a_slot_that_contradicts_the_declared_angle_is_refused(registry):
    """Presence alone would be a declaration nothing checks. The three derivable slots are
    cross-checked against the angle, so a copied front block cannot drift from its own id."""
    slots = _FIX_SLOTS.replace("slot_claim: audit-signed", "slot_claim: transport-pinned")
    hits = [
        v for v in _derivation(_derived_spec(slots=slots), registry) if v.rule == "slot-attribution"
    ]
    assert hits, "a slot citing the wrong claim passed"
    assert "transport-pinned" in hits[0].detail and "audit-signed" in hits[0].detail


def test_the_no_anchor_offer_may_declare_slot_proof_none(registry):
    """`none` is the sanctioned value on the no-anchor shape, and only for the proof slot."""
    slots = _FIX_SLOTS.replace("slot_proof: regulator-note-sg", "slot_proof: none")
    hits = [
        v for v in _derivation(_derived_spec(slots=slots), registry) if v.rule == "slot-attribution"
    ]
    assert not hits, [str(v) for v in hits]


def test_a_slot_declared_outside_the_front_block_is_not_a_declaration(registry):
    """§R5, spec-side. A `slot_claim:` line written anywhere but the fenced front block is
    untrusted text, exactly as a `⟦TO⟧` quoted inside an inbound message is — the 2026-09-16
    reply-gate defect.

    The forged line sits in the spec's PROSE, at column 0, because that is where it is
    dangerous. A line inside a touch body carries the blockquote marker and would not match the
    field regex on the raw text either, so a body-quoted fixture proves nothing about
    `declaration_surface` — found 2026-09-24 by mutating that call out and watching this test
    stay green.
    """
    slots = (
        "\n".join(ln for ln in _FIX_SLOTS.splitlines() if not ln.startswith("slot_claim")) + "\n"
    )
    spec = _derived_spec(slots=slots).replace(
        "**Step 2 — Day 4**", "slot_claim: transport-pinned\n\n**Step 2 — Day 4**"
    )
    hits = [v for v in _derivation(spec, registry) if v.rule == "slot-attribution"]
    assert hits, "a forged slot line outside the front block satisfied the slot contract"
    # The message has to be the MISSING-source one. Asserting only that the finding mentions
    # the claim slot passed while the rule read the forged line and merely disagreed with it —
    # the operator would have been told "your slot cites the wrong claim", not "someone else
    # wrote your slot".
    assert "names no source id" in hits[0].detail, hits[0].detail
    assert "transport-pinned" not in hits[0].detail


# --- the declaration itself (2026-09-24 review, findings 1 and 2) -------------------------
#
# Until this block existed the three rules above resolved their angle through the PURE read
# (`declared_angle(spec)` with no registry), so `registry.angles.get(<typo>)` returned None and
# ALL THREE took the unmigrated branch: `claim-status` returned before the status check,
# `proof-status` dropped to WARN, `slot-attribution` returned []. Nothing reported the id. One
# mistyped word in a front block switched the whole registry gate off and the run went green.
#
# `declared_angle(spec, registry)` — the validating form — existed and was called by exactly one
# unit test. Passing it the registry is the fix; these are the three refusals it can now raise.


def _angle_rules(spec: str, registry, rows=None) -> list:
    """Just the declaration findings, so a control cannot pass on an unrelated rule."""
    violations, _ = lint_merge_render(
        _touches(spec),
        rows if rows is not None else [_row()],
        signoff="Henry",
        spec_text=spec,
        registry=registry,
    )
    return [v for v in violations if v.rule in {"angle-missing", "angle-unknown", "angle-conflict"}]


def test_angle_unknown(registry):
    """A typo in a migrated spec's `angle:` is an ERROR that names the id.

    ERROR and not WARN, and this is the one place the distinction is argued: an *absent*
    `angle:` is an unmigrated spec (0 of the live fleet declares one, so refusing it hard would
    fail everything for a re-draft the skills cannot yet produce), while an *unknown* one is a
    spec that claims to be registry-derived and names an argument nobody wrote. The second is a
    defect in this spec, not a property of the fleet.
    """
    hits = _angle_rules(_derived_spec("sec-audit-xx"), registry)
    assert hits, "a spec declaring an angle id the registry does not hold passed silently"
    assert hits[0].rule == "angle-unknown"
    assert hits[0].level == "ERROR"
    assert "sec-audit-xx" in hits[0].detail, hits[0].detail


def test_an_unknown_angle_does_not_leave_the_three_rules_silently_off(registry):
    """The finding, stated as the property rather than as the message.

    The bypass was not that the id went unreported — it was that the run went GREEN. A spec that
    names an argument nobody wrote must not be sendable, whatever the three derivation rules
    then do with a `None` angle.
    """
    violations, _ = lint_merge_render(
        _touches(_derived_spec("sec-audit-xx")),
        [_row()],
        signoff="Henry",
        spec_text=_derived_spec("sec-audit-xx"),
        registry=registry,
    )
    assert [v for v in violations if v.level == "ERROR"], "an unknown angle produced no ERROR"


def test_a_real_angle_id_resolves_and_the_three_rules_still_run(registry):
    """§R18's other half, and it has to prove two things, not one.

    That a real id raises no `angle-unknown` would pass just as well if the whole check were
    dead, so the control also asserts the rules downstream of the resolution are LIVE: the same
    spec pointed at the design-target angle still trips `claim-status`.
    """
    assert not _angle_rules(_derived_spec("sec-audit-sg"), registry)
    hits = [
        v for v in _derivation(_derived_spec("sec-transport"), registry) if v.rule == "claim-status"
    ]
    assert hits, "resolution succeeded but the rules behind it are inert"


def test_angle_missing(registry):
    """A spec that declares no `angle:` at all — WARN, and it names what it switched off.

    WARN rather than ERROR is a measurement, not a preference: on 2026-09-24 zero of the 47 live
    specs with touches declare an angle, so an ERROR here fails the whole fleet on day one for a
    migration the drafting skills cannot yet perform. What is NOT acceptable is the state this
    replaces, where the absence was silent and `slot-attribution` simply never fired on anything.
    """
    spec = "\n".join(ln for ln in _derived_spec().splitlines() if not ln.startswith("angle:"))
    hits = _angle_rules(spec, registry)
    assert hits, "a spec with no `angle:` reported nothing"
    assert hits[0].rule == "angle-missing"
    assert hits[0].level == "WARN"
    assert "slot-attribution" in hits[0].detail, hits[0].detail


def test_a_spec_that_declares_an_angle_raises_no_angle_missing(registry):
    """The control. Without it `angle-missing` would look identical to a rule that always fires."""
    assert not [v for v in _angle_rules(_derived_spec(), registry) if v.rule == "angle-missing"]


def test_angle_conflict(registry):
    """A legacy field that contradicts what the angle derives — the mid-migration shape.

    `declared_angle(spec, registry)` raises `DerivedFieldConflict` for this, and catching the
    exception family while dropping one member on the floor would re-create the very bypass
    `angle-unknown` closes, one level down: the angle would stay unresolved and the three rules
    would go quiet again.
    """
    hits = _angle_rules(_derived_spec(slots=_FIX_SLOTS + "premise: wrong-premise\n"), registry)
    assert hits, "a legacy `premise:` disagreeing with the angle passed"
    assert hits[0].rule == "angle-conflict"
    assert hits[0].level == "ERROR"
    assert "wrong-premise" in hits[0].detail and "multi-framework" in hits[0].detail


def test_a_conflicting_legacy_field_does_not_disarm_the_derivation_rules(registry):
    """The angle is authoritative and the legacy line is the stale copy, so the three rules keep
    running against it. Reported-and-skipped would be the same silence in a louder shirt."""
    slots = _FIX_SLOTS.replace("slot_claim: audit-signed", "slot_claim: transport-pinned")
    spec = _derived_spec(slots=slots + "premise: wrong-premise\n")
    assert [v for v in _derivation(spec, registry) if v.rule == "slot-attribution"], (
        "a conflicting legacy field silenced slot-attribution"
    )


def test_a_legacy_field_that_agrees_is_not_a_conflict(registry):
    """The control: same spec, same fields, the premise spelled the way the angle derives it."""
    assert not _angle_rules(
        _derived_spec(slots=_FIX_SLOTS + "premise: multi-framework\n"), registry
    )


_INERT_ON_THIS_PATH: frozenset[str] = frozenset()


def _rules_emitted_by(func_names: tuple[str, ...]) -> set[str]:
    """Rule-id literals passed to ``Violation(...)`` inside these ``outreach`` package
    functions, plus the ``lint_*`` helpers they call.

    Static rather than dynamic because the point is coverage of every branch, including ones no
    fixture in this suite happens to reach. A rule guarded behind an opt-in argument still has
    to be either catalogued or named in `_INERT_ON_THIS_PATH`.

    Walks EVERY module of the package, not one file. Before the 2026-09-24 merge this parsed
    ``outreach_pack_linter.py`` alone, so the four ``check_row`` rule ids and everything the
    render half raised were invisible to it — the gap that let the catalogue under-report the
    gate by 20 ids. The package has no single file to point at, which is what makes the
    narrower version impossible to write back by accident.
    """
    import ast
    import pathlib

    funcs: dict[str, ast.FunctionDef] = {}
    for mod in sorted((pathlib.Path(__file__).parent / "outreach").glob("*.py")):
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        funcs.update({n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)})

    def literals(node):
        out = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Violation":
                if len(n.args) >= 3 and isinstance(n.args[2], ast.Constant):
                    out.add(n.args[2].value)
                for kw in n.keywords:
                    if kw.arg == "rule" and isinstance(kw.value, ast.Constant):
                        out.add(kw.value.value)
        return out

    reached = set(func_names)
    for name in func_names:
        for n in ast.walk(funcs[name]):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "").startswith("lint_"):
                reached.add(n.func.id)
    return {r for name in reached if name in funcs for r in literals(funcs[name])}


def test_every_emitted_rule_is_catalogued():
    """The backstop in the direction that was missing until 2026-09-22.

    ``test_every_catalogue_rule_has_a_mutation`` walks catalogue -> test. Nothing walked
    emitted -> catalogue, so four rules fired on this path for weeks while absent from
    ``checks_run`` — and a rule absent from ``checks_run`` has no denominator in
    ``rule_lifecycle_report`` and can never earn keep/recalibrate/delete. The module docstring
    already claimed this property ("fails closed if a future rule is added to either call
    path"); this test is what makes the claim true.
    """
    emitted = _rules_emitted_by(("lint_email", "lint_derivation"))
    missing = sorted(emitted - set(RULE_CATALOGUE) - _INERT_ON_THIS_PATH)
    assert not missing, (
        f"emitted on the merge-render path but not in RULE_CATALOGUE: {missing}. "
        f"Add an entry (and a mutation), or name it in _INERT_ON_THIS_PATH with the reason "
        f"it cannot fire here."
    )


def test_every_catalogue_rule_has_a_mutation():
    """The backstop: every ``RULE_CATALOGUE`` entry must have exactly one test function
    above named ``test_<rule with '-' -> '_'>``. Fails the moment a 65th rule is added to
    either call path without a matching mutation — the same admission test the calibration
    PRD requires of every future lint rule, applied reflexively to this suite itself."""
    import sys

    this_module = sys.modules[__name__]
    missing = [
        rule
        for rule in RULE_CATALOGUE
        if not hasattr(this_module, f"test_{rule.replace('-', '_')}")
    ]
    assert not missing, f"no mutation test for: {sorted(missing)}"
    # 77 -> 79 on 2026-09-04: seat-stakes-missing + offer-does-their-work.
    # 79 -> 83 on 2026-09-22: credit-is-verdict, offer-not-a-solution-overview, cta-omits-gap,
    # problem-asserts-internals — all four already firing here, none catalogued. Found by the
    # new `test_every_emitted_rule_is_catalogued`, which is the reverse of this check.
    # 88 -> 71 on 2026-09-24 (outbound fact registry, FR3): -24 style rules retired (the 25th,
    # capability-unargued, was never catalogued), +3 derivation rules, +4 `score-*`/`segment-*`
    # promoted out of UNCATALOGUED_RULES. First time this number has gone DOWN, which is the
    # point of the phase. `--list-rules` prints ALL_RULE_IDS and is the number to cite (§R14).
    # 71 -> 74 on 2026-09-24 (FR3 invariant review, findings 1 and 2): angle-missing,
    # angle-unknown, angle-conflict. Not new surface — they are the reports for a declaration
    # failure the three derivation rules already branched on silently.
    assert len(RULE_CATALOGUE) == 74, (
        f"RULE_CATALOGUE grew or shrank to {len(RULE_CATALOGUE)} rules without this "
        f"suite's docstring/comment being updated to match"
    )
