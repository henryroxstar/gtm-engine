"""Rules that need a TEMPLATE and a ROW: merge tags, signal evidence, premise — each reads a
``{{Tag}}`` template against a prospect row. Row-data hygiene itself stays in
``gtm_core.merge_hygiene`` and is called from ``driver``.

**What left on 2026-09-24 (outbound fact registry, FR3).** The four DECLARATION rules —
``lint_hook_cell``, ``lint_signal_cell``, ``lint_signal_column`` and ``lint_stakes`` — are
gone. Each checked that a spec's hand-written front-block field was internally consistent with
something else hand-written; the registry makes all four of those fields *derivations* of one
declared ``angle:`` (`gtm_core.hook_coverage.declared.derived_fields`), so the consistency
question is answered by the loader instead of by a rule. ``lint_premise`` stays: it is the one
rule here that judges the ROW's own untrusted evidence rather than a declaration."""

from __future__ import annotations

import re

from gtm_core.merge_hygiene import (
    ends_in_sibilant,
    signal_clause,
    signal_is_event,
    signal_on_topic,
    signal_stray_digits,
    starts_with_article,
)

from .model import Touch, Violation
from .parse import _MERGE_TAG_RE, TAG_TO_COLUMN, render
from .text import _sentences


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

    Renders "the The Summitline Health stack" / "the A Fernway Capital stack". The article
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
# batch-level 6-gram ceiling in `rules_batch` compares FILES in a run, not touches
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
    # phrasing and so missed SIX rows whose clause ships the same CATEGORY under a different
    # vocabulary — six vendors across identity, consulting, telco, data privacy and KYB, each
    # with its own name for it. Pitching agent identity to the company selling agent identity
    # is the same defect wearing a different vocabulary.
    #
    # Which vendor said which is deliberately NOT written here (§R9). Their names live in the
    # tenant's `competitors.toml` `aliases`, and this file ships in the public carve — while
    # `tests/lint/third_party_roster.py` derives its roster from account folders and the
    # case-study list ONLY, never from `competitors.toml`, so a competitor's name in a comment
    # here is invisible to every automated gate. The alternation BELOW is different: those
    # literals are the matcher and are load-bearing, so they stay.
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
# all, so a genuine hybrid ("a multi-agent clinical-assistant system with independent
# agents") still passes.
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
    *refuted* the pitch — a telco, a payments processor and a card network, each whose
    researched clause announced the per-agent identity the body then called missing
    ("requires every AI agent to authenticate with its own identity", "assigns agent
    identities and approval scopes", "agent registration"). The shape is the lesson; the
    roster is theirs, not ours (§R9). Quoting the counter-evidence in line one and asserting the gap in
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


#: Above this failing share, `premise-unsupported` reports one aggregate finding instead of
#: one per row. Same reasoning as `gtm_core.finding_budget`'s SATURATED band and the same
#: number: a class firing on more than half a population is describing the population.
PREMISE_SATURATION = 0.5


#: Strips the greeting so the recipient's first name is not read as the opener's referent.
_GREETING_RE = re.compile(r"^\s*(?:hi|hey|hello)\s+[^,]{0,40},\s*", re.IGNORECASE)


def lint_opener_dated(
    touches: list[Touch], *, require_dated_opener: bool = False
) -> list[Violation]:
    """``opener-undated`` (WARN) — touch 1's opener carries no year or month.

    **Off unless ``require_dated_opener`` is passed** (``--require-dated-opener``), matching how
    ``--hook-matrix`` and ``premise_vocab`` gate their checks. That is not timidity, it is the
    measurement: ZERO of 596 live touch-1 openers carried a dated referent on 2026-09-22, so
    switched on by default this fails every spec in the fleet on day one, and a WARN nobody can
    clear is a WARN everybody learns to skip.

    It is the gate half of what `--craft-report`'s `dated` column reports, and it is the
    survivable form of the EC5 change. The plan's version scored ANCHORS in the opener and was
    abandoned on measurement — it turned 1032 of 1783 live renders into ERRORs and split the
    two copy batches backwards, because `_anchors` cannot tell a rendered ``{{Company}}`` from a
    named external standard. A date can be told apart from a merge value by looking at it, which
    is why this proxy survives where that one did not. It is deliberately the WEAK half of
    "named, dated, external": nothing here can check that the referent is external or
    category-level — that is semantic, and the judge's.

    Turn it on for a profile once its specs have migrated to the referent shape
    (``email-sequence/body_template.md``); until then the column in ``--craft-report`` is the
    honest instrument. PENDING.md EC5.
    """
    if not require_dated_opener:
        return []
    t1 = next((t for t in touches if t.number == 1), None)
    if t1 is None:
        return []
    lines = [ln for ln in t1.body.split("\n") if ln.strip()]
    body = "\n".join(lines[:-1]) if len(lines) > 1 else t1.body
    opener = _GREETING_RE.sub("", " ".join(_sentences(body)[:2]))
    if _DATED_RE.search(opener):
        return []
    return [
        Violation(
            "WARN",
            "SPEC",
            "opener-undated",
            "touch 1's opener carries no year or month — the referent it opens on cannot be "
            "placed in time, which is half of the named/dated/external shape "
            "`email-sequence/body_template.md` asks for",
        )
    ]


def lint_premise(spec_text: str, rows: list[dict], premise_vocab: dict | None) -> list[Violation]:
    """Can each row's own research carry the premise this spec's body requires?

    Three rules:

    * ``premise-missing`` (WARN) — the spec declares no ``premise:``. WARN and not ERROR
      because unlike ``hook_cell`` this field is new and every pre-2026-08-21 spec predates
      it; a hard failure would block copy that is otherwise fine. Promote once the fleet
      has migrated.
    * ``premise-unknown`` (ERROR) — a premise the profile's ``premise-vocab.toml`` does not
      define. Same reasoning as ``angle-unknown``: the vocabulary file IS the vocabulary,
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
    # CORRECT — one row's evidence is one protocol, another's is one product on one platform,
    # a third's is one assistant — and printing 184 identical-shaped ERRORs would bury that under
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


def _domains_aliased(row: dict, aliases: set) -> bool:
    """Delegates to `account_integrity`'s predicate rather than re-deriving it — one
    definition of "these two domains are the same company", used by both gates."""
    from gtm_core.account_integrity import _domains_aliased as impl

    return impl(row, aliases)


#: A year or a month name — the cheap, checkable half of "named, dated, external referent".
#: Reported by `--craft-report`, deliberately never a rule: measured 2026-09-22, ZERO of 596
#: live touch-1 openers carry one, so a gate would fail the entire corpus on day one and a WARN
#: nobody can clear is a WARN everybody learns to skip (the same argument that kept
#: `signal-column-undeclared` at WARN until it was retired, one step further). It also does not
#: separate the two
#: batches — both are 100% — so it is not evidence about WHICH copy is better. It is the
#: distance from the shape `email-sequence/body_template.md` now asks for, and that is all.
_DATED_RE = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:January|February|March|April|May|June|July|August|September"
    r"|October|November|December)\b",
    re.IGNORECASE,
)
