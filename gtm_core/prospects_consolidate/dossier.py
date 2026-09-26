from __future__ import annotations

import csv
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..account_folder import AmbiguousFolder, resolve
from ..merge_hygiene import clean_company, clean_segment, clean_title
from ..slugify import slug
from .columns import MASTER_COLS
from .confidence import org_token
from .paths import _accounts_dir, _pool_dir
from .suppression import MarketGate, _resolve_market_gate


def _read_master_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        rec = {c: (r.get(c) or "") for c in MASTER_COLS}
        rec["company"] = clean_company(rec["company"])
        rec["title"] = clean_title(rec["title"])
        rec["segment"] = clean_segment(rec["segment"])
        out.append(rec)
    return out


# Per-variant glob patterns — public, and the ONE place each dossier variant's file
# naming is defined. gtm_core.account_integrity.dossier_depth() imports these three
# directly rather than keeping its own copy: a second hand-maintained list is exactly
# how the "research pack" variant below went unrecognized for a day after it shipped.
DOSSIER_GLOB_FULL = ("account-dossier-*",)
DOSSIER_GLOB_ONEPAGER = ("*-onepager-*",)
DOSSIER_GLOB_BRIEF = (
    "prospecting-brief-*",
    # the "research pack, no visuals" variant (see account-dossier/SKILL.md §"Research
    # pack variant") — a markdown-only dossier used for bulk coverage where a
    # docx+PDF+images pass per account isn't affordable. Named `dossier-<slug>-<date>.md`
    # to stay visually distinct from the docx `account-dossier-*` full variant. Added
    # 2026-08-13 after this pattern list caused a false `no-dossier` reading on all 439
    # accounts dossiered this way on 2026-08-12 — the files existed, this glob just
    # never looked for them.
    "dossier-*.md",
)
_DOSSIER_GLOB_PATTERNS = DOSSIER_GLOB_FULL + DOSSIER_GLOB_ONEPAGER + DOSSIER_GLOB_BRIEF

#: Closed set of named exclusion reasons for the research queue (W5 / R5.1).
EXCLUSION_REASONS: tuple[str, ...] = (
    "no-contact",
    "no-seat",
    "no-country",
    "out-of-market",
    "suppressed",
    "excluded-lane",
    "dropped",
)

_PSEUDO_VALUES: frozenset[str] = frozenset(
    {"", "none", "null", "n/a", "undefined", "unknown", "unverified"}
)


class ResearchQueue(list):
    """List of candidate accounts needing a dossier, with eligibility metadata."""

    def __init__(
        self,
        accounts: Iterable[dict],
        counts: dict[str, int] | None = None,
        total_input: int = 0,
    ) -> None:
        super().__init__(accounts)
        self.counts: dict[str, int] = counts or dict.fromkeys(EXCLUSION_REASONS, 0)
        self.total_input: int = total_input

    @property
    def included(self) -> int:
        return len(self)

    @property
    def excluded(self) -> int:
        return sum(self.counts.values())

    def report(self) -> dict[str, Any]:
        return {
            "total_input": self.total_input,
            "included": self.included,
            "excluded": self.excluded,
            "counts": dict(self.counts),
        }


