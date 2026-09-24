"""The one command line: ``outreach_linter.py pack|render``, ``--selftest``, ``--list-rules``.

Two subcommands, not one flat parser: the merged CLIs shared ``--csv`` and ``--signoff`` with
DIFFERENT meanings (a tracker CSV to staleness-check vs the prospect CSV to render against;
a placeholder default vs the spec header's own ``Sign-off:``). A flat union would have had to
pick one meaning per flag — a behaviour change dressed as a refactor. Each mode keeps its
flags exactly as they were; the caller adds one word.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from gtm_core.finding_budget import WARN_BUDGET, budget_verdict, render_budget

from .driver import _craft_report, _report, _write_qa_record, lint_merge_render
from .model import ALL_RULE_IDS, RULES_VERSION, Touch, Violation
from .parse import (
    DEFAULT_FIELD_LABELS,
    _load_bans,
    _load_domain_aliases,
    _load_premise_vocab,
    parse_followup,
    parse_spec,
)
from .rules_batch import (
    lint_body_homogeneity,
    lint_formatted_pack,
    lint_pack,
    lint_subject_homogeneity,
    lint_tracker_csv,
)
from .rules_render import lint_merge_tags


def _tenant_ban_phrases() -> tuple[str, ...]:
    """Every phrase every profile in the tree has retired, unioned — the selftest's ban set.

    **The FR0 residue this closes (PENDING.md, 2026-09-24).** Both positive controls below
    shipped copy their own tenant had already banned — ``My hunch:``, ``My bet on the open
    piece:`` — and both went green, because ``--selftest`` was the one entry point that ran the
    gate with an EMPTY ban list. The retired phrase was therefore pinned, by a test, as the
    house pattern: anyone reading the control to learn the shape learned the retired one. A
    positive control that cannot go red for the thing the gate exists to catch is not a control.

    **Why the union rather than one tenant's file.** A literal ``profiles/<tenant>/`` path in
    this package would be a tenant token in ``tests/``, which is carved — ``debrand_check.sh
    --release`` scans exactly here and would refuse it. Reading whatever profiles the tree
    happens to hold keeps the fixture tenant-agnostic AND strictly stronger: house-pattern copy
    should be sendable for every tenant that ships beside it, so a phrase ANY of them retired is
    the wrong thing to pin. In a public cut only ``_template`` exists and the set shrinks to its
    eight entries; the selftest prints the count it loaded so "small" is never mistaken for
    "loaded".
    """
    from gtm_core.paths import resolve_profiles_root

    phrases: set[str] = set()
    for path in sorted(resolve_profiles_root().glob("*/knowledge/voice-bans.txt")):
        phrases.update(_load_bans(str(path)))
    return tuple(sorted(phrases))


# --- the two positive controls -------------------------------------------------------------
#
# Both are REGISTRY-DERIVED in shape (FR3 Task 3.5): a fenced front block declaring the `angle:`
# the body argues and one `slot_<id>:` per body slot, then five slots in order — the row's own
# signal, the claim, the seat's pain, the hedge, the proof. Each slot names where it came from,
# which is the whole question `rules_derivation` asks and the question no pre-registry body
# could answer. The ids are INVENTED: a control must not paste a tenant's claim statements out
# of `profiles/`, or the fixture becomes a second, stale copy of the registry (§R9, and the
# 2026-09-22 lesson below).
#
# Identities are fictional (`gtm_core.fictionalize`, docs/RULES.md §R9). The proof slot names no
# regulator: an earlier version of the render control named what it believed was an invented
# standards body and it was in fact a real vendor-authored spec, and that vendor is a tracked
# competitor in the tenant's own competitors.toml. The roster lint could not see it — it derives
# names from a competitor's `name`, and this was an `aliases` entry — so the rule now is that a
# control refers to an anchor by ROLE ("your market's AI governance guidance") and cites its id
# in `slot_proof`, where a reader looks it up rather than reads it.
#
# WHAT ACTUALLY HOLDS THEM CLEAN, so the next editor does not protect the wrong thing: no banned
# phrase, no time-ask, no link, at most three questions, and inside the word/sentence bands. NOT
# the hedge. `hedge-missing` retired on 2026-09-24, so "Likely" satisfies nothing — it is house
# voice, not a gate's requirement, and `HEDGE_CUES`'s one surviving reader
# (`_hedge_ngram_whitelist`) ignores a cue shorter than NGRAM_N, so it contributes no exemption
# either. Swapping it for another cue changes no verdict; adding a banned one changes every one.
#
# NOT operator-signed. These pin the SHAPE and are a regression net for it; whether the copy is
# good is a judgement code cannot make.
_GOOD = """Rules-Version: 2026-09-04

