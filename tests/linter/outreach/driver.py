"""``lint_merge_render`` — the one driver that runs every rule over a spec + a CSV — and the
reports that print its result. Where the merged halves meet: per-email rules (``rules_copy``),
batch rules (``rules_batch``), template/row rules (``rules_render``) and
``gtm_core.merge_hygiene.check_row``, once per render."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from gtm_core.merge_hygiene import check_row

from .model import RULE_CATALOGUE, RULES_VERSION, EmailBlock, Touch, Violation
from .parse import _MERGE_TAG_RE, DEFAULT_FIELD_LABELS, TAG_TO_COLUMN, render
from .rules_batch import lint_thread_repetition
from .rules_copy import lint_email
from .rules_derivation import lint_derivation
from .rules_render import (
    _DATED_RE,
    _domains_aliased,
    lint_article_collision,
    lint_empty_merge_tags,
    lint_merge_tags,
    lint_opener_dated,
    lint_possessive,
    lint_premise,
    lint_same_company_divergence,
    lint_signal_agent_homonym,
    lint_signal_contradiction,
    lint_signal_relevance,
    lint_touch_personalisation,
    lint_unused_signal_columns,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gtm_core.messaging.registry import Registry

# A copy rule that reads a rendered body cannot tell the TEMPLATE's words from the ROW's.
# Found 2026-08-19: a startup-seat touch that renders {{Why Now}} tripped
# `persona-lead-mismatch` for one recipient because that recipient's own signal clause
# happened to carry the word "attribution" — the clause named the row's own product line,
# which is their fact and not ours to quote (§R9). The copy led on nothing of the sort.
# Judging the seat lead is a judgement
# about the copy the author wrote, so a term that appears ONLY in merged data is not
# evidence about it.
#
# Deliberately narrow: only rules that fire on a vocabulary hit are eligible, and only when
# the term is absent from the template itself. Every mechanical rule (word count, em dash,
# greeting) still sees the full rendered string, because those defects are real no matter
# which half of the render produced them.
_DATA_BORNE_ELIGIBLE = frozenset({"persona-lead-mismatch"})


def _is_data_borne(v: Violation, merged_values: str, template: str) -> bool:
    """True when ``v``'s evidence lives only in the row's merged values, not the copy.

    ``template`` is the UNRENDERED body, and it is required rather than optional on
    purpose: this predicate DELETES a finding, so a caller that forgot the argument must
    break loudly instead of falling back to suppressing more.

    Until 2026-09-22 it took no template at all, and checked only that every borrowed term
    was present in the merged values — so a term the copy genuinely led on was suppressed
    whenever the prospect's own clause happened to reuse the vocabulary, which for a
    security-pain vocabulary and a security-adjacent prospect is the common case. The
    narrowing the comment above has always described ("only when the term is absent from
    the template itself") is now the code. Measured before changing it: zero live renders
    raise ``persona-lead-mismatch`` today, so this widens nothing on the current corpus —
    it stops a future suppression that would have been wrong. PENDING.md EC10.
    """
    if v.rule not in _DATA_BORNE_ELIGIBLE or not merged_values:
        return False
    terms = re.findall(r"\(([^()]*)\)", v.detail)
    if not terms:
        return False
    borrowed = [t.strip() for t in terms[0].split(",") if t.strip()]
    if not borrowed:
        return False
    low = template.lower()
    return all(t.lower() in merged_values and t.lower() not in low for t in borrowed)


def lint_merge_render(
    touches: list[Touch],
    rows: list[dict],
    *,
    signoff: str,
    extra_bans: tuple[str, ...] = (),
    case_studies: tuple[str, ...] = (),
    banned_stems: tuple[str, ...] = (),
    field_labels: tuple[str, ...] = DEFAULT_FIELD_LABELS,
    spec_text: str = "",
    premise_vocab: dict | None = None,
    domain_aliases: set | None = None,
    require_dated_opener: bool = False,
    registry: Registry | None = None,
) -> tuple[list[Violation], dict]:
    """Render every touch against every row and lint the results.

    Returns ``(violations, stats)``. Row-level merge-field defects are reported once per
    row, not once per touch, so a single bad company name doesn't triple-count.

    ``registry`` turns on the three derivation rules (``rules_derivation``). It is an argument
    rather than something this function loads, for the reason ``premise_vocab`` and
    ``domain_aliases`` are: the loader needs a profile, a profile is bound by the caller, and a
    linter that guessed one would be the right-content-wrong-company error at its source. It
    carries that shape's known hazard — an opt-in check is an inert check when nobody opts in,
    which is how ``cta-unstaged-artifact`` was dead for months — so the CLI passes it whenever
    ``--profile`` is given rather than behind a flag of its own.

    ``gift_artifacts`` and ``hook_matrix`` were removed 2026-09-24 with the rules that read
    them. Both are still accepted by the CLI, and ignored with a printed notice, until the
    skills stop passing them.
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
    v += lint_thread_repetition([(f"step {t.number}", t.subject, t.body) for t in touches])
    v += lint_same_company_divergence(touches, rows)
    v += lint_opener_dated(touches, require_dated_opener=require_dated_opener)
    v += lint_premise(spec_text, rows, premise_vocab)
    # TEMPLATES, never the renders below: on this path the author's words and the row's are
    # separable, so `rules_derivation` gets only the first half. The pack path has no such
    # separation and relies on that module's one-directional argument instead.
    v += lint_derivation(spec_text, [(t.subject, t.body) for t in touches], rows, registry)
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
            for x in lint_email(
                block,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
            ):
                if _is_data_borne(x, merged, t.body):
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


#: A flat second-person claim about the reader, and the hedges that make one legal. Used ONLY
#: by `--craft-report`, deliberately never as a rule: voice.md rule 9 REQUIRES beat 1 to be a
#: flat, unhedged, second-person claim taken from their public copy, so this pattern convicts
#: the required shape and the defect alike. It sees grammatical form, never verifiability. As a
#: number to look at while editing it is useful; as a gate it would block the copy shape the
#: 2026-09-22 craft work is trying to produce (measured: 40% of the second-person v2 batch
#: against 5% corpus-wide — calibrated by the corpus being impersonal rather than by being right).
_SECOND_PERSON_CLAIM_RE = re.compile(
    r"\byou(?:'ve|’ve| have| are| run| own)\b"
    r"|\byour\s+[a-z-]+\s+(?:is|are|has|have|sits|lives|holds|runs|carries|captures)\b",
    re.IGNORECASE,
)
_CLAIM_HEDGE_RE = re.compile(
    r"\b(may|might|likely|probably|often|usually|tend|tends|typically|if|unless|guess|hunch|"
    r"read|assume|suspect|imagine|perhaps|maybe|could)\b",
    re.IGNORECASE,
)

#: Vowel groups, for the reading-grade estimate. Deliberately a rough syllable count rather
#: than a dictionary: the number is a direction to read, not a threshold to pass.
_VOWEL_RUN_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)


