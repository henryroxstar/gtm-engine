"""The deterministic preflight — the QC roster as a precondition, not a chore.

Eight gate modules in this repo are pure, stdlib-only and free to run: they open a
CSV, judge it, and return findings. Not one of them was reachable from anything
scheduled. They ran when a human remembered, which — on the evidence this repo has
already written down — is not often enough.

:mod:`gtm_core.preflight` solved exactly this shape for **connector liveness**. Its
docstring records the bill: a 2026-08-11 bulk run spent ~$77 discovering and scoring
500 accounts and delivered **zero contacts**, because two providers were down in ways
knowable in the first ten seconds for nothing. The skill's step 1 said to *note* which
sources were live, and — the sentence this module exists to retire — *"a note is not a
gate, so the run proceeded."*

So the gap was never the concept. It was the roster. ``preflight.py`` gates on
connectors; the content checks gated on nothing. This module is the second half, over
the same idea and deliberately **not** a second parallel "preflight" concept — a rival
notion of the same word would be its own small instance of the defect class the whole
2026-08-27 PRD is about.

Three rules carry it:

**A missing artifact is a skip, not a failure.** A profile with nothing staged is not
broken; it has nothing staged. A daily unit that goes red every morning teaches the
operator to ignore it, and an ignored gate is
:mod:`gtm_core.finding_budget`'s 388-warning failure arriving by a different road.

**ERROR blocks at any count; WARN is graded.** Every warning across every check lands
in one :func:`~gtm_core.finding_budget.budget_verdict`, so the roster as a whole obeys
the readability budget rather than each check obeying it alone and the union being
unreadable anyway. Acknowledgment stays per class (``--ack <rule>``), never a blanket
pass.

**It must stay free.** This module imports no model registry, no ledger, and nothing
that speaks HTTP, and it is the caller's only guarantee that a "precondition" has not
quietly become a paid call. Held open by
``tests/test_preflight_report.py::test_the_preflight_makes_no_network_call_and_resolves_no_model_role``
rather than by this paragraph.

Usage::

    python -m gtm_core.preflight_report --profile <profile>
    python -m gtm_core.preflight_report --profile <profile> --json
    python -m gtm_core.preflight_report --profile <profile> --ack signal-stale

Exit codes: ``0`` clean or nothing staged, ``1`` any ERROR or a blocked WARN tier.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    account_integrity,
    email_compliance,
    hook_coverage,
    list_fit,
    merge_hygiene,
    signal_record,
    suppression,
)
from .finding_budget import (
    EXEMPLARS,
    WARN_BUDGET,
    BudgetVerdict,
    budget_verdict,
    group_by_rule,
    render_budget,
)
from .paths import resolve_content_root, resolve_profiles_root
from .prospect_paths import ready_to_load, suppression_ledger
from .prospect_readiness import readiness_or_error, report_path
from .refusal_copy import Refusal

__all__ = [
    "OK",
    "FAIL",
    "SKIP",
    "Check",
    "CheckResult",
    "PreflightReport",
    "ROSTER",
    "run_preflight",
    "render",
    "write_report",
    "main",
]

OK = "ok"
FAIL = "fail"
SKIP = "skip"


@dataclass
class CheckResult:
    """One roster entry's verdict.

    ``status`` is deliberately three-valued. Collapsing ``skip`` into ``pass`` would
    let "the roster is green" mean "the roster ran nothing", which is the exact
    ambiguity that lets an inert gate look healthy for months.
    """

    name: str
    status: str
    detail: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Population this check examined, per rule name, for the budget's rates.
    denominators: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class Check:
    """A roster entry: a name, the module it lives in, and how to run it.

    ``module`` is carried explicitly so the no-network test can walk each roster
    module's own imports. A check that reaches a model one hop away is still a check
    that reaches a model.
    """

    name: str
    module: str
    run: Callable[[_Inputs], CheckResult]


@dataclass
class _Inputs:
    """Everything the roster may read, resolved once and shared.

    ``rows`` is ``None`` when no list is staged — distinct from ``[]``, which is a
    staged list that happens to be empty. Only the first is a skip.
    """

    profile: str
    content_root: Path
    profiles_root: Path
    as_of: datetime.date
    csv_path: Path
    rows: list[dict] | None
    fieldnames: list[str]
    acked: tuple[str, ...]
    budget: int
    #: Findings ``signal_record`` has already reported, as a multiset.
    #: ``account_integrity.audit_rows`` calls ``audit_records`` internally, so without
    #: this the whole record tier is counted twice — the first run of this module against
    #: one live profile reported 972 ``verdict-missing`` errors over 508 rows. Multiset, not a
    #: set: a CSV genuinely carrying the same person twice is a finding, not a duplicate.
    record_seen: Counter = field(default_factory=Counter)


def _skip(name: str, why: str) -> CheckResult:
    return CheckResult(name=name, status=SKIP, detail=why)


def _no_list(name: str, i: _Inputs) -> CheckResult:
    return _skip(name, f"nothing staged at {i.csv_path}")


# --- the roster ------------------------------------------------------------
#
# Each entry returns errors and warnings as plain "rule: detail" strings, the shape
# `finding_budget.split_rule` already reads, so the aggregate verdict needs no per-check
# special casing.


def _minus(findings: list[str], already: Counter) -> list[str]:
    """``findings`` less what another check already reported, multiset-wise."""
    if not already:
        return list(findings)
    budgeted = Counter(already)
    out: list[str] = []
    for f in findings:
        if budgeted.get(f, 0):
            budgeted[f] -= 1
            continue
        out.append(f)
    return out


def _across_groups(findings: list[str], seen: set[str]) -> list[str]:
    """``findings`` less any this check already emitted for an EARLIER lane group.

    Intra-group multiplicity is kept (the same finding twice in one lane is two real rows);
    only a repeat across groups is dropped, because that is one account being audited once
    per lane it appears in rather than one fact occurring twice. ``seen`` is mutated.
    """
    out = [f for f in findings if f not in seen]
    seen.update(out)
    return out


def _lane_groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """``rows`` partitioned by their ``lane`` column, in file order within each group.

    ``ready-to-load.csv`` is a MIXED-lane file — ``consolidate`` stamps each row with the
    lane the router chose, blank while unrouted — and the gate's rules differ by lane:
    ``audit_rows(..., lane="generic")`` demotes the research-record findings to advisory,
    because a generic body references no research. Until 2026-09-23 this module audited the
    whole file unlaned, so a routed generic row failed the daily unit on ``signal-*`` and
    ``why-now-not-a-signal`` errors the gate itself would have waved through: the P6 defect
    ("a wrapper around a gate becomes part of the gate while inheriting none of its tests")
    one door over. A blank lane is audited unlaned, exactly as before; sorted so it comes
    first and the report order is stable.

    **The column is taken on trust here, and that is deliberate.** The enrollment gate does
    not trust it alone — `account_integrity` calls `_refuse_lane_state_mismatch`, because a
    stale or hand-edited lane must not enrol on an old column's strength. This module is a
    read-only daily report that enrols nothing, so the worst a wrong lane can do is soften
    or harden one morning's advisory tier; the refusal still runs at the door that spends
    money. Re-running the mismatch check here would also re-read `lanes-state.jsonl` on
    every timer fire for a verdict nothing acts on. If this report ever gates an action,
    that trade stops holding and the refusal has to come with it.
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault((r.get("lane") or "").strip().lower(), []).append(r)
    return sorted(groups.items())