```
angle: handoff-evidence-platform
slot_signal: row.signal_evidence
slot_claim: handoff-attested
slot_pain: platform-lead
slot_hedge: voice-rules.hedge.cues
slot_proof: market-ai-governance-anchor
```

### 1. Dana Rivera · Head of Platform, Acme Robotics
**To:** jordan@meridians.example
**Subject:** agent audit trail

Hi Dana,

Acme Robotics moved twelve agents into production, each calling internal tools on its own credential.

Every one of those calls writes a signed audit entry, so you can show what ran. It does not show which agent held the authority once one hands work to another.

Your customers will ask for that, and today each of them rebuilds the answer per deployment.

Likely you have part of this already. Your market's AI governance guidance puts agent accountability in its first control set.

Want the one-pager on per-agent identity at the tool boundary?

Alex

---
"""

# Fixture tenant lists the selftest passes explicitly (the shipped defaults are empty).
_SELFTEST_CASE_STUDIES = ("exampleco",)
_SELFTEST_STEMS = ("service account with no per-call proof",)

_BAD = """Rules-Version: 2026-07-01

### 1. Jane Doe · CEO, Acme
**To:** avery@forgeworks.example
**Subject:** Quick Question About Your Platform Strategy

Hey Jane,

I hope this finds you well. Your agents share a service account with no per-call proof, so no one can tell which agent acted. ExampleCo hit the same wall last year. Worth me sending the teardown?

Best,
Jane's Friend

---
"""

#: The render control: the same five slots as ``_GOOD``, laddered across a three-touch sequence
#: so the thread-scoped rules (``thread-sentence-repeat``, ``thread-reply-prefix``) have a
#: positive control too. It replaces the two fixtures that stood here until 2026-09-24 — a
#: two-touch "good" spec and a separately-commented reference sequence — because BOTH carried
#: copy the tenant had retired (``My read``, ``My hunch``, ``Say so and I``) and both passed, the
#: FR0 residue ``_tenant_ban_phrases`` now closes. One control that goes red on a retired phrase
#: is worth more than two that cannot.
_GOOD_SPEC = """
Rules-Version: 2026-09-04

```
angle: handoff-evidence-platform
slot_signal: row.signal_evidence
slot_claim: handoff-attested
slot_pain: platform-lead
slot_hedge: voice-rules.hedge.cues
slot_proof: market-ai-governance-anchor
```

**Step 1 — Day 1** · Subject: `agent handoff evidence`
> Hi {{First Name}},
>
> Once agents at {{Company}} act on records rather than read them, the first question an
> auditor asks is which agent acted.
>
> Each call writes a signed audit entry, so you can show what ran. It does not show who
> held the authority when one agent handed work to another.
>
> Likely you have part of this. Your market's AI governance guidance puts agent
> accountability in its first control set.
>
> Want the one-page teardown of how a portable credential closes that once?
>
> Henry

**Step 2 — Day 4** (same thread, no subject)
> Hi {{First Name}},
>
> Following the note on agent authority. The gap widens the moment an agent delegates to a
> second one, because the credential that signed the call belongs to the platform and not
> to the actor.
>
> Likely {{Company}} already logs the what. The piece still open is portable evidence of
> the who, carried across partners instead of rebuilt per integration.
>
> Want the crosswalk another platform used before its first enterprise security review?
>
> Henry

