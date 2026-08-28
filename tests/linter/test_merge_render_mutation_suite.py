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
``hook-cell-unknown``. Both are opt-in behind ``hook_matrix``, so they ship with three extra
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

from merge_render_linter import (
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
    "signal_clause": "Truist sits on the AARM agent-runtime-security working group",
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
        [_row(signal_clause="Truist sits on the AARM agent-runtime-security working group")],
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


def test_hedge_stem_repeat():
    # HEDGE_STEM_RE requires "my <read/hunch/bet/guess/sense>" followed by a colon within
    # 60 chars — a looser "my hunch is that..." with no colon never matches. Both touches
    # must carry the colon form for MAX_HEDGE_STEM (1) to be exceeded.
    spec = """
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{First Name}},
>
> My hunch: {{Company}} has not built per-agent attribution yet, and that is worth
> fixing before an auditor asks. Tell me if you've got this covered already.
>
> Would the one-pager be useful?
>
> Henry

**Step 2 — Day 4** (same thread, no subject)
> Hi {{First Name}},
>
> My hunch: the same gap shows up the moment one agent hands work to another at
> {{Company}}, and it is worth closing before it compounds.
>
> Want the before-and-after?
>
> Henry
"""
    _fires("hedge-stem-repeat", _touches(spec), [_row()])


def test_specificity():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> I think about identity a lot these days, and it seems like something worth a
> conversation. A lot of teams are thinking about this too, in different ways, and it is
> worth comparing notes before too long, since things move fast and nobody wants to be
> behind on it when it matters most to the business overall.
>
> Would a chat be useful?
>
> Henry
"""
    _fires("specificity", _touches(spec), [_row()])


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


def test_hedge_missing():
    spec = """
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{First Name}},
>
> Agents at {{Company}} act on regulated records today, and the audit trail does not
> capture which agent held the authority to act, which is a gap that widens every quarter
> as more agents ship into production across the estate.
>
> Would the one-pager on how another team closed it be useful?
>
> Henry
"""
    _fires("hedge-missing", _touches(spec), [_row()])


def test_antithesis():
    spec = """
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{First Name}},
>
> My hunch: this isn't just an audit problem, it's a production one, and {{Company}}'s
> agents are already acting on records an examiner will ask about. Tell me if you've got
> this covered.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("antithesis", _touches(spec), [_row()])


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
    _fires("company-transaction-entity", _touches(), [_row(company="Meridian Group Merger")])


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
    _fires("article-collision", _touches(spec), [_row(company="The Meridian Group")])


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
> Agents at {{Company}} are moving from retrieving data to acting on it, the way Truist
> mapped its own agent-attribution trail, and that is one useful pattern this year.
>
> Would the one-pager be useful?
>
> Henry
"""
    _fires("named-case-study", _touches(spec), [_row()], case_studies=("truist",))


def test_cta_unstaged_artifact():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Want the crosswalk on how another team mapped it?
>
> Henry
"""
    _fires("cta-unstaged-artifact", _touches(spec), [_row()], gift_artifacts=("one-pager",))


def test_cta_question():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Let me know if the one-pager would be useful.
>
> Henry
"""
    _fires("cta-question", _touches(spec), [_row()])


def test_cta_bundled():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Want the one-pager and a short recorded demo?
>
> Henry
"""
    _fires("cta-bundled", _touches(spec), [_row()])


def test_cta_unanchored():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Want the one-pager?
>
> Henry
"""
    _fires("cta-unanchored", _touches(spec), [_row()])


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


def test_cta_overclaim():
    spec = """
