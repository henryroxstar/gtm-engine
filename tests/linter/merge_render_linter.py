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
        --stem-file profiles/<profile>/knowledge/outreach-banned-stems.txt
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from outreach_pack_linter import (  # noqa: E402
    EmailBlock,
    Violation,
    _load_bans,
    lint_email,
)

from gtm_core.merge_hygiene import (  # noqa: E402
    check_row,
    ends_in_sibilant,
    signal_clause,
    starts_with_article,
)

RULES_VERSION = "2026-07-28"

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


def lint_merge_render(
    touches: list[Touch],
    rows: list[dict],
    *,
    signoff: str,
    extra_bans: tuple[str, ...] = (),
    case_studies: tuple[str, ...] = (),
    banned_stems: tuple[str, ...] = (),
    field_labels: tuple[str, ...] = DEFAULT_FIELD_LABELS,
) -> tuple[list[Violation], dict]:
    """Render every touch against every row and lint the results.

    Returns ``(violations, stats)``. Row-level merge-field defects are reported once per
    row, not once per touch, so a single bad company name doesn't triple-count.
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
        escalate |= {"last-name-symbols", "last-name-credentials"}
    if "Job Title" in rendered_fields:
        escalate |= {"title-headline"}

    v += lint_possessive(touches, rows)
    v += lint_article_collision(touches, rows)
    v += lint_empty_merge_tags(touches, rows)
    v += lint_unused_signal_columns(touches, rows)
    v += lint_same_company_divergence(touches, rows)

    copy_hits: dict[tuple[int, str, str, str], list[str]] = {}

    for r in rows:
        label = f"{(r.get('email') or '?').strip()}"
        for f in check_row(r):
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
            for x in lint_email(
                block,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
            ):
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=f"Merge-render linter for sequenced sends (rules {RULES_VERSION})"
    )
    ap.add_argument("spec", nargs="?", help="sequence spec .md containing the touches")
    ap.add_argument("--csv", help="prospect CSV to render against (e.g. ready-to-load.csv)")
    ap.add_argument("--signoff", default="Alex", help="expected bare sign-off (pass the real name)")
    ap.add_argument("--ban-file", help="profile voice-bans.txt")
    ap.add_argument("--case-study-file", help="profile outreach-case-studies.txt")
    ap.add_argument("--stem-file", help="profile outreach-banned-stems.txt")
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
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.spec or not args.csv:
        ap.error("spec path and --csv are both required")

    touches = parse_spec(Path(args.spec).read_text(encoding="utf-8"))
    with Path(args.csv).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    labels = tuple(_load_bans(args.fields)) if args.fields else DEFAULT_FIELD_LABELS
    violations, stats = lint_merge_render(
        touches,
        rows,
        signoff=args.signoff,
        extra_bans=_load_bans(args.ban_file),
        case_studies=_load_bans(args.case_study_file),
        banned_stems=_load_bans(args.stem_file),
        field_labels=labels,
    )
    return _report(violations, stats, show=args.show, daily_cap=args.daily_cap)


if __name__ == "__main__":
    sys.exit(main())