**Step 3 — Day 9** · Subject: `closing the identity loop`
> Hi {{First Name}},
>
> Last one from me. A portable credential is issued once and verified by whoever receives
> the call, so the evidence travels with the agent rather than with your platform.
>
> If you carry that per integration today, the cost grows with every partner you add. If
> you do not carry it yet, the first customer security review sets the date.
>
> Want the reference architecture, or should I leave it here?
>
> Henry
"""


def _selftest_pack() -> int:
    bans = _tenant_ban_phrases()
    good = lint_pack(_GOOD, extra_bans=bans)
    good_errors = [x for x in good if x.level == "ERROR"]
    bad = lint_pack(
        _BAD,
        extra_bans=bans,
        case_studies=_SELFTEST_CASE_STUDIES,
        banned_stems=_SELFTEST_STEMS,
    )
    bad_rules = {x.rule for x in bad}
    expect = {
        "rules-version-stale",
        "subject-length",
        "greeting",
        "sign-off",
        "banned-word",
        "banned-stem",
        "named-case-study",
    }
    ok = not good_errors and expect.issubset(bad_rules)
    print(f"selftest: {len(bans)} tenant ban phrase(s) loaded")
    print(
        f"selftest: good-pack errors={len(good_errors)} (want 0); bad-pack rules hit={sorted(bad_rules)}"
    )
    if not ok:
        for x in good_errors:
            print("  unexpected:", x)
        print("  missing:", expect - bad_rules)
    return 0 if ok else 1


def _selftest_render() -> int:
    bans = _tenant_ban_phrases()
    touches = parse_spec(_GOOD_SPEC)
    assert len(touches) == 3, f"expected 3 touches, got {len(touches)}"
    assert touches[0].subject == "agent handoff evidence"
    assert touches[1].subject == ""
    assert touches[2].subject == "closing the identity loop"
    assert touches[0].day == 1 and touches[1].day == 4 and touches[2].day == 9

    good = {
        "first": "Chris",
        "last": "Renner",
        "email": "chris@cascade.example",
        "company": "Cascade",
        "company_domain": "cascade.example",
        "title": "CISO",
    }
    v, stats = lint_merge_render(
        touches, [good], signoff="Henry", extra_bans=bans, spec_text=_GOOD_SPEC
    )
    assert stats["renders"] == 3
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


def selftest() -> int:
    """Both positive controls. One non-zero verdict fails the whole selftest."""
    return _selftest_pack() or _selftest_render()


#: Flags whose rule retired on 2026-09-24. Accepted so a skill command string that still
#: carries one does not die at argparse mid-migration, and NEVER silently: a flag that is
#: quietly ignored is indistinguishable from a check that ran and passed, which is the exact
#: reachability failure `cta-unstaged-artifact` spent months in.
_RETIRED_FLAGS = {
    "artifact_file": "--artifact-file (cta-unstaged-artifact retired 2026-09-24)",
    "hook_matrix": "--hook-matrix (hook-cell-* / signal-cell-* retired 2026-09-24)",
}


def _notice_retired_flags(args: argparse.Namespace) -> None:
    for attr, label in _RETIRED_FLAGS.items():
        if getattr(args, attr, None):
            print(f"note: {label} — ignored, nothing reads it", file=sys.stderr)


def _load_registry(profile: str | None):
    """The tenant's fact registry, or ``None`` with a loud reason.

    A malformed or absent registry turns the three derivation rules OFF rather than refusing
    to lint at all: this gate's job is the copy, and a tenant that has not yet written
    `claims.toml` still needs `word-count` and `em-dash`. The registry has its OWN gate —
    ``python -m gtm_core.messaging check`` — and the notice names it, so "off" is never
    mistaken for "clean".
    """
    if not profile:
        return None
    from gtm_core.messaging.registry import RegistryError, load

    try:
        return load(profile)
    except RegistryError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "unreadable"
        print(
            f"note: derivation rules OFF for {profile!r} — registry did not load ({first}). "
            f"Run `uv run python -m gtm_core.messaging check --profile {profile}`.",
            file=sys.stderr,
        )
        return None


def _add_ban_flags(ap: argparse.ArgumentParser) -> None:
    """The four profile word-list flags, identical on both modes before the merge."""
    ap.add_argument("--ban-file", help="profile voice-bans.txt for extra banned words")
    ap.add_argument(
        "--case-study-file", help="profile file of case-study company names to flag (one per line)"
    )
    ap.add_argument("--stem-file", help="profile file of banned mail-merge stems (one per line)")
    ap.add_argument(
        "--artifact-file",
        help="RETIRED 2026-09-24 with `cta-unstaged-artifact`. Still accepted, and ignored "
        "with a printed notice, so the skills that pass it do not hard-fail at argparse "
        "before Task 3.7 rewrites them. Delete the flag when they stop passing it.",
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=f"Outreach linter (rules {RULES_VERSION})")
    ap.add_argument("--selftest", action="store_true", help="run both positive controls and exit")
    ap.add_argument(
        "--list-rules",
        nargs="?",
        const="-",
        metavar="PATH",
        help="every rule id this linter can raise, one per line, to PATH or stdout. The "
        "inventory `gtm_core.adjudication novel` reads; a PATH is accepted because a shell "
        "redirect is denied at runtime. See `model.ALL_RULE_IDS`.",
    )
    sub = ap.add_subparsers(dest="mode")

    p = sub.add_parser(
        "pack", help="lint an outreach pack (.md), a batch of them, or a tracker CSV"
    )
    p.add_argument("pack", nargs="?", help="path to the pack .md to lint")
    p.add_argument("--csv", help="tracker CSV to staleness-check (rules_version column)")
    _add_ban_flags(p)
    p.add_argument(
        "--shared-phrase-file",
        help="phrases exempt from body-template-share; the pitch is meant to be consistent",
    )
    p.add_argument(
        "--signoff", default="Alex", help="expected bare sign-off line (pass your real name)"
    )
    p.add_argument(
        "--ack",
        action="append",
        default=[],
        metavar="RULE",
        help="acknowledge a WARN class by rule name; repeatable. It stops counting.",
    )
    p.add_argument(
        "--budget",
        type=int,
        default=WARN_BUDGET,
        help=f"unacknowledged WARN classes tolerated before this blocks (default {WARN_BUDGET})",
    )
    p.add_argument(
        "--format",
        default="auto",
        choices=["auto", "tier-a-manual", "draft-outreach", "prospect-pack"],
        help="pack shape (auto-detected by default)",
    )
    p.add_argument(
        "--batch",
        nargs="+",
        metavar="PATH_OR_GLOB",
        help="packs to lint together (adds the cross-file template checks); globs or paths",
    )
    p.add_argument(
        "--profile",
        help="active profile; loads the fact registry (claims/proof/angles). Without it the "
        "derivation rules are OFF. Added 2026-09-24: `pack` had no such flag, so a 1:1 pack — "
        "the shape a human actually reviews and sends — reached none of the registry rules "
        "while the quality card recorded `claim-status` as authoritative on it.",
    )

    r = sub.add_parser("render", help="lint a sequence spec against the CSV it will render over")
    r.add_argument("spec", nargs="?", help="sequence spec .md containing the touches")
    r.add_argument("--csv", help="prospect CSV to render against (e.g. ready-to-load.csv)")
    _add_ban_flags(r)
    r.add_argument(
        "--signoff",
        default=None,
        help="expected bare sign-off; defaults to the spec header's `Sign-off:` field. A "
        "placeholder default made every CTA rule inspect the signature line, not the offer.",
    )
    r.add_argument(
        "--hook-matrix",
        help="RETIRED 2026-09-24 with `hook-cell-*`, `signal-cell-mismatch` and "
        "`signal-column-*`. The cell a spec implements is now DERIVED from its declared "
        "`angle:` rather than declared beside it. Still accepted, and ignored with a printed "
        "notice, for the reason --artifact-file is.",
    )
    r.add_argument(
        "--fields", help="newline-delimited provider field labels (defaults to the standard set)"
    )
    r.add_argument("--show", type=int, default=3, help="examples to print per rule")
    r.add_argument(
        "--daily-cap",
        type=int,
        default=0,
        help="combined daily send limit; prints how long the list takes to work through",
    )
    r.add_argument(
        "--require-dated-opener",
        action="store_true",
        help="turn on `opener-undated` (WARN); off by default — see lint_opener_dated",
    )
    r.add_argument(
        "--json",
        dest="json_out",
        help="also write a structured QA record here, so it survives the run",
    )
    r.add_argument(
        "--sequence-id", help="sequence this spec+csv pair was staged as; recorded in --json output"
    )
    r.add_argument(
        "--profile",
        help="active profile; loads knowledge/premise-vocab.toml AND the fact registry "
        "(claims/proof/angles). Without it `premise-unsupported` and all three derivation "
        "rules are OFF — an opt-in check nobody opts into is an inert check, so pass it.",
    )
    r.add_argument(
        "--craft-report",
        action="store_true",
        help="per-touch reading grade and person counts on the TEMPLATE, then exit 0 "
        "without linting. None of it is gated — see `_craft_report`.",
    )
    return ap


def _run_pack(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not args.pack and not args.csv and not args.batch:
        ap.error("pack path, --batch or --csv required")

    kw = {
        "extra_bans": _load_bans(args.ban_file),
        "signoff": args.signoff,
        "case_studies": _load_bans(args.case_study_file),
        "banned_stems": _load_bans(args.stem_file),
        # Loaded once and shared by the single-pack and the --batch loop, so a batch cannot
        # silently run a narrower gate than the same packs linted one at a time.
        "registry": _load_registry(args.profile),
    }
    _notice_retired_flags(args)
    violations: list[Violation] = []
    if args.pack:
        violations += lint_formatted_pack(
            Path(args.pack).read_text(encoding="utf-8"), args.format, **kw
        )[0]
    if args.batch:
        # Each entry is a literal path or a glob. Python's glob has no brace expansion, so an
        # unexpanded "{a,b}" would silently match nothing — resolve explicit paths directly.
        seen: dict[str, Path] = {}
        for pat in args.batch:
            p = Path(pat)
            for hit in [p] if p.is_file() else sorted(Path().glob(pat)):
                seen[str(hit)] = hit
        paths = [seen[k] for k in sorted(seen)]
        if not paths:
            print(f"no files matched {args.batch!r} (brace globs are not expanded — pass paths)")
            return 1
        subjects: list[tuple[str, str]] = []
        bodies: list[tuple[str, str]] = []
        fu_subjects: list[tuple[str, str]] = []
        fu_bodies: list[tuple[str, str]] = []
        for p in paths:
            vs, blocks = lint_formatted_pack(p.read_text(encoding="utf-8"), args.format, **kw)
            violations += [Violation(x.level, f"{p.name}:{x.email}", x.rule, x.detail) for x in vs]
            subjects += [(p.name, b.subject) for b in blocks]
            bodies += [(p.name, b.body) for b in blocks]
            fu = parse_followup(p.read_text(encoding="utf-8"))
            if fu:
                fu_subjects.append((p.name, fu[0]))
                fu_bodies.append((p.name, fu[1]))
        shared = _load_bans(args.shared_phrase_file)
        violations += lint_subject_homogeneity(subjects)
        violations += lint_body_homogeneity(bodies, shared_phrases=shared)
        violations += [
            Violation(v.level, "BATCH-followup", v.rule, v.detail)
            for v in lint_subject_homogeneity(fu_subjects)
            + lint_body_homogeneity(fu_bodies, shared_phrases=shared)
        ]
        print(f"linted {len(paths)} pack(s) from {args.batch!r}\n")
    if args.csv:
        violations += lint_tracker_csv(Path(args.csv))

    errors = [x for x in violations if x.level == "ERROR"]
    warns = [x for x in violations if x.level != "ERROR"]

    # Errors always enumerate: they block, so every one has to be actionable.
    for x in errors:
        print(x)

    # Warnings are budgeted. Past the budget this prints rates and exemplars instead of a
    # wall, and BLOCKS — because a wall of warnings is read as "noisy but fine" and the
    # findings that mattered go out with the batch. 388 of them once did exactly that.
    verdict = budget_verdict(
        [f"{x.rule}: {x.detail}" for x in warns],
        denominator=max(len(warns), 1),
        acked=tuple(args.ack),
        budget=args.budget,
    )
    if verdict.enumerable:
        for x in warns:
            print(x)
    else:
        print(render_budget(verdict, unit="warning"))

    print(f"\n{len(errors)} error(s), {len(warns)} warning(s) — rules {RULES_VERSION}")
    return 1 if (errors or verdict.blocked) else 0


def _run_render(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    # `--craft-report` reads the TEMPLATE only, so requiring a CSV it never opens made the
    # one mode an author runs BEFORE editing the copy the one mode that needs the send list.
    # `--anchor-report` genuinely needs rows (it scores every render), so the check stays there.
    if not args.spec or (not args.csv and not args.craft_report):
        ap.error("spec path is required, and --csv for everything but --craft-report")

    spec_header_text = Path(args.spec).read_text(encoding="utf-8")
    touches = parse_spec(spec_header_text)
    if not args.signoff:
        m = re.search(r"^Sign-off:\s*(\S+)", spec_header_text, re.MULTILINE)
        args.signoff = m.group(1) if m else "Alex"
    if args.craft_report and not args.csv:
        return _craft_report(touches)
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

    if args.craft_report:
        return _craft_report(touches)

    _notice_retired_flags(args)
    labels = tuple(_load_bans(args.fields)) if args.fields else DEFAULT_FIELD_LABELS
    violations, stats = lint_merge_render(
        touches,
        rows,
        signoff=args.signoff,
        extra_bans=_load_bans(args.ban_file),
        case_studies=_load_bans(args.case_study_file),
        banned_stems=_load_bans(args.stem_file),
        field_labels=labels,
        spec_text=spec_header_text,
        premise_vocab=(_load_premise_vocab(args.profile) if args.profile else None),
        domain_aliases=(_load_domain_aliases(args.profile) if args.profile else None),
        require_dated_opener=args.require_dated_opener,
        registry=_load_registry(args.profile),
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


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.list_rules:
        text = "\n".join(ALL_RULE_IDS) + "\n"
        if args.list_rules == "-":
            print(text, end="")
            return 0
        out = Path(args.list_rules)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {len(ALL_RULE_IDS)} rule name(s) to {out}")
        return 0
    if args.mode == "pack":
        return _run_pack(ap, args)
    if args.mode == "render":
        return _run_render(ap, args)
    ap.error("choose a mode: `pack` or `render` (or pass --selftest / --list-rules)")
    return 2
