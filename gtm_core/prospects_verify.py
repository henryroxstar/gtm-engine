"""``prospects verify`` — read-only reconciliation across the prospect data stores.

PRD-2026-09-28 (Prospect Data Verification & Integrity Hardening), Phases 4a/4b. Makes
staleness and corruption in prospect data visible at a seam an operator or a daily cron can
check, rather than discoverable weeks later by a human noticing a wrong number.

Six checks, none of which writes anything under ``content/``:

1. **send-list drift** (Gap 1) — an account whose ledger verdict is ``send`` but has no
   matching row in ``ready-to-load.csv``. Joins on ``account_id``, the same key
   :mod:`gtm_core.prospects_consolidate` already uses. A ledger account with more than one
   row (the known duplicate-account shape) is reported once, as
   ``duplicate-account``, never as independent drift findings per row.
2. **vocabulary integrity** — a defense-in-depth re-read of ``latest.json``: does any of the
   four closed-vocabulary fields still hold a value outside its allowed set, despite
   write-time enforcement (Phase 2)? Catches a value written before Phase 2 shipped, or
   through a writer this PRD hasn't found yet.
3. **duplicate stores** — both of the two distinct ``outcomes.jsonl`` files
   (``prospects/outcomes.jsonl`` and the profile-root one) existing at once.
4. **ledger suppression** — :func:`gtm_core.ledger_integrity.check_verdict_leak` (a pure
   read), plus a read-only check for a ``drop``-verdict account with an email/domain that
   has no matching row in the suppression ledger (the *absence* of what
   :func:`gtm_core.ledger_integrity.enforce_drop_suppression` would add — this module never
   calls that writer itself; it stays read-only by construction).
5. **run-state staleness** — a stage left ``running`` with no ``completed_at`` (Gap 4).
6. **profile-guard** — a ``content/<profile>/`` directory with no matching
   ``profiles/<profile>/PROFILE.md`` (the 2026-09-25 incident shape).

Exit codes: ``0`` clean, ``1`` a send-blocking finding exists (vocabulary integrity, ledger
suppression), ``2`` usage/refusal. A non-blocking finding does not raise the exit code past 0
unless ``--strict`` is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .finding_budget import WARN_BUDGET, budget_verdict, render_budget
from .ledger_integrity import check_verdict_leak
from .paths import resolve_content_root, resolve_profiles_root
from .prospect_paths import latest_json, outcomes_jsonl, run_state_json, suppression_ledger
from .prospects_consolidate.paths import ready_to_load_path
from .prospects_item import _VOCABULARIES
from .prospects_state import _identity_keys
from .run_state import load_run_state
from .suppression import email_key
from .suppression import load_index as load_suppression_index

CHECKS = (
    "send-list drift",
    "vocabulary integrity",
    "duplicate stores",
    "ledger suppression",
    "run-state staleness",
    "profile-guard",
)

#: Which check classes are send-blocking — an unrepaired garbage value or a suppression gap
#: can put a person back in an active send; drift/duplicate-store/staleness/profile-guard are
#: real but do not, by themselves, make a send unsafe.
_BLOCKING_CHECKS = frozenset({"vocabulary integrity", "ledger suppression"})


@dataclass
class VerifyReport:
    profile: str
    findings: dict[str, list[str]] = field(default_factory=dict)  # check -> ["rule: detail", ...]
    unreadable: list[str] = field(default_factory=list)

    def add(self, check: str, finding: str) -> None:
        self.findings.setdefault(check, []).append(finding)

    @property
    def all_findings(self) -> list[str]:
        return [f for findings in self.findings.values() for f in findings]

    @property
    def blocking(self) -> list[str]:
        return [f for check in _BLOCKING_CHECKS for f in self.findings.get(check, [])]

    @property
    def failed(self) -> bool:
        return bool(self.blocking) or bool(self.unreadable)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    """(value, unreadable-reason). Missing is (None, None) — not the same as corrupt."""
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return None, f"unreadable: {path} ({exc})"


def _load_latest_items(profile: str, content_root: Path | None, report: VerifyReport) -> list[dict]:
    data, unreadable = _read_json(latest_json(profile, content_root))
    if unreadable:
        report.unreadable.append(unreadable)
        return []
    if data is None:
        return []
    if isinstance(data, dict):
        return data.get("items", [])
    return data if isinstance(data, list) else []


def _ready_keys(rows: list[dict]) -> set[str]:
    """Identity keys for ready-to-load.csv rows, in the same ``d:``/``c:``/``a:`` scheme
    :func:`gtm_core.prospects_state._identity_keys` uses for latest.json items — a CSV row
    spells its domain column ``company_domain``, not ``domain``, so it needs its own builder
    rather than reusing that function directly."""
    keys: set[str] = set()
    for r in rows:
        domain = str(r.get("company_domain") or r.get("domain") or "").strip().lower()
        if domain:
            keys.add(f"d:{domain}")
        account_id = str(r.get("account_id") or "").strip().lower()
        if account_id:
            keys.add(f"a:{account_id}")
        company = " ".join(str(r.get("company") or "").split()).lower()
        if company:
            keys.add(f"c:{company}")
    return keys


def check_send_list_drift(
    profile: str, content_root: Path | None, items: list[dict], report: VerifyReport
) -> None:
    ready = _load_master_rows(ready_to_load_path(profile, content_root))
    ready_keys = _ready_keys(ready)

    # Group by ANY shared identity key (domain/id/company/account_id), not account_id alone —
    # a real duplicate pair is not guaranteed to both carry a stamped account_id (the known
    # two-row-account shape predates or bypasses that stamping), and grouping on account_id
    # alone made such a pair invisible to this check entirely.
    by_key: dict[str, list[int]] = {}
    for pos, item in enumerate(items):
        for key in _identity_keys(item):
            by_key.setdefault(key, []).append(pos)

    reported: set[int] = set()
    seen_groups: set[frozenset[int]] = set()
    for pos, item in enumerate(items):
        positions: set[int] = set()
        for key in _identity_keys(item):
            positions.update(by_key.get(key, []))
        group = frozenset(positions)
        if len(group) > 1:
            if group in seen_groups:
                continue
            seen_groups.add(group)
            rows = [items[p] for p in sorted(group)]
            companies = ", ".join(str(r.get("company")) for r in rows)
            report.add(
                "send-list drift",
                f"duplicate-account: {len(rows)} ledger rows share an identity key "
                f"({companies}) — reconcile before trusting any of their verdicts",
            )
            reported.update(group)
            continue
        if pos in reported:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict != "send":
            continue
        if not (set(_identity_keys(item)) & ready_keys):
            label = item.get("account_id") or item.get("domain") or item.get("company")
            report.add(
                "send-list drift",
                f"send-list-drift: {item.get('company')!r} ({label}) has verdict=send in "
                f"latest.json with no matching row in ready-to-load.csv — re-run consolidate",
            )


def _load_master_rows(path: Path) -> list[dict]:
    from .prospects_consolidate.io import _load_master

    return _load_master(path)


def check_vocabulary_integrity(items: list[dict], report: VerifyReport) -> None:
    for item in items:
        for field_name, allowed in _VOCABULARIES.items():
            value = str(item.get(field_name) or "").strip().lower()
            if value and value not in allowed:
                report.add(
                    "vocabulary integrity",
                    f'refused: {field_name}="{item.get(field_name)}" on '
                    f"{item.get('company')!r} (account_id={item.get('account_id')}) is not "
                    f"in the allowed set for {field_name} ({', '.join(sorted(allowed))})",
                )


def check_duplicate_stores(profile: str, content_root: Path | None, report: VerifyReport) -> None:
    from .outcomes import outcomes_path as profile_outcomes_path

    prospects_outcomes = outcomes_jsonl(profile, content_root)
    root = content_root or resolve_content_root()
    profile_outcomes = profile_outcomes_path(root, profile)
    if prospects_outcomes.exists() and profile_outcomes.exists():
        report.add(
            "duplicate stores",
            f"duplicate-outcomes-store: both {prospects_outcomes} and {profile_outcomes} "
            f"exist — pick one and retire the other, a reader choosing between them silently "
            f"is the failure mode this check exists to catch",
        )


def check_ledger_suppression(
    profile: str, content_root: Path | None, items: list[dict], report: VerifyReport
) -> None:
    for finding in check_verdict_leak(items):
        report.add(
            "ledger suppression",
            f"verdict-leak: {finding['company']!r} why_now matched {finding['matched_pattern']!r}",
        )

    index = load_suppression_index(suppression_ledger(profile, content_root))
    for item in items:
        if str(item.get("verdict") or "").strip().lower() != "drop":
            continue
        email = str(item.get("email") or item.get("contact_email") or "").strip().lower()
        if not email or email_key(email) in index.by_email:
            continue
        report.add(
            "ledger suppression",
            f"unsuppressed-drop: {item.get('company')!r} <{email}> has verdict=drop but no "
            f"matching suppression-ledger row — a dropped account must be durably suppressed",
        )


def check_run_state_staleness(
    profile: str, content_root: Path | None, report: VerifyReport
) -> None:
    path = run_state_json(profile, content_root)
    if not path.exists():
        return
    state = load_run_state(path)
    if state is None:
        report.unreadable.append(f"unreadable: {path}")
        return
    for name, stage in state.stages.items():
        if stage.status == "running" and stage.completed_at is None:
            report.add(
                "run-state staleness",
                f"stale-stage: {name!r} has been running since {stage.started_at} with no "
                f"completed_at — the run that started it may have died mid-stage",
            )


def check_profile_guard(
    profile: str, content_root: Path | None, profiles_root: Path | None, report: VerifyReport
) -> None:
    c_root = content_root or resolve_content_root()
    p_root = profiles_root or resolve_profiles_root()
    if (c_root / profile).is_dir() and not (p_root / profile / "PROFILE.md").exists():
        report.add(
            "profile-guard",
            f"does-not-exist: content/{profile}/ has data but profiles/{profile}/PROFILE.md "
            f"is missing — this profile may be a typo or a retired tenant still being written to",
        )


def verify(
    profile: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> VerifyReport:
    report = VerifyReport(profile=profile)
    items = _load_latest_items(profile, content_root, report)
    check_send_list_drift(profile, content_root, items, report)
    check_vocabulary_integrity(items, report)
    check_duplicate_stores(profile, content_root, report)
    check_ledger_suppression(profile, content_root, items, report)
    check_run_state_staleness(profile, content_root, report)
    check_profile_guard(profile, content_root, profiles_root, report)
    return report


def exit_code_for(report: VerifyReport, *, strict: bool = False) -> int:
    """The one place exit-code semantics live — render() and main() both call this, so the
    printed 'Exit N.' line can never disagree with the process's real exit code (a
    verification-audit finding, round 2: --strict used to compute its own answer separately
    from what render() printed, and the two disagreed)."""
    if report.failed:
        return 1
    if strict and (report.all_findings or report.unreadable):
        return 1
    return 0


def render(
    report: VerifyReport,
    *,
    budget: int = WARN_BUDGET,
    acked: tuple[str, ...] = (),
    strict: bool = False,
) -> str:
    all_findings = report.all_findings + report.unreadable
    exit_code = exit_code_for(report, strict=strict)
    if not all_findings:
        return (
            f"OK — 0 findings across {len(CHECKS)} checks ({', '.join(CHECKS)}). Exit {exit_code}."
        )
    verdict = budget_verdict(all_findings, len(all_findings), acked=acked, budget=budget)
    return render_budget(verdict, unit="finding") + f" Exit {exit_code}."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gtm_core.prospects_verify",
        description="Read-only reconciliation across prospect data stores.",
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", type=Path, default=None)
    ap.add_argument("--profiles-root", type=Path, default=None)
    ap.add_argument("--warn-only", action="store_true", help="always exit 0")
    ap.add_argument("--ack", action="append", default=[], help="acknowledge a finding class")
    ap.add_argument("--budget", type=int, default=WARN_BUDGET)
    ap.add_argument("--strict", action="store_true", help="also fail on a non-blocking finding")
    args = ap.parse_args(argv)

    try:
        report = verify(
            args.profile, content_root=args.content_root, profiles_root=args.profiles_root
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(render(report, budget=args.budget, acked=tuple(args.ack), strict=args.strict))

    if args.warn_only:
        return 0
    return exit_code_for(report, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