def _folder_has_dossier(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(any(folder.glob(pat)) for pat in _DOSSIER_GLOB_PATTERNS)


def dossier_folder(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> tuple[bool, str, str]:
    """``(has_dossier, folder_name, reason)`` for this account, answered from the ONE
    folder :func:`gtm_core.account_folder.resolve` says is this account's — the same
    resolver every skill writes a dossier through. ``folder_name`` is ``""`` when no
    dossier was found; ``reason`` is ``"folder-ambiguous: <candidate>, <candidate>"``
    when more than one existing folder could be the account, else ``""``.

    Until 2026-09-25 this ran its own fuzzy match over every folder name, so the gate
    READ a different folder than the skills WROTE (AF1). Its worst shape: the account's
    own folder exists and is empty, while a parent sharing its domain has a dossier —
    and the gate passed on the parent's research. Hence, deliberately:

    * resolved existing folder → has a dossier iff that folder holds one;
    * no existing folder (``resolve`` would mint a new one) → no dossier. A shorter
      legacy folder that merely begins the name is not borrowed;
    * ambiguous → no dossier (fail closed): picking one candidate is the same guess
      ``resolve`` refuses to make.
    """
    if not slug(company):
        # ``resolve`` raises ValueError on an empty name; checked here rather than caught
        # there, so a ValueError from the profile-segment guard or a malformed ledger
        # still propagates instead of reading as "no dossier".
        return False, "", ""
    try:
        folder, _rung = resolve(company, profile, company_domain, content_root)
    except AmbiguousFolder as e:
        return False, "", f"folder-ambiguous: {', '.join(e.candidates)}"
    if _folder_has_dossier(_accounts_dir(profile, content_root) / folder):
        return True, folder, ""
    return False, "", ""


def account_has_dossier(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> tuple[bool, str]:
    """True if this account's own folder — the one :func:`gtm_core.account_folder.resolve`
    returns — holds a dossier of any kind. See :func:`dossier_folder`, which also says
    why a lookup came back empty (``folder-ambiguous``).

    Public — the dossier-existence primitive :mod:`gtm_core.account_integrity` reuses for
    its ``no-dossier`` gate. The folder question has one owner, ``account_folder``;
    widening what counts as this account's folder belongs there, never here — a second
    matcher is how the gate came to pass on another company's research.

    Returns ``(has_dossier, matched_folder_name)`` — ``matched_folder_name`` is ``""``
    when no dossier was found.
    """
    has, folder, _reason = dossier_folder(profile, company, company_domain, content_root)
    return has, folder


def _has_usable_contact(row: dict) -> bool:
    email = str(row.get("contact_email") or row.get("email") or "").strip().lower()
    if not email or email in _PSEUDO_VALUES or "@" not in email:
        return False
    if str(row.get("contact_status") or "").strip().lower() == "no-contact":
        return False
    if str(row.get("status") or "").strip().lower() in ("no-contact", "contact-defective"):
        return False
    if str(row.get("conf_tier") or "").strip().lower() == "blocked":
        return False
    return True


def _has_usable_seat(row: dict, profile: str | None = None) -> bool:
    seat = str(row.get("seat") or "").strip().lower()
    if seat and seat not in ("none", "null", "unresolved", "unknown", "no-seat"):
        return True
    title = str(row.get("title") or "").strip()
    if not title:
        return False
    try:
        from ..cells import seat_of

        resolved = seat_of(title, profile=profile)
        if resolved and str(resolved).lower() not in ("none", "unresolved", "unknown", "no-seat"):
            return True
    except Exception:
        return False
    return False


def _has_usable_country(row: dict) -> bool:
    country = str(row.get("country") or "").strip()
    return bool(country)


def _is_out_of_market(row: dict, market_gate: MarketGate | None = None) -> bool:
    if row.get("out_of_market") is True:
        return True
    if str(row.get("out_of_market") or "").strip().lower() in ("true", "1", "yes"):
        return True
    if str(row.get("market") or "").strip().lower() in (
        "outside",
        "out-of-market",
        "outside-market",
    ):
        return True
    if str(row.get("reason") or "").strip().lower() == "out-of-market":
        return True
    country = str(row.get("country") or "").strip()
    if market_gate and country and market_gate.blocks(country):
        return True
    return False


def _is_suppressed(row: dict, dnc: Any = None) -> bool:
    supp = str(row.get("suppression") or "").strip()
    if supp:
        return True
    if row.get("suppressed") is True or str(row.get("suppressed") or "").strip().lower() in (
        "true",
        "1",
        "yes",
    ):
        return True
    status = str(row.get("status") or "").strip().lower()
    if status in ("do-not-contact", "suppressed"):
        return True
    if dnc:
        email = str(row.get("email") or "").strip()
        domain = str(row.get("company_domain") or "").strip()
        if dnc.blocks(email, domain):
            return True
    return False


def _is_excluded_lane(row: dict) -> bool:
    lane = str(row.get("lane") or "").strip().lower()
    if lane in ("excluded", "excluded-lane"):
        return True
    reason = str(row.get("lane_reason") or "").strip().lower()
    if reason in ("excluded", "excluded-lane"):
        return True
    return False


def _is_dropped(row: dict, dropped_accounts: set[str] | None = None) -> bool:
    verdict = str(row.get("verdict") or "").strip().lower()
    if verdict == "drop":
        return True
    status = str(row.get("status") or "").strip().lower()
    if status == "drop":
        return True
    if dropped_accounts:
        acct_id = str(row.get("account_id") or "").strip()
        slug_name = slug(str(row.get("company") or ""))
        if (acct_id and acct_id in dropped_accounts) or (
            slug_name and slug_name in dropped_accounts
        ):
            return True
    return False


def _check_row_eligibility(
    row: dict,
    profile: str | None = None,
    market_gate: MarketGate | None = None,
    dnc: Any = None,
    dropped_accounts: set[str] | None = None,
) -> tuple[bool, str | None]:
    if not _has_usable_contact(row):
        return False, "no-contact"
    if not _has_usable_seat(row, profile):
        return False, "no-seat"
    if not _has_usable_country(row):
        return False, "no-country"
    if _is_out_of_market(row, market_gate):
        return False, "out-of-market"
    if _is_suppressed(row, dnc):
        return False, "suppressed"
    if _is_excluded_lane(row):
        return False, "excluded-lane"
    if _is_dropped(row, dropped_accounts):
        return False, "dropped"
    return True, None


def _heat_val(v: Any) -> float:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return 0.0


def _score_val(v: Any) -> float:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return 0.0


def _sort_key(item: dict) -> tuple[float, float, str]:
    heat = _heat_val(item.get("heat"))
    score = _score_val(item.get("score") or item.get("top_intent_score"))
    acct_id = str(item.get("account_id") or item.get("canonical_slug") or item.get("company") or "")
    return (-heat, -score, acct_id)


def _load_dropped_accounts(profile: str, content_root: Path | None) -> set[str]:
    dropped: set[str] = set()
    try:
        from .accounts import _ledger_items

        for item in _ledger_items(profile, content_root):
            if str(item.get("verdict") or "").strip().lower() == "drop" or str(
                item.get("status") or ""
            ).strip().lower() in ("drop", "disqualified"):
                if item.get("account_id"):
                    dropped.add(str(item["account_id"]).strip())
                if item.get("company"):
                    dropped.add(slug(str(item["company"])))
    except Exception:
        return dropped
    return dropped


def _build_account_item(rows: list[dict], wave_val: str) -> dict:
    rep = rows[0]
    max_heat = max((_heat_val(r.get("heat")) for r in rows), default=0.0)
    max_score = max(
        (_score_val(r.get("score") or r.get("top_intent_score")) for r in rows),
        default=0.0,
    )
    acct_id = next(
        (str(r.get("account_id")).strip() for r in rows if str(r.get("account_id") or "").strip()),
        "",
    )
    heat_val = next(
        (str(r.get("heat")).strip() for r in rows if str(r.get("heat") or "").strip()),
        str(int(max_heat)) if max_heat > 0 else "",
    )
    score_val = next(
        (str(r.get("score")).strip() for r in rows if str(r.get("score") or "").strip()),
        str(int(max_score)) if max_score > 0 else "",
    )
    return {
        "company": rep["company"],
        "company_domain": rep.get("company_domain", ""),
        "canonical_slug": slug(rep["company"]),
        "why_now": rep.get("why_now", ""),
        "cohort": rep.get("cohort", ""),
        "top_intent_score": rep.get("top_intent_score", ""),
        "account_id": acct_id,
        "heat": heat_val,
        "score": score_val,
        "tier": rep.get("tier", ""),
        "researched_for_wave": wave_val or rep.get("researched_for_wave", ""),
    }


def _eval_candidate_eligibility(
    rows: list[dict],
    profile: str,
    market_gate: MarketGate | None,
    dropped_accounts: set[str],
) -> tuple[bool, str]:
    row_evals = [
        _check_row_eligibility(r, profile, market_gate, None, dropped_accounts) for r in rows
    ]
    if any(can_send for can_send, _ in row_evals):
        return True, ""
    reasons = [reason for _, reason in row_evals if reason is not None]
    for exp_reason in EXCLUSION_REASONS:
        if exp_reason in reasons:
            return False, exp_reason
    return False, "unknown"


def accounts_needing_dossier(
    profile: str,
    content_root: Path | None = None,
    *,
    tier: str | None = "A",
    eligible_only: bool = False,
    limit: int | None = None,
    wave: str | None = None,
    target_markets: list[str] | None = None,
    with_counts: bool = False,
) -> ResearchQueue | tuple[ResearchQueue, dict[str, int]]:
    """Accounts in ``master-list.csv`` with no dossier yet — the candidate list the
    ``prospect`` skill's dossier sweep loops over to auto-generate a prospecting brief +
    outreach draft for each. One entry per account (deduped by the same ``org_token``
    identity used throughout this module), not per person.

    ``tier="A"`` (default) preserves the original Tier-A-only sweep. Pass ``tier=None``
    to check every account regardless of tier — the wider check the sweep now runs
    before a bulk sequence load, since a Tier-B/bulk-sourced account reaching a merge
    sequence with zero research behind its Why Now clause is exactly the gap that let
    439 of 496 contacts ship with no dossier on 2026-08-12.

    When ``eligible_only=True`` (W5 / R5.1), accounts with no row that could send are
    excluded with a named reason from :data:`EXCLUSION_REASONS`. ``needs-research``
    accounts stay in the queue.

    When ``limit`` is specified (W5 / R5.2), candidates are ordered by heat descending,
    then score descending, then account id ascending, and capped at ``limit``.

    When ``wave`` is specified (W5 / R5.3), records carry ``researched_for_wave``.

    Idempotent by construction: re-running this after the sweep generates a dossier
    for an account removes that account from the list on its own — no separate state
    file needed, the filesystem is the source of truth (see ``account_has_dossier``).
    """
    master_path = _pool_dir(profile, content_root) / "master-list.csv"
    if not master_path.exists():
        empty_queue = ResearchQueue([])
        return (empty_queue, empty_queue.counts) if with_counts else empty_queue

    master = _read_master_rows(master_path)
    rows_by_token: dict[str, list[dict]] = {}
    for r in master:
        if tier is not None and (r.get("tier") or "").strip().upper() != tier.upper():
            continue
        token = org_token(r.get("company_domain", ""), r.get("company", ""))
        if not token:
            continue
        rows_by_token.setdefault(token, []).append(r)

    candidate_accounts: list[tuple[str, list[dict]]] = []
    for token, rows in rows_by_token.items():
        rep = rows[0]
        has_dossier, _ = account_has_dossier(
            profile, rep["company"], rep.get("company_domain", ""), content_root
        )
        if not has_dossier:
            candidate_accounts.append((token, rows))

    total_input = len(candidate_accounts)
    counts = dict.fromkeys(EXCLUSION_REASONS, 0)
    eligible_items: list[dict] = []

    market_gate = (
        MarketGate(target_markets)
        if target_markets is not None
        else _resolve_market_gate(profile, strict=False)
    )
    dropped_accounts = _load_dropped_accounts(profile, content_root)
    wave_val = wave or os.environ.get("GTM_WAVE") or ""

    for _token, rows in candidate_accounts:
        item = _build_account_item(rows, wave_val)
        if not eligible_only:
            eligible_items.append(item)
            continue

        ok, reason = _eval_candidate_eligibility(rows, profile, market_gate, dropped_accounts)
        if ok:
            eligible_items.append(item)
        else:
            counts[reason] = counts.get(reason, 0) + 1

    if eligible_only or limit is not None:
        eligible_items.sort(key=_sort_key)
    if limit is not None and limit > 0:
        eligible_items = eligible_items[:limit]

    queue = ResearchQueue(eligible_items, counts=counts, total_input=total_input)
    if with_counts:
        return queue, queue.counts
    return queue


def tier_a_needing_dossier(profile: str, content_root: Path | None = None) -> list[dict]:
    """Back-compat wrapper — Tier-A only. See :func:`accounts_needing_dossier`."""
    return accounts_needing_dossier(profile, content_root, tier="A", eligible_only=False)