def _lane_detail(groups: list[tuple[str, list[dict]]]) -> str:
    return ", ".join(f"{lane or 'unrouted'}={len(rows)}" for lane, rows in groups)


def _run_account_integrity(i: _Inputs) -> CheckResult:
    if i.rows is None:
        return _no_list("account_integrity", i)
    groups = _lane_groups(i.rows)
    errors: list[str] = []
    warnings: list[str] = []
    seen_errors: set[str] = set()
    seen_warnings: set[str] = set()
    rows = accounts = 0
    for lane, group in groups:
        a = account_integrity.audit_rows(
            group,
            i.profile,
            content_root=i.content_root,
            profiles_root=i.profiles_root,
            fieldnames=i.fieldnames,
            acked=i.acked,
            budget=i.budget,
            as_of=i.as_of,
            lane=lane,
        )
        # `audit_rows` runs `audit_records` inside itself, so the record tier arrives here
        # a second time. `signal_record` owns those findings; this entry keeps only what
        # is genuinely account-level (no-dossier, domain-*, competitor, leadership).
        # `_across_groups` is what stops the NUMERATOR repeating the mistake the account
        # count made below. `audit_rows` dedupes its account tier per CALL, so an account
        # whose rows sit in two lanes is audited twice and reports `no-dossier` (and the
        # competitor / leadership lines) once per lane — against a denominator that is now
        # distinct over the whole file. A rate with a summed numerator over a distinct
        # denominator is wrong in the direction that reads as precise. Within one group the
        # multiset is preserved: a CSV genuinely carrying the same person twice is a
        # finding, not a duplicate — the same rule `_minus` follows.
        errors += _across_groups(_minus(list(a.errors), i.record_seen), seen_errors)
        # A header predating the research record is one file-level fact, not N row facts —
        # `signal_record` reports it that way and this must not re-expand it.
        errors += _across_groups(
            [f"record-columns-missing: {c}" for c in a.record_missing_columns], seen_errors
        )
        warnings += _across_groups(_minus(list(a.warnings), i.record_seen), seen_warnings)
        rows += a.rows
    # Counted across the WHOLE file, never summed over the lane groups. An account's rows
    # can sit in two lanes at once — 25 of 578 did on the first live run, mostly
    # `excluded`+`generic` — and summing each group's own distinct count reported 603.
    # That number is not just printed: it is the `denominators` base every WARN-tier rate
    # is measured against, so the inflation quietly understated all of them. `account_key` is
    # the identity `audit_rows` itself dedupes on, so the two cannot drift apart.
    from .lanes.router import account_key

    accounts = len({tok for r in i.rows if (tok := account_key(r))})
    return CheckResult(
        name="account_integrity",
        status=FAIL if errors else OK,
        detail=f"{rows} row(s), {accounts} account(s); lanes: {_lane_detail(groups)}",
        errors=errors,
        warnings=warnings,
        denominators=dict.fromkeys((f.partition(": ")[0] for f in warnings), accounts or rows),
    )