def _syllables(word: str) -> int:
    w = re.sub(r"[^A-Za-z]", "", word).lower()
    if not w:
        return 0
    n = len(_VOWEL_RUN_RE.findall(w))
    if w.endswith("e") and n > 1:
        n -= 1
    return max(n, 1)


def _craft_report(touches: list[Touch]) -> int:
    """Per-touch craft metrics on the TEMPLATE, printed before anyone edits the copy.

    Why template-time and not per-row. `rules_copy` rejects Flesch-Kincaid as a merge
    gate for a good reason: the syllable term is dominated by this ICP's unavoidable vocabulary
    and by the rendered company name, so one template would score a different grade row by row
    for reasons no writer can act on. That objection is about the ROW. It does not hold for the
    template with merge tags stripped, which is a single artifact an author can actually edit —
    and the measurement that prompted this said so: the 2026-09-09 generic bodies carry zero
    merge tags in the body and still score grade 9.3 against grade 6.6 for the 2026-08-25 batch.

    Why a report and not a rule. Every threshold here would have to be calibrated against the
    existing corpus, which is the corpus the numbers say is the problem — a gate fitted to
    current practice ratifies current practice, which is the failure the WORDS_SOFT comment in
    `rules_copy` documents at length. Print the distance instead and let the author
    close it. Promotion to WARN needs a floor argued from something other than "what we already
    ship" (PENDING.md EC-series).

    Read-only and always exit 0, the same contract as `--anchor-report`.
    """
    print(f"craft report — {len(touches)} touch(es), merge tags stripped\n")
    print(f"  {'touch':<7}{'words':>7}{'grade':>7}{'you':>5}{'I/we':>6}{'flat-2p':>9}{'dated':>7}")
    for t in touches:
        body = _MERGE_TAG_RE.sub("", t.body)
        body = re.sub(r"^\s*Hi\s*,", "", body.strip())
        words = re.findall(r"[A-Za-z']+", body)
        sents = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        if not words or not sents:
            continue
        grade = (
            0.39 * (len(words) / len(sents))
            + 11.8 * (sum(_syllables(w) for w in words) / len(words))
            - 15.59
        )
        you = len(re.findall(r"\b(?:you|your|yours)\b", body, re.IGNORECASE))
        # `I` is case-SENSITIVE (lowercase "i" is not the pronoun); the rest are not, or a
        # sentence-initial "My read:" / "We see" / "Our take" counts as no sender at all.
        # Found 2026-09-22 measuring the EC7 recut: a touch opening "My read:" reported 0.
        me = len(re.findall(r"\bI\b", body)) + len(
            re.findall(r"\b(?:we|our|us|my)\b", body, re.IGNORECASE)
        )
        flat = sum(
            1 for s in sents if _SECOND_PERSON_CLAIM_RE.search(s) and not _CLAIM_HEDGE_RE.search(s)
        )
        dated = len(_DATED_RE.findall(body))
        print(f"  {t.number:<7}{len(words):>7}{grade:>7.1f}{you:>5}{me:>6}{flat:>9}{dated:>7}")
    print(
        "\n  grade  = Flesch-Kincaid on the template. docs/cold-email-craft-evidence.md 5.5 puts\n"
        "           the evidence at 3-5 (Boomerang, 5.3M messages, +36% reply vs college level).\n"
        "           No gate enforces it; this is the distance between the two.\n"
        "  you    = second-person tokens. A body with none is a thesis, not a letter. Ungated.\n"
        "  I/we   = whether a person is visibly sending this. Ungated.\n"
        "  dated  = year/month tokens. The checkable half of the named-dated-external referent\n"
        "           body_template.md asks touch 1 to open on. ZERO of 596 live touch-1 openers\n"
        "           carried one on 2026-09-22, which is why this is a column and not a rule.\n"
        "  flat-2p= unhedged claims of the shapes `you have/are/run/own ...` and `your X is/has\n"
        "           ...` only. A PARTIAL count by construction — 'You shipped in March' is the\n"
        "           same shape and is not counted — because this pattern is the one measured on\n"
        "           2026-09-22 (5% corpus-wide, 40% on the second-person v2 batch) and widening\n"
        "           it would silently invalidate that number. Treat 0 as 'none of these shapes',\n"
        "           never as 'no claims'. NOT a defect count either: voice.md rule 9 requires\n"
        "           beat 1 to be exactly this shape, sourced from their public copy — so high is\n"
        "           normal for a researched opener. Read it beside `you`, and ask of each whether\n"
        "           you could quote the source."
    )
    return 0
