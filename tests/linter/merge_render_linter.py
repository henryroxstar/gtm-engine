#!/usr/bin/env python3
"""Merge-render linter — the gate for a *sequenced* (mail-merge) send.

``outreach_pack_linter.py`` judges hand-written 1:1 packs, where every email is already
its final text. A sequence is different: three templates are rendered against N prospect
rows at send time, and the copy gate never sees the result. On 2026-07-28 that gap let
334 rows pass with **0 errors across 1,002 renders** while 9 of them would have sent
"Hi 🍦," or "agents at Canopus GBS | SAP Consulting | AI & Automation | move...".

This linter closes it by doing what the sequencer will do:

  1. parse the touches out of a sequence spec (the ``**Step N — Day X**`` + blockquote
     format the specs already use — no new artifact to author),
  2. verify every ``{{merge tag}}`` is a field label the provider actually exposes
     (the "Company Domain Name" vs "Company Domain" bug, caught before the 400),
  3. render EVERY touch against EVERY row of the prospect CSV,
  4. run the real copy rules (``outreach_pack_linter.lint_email``) on each render, and
  5. run ``gtm_core.merge_hygiene.check_row`` on each row's field values.

Exit 1 on any ERROR — a copy violation in a render, an unknown merge tag, or a blocking
merge-field defect. Warnings advise and never gate.

Usage::

    uv run python tests/linter/merge_render_linter.py \\
        content/<profile>/prospects/sequences/spec-<name>.md \\
        --csv content/<profile>/prospects/sequences/ready-to-load.csv \\
        --signoff Henry \\
        --ban-file profiles/<profile>/knowledge/voice-bans.txt \\
        --case-study-file profiles/<profile>/knowledge/outreach-case-studies.txt \\
        --stem-file profiles/<profile>/knowledge/outreach-banned-stems.txt \\
        --artifact-file profiles/<profile>/knowledge/gift-artifacts.txt
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from outreach_pack_linter import (  # noqa: E402
    EmailBlock,
    Violation,
    _anchors,
    _load_bans,
    lint_email,
    lint_hedge_stem,
)

from gtm_core.merge_hygiene import (  # noqa: E402
    check_row,
    ends_in_sibilant,
    signal_clause,
    signal_is_event,
    signal_on_topic,
    signal_stray_digits,
    starts_with_article,
)

RULES_VERSION = "2026-08-20"

# Merge tags a provider field list is expected to expose. Keep in sync with
# Saleshandy's `list_fields` labels; override per-run with --fields.
DEFAULT_FIELD_LABELS = (
    "First Name",
    "Last Name",
    "Email",
    "Company",
    "Company Domain",
    "Company Website",
    "Company Industry",
    "Job Title",
    "Industry",
    "Department",
    "City",
    "State",
    "Country",
    "Phone Number",
    "LinkedIn",
    "Website",
    # Custom fields this repo's sequences rely on. A custom field must exist in the
    # provider before import; --fields with the live label list is the real check.
    "Why Now",
)

# CSV column each merge tag reads from. A tag with no mapping here can still be valid
# for the provider; it is reported as unrenderable-from-this-CSV instead.
TAG_TO_COLUMN = {
    "First Name": "first",
    "Last Name": "last",
    "Email": "email",
    "Company": "company",
    "Company Domain": "company_domain",
    "Job Title": "title",
    "City": "city",
    "Country": "country",
    # NOT why_now: the raw research column is a multi-fact operator note. Only the
    # reduced, fail-closed clause is safe to render.
    "Why Now": "signal_clause",
}

_STEP_RE = re.compile(
    r"^\*\*Step\s+(?P<n>\d+)\s*[—\-–]\s*Day\s+(?P<day>\d+)\*\*(?P<rest>.*?)$",
    re.MULTILINE,
)
_SUBJECT_RE = re.compile(r"Subject:\s*`(?P<subject>[^`]*)`")
_MERGE_TAG_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


@dataclass
class Touch:
    number: int
    day: int
    subject: str  # "" for a same-thread follow-up
    body: str


def parse_spec(text: str) -> list[Touch]:
    """Pull the touches out of a sequence spec.

    A touch is a ``**Step N — Day D**`` header (optionally carrying ``Subject: `...` ``)
    followed by the body as a markdown blockquote. Everything outside a blockquote
    between headers is prose about the touch and is ignored.
    """
    touches: list[Touch] = []
    matches = list(_STEP_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        sm = _SUBJECT_RE.search(m.group("rest"))
        body_lines: list[str] = []
        started = False
        for line in block.splitlines():
            if line.startswith(">"):
                started = True
                body_lines.append(line[1:].removeprefix(" "))
            elif started and not line.strip():
                body_lines.append("")
            elif started:
                break
        body = "\n".join(body_lines).strip("\n")
        body = re.sub(r"\n{3,}", "\n\n", body)
        if body:
            touches.append(
                Touch(
                    number=int(m.group("n")),
                    day=int(m.group("day")),
                    subject=(sm.group("subject").strip() if sm else ""),
                    body=body,
                )
            )
    return touches


def render(template: str, row: dict) -> str:
    """Substitute every ``{{Tag}}`` from ``row`` via :data:`TAG_TO_COLUMN`.

    An unmapped tag is left in place so ``lint_email``'s placeholder rule reports it —
    an unresolved tag reaching a prospect is exactly the failure this linter exists for.
    """

    def sub(m: re.Match[str]) -> str:
        col = TAG_TO_COLUMN.get(m.group(1).strip())
        if col is None:
            return m.group(0)
        return (row.get(col) or "").strip()

    return _MERGE_TAG_RE.sub(sub, template)


def lint_merge_tags(touches: list[Touch], field_labels: tuple[str, ...]) -> list[Violation]:
    """Every merge tag must be a label the provider exposes AND be resolvable from the CSV."""
    out: list[Violation] = []
    known = {f.lower() for f in field_labels}
    for t in touches:
        for tag in set(_MERGE_TAG_RE.findall(t.subject + "\n" + t.body)):
            tag = tag.strip()
            if tag.lower() not in known:
                out.append(
                    Violation(
                        "ERROR",
                        f"touch{t.number}",
                        "unknown-merge-tag",
                        f"{{{{{tag}}}}} is not a provider field label — the send 400s or "
                        f"renders literally",
                    )
                )
            elif tag not in TAG_TO_COLUMN:
                out.append(
                    Violation(
                        "WARN",
                        f"touch{t.number}",
                        "merge-tag-not-in-csv",
                        f"{{{{{tag}}}}} is a valid field but has no column in this CSV",
                    )
                )
    return out


_POSSESSIVE_RE = re.compile(r"\{\{\s*Company\s*\}\}['’]s")


def lint_possessive(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """Flag ``{{Company}}'s`` against companies that already end in s/x/z.

    Reported once per touch with a count, not once per row: the defect is in the
    *template*, and 66 identical row warnings bury the one line that needs changing.
    Rephrasing to "the stack at {{Company}}" reads correctly for every name in the list.
    """
    affected = [r for r in rows if ends_in_sibilant(r.get("company") or "")]
    if not affected:
        return []
    out: list[Violation] = []
    for t in touches:
        if _POSSESSIVE_RE.search(t.body) or _POSSESSIVE_RE.search(t.subject):
            examples = ", ".join(f"{(r.get('company') or '').strip()}'s" for r in affected[:3])
            out.append(
                Violation(
                    "WARN",
                    f"touch{t.number}",
                    "possessive-sibilant",
                    f"{{{{Company}}}}'s renders a double sibilant for {len(affected)}/{len(rows)} "
                    f"rows ({examples}) — rephrase to 'at {{{{Company}}}}'",
                )
            )
    return out


_ARTICLE_BEFORE_TAG_RE = re.compile(r"\b(the|a|an)\s+\{\{\s*Company\s*\}\}", re.IGNORECASE)


def lint_article_collision(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """Flag ``the {{Company}}`` against companies that carry their own leading article.

    Renders "the The Meridian Group stack" / "the A Better Place stack". The article
    belongs to the brand, so the data is right and the template is wrong: "the stack at
    {{Company}}" is correct for every name, article or not.

    This rule exists because fixing the possessive (``{{Company}}'s`` -> ``the {{Company}}``)
    introduced exactly this bug. One collision class traded for another; both are caught here.
    """
    affected = [r for r in rows if starts_with_article(r.get("company") or "")]
    if not affected:
        return []
    out: list[Violation] = []
    for t in touches:
        if _ARTICLE_BEFORE_TAG_RE.search(t.body) or _ARTICLE_BEFORE_TAG_RE.search(t.subject):
            examples = ", ".join(f"the {(r.get('company') or '').strip()}" for r in affected[:3])
            out.append(
                Violation(
                    "WARN",
                    f"touch{t.number}",
                    "article-collision",
                    f"'the {{{{Company}}}}' doubles the article for {len(affected)}/{len(rows)} "
                    f"rows ({examples}) — rephrase to 'at {{{{Company}}}}'",
                )
            )
    return out


def lint_unused_signal_columns(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """Flag researched per-prospect signal the copy never renders.

    ``why_now`` is the dated, specific trigger the prospecting run worked to find, and the
    skill's own rule is that a touch opens on it. A merge template that ignores a populated
    ``why_now`` column sends generic copy to prospects we *have* a real reason to contact —
    the research is paid for and then discarded. Advisory: splitting a sequence by signal is
    a campaign decision, not something a linter should force.
    """
    all_copy = "\n".join(t.subject + "\n" + t.body for t in touches)
    if re.search(r"\{\{\s*Why Now\s*\}\}", all_copy, re.IGNORECASE):
        return []
    # Count only rows whose why_now actually REDUCES to a sendable clause. A populated
    # column is not the same as a usable signal — most of this pool's values are
    # intent-topic scores, and reporting those as wasted research overstates the gap and
    # points at work that cannot be done.
    usable = [r for r in rows if signal_clause(r.get("why_now") or "")]
    if not usable:
        return []
    return [
        Violation(
            "WARN",
            "SPEC",
            "unused-signal-column",
            f"{len(usable)}/{len(rows)} rows reduce to a sendable 'why_now' clause that no "
            f"touch renders — split with `prospects_consolidate split-by-signal`",
        )
    ]


def lint_empty_merge_tags(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """A rendered tag that is blank for some rows — the failure that forces the split.

    Saleshandy substitutes an empty string for a missing value, so ``Saw the news out of
    {{Company}}: {{Why Now}}.`` becomes ``Saw the news out of Acme: .`` for every row whose
    column is empty. Nothing else catches this: the copy linter sees a grammatical sentence
    with a word missing, and the merge-tag check only verifies the label *exists*.

    ERROR, because a blank tag ships broken copy to a real prospect. The fix is to split the
    list (``prospects_consolidate split-by-signal``) so each sequence gets rows that can
    fill every tag it renders — not to add a fallback, which just sends filler.
    """
    out: list[Violation] = []
    rendered = {
        tag
        for tag, col in TAG_TO_COLUMN.items()
        for t in touches
        if re.search(r"\{\{\s*" + re.escape(tag) + r"\s*\}\}", t.subject + "\n" + t.body)
    }
    for tag in sorted(rendered):
        column = TAG_TO_COLUMN[tag]
        blank = [r for r in rows if not (r.get(column) or "").strip()]
        if blank:
            who = ", ".join((r.get("email") or "?").strip() for r in blank[:3])
            out.append(
                Violation(
                    "ERROR",
                    "SPEC",
                    "empty-merge-tag",
                    f"{{{{{tag}}}}} renders blank for {len(blank)}/{len(rows)} rows "
                    f"(column '{column}': {who}) — split the list rather than send a gap",
                )
            )
    return out


# A touch must carry at least this share of recipient-derived words. Measured 2026-08-19
# on the Run-500 specs: touch 1 was 19-20% recipient-unique and touches 2 and 3 were 3-4%
# each, because the only thing varying in them was {{Company}} substituted twice. 453
# people received a byte-identical step 2. One forward between two recipients ends the
# campaign, and no other rule sees it: every individual render lints clean, and the
# batch-level 6-gram ceiling in outreach_pack_linter compares FILES in a run, not touches
# in a sequence.
MIN_UNIQUE_SHARE = 0.10


def _unique_share(touch: Touch, rows: list[dict]) -> float:
    """Share of a rendered touch's words that come from the recipient's own row.

    Measured against the mean rendered length rather than any one row, so a single verbose
    clause cannot flatter the whole touch.
    """
    total = uniq = 0.0
    for r in rows:
        body = render(touch.body, r)
        words = len(re.findall(r"[A-Za-z'’-]+", body))
        if not words:
            continue
        filled = 0
        for tag, col in TAG_TO_COLUMN.items():
            hits = len(re.findall(r"\{\{\s*" + re.escape(tag) + r"\s*\}\}", touch.body))
            if hits:
                filled += hits * len(re.findall(r"[A-Za-z'’-]+", (r.get(col) or "")))
        total += words
        uniq += filled
    return (uniq / total) if total else 0.0


_SIGNAL_CONTRADICTION_RE = re.compile(
    r"\b(agent identit(?:y|ies)|its own identity|verified agent identit\w*"
    r"|agent registration|approval scopes?|token auth\w*"
    r"|credentials? (?:per|for) (?:each |every )?agent"
    r"|standardi[sz]e?s? (?:enterprise[- ]wide )?on"
    # Widened 2026-08-19 after a fresh 50-recipient review: the first pass keyed on OUR
    # phrasing and so missed six rows whose clause ships the same CATEGORY under another
    # name — Experian's "Know Your Agent", Kite's "Agent Passport", Thoughtworks'
    # "governed control plane", StarHub's agent kill-switch, Skyflow's agent-policy layer,
    # AiPrise's agentic identity orchestration. Pitching agent identity to the company
    # selling agent identity is the same defect wearing a different vocabulary.
    r"|know your agent|agent (?:passport|trust|governance|registry|control plane)"
    r"|identity (?:and payment )?layer for ai agents|governed control plane"
    r"|enforces polic(?:y|ies) on agent|governance and kill switch"
    r"|agentic identity verification)\b",
    re.IGNORECASE,
)

# "Agents" is a homonym, and the insurance/real-estate sense is a person. A body about
# proving which AI agent acted, opening on a clause about a carrier's independent sales
# agents, is nonsense to the reader — and `signal_on_topic` waves it through precisely
# BECAUSE the word "agents" is present. Fires only when the clause carries no AI marker at
# all, so a genuine hybrid ("a multi-agent PatientGPT system with independent agents")
# still passes.
_AGENT_HOMONYM_RE = re.compile(
    r"\b(independent|insurance|real estate|licensed|captive|travel|booking)\s+agents?\b"
    r"|\bagents?\s+and\s+(advisors|brokers)\b",
    re.IGNORECASE,
)
_AI_MARKER_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|agentic|multi[- ]agent|llm|copilot|genai"
    r"|autonomous|chatbot|machine learning)\b",
    re.IGNORECASE,
)


def lint_signal_agent_homonym(rows: list[dict]) -> list[Violation]:
    """Fail a row whose clause means human sales agents, not AI agents."""
    out: list[Violation] = []
    col = TAG_TO_COLUMN["Why Now"]
    for r in rows:
        clause = signal_clause(r.get(col) or "")
        m = _AGENT_HOMONYM_RE.search(clause)
        if m and not _AI_MARKER_RE.search(clause):
            out.append(
                Violation(
                    "ERROR",
                    (r.get("email") or "?").strip(),
                    "signal-agent-homonym",
                    f'clause means human agents, not AI agents ("{m.group(0)}") — the body '
                    f"would read as nonsense; re-research or suppress",
                )
            )
    return out


def lint_signal_contradiction(rows: list[dict]) -> list[Violation]:
    """Fail a row whose clause announces the capability the body claims is missing.

    The 2026-08-19 76-recipient persona review found 11 sends whose researched fact
    *refuted* the pitch: T-Mobile ("requires every AI agent to authenticate with its own
    identity"), Stripe ("assigns agent identities and approval scopes"), Amex ("agent
    registration"). Quoting the counter-evidence in line one and asserting the gap in
    line two proves the sender never read their own research — the most expensive send in
    the batch, and invisible to `signal_on_topic`, which these clauses pass *because*
    they are perfectly on-topic. Remedy is per-row: re-angle the clause or suppress the
    row; never soften the body to fit it.
    """
    out: list[Violation] = []
    col = TAG_TO_COLUMN["Why Now"]
    for r in rows:
        clause = signal_clause(r.get(col) or "")
        m = _SIGNAL_CONTRADICTION_RE.search(clause)
        if m:
            out.append(
                Violation(
                    "ERROR",
                    (r.get("email") or "?").strip(),
                    "signal-contradicts-pitch",
                    f'clause says they already have this ("{m.group(0)}") — the body '
                    f"would assert a gap the fact refutes; re-angle or suppress",
                )
            )
    return out


def lint_touch_personalisation(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """Fail a touch whose copy is effectively identical for every recipient.

    ERROR, not WARN: a follow-up that varies only by the company name is the property a
    recipient detects fastest, and it is invisible to every per-render rule.
    """
    out: list[Violation] = []
    # Only fires when the rows actually carry signal a touch COULD have used. A generic
    # sequence sent to a list with no researched trigger has nothing to personalise with,
    # and failing it would block a legitimate send rather than improve one — the same
    # "report only work that can be done" rule `lint_unused_signal_columns` follows.
    if not rows or not any(signal_clause(r.get(TAG_TO_COLUMN["Why Now"]) or "") for r in rows):
        return out
    for t in touches:
        # A same-thread follow-up (no new subject) is read attached to the touch above it,
        # which already carried the recipient's own signal. Its personalisation lives in
        # the quoted thread, and repeating the clause verbatim three days later is its own
        # tell. A touch that opens a NEW thread has no such context and must stand alone.
        if not t.subject:
            continue
        share = _unique_share(t, rows)
        if share < MIN_UNIQUE_SHARE:
            out.append(
                Violation(
                    "ERROR",
                    "SPEC",
                    "touch-not-personalised",
                    f"step {t.number} is only {share:.0%} recipient-derived "
                    f"(min {MIN_UNIQUE_SHARE:.0%}) — every recipient gets near-identical copy; "
                    f"carry the row's own signal into this touch",
                )
            )
    return out


def lint_signal_relevance(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """A rendered signal clause must be about the same subject as the body it opens.

    Only fires when the copy actually renders ``{{Why Now}}``: a sequence that does not
    open on the signal has nothing to be irrelevant to.

    ERROR on off-topic, because those two paragraphs visibly do not connect
    ("<Company> advises fintech firms on their sale transactions." then "agents acting on
    regulated records at <Company>..."). WARN on a clause with no event verb, which is a
    weaker opener but still a true, specific fact about the recipient.
    """
    out: list[Violation] = []
    all_copy = "\n".join(t.subject + "\n" + t.body for t in touches)
    if not re.search(r"\{\{\s*Why Now\s*\}\}", all_copy, re.IGNORECASE):
        return out

    column = TAG_TO_COLUMN["Why Now"]
    off = [r for r in rows if (r.get(column) or "").strip() and not signal_on_topic(r[column])]
    static = [
        r
        for r in rows
        if (r.get(column) or "").strip()
        and signal_on_topic(r[column])
        and not signal_is_event(r[column])
    ]
    for r in off:
        out.append(
            Violation(
                "ERROR",
                (r.get("email") or "?").strip(),
                "signal-off-topic",
                f"clause never mentions the subject this body claims: {r[column]!r}",
            )
        )
    for r in rows:
        stray = signal_stray_digits(r.get(column) or "", r.get("company") or "")
        if stray:
            out.append(
                Violation(
                    "ERROR",
                    (r.get("email") or "?").strip(),
                    "signal-stray-digit",
                    f"clause carries a date or metric ({', '.join(stray)}) — an opener states "
                    f"what happened, not when or how much: {r[column]!r}",
                )
            )
    if static:
        who = ", ".join((r.get("email") or "?").strip() for r in static[:3])
        out.append(
            Violation(
                "WARN",
                "SPEC",
                "signal-not-an-event",
                f"{len(static)}/{len(rows)} clauses are standing descriptions with no event "
                f"verb, not dated triggers ({who})",
            )
        )
    return out


def lint_same_company_divergence(touches: list[Touch], rows: list[dict]) -> list[Violation]:
    """Flag two or more contacts at one company receiving byte-identical copy.

    The pack linter enforces divergence for hand-written 1:1 packs. A merge sequence is
    strictly worse: colleagues who compare notes see the same template with their own name
    swapped in. Worth surfacing every run — the list grows, and a normalization pass can
    merge two spellings of one company into a cluster that wasn't there before.
    """
    by_company: dict[str, list[dict]] = {}
    for r in rows:
        key = (r.get("company") or "").strip().lower()
        if key:
            by_company.setdefault(key, []).append(r)

    out: list[Violation] = []
    for key, group in sorted(by_company.items()):
        if len(group) < 2:
            continue
        for t in touches:
            bodies = {render(t.body, r) for r in group}
            if len(bodies) < len(group):
                who = ", ".join(f"{(r.get('email') or '').strip()}" for r in group[:4])
                out.append(
                    Violation(
                        "WARN",
                        f"touch{t.number}",
                        "same-company-identical-copy",
                        f"{len(group)} contacts at {group[0].get('company')!r} receive identical "
                        f"copy ({who}) — vary the touch or enroll one of them",
                    )
                )
    return out


# A copy rule that reads a rendered body cannot tell the TEMPLATE's words from the ROW's.
# Found 2026-08-19: a startup-seat touch that renders {{Why Now}} tripped
# `persona-lead-mismatch` for one recipient because that recipient's own signal clause
# happened to contain the word "attribution" ("...InfluenceOS for creator commerce
# attribution"). The copy led on nothing of the sort. Judging the seat lead is a judgement
# about the copy the author wrote, so a term that appears ONLY in merged data is not
# evidence about it.
#
# Deliberately narrow: only rules that fire on a vocabulary hit are eligible, and only when
# the term is absent from the template itself. Every mechanical rule (word count, em dash,
# greeting) still sees the full rendered string, because those defects are real no matter
# which half of the render produced them.
_DATA_BORNE_ELIGIBLE = frozenset({"persona-lead-mismatch"})


def _is_data_borne(v: Violation, merged_values: str) -> bool:
    """True when ``v``'s evidence lives only in the row's merged values, not the copy."""
    if v.rule not in _DATA_BORNE_ELIGIBLE or not merged_values:
        return False
    terms = re.findall(r"\(([^()]*)\)", v.detail)
    if not terms:
        return False
    borrowed = [t.strip() for t in terms[0].split(",") if t.strip()]
    return bool(borrowed) and all(t.lower() in merged_values for t in borrowed)


#: Above this failing share, `premise-unsupported` reports one aggregate finding instead of
#: one per row. Same reasoning as `gtm_core.finding_budget`'s SATURATED band and the same
#: number: a class firing on more than half a population is describing the population.
PREMISE_SATURATION = 0.5


def lint_premise(spec_text: str, rows: list[dict], premise_vocab: dict | None) -> list[Violation]:
    """Can each row's own research carry the premise this spec's body requires?

    Three rules:

    * ``premise-missing`` (WARN) — the spec declares no ``premise:``. WARN and not ERROR
      because unlike ``hook_cell`` this field is new and every pre-2026-08-21 spec predates
      it; a hard failure would block copy that is otherwise fine. Promote once the fleet
      has migrated.
    * ``premise-unknown`` (ERROR) — a premise the profile's ``premise-vocab.toml`` does not
      define. Same reasoning as ``hook-cell-unknown``: the vocabulary file IS the vocabulary,
      and a spec free to invent premise ids can declare conformance to nothing.
    * ``premise-unsupported`` (ERROR) — the row's recorded evidence carries fewer than
      ``min_distinct`` attesting terms. This is the rule the operator's six "doesn't establish
      the claim" rejections were reaching for and no gate had.

    Off unless ``premise_vocab`` is supplied, matching how ``--hook-matrix`` and
    ``--case-study-file`` gate their checks. That convention is exactly how
    ``cta-unstaged-artifact`` sat inert for months, so the CLI passes it whenever the profile
    ships the file, and ``email-sequence``'s gate step passes ``--profile``.

    Reports per row (not collapsed template-wide): unlike a copy defect, this genuinely IS
    per-row — the template is constant and the evidence is not. A run where it fires on every
    row is telling you the LIST is wrong for this argument, which is a different action than
    editing the copy, and the itemisation is what makes that visible.
    """
    if not premise_vocab:
        return []
    from gtm_core.hook_coverage import declared_premise, premise_unsupported

    key = declared_premise(spec_text or "")
    if not key:
        return [
            Violation(
                "WARN",
                "SPEC",
                "premise-missing",
                "no `premise:` in the spec front block — what this body needs the recipient's "
                "own facts to establish is undeclared, so no row can be checked against it",
            )
        ]
    premise = premise_vocab.get(key)
    if premise is None:
        return [
            Violation(
                "ERROR",
                "SPEC",
                "premise-unknown",
                f"declared premise {key!r} is not defined in the profile's premise-vocab.toml "
                f"({', '.join(sorted(premise_vocab)) or 'no premises defined'})",
            )
        ]
    from gtm_core.hook_coverage import premise_attestation

    out: list[Violation] = []
    # A PASS carried by one common word is worth reporting even when nothing fails, because
    # nothing else in the run contradicts it. `cross-org-agents` has `min_distinct = 1` and
    # lists `partner` among its terms; measured on the shipped enterprise-security list, all
    # 15 rows passed and 9 attested on a single term, 6 of those on the bare word "partner" —
    # describing a commercial relationship (an integrator joining a partner network, a
    # hospital selecting a vendor), not agents crossing an organisational boundary, which is
    # what the premise's own claim asserts. That is the "relevance is not entailment" error
    # this rule exists to catch, committed by the rule.
    #
    # WARN, and the fix is not here: `min_distinct` and the term list live in the tenant's
    # `premise-vocab.toml`, so tightening them is the operator's call about their own
    # messaging. Code's job is to stop the weak pass reading like a strong one.
    solo = premise_attestation(rows, premise)
    if rows and sum(solo.values()) > len(rows) / 2:
        top = ", ".join(f"{term} x{n}" for term, n in solo.most_common(3))
        out.append(
            Violation(
                "WARN",
                "SPEC",
                "premise-thin",
                f"{sum(solo.values())}/{len(rows)} rows attest premise {premise.key!r} on a "
                f"SINGLE term ({top}). With min_distinct={premise.min_distinct} this is a "
                f"word-presence check, not entailment — the claim is "
                f"{premise.claim or premise.key!r}. Tighten the terms or raise min_distinct "
                f"in premise-vocab.toml if these rows do not actually establish it.",
            )
        )

    failures = premise_unsupported(rows, premise)
    if not failures:
        return out

    # Saturation, handled the way `finding_budget` and `leadership-freshness` already handle
    # it. Measured 2026-08-21 on the four re-cut lists this rule was written for: it fails
    # 56/61 builder rows, 50/57 security, 54/71 exec, 17/18 architect. Those numbers are
    # CORRECT — Propel's evidence is one protocol, Autodesk's is one product on one platform,
    # DBS's is one assistant — and printing 184 identical-shaped ERRORs would bury that under
    # its own volume, which is the failure mode this repo keeps re-learning.
    #
    # Past the threshold this is not a finding about the rows. It is a finding about the
    # ARGUMENT: the spec is asking this list to establish something it was never selected to
    # establish. The remedy is to change the argument or change the list, and neither is
    # visible from a wall of per-row lines.
    share = len(failures) / len(rows) if rows else 0.0
    if share > PREMISE_SATURATION:
        exemplars = "; ".join(f.email for f in failures[:3])
        return out + [
            Violation(
                "ERROR",
                "SPEC",
                "premise-unsupported",
                f"{len(failures)}/{len(rows)} rows ({share:.0%}) cannot attest premise "
                f"{premise.key!r} — SATURATED: this is a property of the LIST against this "
                f"argument, not a defect in each row. The body claims "
                f"{premise.claim or premise.key!r}; re-aim the argument or re-cut the list. "
                f"Softening the body is the one repair that is always wrong. "
                f"e.g. {exemplars}",
            )
        ]
    return out + [Violation("ERROR", f.email, "premise-unsupported", f.detail) for f in failures]


def lint_stakes(spec_text: str, touches: list[Touch]) -> list[Violation]:
    """Does the body actually carry the consequence the spec says it carries?

    ``voice.md`` names six jobs a cold body must do. Job 4 is *"Stakes — who asks, and when.
    A gap with no consequence is trivia."* Of the six, it was the only one no rule checked,
    and the cost of that showed up twice in a row:

    * ``spec-ship30-security-2026-08-21.md`` ended *"and neither survives the second
      partner"* — an assertion with nothing behind it — while its §2 claimed *"the stakes
      name a limit, not a feeling."*
    * ``spec-ship30-exec-2026-08-21.md`` ended *"can answer for the account but not for the
      agent"* — a true gap, no cost — while its §2 claimed the why *"explains why it is
      HARD."*

    Both claims were prose in a section nothing reads. The operator caught both by hand, a
    round apart, with the same sentence each time: *"we say what is missing but never what it
    costs them."* Two rules:

    * ``stakes-missing`` (WARN) — no ``stakes:`` in the front block. WARN not ERROR for the
      same reason ``premise-missing`` is: the field is new and every earlier spec predates
      it, so failing hard would block copy that is otherwise fine. Promote once the fleet has
      migrated.
    * ``stakes-unattested`` (ERROR) — the declared consequence does not appear in touch 1's
      body. ERROR because this is the rule doing the actual work: it makes "we fixed the
      stakes" a claim the gate can refute. A spec can no longer assert in prose that the body
      names a cost while the body does not.

    **What this does not check, stated plainly so nobody reads more into a PASS.** It proves
    the consequence was *declared* and is *present*. It cannot judge whether the consequence
    is true, material, or persuasive — no regex can, and a lexicon of consequence-shaped
    words was tried and rejected during design: the pre-fix security line contains "survives"
    and "second", so a keyword check would have passed the very copy that failed twice, which
    is worse than no rule at all. That judgment stays with a human, and the declared field is
    what makes it *askable* — ``build_eval_sheet`` shows the stakes claim to the labeler so
    the question becomes "does this cost land?" rather than a general verdict on the email.

    Matching is whitespace-normalised and case-insensitive: the body hard-wraps at ~90 chars
    and re-wrapping a paragraph is not a rewrite, so a line break inside the declared phrase
    must not fail the check. That is the same normalisation ``build.py``'s ``body_hash``
    guard uses, and for the same reason.
    """
    from gtm_core.hook_coverage import declared_stakes

    stakes = declared_stakes(spec_text or "")
    if not stakes:
        return [
            Violation(
                "WARN",
                "SPEC",
                "stakes-missing",
                "no `stakes:` in the spec front block — what this gap COSTS the recipient is "
                "undeclared, so `voice.md` job 4 ('a gap with no consequence is trivia') "
                "cannot be checked against the copy",
            )
        ]
    t1 = next((t for t in touches if t.number == 1), None)
    if t1 is None:
        return []

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s or "").strip().lower()

    if _norm(stakes) not in _norm(t1.body):
        return [
            Violation(
                "ERROR",
                "SPEC",
                "stakes-unattested",
                f"declared stakes {stakes!r} does not appear in touch 1 — the spec claims a "
                f"consequence the body never states. Either write it into the body or stop "
                f"declaring it; a stakes claim that lives only in the spec is exactly the "
                f"defect this rule exists to catch",
            )
        ]
    return []


def lint_hook_cell(spec_text: str, hook_matrix_text: str) -> list[Violation]:
    """Does this spec declare which hook-matrix cell it implements, and does that cell exist?

    Two rules, both ERROR:

    * ``hook-cell-missing`` — the spec's front block carries no ``hook_cell:``. The skill is
      told to pick the hook from the matrix at the persona x signal intersection; without a
      declaration nothing can check that it did, which is how 397 recipients across seven
      personas received one argument. An instruction with no check is a suggestion.
    * ``hook-cell-unknown`` — a cell was declared that the matrix does not define. The matrix
      is the vocabulary; a spec free to invent coordinates can declare conformance to a cell
      that does not exist, which is indistinguishable from declaring nothing.

    Off unless ``hook_matrix_text`` is supplied (the ``--hook-matrix`` flag), matching how
    ``--case-study-file`` and ``--artifact-file`` gate their checks. That convention is exactly
    how ``cta-unstaged-artifact`` sat inert for months, so the caller MUST pass it: the
    ``email-sequence`` merge-render gate step does, and ``gtm_core.hook_coverage`` checks the
    same thing campaign-wide.

    When the tenant's matrix has no persona/signal axis at all (the ``_template`` shape), the
    declaration cannot be verified — that is reported as ``hook-cell-unknown`` at WARN with the
    reason, never as silence.
    """
    if not (hook_matrix_text or "").strip():
        return []

    # Deferred: gtm_core.hook_coverage imports parse_spec from this module, so a top-level
    # import here would be a cycle.
    from gtm_core.hook_coverage import declared_cell, parse_matrix

    v: list[Violation] = []
    matrix = parse_matrix(hook_matrix_text)
    declared = declared_cell(spec_text or "")
    if declared is None:
        v.append(
            Violation(
                "ERROR",
                "SPEC",
                "hook-cell-missing",
                "no `hook_cell:` in the spec front block — the matrix cell this copy "
                "implements is unverifiable, and sibling specs cannot be checked for "
                "carrying the same argument",
            )
        )
        return v

    if not matrix.ok:
        v.append(
            Violation(
                "WARN",
                "SPEC",
                "hook-cell-unknown",
                f"declared `{declared.raw}` but the profile's hook-matrix has no "
                f"persona/signal axis to verify it against — {matrix.reason}",
            )
        )
    elif matrix.find(declared.persona, declared.signal) is None:
        v.append(
            Violation(
                "ERROR",
                "SPEC",
                "hook-cell-unknown",
                f"declared `{declared.raw}` is not a cell in the matrix "
                f"({len(matrix.cells)} cells across {len(matrix.personas)} personas) — "
                f"the matrix is the vocabulary; a hook it does not define is an invented one",
            )
        )
    return v


def lint_signal_cell(spec_text: str, rows: list[dict], hook_matrix_text: str) -> list[Violation]:
    """Does each row's own recorded signal match the spec's declared signal?

    The row-level counterpart to :func:`lint_hook_cell`: that rule proves a spec's declared
    cell EXISTS in the matrix; this one proves the recipients it was sent to actually HOLD
    that cell's signal. Measured 2026-08-23: `hook_cell` already has a campaign-wide ERROR
    (``cell-row-mismatch`` in ``gtm_core.hook_coverage``) and was unpopulated on all 207 live
    send-ready rows — a correct gate, inert because nothing fed it. Reuses
    :func:`gtm_core.hook_coverage.derive_row_cell` rather than re-matching labels here, so
    there is exactly ONE place a row's persona x segment x signal becomes a ``Cell`` — a
    second implementation is how a comparison silently drifts from the one the campaign-wide
    report already uses.

    Three rules, gated the same way as ``lint_hook_cell`` — OFF unless ``hook_matrix_text``
    is supplied (the ``--hook-matrix`` flag), the same opt-in convention that kept
    ``cta-unstaged-artifact`` correct-but-inert for months:

    * ``signal-column-unknown`` (ERROR, per row) — the row's recorded signal is not a valid
      column for the row's OWN segment grid: an invented value, or one that belongs to the
      OTHER grid. ``cell-segment-fit`` already proved this exact cross-grid mistake happens
      (builder 2026-08-21: a valid cell aimed at a list that was 70% the wrong grid).
    * ``signal-cell-mismatch`` (ERROR, per row) — the row's signal is valid but is not the
      ONE the spec declares. The symmetric gate to ``persona-lead-mismatch``: that rule
      catches copy leading on the wrong ROLE's pain; this catches copy leading on a trigger
      the recipient does not have — the defect this rule exists to fix (measured 2026-08-23
      on ``clean-security-20260820-send.csv``: the spec declares "Partner / third-party
      agents entering the estate" while sampled rows' own why-now spans a new CEO, an MCP
      tool launch, and a prior-authorization policy change).
    * ``signal-column-unrecorded`` (WARN, one aggregate per list, never per row) — no live
      row carries the column at all. Without this, ``signal-cell-mismatch`` is silently
      inert on every list written before this column existed — exactly how
      ``HOOK_CELL_COLUMN`` sat unpopulated despite ``cell-row-mismatch`` already existing as
      an ERROR. Mirrors ``gtm_core.hook_coverage``'s ``cell-row-unrecorded``.

    A row whose title does not resolve to a persona at all is not this rule's concern — that
    is ``hook_coverage``'s ``unresolved`` bucket and ``persona-lead-mismatch``'s silent
    default; reporting it again here under a different rule name would count the same gap
    twice under two names.
    """
    if not (hook_matrix_text or "").strip():
        return []

    # Deferred for the same reason lint_hook_cell defers its own import: gtm_core.hook_coverage
    # imports parse_spec from this module, so a top-level import here would be a cycle.
    from gtm_core.hook_coverage import declared_cell, derive_row_cell, parse_matrix
    from gtm_core.signal_record import SIGNAL_COLUMN

    matrix = parse_matrix(hook_matrix_text)
    if not matrix.ok:
        return []  # hook-cell-unknown already reports the unsupported matrix
    declared = declared_cell(spec_text or "")
    if declared is None or matrix.find(declared.persona, declared.signal) is None:
        return []  # hook-cell-missing / hook-cell-unknown already report this

    live_rows = [r for r in rows if not (r.get("suppression") or "").strip()]
    if not live_rows:
        return []

    v: list[Violation] = []
    recorded_any = False
    for r in live_rows:
        signal_value = (r.get(SIGNAL_COLUMN) or "").strip()
        if not signal_value:
            continue
        recorded_any = True
        email = (r.get("email") or "?").strip()
        rc = derive_row_cell(
            matrix,
            email=email,
            title=r.get("title") or "",
            segment=r.get("segment") or "",
            signal_column=signal_value,
        )
        if rc.cell is None:
            if rc.persona_key is None:
                continue  # unresolved title is a different rule's concern, not this one's
            v.append(
                Violation(
                    "ERROR",
                    email,
                    "signal-column-unknown",
                    f"{SIGNAL_COLUMN} {signal_value!r} — {rc.reason}",
                )
            )
            continue
        if rc.cell.signal.lower() != declared.signal.lower():
            v.append(
                Violation(
                    "ERROR",
                    email,
                    "signal-cell-mismatch",
                    f"row records signal {rc.cell.signal!r} but the spec declares "
                    f"{declared.signal!r} — this recipient does not hold the trigger the "
                    f"copy opens on",
                )
            )

    if not recorded_any:
        v.append(
            Violation(
                "WARN",
                "SPEC",
                "signal-column-unrecorded",
                f"none of {len(live_rows)} row(s) record a {SIGNAL_COLUMN!r}, so which "
                f"signal each recipient actually has is still unverified; "
                f"signal-cell-mismatch stays silent on this list. Re-run the prospect "
                f"skill's research step.",
            )
        )
    return v


def lint_merge_render(
    touches: list[Touch],
    rows: list[dict],
    *,
    signoff: str,
    extra_bans: tuple[str, ...] = (),
    case_studies: tuple[str, ...] = (),
    banned_stems: tuple[str, ...] = (),
    gift_artifacts: tuple[str, ...] = (),
    field_labels: tuple[str, ...] = DEFAULT_FIELD_LABELS,
    spec_text: str = "",
    hook_matrix: str = "",
    premise_vocab: dict | None = None,
    domain_aliases: set | None = None,
) -> tuple[list[Violation], dict]:
    """Render every touch against every row and lint the results.

    Returns ``(violations, stats)``. Row-level merge-field defects are reported once per
    row, not once per touch, so a single bad company name doesn't triple-count.

    ``gift_artifacts`` fixed 2026-08-20 (email-eval-calibration PRD, P0.5): it was accepted
    by ``lint_email`` but never threaded through from here, so ``cta-unstaged-artifact`` was
    silently dead on every sequence run regardless of what a CTA promised — the same class of
    reachability gap as the 5 catalogue entries this module never produces at all (see
    ``RULE_CATALOGUE``'s header), except this one *looked* wired because the string literal
    and the call site both existed; only the missing kwarg made it inert.
    """
    v: list[Violation] = list(lint_merge_tags(touches, field_labels))
    if not touches:
        v.append(Violation("ERROR", "SPEC", "parse", "no touches found in spec"))
        return v, {"rows": len(rows), "touches": 0, "renders": 0}

    fallback_subject = next((t.subject for t in touches if t.subject), "")
    all_copy = "\n".join(t.subject + "\n" + t.body for t in touches)

    # Severity follows what the copy actually renders. `last`/`title` defects are advisory
    # in check_row because today's touches don't use those tags; the moment a template does,
    # the same defect ships to a prospect and becomes an error.
    rendered_fields = {
        tag
        for tag in TAG_TO_COLUMN
        if re.search(r"\{\{\s*" + re.escape(tag) + r"\s*\}\}", all_copy)
    }
    escalate: set[str] = set()
    if "Last Name" in rendered_fields:
        escalate |= {"last-name-symbols", "last-name-credentials", "last-name-initial"}
    if "Job Title" in rendered_fields:
        escalate |= {"title-headline", "title-bio-fragment"}

    v += lint_possessive(touches, rows)
    v += lint_article_collision(touches, rows)
    v += lint_empty_merge_tags(touches, rows)
    v += lint_unused_signal_columns(touches, rows)
    v += lint_signal_relevance(touches, rows)
    v += lint_signal_contradiction(rows)
    v += lint_signal_agent_homonym(rows)
    v += lint_touch_personalisation(touches, rows)
    v += lint_hedge_stem([(f"step {t.number}", t.body) for t in touches])
    v += lint_same_company_divergence(touches, rows)
    v += lint_hook_cell(spec_text, hook_matrix)
    v += lint_signal_cell(spec_text, rows, hook_matrix)
    v += lint_stakes(spec_text, touches)
    v += lint_premise(spec_text, rows, premise_vocab)
    domain_aliases = domain_aliases or set()

    copy_hits: dict[tuple[int, str, str, str], list[str]] = {}

    for r in rows:
        label = f"{(r.get('email') or '?').strip()}"
        for f in check_row(r):
            # The profile's declared parent/subsidiary pairs suppress here too, not only in
            # `account_integrity`. Both consume the SAME `check_row` finding, so letting one
            # honour the alias file and not the other would give two answers to one question —
            # the drift this file's own header warns about, and the reader would rightly
            # believe whichever ran last.
            if f.rule == "email-domain-mismatch" and _domains_aliased(r, domain_aliases):
                continue
            level = "ERROR" if (f.level == "block" or f.rule in escalate) else "WARN"
            v.append(Violation(level, label, f.rule, f.detail))
        for t in touches:
            body = render(t.body, r)
            block = EmailBlock(
                index=t.number,
                header=f"{(r.get('first') or '').strip()} {(r.get('last') or '').strip()}"
                f" · {(r.get('title') or '').strip()}, {(r.get('company') or '').strip()}",
                first=(r.get("first") or "").strip(),
                company=(r.get("company") or "").strip(),
                to=label,
                subject=render(t.subject, r) or fallback_subject,
                body=body,
            )
            merged = " ".join((r.get(col) or "") for col in TAG_TO_COLUMN.values()).lower()
            # `_anchors` counts only mid-sentence capitalized tokens, so it misses the
            # company in three routine cases: a lowercase brand (athenahealth, isolved),
            # an internal abbreviation dot ("U.S. Bank"), and — since the clause contract
            # REQUIRES the clause to open with the company name — every row where the
            # company appears only at a sentence start. That last one went unnoticed while
            # a jargon-carrying proof sentence ("W3C…") happened to supply a second anchor;
            # removing that proof on 2026-08-19 exposed it as a measurement bug, not thin
            # copy. Ask the proxy directly: if none of the company's tokens survive into
            # `_anchors`, an anchor SHORTAGE is the proxy failing to see a fact the render
            # provably carries. Presence-type rules are untouched.
            company_invisible = _company_invisible(r, body)
            for x in lint_email(
                block,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
                gift_artifacts=gift_artifacts,
            ):
                if _is_data_borne(x, merged):
                    continue
                if x.rule == "specificity" and x.level == "ERROR" and company_invisible:
                    continue
                copy_hits.setdefault((t.number, x.level, x.rule, x.detail), []).append(label)

    v += _collapse_template_wide(copy_hits, len(rows))

    return v, {
        "rows": len(rows),
        "touches": len(touches),
        "renders": len(rows) * len(touches),
    }


def _collapse_template_wide(
    copy_hits: dict[tuple[int, str, str, str], list[str]], total_rows: int
) -> list[Violation]:
    """Fold a copy finding that fires identically on EVERY render of a touch into one line.

    A merge template produces the same prose for every prospect, so a rule that depends
    only on the template (``cta-unanchored``, ``specificity``) fires on all N renders with
    the same detail. Printing it 437 times buries the findings that are actually per-row
    (``word-count``, which varies with company-name length) and trains the reader to skim
    past the gate. Same information, one line, with the render count attached.

    Only a finding hitting *every* row collapses. A rule firing on a subset is genuinely
    data-dependent and stays itemized, so nothing is hidden.
    """
    # Group by (touch, level, rule) — NOT by detail. A rule can fire on every render while
    # its message varies slightly ("3 anchors" vs "2 anchors"); that is still one template
    # problem, and keying on the exact detail would leave it itemized 437 times.
    by_rule: dict[tuple[int, str, str], dict[str, list[str]]] = {}
    for (touch, level, rule, detail), labels in copy_hits.items():
        by_rule.setdefault((touch, level, rule), {})[detail] = labels

    out: list[Violation] = []
    for (touch, level, rule), details in by_rule.items():
        covered = {lbl for labels in details.values() for lbl in labels}
        if total_rows and len(covered) == total_rows:
            shown = "; ".join(sorted(details)[:2])
            more = f" (+{len(details) - 2} more variants)" if len(details) > 2 else ""
            out.append(
                Violation(
                    level,
                    f"touch{touch} (all {total_rows} renders)",
                    rule,
                    f"{shown}{more} — template-wide, fix the touch not the rows",
                )
            )
        else:
            out += [
                Violation(level, f"T{touch} {lbl}", rule, detail)
                for detail, labels in details.items()
                for lbl in labels
            ]
    return out


def capacity_note(stats: dict, daily_cap: int) -> str:
    """How long this list takes to send at the mailboxes' real daily cap.

    Informational, never a gate — but a 334-row × 3-touch list against 3 warmed mailboxes
    at 10/day is ~7 weeks, which changes whether the sequence is the right shape at all.
    """
    total = stats["renders"]
    if daily_cap <= 0 or not total:
        return ""
    days = total / daily_cap
    return (
        f"  capacity: {total} emails at {daily_cap}/day = {days:.0f} working days "
        f"({days / 5:.1f} weeks) to complete every touch"
    )


def _report(violations: list[Violation], stats: dict, *, show: int, daily_cap: int = 0) -> int:
    errors = [x for x in violations if x.level == "ERROR"]
    warns = [x for x in violations if x.level == "WARN"]

    print(
        f"merge-render lint — {stats['rows']} rows x {stats['touches']} touches "
        f"= {stats['renders']} renders (rules {RULES_VERSION})"
    )
    print(f"  {len(errors)} error(s), {len(warns)} warning(s)")
    note = capacity_note(stats, daily_cap)
    if note:
        print(note)
    print()

    for level, bucket in (("ERROR", errors), ("WARN", warns)):
        if not bucket:
            continue
        by_rule: dict[str, list[Violation]] = {}
        for x in bucket:
            by_rule.setdefault(x.rule, []).append(x)
        print(f"{level}S by rule:")
        for rule, items in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
            print(f"  {rule:26} {len(items):5}")
            for x in items[:show]:
                print(f"      {x.email}: {x.detail}")
            if len(items) > show:
                print(f"      ... +{len(items) - show} more")
        print()

    print("PASS" if not errors else "FAIL")
    return 1 if errors else 0


def _selftest() -> int:
    spec = """
Rules-Version: 2026-07-16

**Step 1 — Day 1** · Subject: `your agents in production`
> Hi {{First Name}},
>
> Once agents at {{Company}} move from retrieving data to acting on it, identity becomes
> the question an auditor asks first. My read, tell me if you've got this covered: your
> logs capture which account touched a record, but not which agent held the authority to
> act, and that gap widens the moment one agent hands work to another. An SME-operations
> platform running eight agents per user swapped one shared audit-log identity for a
> cryptographic DID per agent. Want that mapped to {{Company}}'s stack, plus a 90-second
> recording?
>
> Henry

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
    touches = parse_spec(spec)
    assert len(touches) == 2, f"expected 2 touches, got {len(touches)}"
    assert touches[0].subject == "your agents in production"
    assert touches[1].subject == ""
    assert touches[0].day == 1 and touches[1].day == 4

    good = {
        "first": "Chris",
        "last": "Renner",
        "email": "chris@cascade.example",
        "company": "Cascade",
        "company_domain": "cascade.example",
        "title": "CISO",
    }
    v, stats = lint_merge_render([touches[0]], [good], signoff="Henry")
    assert stats["renders"] == 1
    assert not [x for x in v if x.level == "ERROR"], [str(x) for x in v]

    # The two real 2026-07-28 defects must both be ERRORs.
    emoji = dict(good, first="\U0001f366", email="marcus@summitline.example")
    v, _ = lint_merge_render([touches[0]], [emoji], signoff="Henry")
    assert any(x.rule == "first-name-unrenderable" for x in v if x.level == "ERROR")

    headline = dict(good, company="DevTrial | We Build Tests", email="arjun@devtrial.example")
    v, _ = lint_merge_render([touches[0]], [headline], signoff="Henry")
    assert any(x.rule == "company-headline" for x in v if x.level == "ERROR")

    # A wrong field label is caught before the provider 400s.
    bad_tag = Touch(1, 1, "subject here", "Hi {{First Name}},\n\n{{Company Domain Name}}\n\nHenry")
    v = lint_merge_tags([bad_tag], DEFAULT_FIELD_LABELS)
    assert any(x.rule == "unknown-merge-tag" for x in v), [str(x) for x in v]

    print("selftest OK")
    return 0


#: Every check this gate can make, with a plain-English description of what it protects.
#: The QA record used to list only the rules that FIRED, so a clean run looked like a thin
#: one and "never checked" was indistinguishable from "checked and clean". Publishing the
#: full catalogue alongside the findings is what makes a PASS mean something to a reader
#: who does not have the source open.
# Corrected 2026-08-20 (email-eval-calibration PRD, P0.5). This dict is documentation, not
# a source of truth the pipeline reads — nothing in ``lint_merge_render`` consults it — so it
# had drifted from what the pipeline actually emits, silently, for as long as it existed:
#
#   * 5 entries never fire from this module at all: "company" and "signoff" are typos/ghosts
#     (the real rules are the field-specific "company-*" names and "sign-off"); "word-count-
#     mismatch" and "same-company-subject" are ``outreach_pack_linter`` PACK-format checks that
#     ``lint_merge_render`` never calls (a merge sequence has no stated word count to mismatch,
#     and every recipient sharing a static subject line is the templating working as intended,
#     not a same-company collision); "same-company-overlap" is the same story — implemented,
#     tested, and real in ``lint_pack``, unreachable from a sequence.
#   * 26 rules this module DOES emit on every run were never listed here at all — mostly the
#     mechanical ``lint_email`` hygiene checks (no-links, em-dash, spintax, placeholder,
#     subject-placeholder, hedge-missing, the four CTA-shape rules, antithesis, time-ask,
#     unresolved-contact) plus 15 ``check_row`` field defects (company-empty/-placeholder/
#     -trademark-glyph/-sentence-punct/-trailing-period/-transaction-entity/-domain-junk,
#     first-name-empty/-placeholder, email-malformed/-role-address/-freemail, title-headline/
#     -bio-fragment). ``_write_qa_record`` calls this "every check that ran, not only the ones
#     that found something" — it was never true; those 26 ran and were invisible to it.
#
# Net effect: the "43-rule inventory" this module advertised was neither the rule count NOR an
# accurate map of what fires. It is now the true, exhaustive, reachable set — every entry below
# is proven to fire by ``tests/linter/test_merge_render_mutation_suite.py``, which fails closed
# if a future rule is added to either call path without a matching entry here and a mutation.
#
# 2026-08-20 (hook-coverage PRD, H2): +2 — "hook-cell-missing" and "hook-cell-unknown", the
# per-spec half of the message-axis gate. Both are OFF unless --hook-matrix is passed, the same
# opt-in shape as --case-study-file/--artifact-file; the campaign-wide half (is this argument
# distinguishable from its siblings, and does every populated persona have one) cannot live here
# because lint_merge_render sees exactly one spec — it is gtm_core.hook_coverage.
RULE_CATALOGUE: dict[str, tuple[str, str]] = {
    # (category, what it protects against)
    "hook-cell-missing": (
        "relevance",
        "Spec does not declare which hook-matrix cell it implements, so nothing can check "
        "that a hook was chosen or that a sibling spec did not choose the same one",
    ),
    "hook-cell-unknown": (
        "relevance",
        "Spec declares a persona x signal cell the tenant's hook-matrix does not define — an "
        "invented hook wearing a declaration",
    ),
    "signal-column-unknown": (
        "relevance",
        "Row's own recorded signal is not a valid column for its segment grid — an invented "
        "value, or one that belongs to the other grid",
    ),
    "signal-cell-mismatch": (
        "relevance",
        "Row's own recorded signal differs from the one the spec declares — this recipient "
        "does not hold the trigger the copy opens on",
    ),
    "signal-column-unrecorded": (
        "relevance",
        "No row in this list records which matrix signal it actually attests, so "
        "signal-cell-mismatch cannot check anything on it yet",
    ),
    "persona-lead-mismatch": (
        "relevance",
        "Email leads on a different seat's pain than the recipient holds",
    ),
    "premise-missing": (
        "relevance",
        "Spec does not declare the premise its body requires, so no row can be checked against it",
    ),
    "premise-unknown": (
        "relevance",
        "Spec declares a premise the profile's premise-vocab.toml does not define — an "
        "invented requirement wearing a declaration",
    ),
    "premise-unsupported": (
        "relevance",
        "The row's own researched evidence cannot establish what the body then claims — "
        "the body asserts plurality, the fact attests one thing",
    ),
    "premise-thin": (
        "relevance",
        "Most rows attest the declared premise on a single common word — a word-presence "
        "check wearing an entailment check's name",
    ),
    "stakes-missing": (
        "substance",
        "Spec does not declare what the gap COSTS the recipient, so voice.md job 4 "
        "('a gap with no consequence is trivia') cannot be checked against the copy",
    ),
    "stakes-unattested": (
        "substance",
        "Spec declares a consequence its body never states — the 'we named the stakes' "
        "claim that two consecutive specs made in prose while the copy did not carry it",
    ),
    "signal-off-topic": (
        "relevance",
        "Opening line is about a different subject than the body's claim — the two paragraphs "
        "visibly do not connect",
    ),
    "signal-not-an-event": (
        "relevance",
        "Opening line is a standing description of the company, not a dated trigger",
    ),
    "signal-stray-digit": (
        "relevance",
        "Opening line carries a date or metric — an opener says what happened, not when or "
        "how much (a digit inside the company's own name is fine)",
    ),
    "touch-not-personalised": (
        "substance",
        "A follow-up that opens a new thread varies only by company name — every recipient "
        "gets near-identical copy",
    ),
    "signal-contradicts-pitch": (
        "substance",
        "The row's own researched fact announces the capability the body claims is missing — "
        "the send refutes itself",
    ),
    "signal-agent-homonym": (
        "substance",
        "The clause's 'agents' are people (insurance/real-estate), not AI agents — the body "
        "does not apply to this company",
    ),
    "cta-overclaim": (
        "brand",
        "The ask promises a result inside the reader's own review/audit environment rather "
        "than describing what the artifact contains",
    ),
    "hedge-stem-repeat": (
        "substance",
        "The same hedge construction frames more than one touch — the template becomes visible",
    ),
    "specificity": ("substance", "Body has too few concrete anchors — reads as generic filler"),
    "word-count": ("substance", "Body is outside the length band the format allows"),
    "sentence-length": (
        "substance",
        "A sentence is too long to skim — or the body's mean sentence runs long",
    ),
    "question-count": (
        "substance",
        "Body asks more questions than the reader can answer in one reply",
    ),
    "hedge-missing": (
        "substance",
        "Gap is asserted with no hedge — reads as a claim about their internals, not a guess",
    ),
    "antithesis": (
        "substance",
        "Copy uses a canned 'not just X but Y' rhetorical construction",
    ),
    "empty-merge-tag": (
        "merge",
        "A merge field is blank for some rows — renders a broken sentence",
    ),
    "unknown-merge-tag": ("merge", "Copy uses a merge tag the provider does not define"),
    "merge-tag-not-in-csv": ("merge", "Copy uses a merge tag the enrolment list has no column for"),
    "unused-signal-column": ("merge", "Rows carry research the copy never uses"),
    "first-name-unrenderable": ("merge", "First name cannot be rendered into the greeting"),
    "greeting": ("merge", "Greeting is malformed"),
    "greeting-not-a-name": ("merge", "Greeting resolves to something that is not a person's name"),
    "unresolved-contact": (
        "merge",
        "Contact name never resolved — the sentinel is the correct greeting until it does",
    ),
    "placeholder": (
        "merge",
        "Copy carries an unresolved bracket placeholder ([INSERT], [TBD]) or a stray merge tag",
    ),
    "spintax": ("merge", "Copy carries unresolved spintax ({a|b}) instead of a chosen variant"),
    "last-name-initial": ("data", "Last name is a bare initial"),
    "last-name-symbols": ("data", "Last name carries symbols that will render badly"),
    "last-name-credentials": (
        "data",
        "Last name carries post-nominals (MD, PhD) that read wrong inline",
    ),
    "company-headline": ("data", "Company field holds a headline, not a company"),
    "company-empty": ("data", "No company name at all"),
    "company-placeholder": ("data", "Company field holds a placeholder value (n/a, none, unknown)"),
    "company-trademark-glyph": (
        "data",
        "Company name carries a ®/™/©/℗ glyph that renders wrong mid-sentence",
    ),
    "company-sentence-punct": (
        "data",
        "Company name carries a ! or ? that reads as shouting mid-sentence",
    ),
    "company-trailing-period": ("data", "Company name ends in a stray period"),
    "company-transaction-entity": (
        "data",
        "Company field holds a deal/shell-entity name (a SPAC vehicle), not the operating company",
    ),
    "company-domain-junk": (
        "data",
        "Company domain is an enrichment artifact (localhost, geo-blocked, a social profile URL), "
        "not a real site",
    ),
    "first-name-empty": ("data", "No usable given name"),
    "first-name-placeholder": (
        "data",
        "Given name is a placeholder value (n/a, team, admin)",
    ),
    "email-malformed": ("data", "Email address does not parse as an address at all"),
    "email-role-address": ("data", "Email is a role/shared inbox (info@, sales@), not a person"),
    "email-freemail": (
        "data",
        "Recipient's email is a free consumer domain (gmail.com etc.), not a work address",
    ),
    "title-headline": (
        "data",
        "Job title field holds a pipe/bullet-delimited headline, not a title",
    ),
    "title-bio-fragment": (
        "data",
        "Job title carries a bio fragment ('12+ years...') rather than a title",
    ),
    "possessive-sibilant": ("grammar", "{{Company}}'s produces a double sibilant"),
    "article-collision": ("grammar", "'the {{Company}}' doubles the article"),
    "subject-length": ("deliverability", "Subject is too long for the inbox preview"),
    "subject-lowercase": ("deliverability", "Subject casing looks automated"),
    "subject-placeholder": ("deliverability", "Subject carries a merge tag or bracket placeholder"),
    "no-links": ("deliverability", "First touch carries a URL — first touch must be link-free"),
    "em-dash": (
        "deliverability",
        "Copy contains an em dash — banned outright, not just above a density threshold",
    ),
    "sign-off": ("brand", "Sign-off is missing or not the expected name"),
    "banned-word": ("brand", "Copy uses a phrase this profile has banned"),
    "banned-stem": ("brand", "Copy uses a banned word stem"),
    "named-case-study": ("brand", "Copy names one of our own case-study companies"),
    "cta-unstaged-artifact": ("brand", "The offer names an artifact this profile cannot produce"),
    "cta-question": ("brand", "Last sentence is not the offer question"),
    "cta-bundled": ("brand", "CTA offers more than one artifact in the same question"),
    "cta-unanchored": ("brand", "Ask names the artifact but not what is in it"),
    "time-ask": (
        "brand",
        "CTA asks for a specific block of time (a call, a meeting) instead of offering an artifact",
    ),
    "same-company-identical-copy": ("dedupe", "Two people at one company get identical bodies"),
    "email-domain-mismatch": (
        "risk",
        "Recipient's email domain is a different organisation than the copy describes — a job-change artefact",
    ),
    "company-allcaps": ("cosmetic", "Company renders in all caps"),
    # Stays WARN. Promotion to ERROR was requested 2026-08-23 after the operator flagged a
    # ragged line on an eval sheet as a "formatting error", and was REFUSED on evidence:
    #   * it does not detect what was flagged — the flagged row's company was 28 chars, under
    #     this rule's 32 threshold, and the longest ragged line on that list came from a
    #     22-char company. The rule and the defect are barely correlated;
    #   * the defect is not real at the recipient. The live Saleshandy variant payload stores
    #     each paragraph as ONE unbroken line (`\n\n` between paragraphs, no mid-paragraph
    #     wrap), so the spec's ~90-char authoring wrap is de-wrapped at staging and never
    #     ships. The raggedness exists only in the linter's and the labeling sheet's render.
    # Promoting it would therefore have blocked a sendable row for a defect the reader never
    # sees. The real fix was to the review surface, not the gate. Left WARN as a cheap
    # cosmetic signal for the human reading a spec, with its rationale corrected below.
    "company-long": (
        "cosmetic",
        "Company name is long enough to wrap awkwardly in a spec's hard-wrapped source — a "
        "reviewing-a-draft signal only; staging de-wraps, so this never reaches a recipient",
    ),
    "company-legal-suffix": (
        "cosmetic",
        "Company carries a legal suffix (Inc, Pte Ltd) that reads stiff inline",
    ),
    "company-parenthetical": (
        "cosmetic",
        "Company carries a parenthetical that reads badly inline",
    ),
    "first-name-initial": ("cosmetic", "First name is a bare initial"),
}


def _write_qa_record(
    out: Path,
    violations: list[Violation],
    stats: dict,
    *,
    spec: str,
    csv_path: str,
    sequence_id: str,
    verdict: str,
    examples: int = 3,
) -> Path:
    """Persist the copy-QA result so a page can show it.

    Every gate this repo runs prints and exits; nothing kept the answer. So the
    strongest quality evidence the pipeline produces — on 2026-08-18 a single rule
    caught 236 persona mismatches across 121 of 292 recipients and forced a
    restructure before any send — was invisible ten seconds after it was printed, and
    a reader had no way to tell checked-and-clean from never-checked.
    """
    by_rule: dict[str, dict[str, int]] = {}
    for v in violations:
        by_rule.setdefault(v.rule, {}).setdefault(v.level, 0)
        by_rule[v.rule][v.level] += 1
    spec_text = Path(spec).read_bytes() if Path(spec).is_file() else b""
    csv_text = Path(csv_path).read_bytes() if Path(csv_path).is_file() else b""
    record = {
        "sequence_id": sequence_id,
        "spec": spec,
        "csv": csv_path,
        # Fingerprints of exactly what was linted. A PASS is about a specific body and a
        # specific list; once either changes on disk, the staged sequence is no longer the
        # thing this record vouches for. Without these a reader cannot tell a verified
        # sequence from one whose copy was rewritten after the check.
        "spec_sha256": hashlib.sha256(spec_text).hexdigest()[:16] if spec_text else "",
        "csv_sha256": hashlib.sha256(csv_text).hexdigest()[:16] if csv_text else "",
        "ran_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "rows": stats.get("rows", 0),
        "touches": stats.get("touches", 0),
        "renders": stats.get("renders", stats.get("rows", 0) * stats.get("touches", 0)),
        "errors": sum(1 for v in violations if v.level == "ERROR"),
        "warnings": sum(1 for v in violations if v.level == "WARN"),
        "by_rule": by_rule,
        # Every check that ran, not only the ones that found something.
        "checks_run": {r: {"category": c, "protects": d} for r, (c, d) in RULE_CATALOGUE.items()},
        "examples": [
            {"level": v.level, "email": v.email, "rule": v.rule, "detail": v.detail}
            for v in violations[:examples]
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out


def _load_premise_vocab(profile: str) -> dict:
    """Load the profile's premise vocabulary, or ``{}`` if it ships none.

    Deferred import: ``gtm_core.hook_coverage`` imports ``parse_spec`` from THIS module, so a
    top-level import would be a cycle — the same reason ``lint_hook_cell`` defers its own.
    """
    from gtm_core.hook_coverage import load_premise_vocab

    return load_premise_vocab(profile)


def _load_domain_aliases(profile: str) -> set:
    """The profile's declared parent/subsidiary domain pairs. Deferred import, same cycle
    reason as :func:`_load_premise_vocab`."""
    from gtm_core.account_integrity import load_domain_aliases

    return load_domain_aliases(profile)


def _domains_aliased(row: dict, aliases: set) -> bool:
    """Delegates to `account_integrity`'s predicate rather than re-deriving it — one
    definition of "these two domains are the same company", used by both gates."""
    from gtm_core.account_integrity import _domains_aliased as impl

    return impl(row, aliases)


def _company_invisible(row: dict, body: str) -> bool:
    """Is the company name provably in this render but uncounted by the anchor proxy?

    `_anchors` counts only mid-sentence capitalized tokens, so it misses the company in
    three routine cases: a lowercase brand (athenahealth, isolved), an internal
    abbreviation dot ("U.S. Bank"), and — since the clause contract REQUIRES the clause to
    open with the company name — every row where the company appears only at a sentence
    start. That last one went unnoticed while a jargon-carrying proof sentence ("W3C…")
    happened to supply a second anchor; removing that proof on 2026-08-19 exposed it as a
    measurement bug, not thin copy. Ask the proxy directly: if none of the company's tokens
    survive into `_anchors`, an anchor SHORTAGE is the proxy failing to see a fact the
    render provably carries. Presence-type rules are untouched.

    Extracted 2026-08-21 so ``--anchor-report`` applies the SAME exemption the gate does.
    A report that disagreed with the gate would be a second, driftable opinion about the
    same property — the exact failure this repo keeps finding between a checker and its
    dashboard.
    """
    company = (row.get("company") or "").strip()
    return bool(
        company
        and company.lower() in body.lower()
        and not (set(re.findall(r"[A-Za-z0-9'-]+", company)) & _anchors(body))
        and signal_clause(row.get(TAG_TO_COLUMN["Why Now"]) or "")
    )


def _anchor_report(touches: list[Touch], rows: list[dict]) -> int:
    """Per-touch specificity-anchor distribution, printed before anyone edits the copy.

    Why this exists as its own mode rather than as another rule. On 2026-08-21 the builder
    CTA dropped the words "MCP and A2A" — a correct copy change, made because the operator
    objected to assuming a prospect's protocol stack. Those two capitalised tokens were also
    ANCHORS in every render, so their removal pushed four rows under ``MIN_ANCHORS`` and
    turned a clean gate red for a reason that had nothing to do with the edit's intent. The
    repair was to put ``{{Company}}`` back into the body, which happened to be an improvement,
    but it was found by re-running the gate and reading an unrelated-looking failure.

    The coupling is structural and will recur: anchors are counted from the RENDERED text, so
    they come partly from the template's own proper nouns and partly from the row's merge
    values. An author editing a template cannot see which of their words are load-bearing for
    a rule they are not thinking about. This prints exactly that, and prints the HEADROOM —
    how many anchors a touch can afford to lose before its worst row fails.

    Read-only and always exit 0: a report that could fail a run would just become another
    gate, and the point is to be consulted before the edit rather than after it.
    """
    from outreach_pack_linter import MIN_ANCHORS, SOFT_ANCHORS

    print(f"anchor report — {len(rows)} row(s) x {len(touches)} touch(es)")
    print(f"  floors: MIN_ANCHORS={MIN_ANCHORS} (ERROR)  SOFT_ANCHORS={SOFT_ANCHORS} (WARN)\n")
    print(
        f"  {'touch':<7}{'min':>5}{'med':>5}{'max':>5}{'headroom':>10}{'at-floor':>10}{'exempt':>8}"
    )
    for t in touches:
        # Score every row the way the gate does: rows the `company_invisible` exemption
        # covers cannot ERROR on specificity, so counting them as at-floor would overstate
        # the risk of an edit and train the reader to ignore this report.
        graded: list[tuple[int, bool]] = []
        for r in rows:
            body = render(t.body, r)
            graded.append((len(_anchors(body)), _company_invisible(r, body)))
        counts = sorted(c for c, _ in graded)
        if not counts:
            continue
        enforced = sorted(c for c, exempt in graded if not exempt)
        exempt_n = len(graded) - len(enforced)
        lo, hi = counts[0], counts[-1]
        med = counts[len(counts) // 2]
        # Headroom is measured against the worst ENFORCED row, not the median and not the
        # worst overall: the gate fails on that row, so a median with slack is not slack.
        headroom = (min(enforced) - MIN_ANCHORS) if enforced else "n/a"
        at_floor = sum(1 for c in enforced if c <= MIN_ANCHORS)
        flag = "  <- no headroom" if isinstance(headroom, int) and headroom <= 0 else ""
        print(
            f"  {'T' + str(t.number):<7}{lo:>5}{med:>5}{hi:>5}{str(headroom):>10}"
            f"{at_floor:>10}{exempt_n:>8}{flag}"
        )
    print(
        "\n  headroom = anchors the worst ENFORCED row can lose before it ERRORs. Removing a\n"
        "  capitalised token from a template costs one anchor on every render that carried it.\n"
        "  exempt = rows whose company name is in the body but invisible to the anchor proxy\n"
        "  (lowercase brand, sentence-start); the gate cannot ERROR on those, so nor does this."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=f"Merge-render linter for sequenced sends (rules {RULES_VERSION})"
    )
    ap.add_argument("spec", nargs="?", help="sequence spec .md containing the touches")
    ap.add_argument("--csv", help="prospect CSV to render against (e.g. ready-to-load.csv)")
    ap.add_argument(
        "--signoff",
        default=None,
        help="expected bare sign-off; defaults to the spec header's `Sign-off:` field. "
        "The 2026-08-18 specs signed 'Henry' while the old default 'Alex' made every "
        "CTA rule inspect the signature line instead of the offer — never rely on a "
        "placeholder default for a real send.",
    )
    ap.add_argument("--ban-file", help="profile voice-bans.txt")
    ap.add_argument("--case-study-file", help="profile outreach-case-studies.txt")
    ap.add_argument("--stem-file", help="profile outreach-banned-stems.txt")
    ap.add_argument(
        "--artifact-file",
        help="profile gift-artifacts.txt (one per line); missing = cta-unstaged-artifact "
        "check off. Threaded through 2026-08-20 — this flag existed on the pack linter "
        "since gift_artifacts was added there, but lint_merge_render never accepted or "
        "passed it, so a sequence's CTA could promise any artifact with no check at all.",
    )
    ap.add_argument(
        "--hook-matrix",
        help="profile hook-matrix.md; missing = hook-cell checks off. Resolve it with "
        "`python -m gtm_core.resolve_knowledge hook-matrix.md --profile <p>` so a product "
        "override is honoured. Campaign-wide coverage is `python -m gtm_core.hook_coverage`.",
    )
    ap.add_argument(
        "--fields",
        help="newline-delimited provider field labels (defaults to the standard set)",
    )
    ap.add_argument("--show", type=int, default=3, help="examples to print per rule")
    ap.add_argument(
        "--daily-cap",
        type=int,
        default=0,
        help="combined daily send limit across the attached mailboxes (e.g. 3 warmed "
        "mailboxes x 10/day = 30); prints how long the list takes to work through",
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--list-rules",
        metavar="PATH",
        help="write every rule name in RULE_CATALOGUE, one per line, to PATH and exit. "
        "This is the regression-suite inventory: `gtm_core.adjudication novel` reads it "
        "to tell a defect class that needs a NEW rule from one an existing rule should "
        "already have caught. Takes a path rather than printing, because a shell "
        "redirect is denied at runtime and a documented command that only works "
        "interactively is the 0.11.1 failure this repo already had once.",
    )
    ap.add_argument(
        "--json",
        dest="json_out",
        help="also write a structured QA record here (e.g. .pool/lint-<sequence_id>.json) "
        "so the result survives the run instead of scrolling past",
    )
    ap.add_argument(
        "--sequence-id",
        help="sequence this spec+csv pair was staged as; recorded in --json output",
    )
    ap.add_argument(
        "--profile",
        help=(
            "active profile; enables the premise checks by loading its "
            "knowledge/premise-vocab.toml. Without it `premise-unsupported` is OFF — the same "
            "opt-in shape as --hook-matrix, and the same trap, so pass it."
        ),
    )
    ap.add_argument(
        "--anchor-report",
        action="store_true",
        help=(
            "print per-touch specificity-anchor counts (min/median/max across renders, and "
            "the headroom above the hard floor) and exit 0 without linting. Run this BEFORE "
            "editing copy: anchors come partly from capitalised tokens in the template, so "
            "removing a proper noun can push rows under the floor with no other change."
        ),
    )
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.list_rules:
        out = Path(args.list_rules)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(sorted(RULE_CATALOGUE)) + "\n", encoding="utf-8")
        print(f"wrote {len(RULE_CATALOGUE)} rule name(s) to {out}")
        return 0
    if not args.spec or not args.csv:
        ap.error("spec path and --csv are both required")

    spec_header_text = Path(args.spec).read_text(encoding="utf-8")
    touches = parse_spec(spec_header_text)
    if not args.signoff:
        m = re.search(r"^Sign-off:\s*(\S+)", spec_header_text, re.MULTILINE)
        args.signoff = m.group(1) if m else "Alex"
    with Path(args.csv).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # A suppressed row is not being sent, so linting it reports defects nobody can act on
    # and — worse — keeps a gate red for copy that will never render. `account_integrity`
    # already reads the column this way (`--ignore-suppressed`); this makes the copy gate
    # agree with it. Reported, never silent: a shrinking send list is a fact the operator
    # must see next to the PASS.
    suppressed = [r for r in rows if (r.get("suppression") or "").strip()]
    if suppressed:
        rows = [r for r in rows if not (r.get("suppression") or "").strip()]
        print(
            f"skipping {len(suppressed)} suppressed row(s); linting {len(rows)} live",
            file=sys.stderr,
        )

    if args.anchor_report:
        return _anchor_report(touches, rows)

    labels = tuple(_load_bans(args.fields)) if args.fields else DEFAULT_FIELD_LABELS
    violations, stats = lint_merge_render(
        touches,
        rows,
        signoff=args.signoff,
        extra_bans=_load_bans(args.ban_file),
        case_studies=_load_bans(args.case_study_file),
        banned_stems=_load_bans(args.stem_file),
        gift_artifacts=_load_bans(args.artifact_file),
        field_labels=labels,
        spec_text=spec_header_text,
        hook_matrix=(
            Path(args.hook_matrix).read_text(encoding="utf-8") if args.hook_matrix else ""
        ),
        premise_vocab=(_load_premise_vocab(args.profile) if args.profile else None),
        domain_aliases=(_load_domain_aliases(args.profile) if args.profile else None),
    )
    rc = _report(violations, stats, show=args.show, daily_cap=args.daily_cap)
    if args.json_out:
        _write_qa_record(
            Path(args.json_out),
            violations,
            stats,
            spec=args.spec,
            csv_path=args.csv,
            sequence_id=args.sequence_id or "",
            verdict="FAIL" if rc else "PASS",
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