def _run_list_fit(i: _Inputs) -> CheckResult:
    if i.rows is None:
        return _no_list("list_fit", i)
    a = list_fit.audit_rows(i.rows, i.as_of, acked=i.acked, budget=i.budget)
    # list_fit's findings are advisory by construction: it flags a wrong seat, it never
    # disqualifies a real person. They are warnings here for the same reason.
    return CheckResult(
        name="list_fit",
        status=OK,
        detail=f"{a.rows} row(s), {a.signal_usable} usable signal(s)",
        warnings=list(a.findings),
        denominators=dict.fromkeys((f.partition(": ")[0] for f in a.findings), a.rows),
    )


def _run_merge_hygiene(i: _Inputs) -> CheckResult:
    if i.rows is None:
        return _no_list("merge_hygiene", i)
    errors: list[str] = []
    warnings: list[str] = []
    for r in i.rows:
        who = (r.get("email") or r.get("company") or "?").strip()
        for f in merge_hygiene.check_row(r):
            line = f"{f.rule}: {who} — {f.detail}"
            (errors if f.level == "block" else warnings).append(line)
    return CheckResult(
        name="merge_hygiene",
        status=FAIL if errors else OK,
        detail=f"{len(i.rows)} row(s)",
        errors=errors,
        warnings=warnings,
        denominators=dict.fromkeys((f.partition(": ")[0] for f in warnings), len(i.rows)),
    )