**Step 1 — Day 1** · Subject: `a quick note`
> Hi {{First Name}},
>
> Agents at {{Company}} are moving from retrieving data to acting on it, and the audit
> trail has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager on how the trail clears an examiner review be useful?
>
> Henry
"""
    _fires("cta-overclaim", _touches(spec), [_row()])


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


def test_hook_cell_missing():
    """A spec that declares no cell: nothing can check which argument it carries."""
    hits = _fires(
        "hook-cell-missing",
        _touches(),
        [_row()],
        spec_text=_spec_front(),
        hook_matrix=MINI_MATRIX,
    )
    assert hits[0].level == "ERROR"


def test_hook_cell_unknown():
    """A declared cell the matrix does not define — an invented hook wearing a declaration."""
    hits = _fires(
        "hook-cell-unknown",
        _touches(),
        [_row()],
        spec_text=_spec_front("hook_cell:   Head of Vibes × Compliance event"),
        hook_matrix=MINI_MATRIX,
    )
    assert hits[0].level == "ERROR"


def test_a_declared_cell_that_exists_fires_neither_hook_rule():
    """The negative control: a real cell is clean, so the rules discriminate."""
    violations, _ = lint_merge_render(
        _touches(),
        [_row()],
        signoff="Henry",
        spec_text=_spec_front("hook_cell:   FinOps lead × Compliance event"),
        hook_matrix=MINI_MATRIX,
    )
    assert not [v for v in violations if v.rule.startswith("hook-cell")]


def test_hook_cell_checks_are_silent_without_a_matrix():
    """Off unless --hook-matrix is passed — pinned, because every spec written before the
    field exists would otherwise fail, and because the opposite mistake (an opt-in check
    nobody opts into) is the one this PRD is about. The caller that must opt in is the
    ``email-sequence`` merge-render gate step."""
    violations, _ = lint_merge_render(
        _touches(), [_row()], signoff="Henry", spec_text=_spec_front()
    )
    assert not [v for v in violations if v.rule.startswith("hook-cell")]


def test_an_unsupported_matrix_shape_warns_rather_than_passing_silently():
    """A matrix with no persona/signal axis cannot verify a declaration. That is reported,
    not swallowed — a gate that passes by finding nothing is the failure being fixed."""
    flat = "# Hook matrix\n\n| id | angle | payoff | formats | status |\n|---|---|---|---|---|\n"
    flat += "| a | an angle | a payoff | carousel | test |\n"
    hits = _fires(
        "hook-cell-unknown",
        _touches(),
        [_row()],
        spec_text=_spec_front("hook_cell:   CISO × Compliance event"),
        hook_matrix=flat,
    )
    assert hits[0].level == "WARN"


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


def test_signal_column_unknown():
    """An invented signal value — not a column of the row's own segment grid at all."""
    hits = _fires(
        "signal-column-unknown",
        _touches(),
        [_row(segment="Enterprise", signal_column="A signal nobody declared")],
        spec_text=_SPEC_DECLARING_COMPLIANCE,
        hook_matrix=MINI_MATRIX,
    )
    assert hits[0].level == "ERROR"


def test_signal_cell_mismatch():
    """A real, valid signal for this row's grid — just not the one the spec declares. This
    is the exact defect the PRD exists to fix: the copy asserts one trigger, the recipient's
    own recorded evidence attests a different one."""
    hits = _fires(
        "signal-cell-mismatch",
        _touches(),
        [_row(segment="Enterprise", signal_column="M&A / consolidation")],
        spec_text=_SPEC_DECLARING_COMPLIANCE,  # spec declares "Compliance event"
        hook_matrix=MINI_MATRIX,
    )
    assert hits[0].level == "ERROR"


def test_signal_column_unrecorded():
    """No row in the list records the column at all — the migration line, not a per-row
    wall. Without this, `signal-cell-mismatch` is silently inert on every pre-existing
    list, which is precisely how `HOOK_CELL_COLUMN` went unpopulated for months."""
    hits = _fires(
        "signal-column-unrecorded",
        _touches(),
        [_row(segment="Enterprise")],  # no signal_column key at all
        spec_text=_SPEC_DECLARING_COMPLIANCE,
        hook_matrix=MINI_MATRIX,
    )
    assert hits[0].level == "WARN"
    assert hits[0].email == "SPEC"


def test_signal_column_unrecorded_reports_once_not_per_row():
    """Ten unrecorded rows must produce ONE aggregate finding, never ten — the same
    finding-budget discipline `cell-row-unrecorded` already follows."""
    rows = [_row(segment="Enterprise", email=f"r{i}@cascade.example") for i in range(10)]
    violations, _ = lint_merge_render(
        _touches(),
        rows,
        signoff="Henry",
        spec_text=_SPEC_DECLARING_COMPLIANCE,
        hook_matrix=MINI_MATRIX,
    )
    hits = [v for v in violations if v.rule == "signal-column-unrecorded"]
    assert len(hits) == 1


def test_a_matching_signal_column_fires_neither_signal_rule():
    """The negative control: a row recording the spec's own declared signal, in a valid
    grid, is silent on both signal-cell rules."""
    violations, _ = lint_merge_render(
        _touches(),
        [_row(segment="Enterprise", signal_column="Compliance event")],
        signoff="Henry",
        spec_text=_SPEC_DECLARING_COMPLIANCE,
        hook_matrix=MINI_MATRIX,
    )
    assert not [v for v in violations if v.rule in _SIGNAL_CELL_RULES]


