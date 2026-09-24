"""Pack- and batch-level rules, plus the pack entry points. A rule here needs more than one
email to have an opinion (template share, thread repetition, touch counts) or judges the pack
document itself (rules-version staleness, format detection, the tracker CSV).

**The pack entry points also run the derivation rules** (2026-09-24 review, finding 3). Before
that they did not, and only ``driver.lint_merge_render`` called ``lint_derivation`` — so a 1:1
pack, which is the shape a human actually reviews and sends, reached none of the registry
rules while ``gtm_core/messaging/card.py`` documented ``claim-status`` as "authoritative
there". The §R5 consequence of linting an already-rendered body, and what holds instead of
"templates, never renders", is argued in ``rules_derivation``'s module docstring; read it
before adding a derivation rule that CLEARS a finding on evidence found in the copy.
"""

from __future__ import annotations

import csv as _csv
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .model import RULES_VERSION, EmailBlock, PackMeta, Violation
from .parse import (
    _EMAIL_HEADING_RE,
    detect_format,
    parse_draft_outreach_pack,
    parse_pack,
    parse_prospect_pack,
)
from .rules_copy import DEFAULT_BANNED_STEMS, DEFAULT_CASE_STUDY_NAMES, lint_email
from .rules_derivation import lint_derivation
from .text import (
    JACCARD_MAX,
    MAX_NGRAM_EMAILS,
    NGRAM_N,
    ROLE_INBOX_SENTINEL,
    _content_words,
    _hedge_ngram_whitelist,
    _ngrams,
    _norm_tokens,
    _sentences,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gtm_core.messaging.registry import Registry

MAX_TOUCHES = 4  # voice.md's gift ladder caps at 4; a 5th correlates with rising spam/unsub
WORD_COUNT_TOLERANCE = 5  # a self-reported count may differ from the real one by this much


_ATTACH_IN_BODY_RE = re.compile(r"\battach(?:ed|ment|ing)\b", re.IGNORECASE)
# "Attach: none — the demo is offered on reply" is the rule being followed, not broken.
_ATTACH_NONE_RE = re.compile(r"^\s*(none|n/?a|—|-)\b", re.IGNORECASE)


def lint_meta(meta: PackMeta, blocks: list[EmailBlock]) -> list[Violation]:
    """Rules about the pack around the email — invisible to any body-level check."""
    v: list[Violation] = []
    body = blocks[0].body if blocks else ""

    # The gift is the OFFER, not the payload: an attachment on a cold first touch degrades the
    # spam profile before a relationship exists, and enterprise filters quarantine it outright.
    if meta.attach and not _ATTACH_NONE_RE.match(meta.attach):
        v.append(
            Violation(
                "ERROR",
                meta.label,
                "attachment-on-touch1",
                f"touch 1 must describe the gift and gate it behind the reply, not ship it: {meta.attach!r}",
            )
        )
    if _ATTACH_IN_BODY_RE.search(body):
        v.append(
            Violation(
                "ERROR",
                meta.label,
                "attachment-on-touch1",
                'body says the asset is attached — offer it instead ("want the one-pager?")',
            )
        )

    # A self-reported count that disagrees with reality is worse than none: it reads as verified.
    if meta.stated_words is not None and blocks:
        actual = len(re.sub(r"\s+", " ", body).split())
        if abs(meta.stated_words - actual) > WORD_COUNT_TOLERANCE:
            v.append(
                Violation(
                    "ERROR",
                    meta.label,
                    "word-count-mismatch",
                    f"claims {meta.stated_words} words, body is {actual} — compute it, never estimate",
                )
            )

    if meta.declared_touches is not None:
        if meta.declared_touches > MAX_TOUCHES:
            v.append(
                Violation(
                    "ERROR",
                    meta.label,
                    "touch-count",
                    f"{meta.declared_touches} touches declared; the ladder caps at {MAX_TOUCHES}",
                )
            )
        if meta.enumerated_touches and meta.enumerated_touches != meta.declared_touches:
            v.append(
                Violation(
                    "ERROR",
                    meta.label,
                    "touch-count",
                    f"header declares {meta.declared_touches} touches, {meta.enumerated_touches} are written out",
                )
            )

    # LinkedIn first, then email: two cold emails followed by a connection request reads as
    # pressure, where a visit before the email reads as genuine.
    if meta.has_linkedin_dm and meta.touch_channels:
        if meta.touch_channels[0].lower().startswith("email"):
            # A role inbox has no profile to connect to, so "lead with LinkedIn" is advice the
            # pack cannot take — the DM is the part that does not belong, not the ladder. Same
            # rule, same severity: exempting the case would have left the pack carrying a
            # section nobody can send, reported by nothing.
            role_inbox = bool(blocks) and blocks[0].first == ROLE_INBOX_SENTINEL
            detail = (
                "a role inbox has no LinkedIn profile to DM — drop the LinkedIn DM section "
                "rather than reorder the ladder"
                if role_inbox
                else "touch 1 is email while a LinkedIn DM exists — lead with LinkedIn, then email"
            )
            v.append(Violation("WARN", meta.label, "channel-order", detail))
    return v


def _subject_shape(subject: str) -> str:
    """Collapse a subject to its reusable template, so `X's missing primitive` groups with `Y's`."""
    s = subject.strip().lower()
    s = re.sub(r"^\S+(?:'s|s')\s+", "", s)  # drop a leading possessive noun
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    s = re.sub(r"^(?:\S+\s+){0,2}?\S+(?:'s|s')\s+", "", s)  # multi-word possessive
    return re.sub(r"\s+", " ", s).strip()


def lint_subject_homogeneity(
    subjects: list[tuple[str, str]], ceiling: int = MAX_NGRAM_EMAILS
) -> list[Violation]:
    """Flag a subject template reused across more than `ceiling` files in a batch.

    `subjects` is (label, subject). This is the batch-level analogue of `template-share`: every
    other rule here is per-email, which is structurally blind to 25 individually-compliant
    subjects that happen to be the same mad-lib.
    """
    shapes: dict[str, list[str]] = {}
    for label, subject in subjects:
        shape = _subject_shape(subject)
        if shape:
            shapes.setdefault(shape, []).append(label)
    v: list[Violation] = []
    for shape, labels in sorted(shapes.items(), key=lambda kv: -len(kv[1])):
        if len(labels) > ceiling:
            v.append(
                Violation(
                    "ERROR",
                    "BATCH",
                    "subject-template-share",
                    f'"{shape}" is the subject shape in {len(labels)} files (max {ceiling}): '
                    f"{', '.join(sorted(labels)[:5])}{' …' if len(labels) > 5 else ''}",
                )
            )
    return v


def lint_body_homogeneity(
    bodies: list[tuple[str, str]],
    ceiling: int = MAX_NGRAM_EMAILS,
    shared_phrases: tuple[str, ...] = (),
) -> list[Violation]:
    """Flag a body phrase shared by more than `ceiling` files in a batch.

    The same 6-gram ceiling `lint_pack` applies *within* one multi-recipient pack, applied
    *across* the files of one run. Note the hedge whitelist is deliberately NOT used here: the
    2026-07-19 run opened 46 of 50 gaps with the same hedge wording, and whitelisting it is
    exactly what let that through. Hedging is mandatory; one scripted wording is not.

    `shared_phrases` is the ONE deliberate exemption, and it exists because this rule polices
    *personalisation*, not vocabulary. A batch describes one product and cites the same reference
    customers; "acme gateway gives each agent a verifiable identity" recurring across
    390 accounts is the pitch being consistent, not a template being pasted. Forcing 390 distinct
    phrasings of one product sentence makes the copy worse, not less robotic. Loaded per-profile
    (`--shared-phrase-file`) rather than hardcoded, so no tenant's product vocabulary lives in
    this cross-tenant linter.

    The exemption is deliberately narrow: a 6-gram is skipped only when it falls ENTIRELY inside
    a declared phrase. A template that merely quotes a product name in passing still trips, because
    its surrounding words are not in the phrase.
    """
    exempt: set[tuple[str, ...]] = set()
    for phrase in shared_phrases:
        toks = _norm_tokens(phrase)
        if len(toks) >= NGRAM_N:
            exempt |= _ngrams(toks, NGRAM_N)

    ngram_files: dict[tuple[str, ...], set[str]] = {}
    for label, body in bodies:
        for g in _ngrams(_norm_tokens(body), NGRAM_N):
            if g in exempt:
                continue
            ngram_files.setdefault(g, set()).add(label)
    flagged = {g: f for g, f in ngram_files.items() if len(f) > ceiling}
    v: list[Violation] = []
    # Tie-broken on the gram itself. Ordering by count alone left equal-count 6-grams in
    # SET iteration order, so a batch with more than five equally-shared phrases reported a
    # different five run to run (and under a different PYTHONHASHSEED). A gate whose output
    # is not reproducible cannot be diffed between two runs, which is how a behaviour change
    # hides inside noise — found 2026-09-24 while diffing the whole corpus.
    for g in sorted(flagged, key=lambda g: (-len(flagged[g]), g))[:5]:
        v.append(
            Violation(
                "ERROR",
                "BATCH",
                "body-template-share",
                f'"{" ".join(g)}" appears in {len(flagged[g])} files (max {ceiling})',
            )
        )
    return v


#: A sentence shorter than this is boilerplate a thread may legitimately repeat ("Thanks
#: for the time.") — only a substantive sentence recurring reads as a template showing
#: through. Tokens, not characters, so a long stock phrase cannot slip under a char cap.
MIN_REPEAT_TOKENS = 6


def _threads(touches: list[tuple[str, str, str]]) -> list[list[tuple[str, str, str]]]:
    """Group `(label, subject, body)` in send order into threads.

    A touch with a subject opens a new thread; a subject-less one is a same-thread
    follow-up. That is not a new convention — `lint_touch_personalisation` already reads
    an absent subject exactly this way.
    """
    out: list[list[tuple[str, str, str]]] = []
    cur: list[tuple[str, str, str]] = []
    for t in touches:
        if (t[1] or "").strip() and cur:
            out.append(cur)
            cur = []
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def lint_thread_repetition(touches: list[tuple[str, str, str]]) -> list[Violation]:
    """Defects no rule reading ONE body can see (2026-09-22, PENDING.md EC6).

    `(label, subject, body)` per touch, in send order, from the UNRENDERED templates — the
    same input contract as :func:`lint_hedge_stem`, and for the same reason: recurrence is a
    property of the template, not of the row that fills it. Reported once per sequence,
    `SPEC`-scoped, because the fix is to rewrite a touch and not to annotate every render.

    * ``thread-sentence-repeat`` — the same substantive sentence appears in more than one
      touch landing in ONE thread, where the reader has the earlier message directly above.
      Measured on 37 live specs before building: 3 carry it (8%), comfortably under the 0.40
      rate at which `rule_lifecycle_report` calls a rule saturated.
    * ``thread-reply-prefix`` — a touch that OPENS a thread carries a ``Re:``/``Fwd:``
      subject, which claims a conversation that never happened. Zero live specs do this
      today; it is a guard on a shape nothing else gates, not a cleanup.

    **Deliberately NOT gated here: offer repetition.** The plan that produced this rule also
    named "~38 CTAs are variants of one offer". Measured the same way, the same artifact noun
    recurs across touches in 26 of 37 specs (70%) — a class firing on 70% of a population
    describes the population. Gating it would fail almost every sequence for a property the
    portfolio has, not a defect this sequence introduced.
    """
    v: list[Violation] = []
    for thread in _threads(touches):
        if len(thread) < 2:
            continue
        seen: dict[tuple[str, ...], list[str]] = {}
        for label, _subject, body in thread:
            for sentence in _sentences(body):
                tokens = tuple(sorted(set(_norm_tokens(sentence))))
                if len(tokens) < MIN_REPEAT_TOKENS:
                    continue
                where = seen.setdefault(tokens, [])
                if label not in where:
                    where.append(label)
        for tokens, where in seen.items():
            if len(where) > 1:
                v.append(
                    Violation(
                        "ERROR",
                        "SPEC",
                        "thread-sentence-repeat",
                        f"the same sentence runs in {', '.join(where)}, which land in one "
                        f"thread — the reader sees it twice in the same window "
                        f"({' '.join(tokens[:8])}...)",
                    )
                )
                break
    for thread in _threads(touches):
        label, subject, _body = thread[0]
        if re.match(r"\s*(?:re|fwd|fw)\s*:", subject or "", re.IGNORECASE):
            v.append(
                Violation(
                    "ERROR",
                    "SPEC",
                    "thread-reply-prefix",
                    f"{label} opens a thread with subject {subject!r} — a Re:/Fwd: prefix "
                    f"claims a conversation the recipient never had",
                )
            )
    return v


def _derivation_on_a_pack(
    text: str, blocks: list[EmailBlock], registry: Registry | None
) -> list[Violation]:
    """The derivation rules over a pack document, relabelled ``PACK``.

    ``rows=[]`` because a pack document carries no enrolment list: it is one recipient,
    described in prose, with no country column. The one rule that needs rows —
    ``proof-status``'s foreign-anchor branch — is therefore silent here by absence rather than
    by exemption, which is the honest answer; inventing a market for the reader would be this
    gate authoring messaging.

    The scope label is ``PACK`` and not ``SPEC`` on purpose. The two paths judge the same
    registry facts against materially different text — a template on one, a rendered body on
    the other — and an operator reading ``claim-status`` on a pack needs to know the phrase may
    have arrived in a clause the drafter pasted rather than in a sentence they wrote.
    """
    if registry is None:
        return []
    copy_pairs = [(b.subject, b.body) for b in blocks]
    return [
        Violation(x.level, "PACK", x.rule, x.detail)
        for x in lint_derivation(text, copy_pairs, [], registry)
    ]


def lint_pack(
    text: str,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Alex",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
    registry: Registry | None = None,
) -> list[Violation]:
    v: list[Violation] = []
    version, blocks = parse_pack(text)

    # Staleness gate
    if version is None:
        v.append(
            Violation(
                "ERROR",
                "PACK",
                "rules-version-missing",
                f"add 'Rules-Version: {RULES_VERSION}' to the pack header",
            )
        )
    elif version != RULES_VERSION:
        v.append(
            Violation(
                "ERROR",
                "PACK",
                "rules-version-stale",
                f"pack drafted under {version}, current is {RULES_VERSION} — regenerate/re-review before sending",
            )
        )

    if not blocks:
        v.append(
            Violation("ERROR", "PACK", "parse", "no email blocks found (### N. header format)")
        )
        return v

    # Per-email
    for b in blocks:
        v.extend(
            lint_email(
                b,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
            )
        )

    v += _derivation_on_a_pack(text, blocks, registry)

    # Duplicate recipients
    seen: dict[str, int] = {}
    for b in blocks:
        if b.to in seen:
            v.append(Violation("ERROR", b.label, "duplicate-to", f"also block #{seen[b.to]}"))
        seen[b.to] = b.index

    # Template-share ceiling (6-grams across emails)
    wl = _hedge_ngram_whitelist()
    ngram_emails: dict[tuple[str, ...], set[int]] = {}
    for b in blocks:
        toks = _norm_tokens(b.body)
        for g in _ngrams(toks, NGRAM_N):
            if g in wl:
                continue
            ngram_emails.setdefault(g, set()).add(b.index)
    flagged: set[tuple[str, ...]] = {
        g for g, e in ngram_emails.items() if len(e) > MAX_NGRAM_EMAILS
    }
    # Report the longest offenders only (merge overlapping grams by picking top few).
    # Tie-broken on the gram itself for the reason `lint_body_homogeneity` records: without
    # it, equal-count grams came out in set-iteration order and the report was not reproducible.
    if flagged:
        samples = sorted(flagged, key=lambda g: (-len(ngram_emails[g]), g))[:5]
        for g in samples:
            v.append(
                Violation(
                    "ERROR",
                    "PACK",
                    "template-share",
                    f'"{" ".join(g)}" appears in {len(ngram_emails[g])} emails (max {MAX_NGRAM_EMAILS})',
                )
            )

    # Same-company divergence
    by_domain: dict[str, list[EmailBlock]] = {}
    for b in blocks:
        dom = b.to.split("@")[-1]
        by_domain.setdefault(dom, []).append(b)
    for dom, group in by_domain.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, c = group[i], group[j]
                if a.subject.strip().lower() == c.subject.strip().lower():
                    v.append(
                        Violation(
                            "ERROR",
                            "PACK",
                            "same-company-subject",
                            f"{a.label} and {c.label} share subject {a.subject!r}",
                        )
                    )
                wa, wc_ = _content_words(a.body), _content_words(c.body)
                if wa and wc_:
                    jac = len(wa & wc_) / len(wa | wc_)
                    if jac > JACCARD_MAX:
                        v.append(
                            Violation(
                                "ERROR",
                                "PACK",
                                "same-company-overlap",
                                f"{a.label} vs {c.label}: Jaccard {jac:.2f} > {JACCARD_MAX} — diverge angle/signal",
                            )
                        )
    return v


_PARSERS = {
    "draft-outreach": parse_draft_outreach_pack,
    "prospect-pack": parse_prospect_pack,
}


def lint_formatted_pack(
    text: str,
    fmt: str,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Alex",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
    registry: Registry | None = None,
) -> tuple[list[Violation], list[EmailBlock]]:
    """Lint one pack in any of the three known shapes. Returns (violations, blocks).

    The blocks come back so a batch run can pool subjects for `lint_subject_homogeneity`.

    ``registry`` turns on the derivation rules, exactly as it does on
    ``driver.lint_merge_render``, and for the same stated reason: the loader needs a profile,
    a profile is bound by the caller, and a linter that guessed one would be the
    right-content-wrong-company error at its source. The CLI passes it whenever ``--profile``
    is given on either mode.
    """
    if fmt == "auto":
        fmt = detect_format(text)
    if fmt == "tier-a-manual":
        _, blocks = parse_pack(text)
        return (
            lint_pack(
                text,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
                registry=registry,
            ),
            blocks,
        )

    version, blocks, meta = _PARSERS[fmt](text)
    v: list[Violation] = []
    # The pack header's `Capability:` field is no longer read here. `capability-unargued`
    # retired 2026-09-24: the field is a DERIVATION of the declared angle's claim group
    # (`gtm_core.hook_coverage.declared.derived_fields`), so a disagreement between the two is
    # the loader's error to raise, not a vocabulary count's.
    if version is None:
        v.append(
            Violation(
                "WARN",
                meta.label,
                "rules-version-missing",
                f"add 'Rules-Version: {RULES_VERSION}' to the pack header",
            )
        )
    elif version != RULES_VERSION:
        v.append(
            Violation(
                "ERROR",
                meta.label,
                "rules-version-stale",
                f"drafted under {version}, current is {RULES_VERSION} — regenerate before sending",
            )
        )
    if not blocks:
        # A contact-resolution addendum (`-b.md`) carries no email at all — it exists to record a
        # resolved persona for a pack that lives in another file. That is not a malformed pack and
        # must not read as one. A document that DOES declare a touch-1 section but yields no block
        # is still a hard error: that is the linter failing to read copy that will actually ship.
        has_email_section = bool(_EMAIL_HEADING_RE.search(text)) or "**Subject:**" in text
        if fmt == "prospect-pack" and not has_email_section:
            v.append(
                Violation(
                    "WARN",
                    meta.label,
                    "not-an-outreach-pack",
                    "no touch-1 email section — treated as a contact-resolution addendum, not linted",
                )
            )
        else:
            v.append(
                Violation("ERROR", meta.label, "parse", f"no touch-1 email found ({fmt} format)")
            )
        return v, blocks

    for b in blocks:
        v.extend(
            lint_email(
                b,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
            )
        )
    v.extend(lint_meta(meta, blocks))
    v += [
        Violation(x.level, meta.label, x.rule, x.detail)
        for x in _derivation_on_a_pack(text, blocks, registry)
    ]
    return v, blocks


def lint_tracker_csv(path: Path) -> list[Violation]:
    """Staleness check for a tracker CSV: DRAFTED rows must carry the current rules_version."""
    v: list[Violation] = []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    if not rows:
        return [Violation("WARN", "CSV", "empty", str(path))]
    has_col = "rules_version" in rows[0]
    for i, r in enumerate(rows, start=2):
        status = (r.get("status") or "").upper()
        if status.startswith("DRAFTED"):
            rv = (r.get("rules_version") or "").strip() if has_col else ""
            if rv != RULES_VERSION:
                v.append(
                    Violation(
                        "ERROR",
                        f"csv row {i} ({r.get('email', '?')})",
                        "rules-version-stale",
                        f"drafted under {rv or 'unversioned'}; re-validate under {RULES_VERSION} before sending",
                    )
                )
    return v