def _run_suppression(i: _Inputs) -> CheckResult:
    ledger_path = suppression_ledger(i.profile, i.content_root)
    if not ledger_path.is_file():
        return _skip("suppression", f"no suppression ledger at {ledger_path}")
    if i.rows is None:
        return _no_list("suppression", i)
    findings = suppression.verify(i.csv_path, suppression.load(ledger_path))
    # A build output disagreeing with the ledger means a suppressed person is sitting in
    # a load file. That blocks — it is the one finding class here that cannot be a warning.
    return CheckResult(
        name="suppression",
        status=FAIL if findings else OK,
        detail=f"ledger {ledger_path.name}, {len(i.rows)} row(s)",
        errors=[f"suppression-disagreement: {f}" for f in findings],
    )


def _run_hook_coverage(i: _Inputs) -> CheckResult:
    try:
        c = hook_coverage.audit_campaign(
            i.profile, content_root=i.content_root, profiles_root=i.profiles_root
        )
    except (FileNotFoundError, ValueError) as exc:
        # No matrix or no cells.toml is "not configured", not "broken".
        return _skip("hook_coverage", f"not measurable for this profile: {exc}")
    return CheckResult(
        name="hook_coverage",
        status=OK,
        detail=f"{c.unassignable_rows} unassignable row(s)",
        warnings=list(c.findings),
    )


def _run_email_compliance(i: _Inputs) -> CheckResult:
    """Only the offline third of the compliance gate.

    ``check_addresses`` and ``check_optout`` read raw ``list_email_accounts`` /
    ``get_sequence_settings`` payloads that only a live provider call produces. Running
    the market check and saying so is honest; claiming to run all three would put a
    connector-dependent check inside a gate whose whole warrant is that it needs nothing.
    """
    if i.rows is None:
        return _no_list("email_compliance", i)
    try:
        markets = email_compliance.read_target_markets(i.profile, i.profiles_root)
    except (FileNotFoundError, ValueError) as exc:
        return _skip("email_compliance", f"no bounded market list: {exc}")
    ledger_path = suppression_ledger(i.profile, i.content_root)
    suppressed: set[str] = set()
    if ledger_path.is_file():
        suppressed = {k.lower() for k in suppression.load(ledger_path)}
    r = email_compliance.check_markets(i.rows, markets, suppressed=suppressed)
    lines = [f"out-of-market: {d}" for d in r.detail]
    return CheckResult(
        name="email_compliance",
        status=FAIL if r.failed else OK,
        detail=f"markets={','.join(markets)} (market check only)",
        errors=lines if r.failed else [],
        warnings=[] if r.failed else lines,
        denominators=dict.fromkeys(("out-of-market",), len(i.rows)),
    )


def _run_groundedness(i: _Inputs) -> CheckResult:
    """Tiers 1–2 need a *rendered* body, which a load list does not carry.

    Tier 0 is ``signal_record.check_record``, which the roster already runs directly.
    Tiers 1 and 2 read rendered email text, and that only exists once copy is composed —
    where :mod:`agent.mcp.judge` runs this same cascade per row (W1a). Reporting the skip
    with that pointer is the honest state; silently omitting the entry would leave the
    roster looking like it covers something it does not.
    """
    return _skip(
        "groundedness",
        "tiers 1-2 need rendered bodies (run per-row in the judge path); "
        "tier 0 is covered by signal_record",
    )


def _run_signal_record(i: _Inputs) -> CheckResult:
    if i.rows is None:
        return _no_list("signal_record", i)
    groups = _lane_groups(i.rows)
    errors: list[str] = []
    warnings: list[str] = []
    checked = rows = 0
    missing: list[str] = []
    for lane, group in groups:
        a = signal_record.audit_records(
            group,
            i.fieldnames,
            as_of=i.as_of,
            lane=lane,
            sources_dir=i.content_root / "sources" if i.content_root else None,
            profile=i.profile,
        )
        errs, warns = list(a.errors), list(a.warnings)
        if lane == "generic":
            # The gate's own rule for this lane (`audit_rows(..., lane="generic")`), applied
            # by the same function so the daily unit and the gate cannot disagree.
            errs, demoted = account_integrity.demote_generic_advisory(errs)
            warns += demoted
        errors += errs
        warnings += warns
        checked += a.checked
        rows += a.rows
        missing = a.missing_columns  # a property of the header, identical for every group
    errors += [f"record-columns-missing: {c}" for c in missing]
    return CheckResult(
        name="signal_record",
        status=FAIL if errors else OK,
        detail=f"{checked}/{rows} row(s) checked; lanes: {_lane_detail(groups)}",
        errors=errors,
        warnings=warnings,
        denominators=dict.fromkeys((f.partition(": ")[0] for f in warnings), checked or rows),
    )