def test_signal_cell_checks_are_silent_without_a_matrix():
    """Off unless --hook-matrix is passed — the same off-state discipline as the hook-cell
    pair: a rule with no pinned off-state is how a correct check ships silently inert."""
    violations, _ = lint_merge_render(
        _touches(),
        [_row(segment="Enterprise", signal_column="A signal nobody declared")],
        signoff="Henry",
        spec_text=_SPEC_DECLARING_COMPLIANCE,
    )
    assert not [v for v in violations if v.rule in _SIGNAL_CELL_RULES]


def test_suppressed_rows_are_not_signal_linted():
    """A suppressed row is not being sent; a mismatched signal on it is not actionable."""
    violations, _ = lint_merge_render(
        _touches(),
        [_row(segment="Enterprise", signal_column="A signal nobody declared", suppression="dnc")],
        signoff="Henry",
        spec_text=_SPEC_DECLARING_COMPLIANCE,
        hook_matrix=MINI_MATRIX,
    )
    assert not [v for v in violations if v.rule in _SIGNAL_CELL_RULES]


def test_an_unresolved_title_is_not_double_reported_by_the_signal_rules():
    """A title `persona_of` cannot place is `hook_coverage`'s `unresolved` bucket's concern,
    not this rule's — reporting it again here under a different name would count the same
    gap twice."""
    violations, _ = lint_merge_render(
        _touches(),
        [
            _row(
                title="Regional Sales Manager",
                segment="Enterprise",
                signal_column="M&A / consolidation",
            )
        ],
        signoff="Henry",
        spec_text=_SPEC_DECLARING_COMPLIANCE,
        hook_matrix=MINI_MATRIX,
    )
    assert not [v for v in violations if v.rule in _SIGNAL_CELL_RULES]


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


def test_stakes_missing():
    """A spec that declares no `stakes:`. The gap the body names has no stated cost, and
    nothing can check `voice.md` job 4 against the copy. WARN, not ERROR: every spec written
    before 2026-08-23 predates the field, exactly as `premise-missing` handles its own."""
    hits = _fires("stakes-missing", _touches(), [_row()], spec_text=_spec_front())
    assert hits[0].level == "WARN"
    assert hits[0].email == "SPEC"


def test_stakes_unattested():
    """The rule that does the work: a spec CLAIMING a consequence its body never states.

    This is the shipped 2026-08-21 defect in miniature. Both ship30 specs asserted in prose
    that they had named the stakes while the copy carried none, and because the assertion
    lived in a §2 section no gate reads, it survived two rounds and the operator caught it by
    hand both times. Declaring the consequence as a field makes the same false claim
    refutable in CI."""
    hits = _fires(
        "stakes-unattested",
        _touches(),
        [_row()],
        spec_text=_spec_front("stakes:      the next buyer asks again and it is rewritten"),
    )
    assert hits[0].level == "ERROR"
    assert hits[0].email == "SPEC"


def test_stakes_attested_passes_across_a_line_wrap():
    """A declared consequence the body DOES carry must pass even though the body hard-wraps.

    The bodies wrap at ~90 chars, so a declared phrase routinely straddles a newline. Naive
    substring matching would fail every correctly-written spec — the rule would then be
    "delete the stakes field", which is the opposite of the point. Whitespace-normalised
    matching is what makes the rule usable rather than merely present."""
    body = (
        "Hi {{First Name}},\n\n"
        "{{Why Now}}. Once agents at {{Company}} move from retrieving data inside Epic to "
        "acting on it, identity becomes the question an auditor asks first. Tell me if "
        "you've got this covered: your logs capture which account touched a record, not "
        "which agent held the authority to act. So the next\naudit is another manual "
        "reconstruction.\n\n"
        "Would the one-pager on how another team mapped that to {{Company}}'s agent path be "
        "useful?\n\n"
        "Henry"
    )
    spec = _spec_front("stakes:      the next audit is another manual reconstruction")
    spec += (
        "\n**Step 1 — Day 1** · Subject: `x`\n"
        + _blockquote(body)
        + "\n\n**Step 2 — Day 4**\n"
        + _blockquote(GOOD_BODY_2)
        + "\n"
    )
    violations, _ = lint_merge_render(parse_spec(spec), [_row()], signoff="Henry", spec_text=spec)
    assert not [v for v in violations if v.rule.startswith("stakes-")], [
        str(v) for v in violations if v.rule.startswith("stakes-")
    ]


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
    assert len(RULE_CATALOGUE) == 77, (
        f"RULE_CATALOGUE grew or shrank to {len(RULE_CATALOGUE)} rules without this "
        f"suite's docstring/comment being updated to match"
    )