#: The declared roster. Order is report order — cheapest and most structural first, so a
#: header defect is read before the findings it invalidates.
ROSTER: tuple[Check, ...] = (
    Check("signal_record", "gtm_core.signal_record", _run_signal_record),
    Check("account_integrity", "gtm_core.account_integrity", _run_account_integrity),
    Check("merge_hygiene", "gtm_core.merge_hygiene", _run_merge_hygiene),
    Check("suppression", "gtm_core.suppression", _run_suppression),
    Check("list_fit", "gtm_core.list_fit", _run_list_fit),
    Check("email_compliance", "gtm_core.email_compliance", _run_email_compliance),
    Check("hook_coverage", "gtm_core.hook_coverage", _run_hook_coverage),
    Check("groundedness", "gtm_core.groundedness", _run_groundedness),
)


# --- the report ------------------------------------------------------------


@dataclass
class PreflightReport:
    profile: str
    ran_at: str
    csv_path: str
    rows: int
    checks: list[CheckResult] = field(default_factory=list)
    acked: tuple[str, ...] = ()
    budget: int = WARN_BUDGET
    #: Every send-list row's fate under the enrollment gate's rules (PS15,
    #: :mod:`gtm_core.prospect_readiness`). ``None`` when no list is staged; ``{"error": …}``
    #: when it could not be computed. Observation only — it does not change ``failed``.
    readiness: dict | None = None

    @property
    def errors(self) -> list[str]:
        return [e for c in self.checks for e in c.errors]

    @property
    def warnings(self) -> list[str]:
        return [w for c in self.checks for w in c.warnings]

    @property
    def verdict(self) -> BudgetVerdict:
        """One budget over the whole roster.

        Grading each check separately would let eight individually-legible walls add up
        to one illegible one — the union is what a human actually reads.
        """
        denominators: dict[str, int] = {}
        for c in self.checks:
            denominators.update(c.denominators)
        return budget_verdict(
            self.warnings,
            self.rows,
            denominators=denominators,
            acked=self.acked,
            budget=self.budget,
        )

    @property
    def failed(self) -> bool:
        return bool(self.errors) or self.verdict.blocked

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def counts(self) -> dict[str, int]:
        out = {OK: 0, FAIL: 0, SKIP: 0}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def to_dict(self) -> dict:
        v = self.verdict
        return {
            "profile": self.profile,
            "ran_at": self.ran_at,
            "csv": self.csv_path,
            "rows": self.rows,
            "verdict": "fail" if self.failed else "pass",
            "counts": self.counts(),
            "error_total": len(self.errors),
            # Classes, so the cockpit can answer "what failed last night" without
            # reading 2000 lines. The full list stays for anything that wants it.
            "error_classes": [
                {"rule": rule, "count": len(details)}
                for rule, details in sorted(
                    group_by_rule(self.errors).items(), key=lambda kv: (-len(kv[1]), kv[0])
                )
            ],
            "errors": self.errors,
            "warn_total": v.total,
            "warn_unacked": v.unacked,
            "warn_blocked": v.blocked,
            "warn_classes": [
                {"rule": c.rule, "count": c.count, "denominator": c.denominator, "acked": c.acked}
                for c in v.classes
            ],
            "acked": list(self.acked),
            "checks": [c.to_dict() for c in self.checks],
            "readiness": self.readiness,
        }


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def _read_rows(path: Path) -> tuple[list[dict] | None, list[str]]:
    if not path.is_file():
        return None, []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        names = list(reader.fieldnames or [])
        return list(reader), names


def run_preflight(
    profile: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    acked: tuple[str, ...] = (),
    budget: int = WARN_BUDGET,
    as_of: datetime.date | None = None,
) -> PreflightReport:
    """Run the whole deterministic roster for one profile. Never raises on a check."""
    croot = content_root if content_root is not None else resolve_content_root()
    proot = profiles_root if profiles_root is not None else resolve_profiles_root()
    csv_path = ready_to_load(profile, croot)
    rows, fieldnames = _read_rows(csv_path)
    inputs = _Inputs(
        profile=profile,
        content_root=croot,
        profiles_root=proot,
        as_of=as_of or datetime.date.today(),
        csv_path=csv_path,
        rows=rows,
        fieldnames=fieldnames,
        acked=acked,
        budget=budget,
    )
    results: list[CheckResult] = []
    for check in ROSTER:
        try:
            result = check.run(inputs)
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
            result = CheckResult(
                name=check.name,
                status=FAIL,
                detail=f"check raised {type(exc).__name__}",
                errors=[f"preflight-check-crashed: {check.name} — {exc}"],
            )
        if check.name == "signal_record":
            # Hand the record tier forward so `account_integrity`, which computes it
            # again internally, does not report it a second time. ROSTER order is what
            # makes this work, and `test_signal_record_precedes_account_integrity`
            # is what keeps that order from being reshuffled by accident.
            inputs.record_seen = Counter(result.errors) + Counter(result.warnings)
        results.append(result)
    return PreflightReport(
        profile=profile,
        ran_at=_utc_now_iso(),
        csv_path=str(csv_path),
        rows=len(rows or []),
        checks=results,
        acked=acked,
        budget=budget,
        readiness=_readiness(inputs),
    )


def _readiness(i: _Inputs) -> dict | None:
    """The send list's fates under the gate's rules (PS15), or the recorded reason they could
    not be found. ``None`` only when no list is staged."""
    if i.rows is None:
        return None
    return readiness_or_error(
        i.profile,
        i.rows,
        i.fieldnames,
        _lane_groups(i.rows),
        content_root=i.content_root,
        profiles_root=i.profiles_root,
        acked=i.acked,
        budget=i.budget,
        as_of=i.as_of,
    )


#: Errors are never acknowledged away, but past this many they stop being readable and
#: start being scenery. Measured, not guessed: the first real run of this module against
#: one live profile emitted **2283** of them — the WARN tier's 388-warning collapse
#: (:mod:`gtm_core.finding_budget`) reproduced one tier up, by a gate written to prevent
#: exactly that. Blocking is unaffected; only the printing changes.
ERROR_ENUMERATION_LIMIT = 20


def render_errors(errors: list[str], *, limit: int = ERROR_ENUMERATION_LIMIT) -> str:
    """Render the ERROR tier: enumerate while short, class rates plus exemplars past it.

    Deliberately *not* :func:`~gtm_core.finding_budget.budget_verdict`. That grades a
    tier against a budget and lets ``--ack`` buy headroom, which is right for WARN and
    wrong here — an ERROR blocks at any count and cannot be accepted for a run. What
    carries over is only the readability rule, and the rule is about the reader.
    """
    if not errors:
        return ""
    if len(errors) <= limit:
        return "\n".join(
            [f"  ERRORS — {len(errors)}, blocking at any count:", ""] + [f"    {e}" for e in errors]
        )
    grouped = group_by_rule(errors)
    lines = [
        f"  ERRORS — {len(errors)} across {len(grouped)} class(es), blocking at any count.",
        f"  Enumeration suppressed past {limit}; rates and exemplars only. These cannot "
        f"be acknowledged — fix the class or the rows do not send.",
        "",
    ]
    for rule, details in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"    {rule}: {len(details)}")
        for ex in details[:EXEMPLARS]:
            lines.append(f"        e.g. {ex}")
        if len(details) > EXEMPLARS:
            lines.append(f"        ... +{len(details) - EXEMPLARS} more in this class")
    return "\n".join(lines)


def preflight_refusal(report: PreflightReport) -> Refusal | None:
    counts = report.counts()
    is_blocked = False
    if report.verdict and getattr(report.verdict, "budget", None) is not None:
        try:
            is_blocked = bool(report.verdict.blocked)
        except (TypeError, ValueError):
            is_blocked = False
    if not (report.errors or counts[FAIL] > 0 or is_blocked):
        return None
    err_count = len(report.errors) if report.errors else counts[FAIL]
    return Refusal(
        what="I stopped before sending",
        why=f"{err_count} check(s) need attention",
        next_step="review the findings below to fix them",
        alternative=None,
        cost="Nothing was spent.",
        technical=f"{counts[FAIL]} failing checks, {len(report.errors)} errors across {report.rows} staged row(s)",
    )


def render(report: PreflightReport) -> str:
    counts = report.counts()
    summary: list[str] = []
    refusal = preflight_refusal(report)
    if refusal:
        summary = [refusal.render(), ""]
    lines = [
        *summary,
        f"preflight — {report.profile} — {report.ran_at}",
        f"  {counts[OK]} pass, {counts[FAIL]} fail, {counts[SKIP]} skip "
        f"over {report.rows} staged row(s)",
        "",
    ]
    for c in report.checks:
        mark = {OK: "ok  ", FAIL: "FAIL", SKIP: "skip"}[c.status]
        lines.append(f"  [{mark}] {c.name}: {c.detail}")
    errs = render_errors(report.errors)
    if errs:
        lines += ["", errs]
    warn = render_budget(report.verdict, unit="row")
    if warn:
        lines += ["", warn]
    if not report.errors and not warn:
        lines += ["", "  no findings."]
    return "\n".join(lines)


def write_report(report: PreflightReport, *, content_root: Path | None = None) -> Path:
    """Write the report under the resolved content root, and nowhere else.

    Two files: a dated one for the record, and ``latest.json`` so the cockpit brain can
    answer *"what failed last night"* from state rather than from the operator narrating
    it. Plain writes — this module never touches the cost ledger, by construction.
    """
    croot = content_root if content_root is not None else resolve_content_root()
    # One spelling of the path, shared with the reader (`prospect_readiness.load_readiness`).
    latest = report_path(report.profile, croot)
    out_dir = latest.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2) + "\n"
    day = report.ran_at[:10]
    dated = out_dir / f"report-{day}.json"
    dated.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return dated


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m gtm_core.preflight_report",
        description="Run every deterministic content gate as a precondition. Costs nothing.",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--content-root", default=None, type=Path)
    p.add_argument("--profiles-root", default=None, type=Path)
    p.add_argument(
        "--ack",
        action="append",
        default=[],
        metavar="RULE",
        help="accept one warning class for this run (repeatable, never a blanket pass)",
    )
    p.add_argument(
        "--budget",
        type=int,
        default=WARN_BUDGET,
        help=f"unacknowledged warnings enumerable before the gate blocks (default {WARN_BUDGET})",
    )
    p.add_argument(
        "--as-of",
        type=datetime.date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="pin today's date for freshness checks (tests, replays)",
    )
    p.add_argument("--json", action="store_true", help="emit the report as JSON on stdout")
    p.add_argument("--no-write", action="store_true", help="do not write the report to disk")
    p.add_argument(
        "--warn-only", action="store_true", help="always exit 0 (observation, not a gate)"
    )
    args = p.parse_args(argv)

    report = run_preflight(
        args.profile,
        content_root=args.content_root,
        profiles_root=args.profiles_root,
        acked=tuple(args.ack),
        budget=args.budget,
        as_of=args.as_of,
    )
    if not args.no_write:
        path = write_report(report, content_root=args.content_root)
        if not args.json:
            print(f"report: {path}", file=sys.stderr)
    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(report))
    return 0 if args.warn_only else report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
