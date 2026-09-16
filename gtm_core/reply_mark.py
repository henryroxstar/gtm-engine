"""Reply marking: join replier emails to accounts, update status, and audit.

Addresses PRD PS6 and review finding PS-R C2:
  - Resolves replier emails through multiple data sources:
      1. Contact emails on existing items in ``latest.json``;
      2. CSV rows in ``sequences/ready-to-load.csv``;
      3. CSV rows in ``sequences/.pool/master-list.csv``;
      4. Registered enrolled CSVs in ``sequences/cells.toml``.
  - Resolves identity keys trying ``a:<account_id>`` first (most authoritative)
    before falling back to domain, id, or company derivations.
  - Never overwrites retired statuses (:data:`gtm_core.prospects_state.RETIRED_STATUSES`).
  - Writes unmatched and failed marks to ``history.jsonl`` for auditability.
  - Idempotent: can be re-run safely on every sweep / outcome sync.
"""

from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from . import prospects_state as ps
from .cells import load_cell_map
from .paths import resolve_content_root
from .prospects_consolidate.paths import _pool_dir, _sequences_dir, ready_to_load_path
from .prospects_state import (
    ACCOUNT_ID_FIELD,
    RETIRED_STATUSES,
    _identity_keys,
)


def _log_reply_mark_history(
    profile: str,
    event: str,
    email: str,
    source: str,
    content_root: Path | None = None,
    error: str = "",
) -> None:
    """Audit an unmatched or failed mark_replied event to history.jsonl."""
    try:
        from .ledgers import Ledgers

        root = content_root or resolve_content_root()
        ledgers = Ledgers(SimpleNamespace(content_root=root), profile)
        record = {
            "event": event,
            "who": email,
            "source": source,
        }
        if error:
            record["error"] = error
        ledgers.append_history(record)
    except Exception:
        pass  # nosec B110


def _extract_keys_a_first(item_or_row: dict) -> list[str]:
    """Derive identity keys from a dict (ledger item or CSV row), with 'a:' first."""
    keys: list[str] = []
    aid = (
        str(item_or_row.get(ACCOUNT_ID_FIELD) or item_or_row.get("account_id") or "")
        .strip()
        .lower()
    )
    if aid:
        keys.append(f"a:{aid}")
    domain = (
        str(item_or_row.get("domain") or item_or_row.get("company_domain") or "").strip().lower()
    )
    if domain:
        keys.append(f"d:{domain}")
    cid = str(item_or_row.get("id") or "").strip().lower()
    if cid:
        keys.append(f"i:{cid}")
    company = " ".join(str(item_or_row.get("company") or "").split()).lower()
    if company:
        keys.append(f"c:{company}")
    return keys


def _collect_csv_paths(profile: str, content_root: Path | None) -> list[Path]:
    csv_paths: list[Path] = []
    ready_csv = ready_to_load_path(profile, content_root)
    if ready_csv.is_file():
        csv_paths.append(ready_csv)

    master_csv = _pool_dir(profile, content_root) / "master-list.csv"
    if master_csv.is_file():
        csv_paths.append(master_csv)

    seq_dir = _sequences_dir(profile, content_root)
    for src in load_cell_map(profile, content_root):
        src_csv = src.get("csv")
        if not src_csv:
            continue
        p = seq_dir / src_csv if not Path(src_csv).is_absolute() else Path(src_csv)
        if p.is_file() and p not in csv_paths:
            csv_paths.append(p)
    return csv_paths


def _find_email_candidates(
    wanted: set[str],
    items: list[dict],
    csv_paths: list[Path],
) -> dict[str, list[str]]:
    email_candidates: dict[str, list[str]] = {}
    for item in items:
        ikeys = _extract_keys_a_first(item)
        for field in ("contact_email", "email"):
            val = str(item.get(field) or "").strip().lower()
            if val and val in wanted:
                email_candidates.setdefault(val, []).extend(ikeys)

    for p in csv_paths:
        try:
            with p.open(newline="", encoding="utf-8", errors="ignore") as fh:
                for row in csv.DictReader(fh):
                    email = (row.get("email") or "").strip().lower()
                    if email in wanted:
                        email_candidates.setdefault(email, []).extend(_extract_keys_a_first(row))
        except OSError:
            continue
    return email_candidates


def _resolve_email_to_key(
    wanted: set[str],
    email_candidates: dict[str, list[str]],
    ledger_keys: set[str],
) -> dict[str, str]:
    email_to_key: dict[str, str] = {}
    for email in wanted:
        candidates = email_candidates.get(email, [])
        seen = set()
        a_keys = []
        other_keys = []
        for k in candidates:
            if k and k not in seen:
                seen.add(k)
                if k.startswith("a:"):
                    a_keys.append(k)
                else:
                    other_keys.append(k)
        for k in a_keys + other_keys:
            if k in ledger_keys:
                email_to_key[email] = k
                break
    return email_to_key


def _apply_reply_updates(
    profile: str,
    email_to_key: dict[str, str],
    items: list[dict],
    source: str,
    content_root: Path | None,
) -> tuple[int, list[str], set[str]]:
    retired_keys: set[str] = set()
    for item in items:
        if str(item.get("status") or "").strip().lower() in RETIRED_STATUSES:
            retired_keys.update(_identity_keys(item))

    updates: dict[str, str] = {}
    retired_emails: list[str] = []
    for email, key in email_to_key.items():
        if key in retired_keys:
            retired_emails.append(email)
        else:
            updates[key] = "replied"

    changed = 0
    unmatched_keys: set[str] = set()
    if updates:
        summary = ps.set_status(profile, updates, source=source, content_root=content_root)
        changed = summary["changed"]
        unmatched_keys = set(summary["unmatched"])

    key_to_emails: dict[str, list[str]] = {}
    for email, key in email_to_key.items():
        key_to_emails.setdefault(key, []).append(email)
    truly_unmatched = {e for k in unmatched_keys for e in key_to_emails.get(k, [])}

    return changed, retired_emails, truly_unmatched


def mark_replied(
    profile: str,
    emails: list[str] | set[str],
    *,
    source: str,
    content_root: Path | None = None,
) -> dict:
    """Set ``status: replied`` in ``latest.json`` for the accounts that own these emails.

    Resolves prospect emails through multiple data sources in priority order:
      1. Contact emails in ``latest.json`` itself;
      2. CSV rows in ``sequences/ready-to-load.csv``;
      3. CSV rows in ``sequences/.pool/master-list.csv``;
      4. Registered enrolled CSVs in ``sequences/cells.toml``.

    For each email, matches identity keys with stamped ``a:<account_id>`` prioritized
    first over domain/company derivations.

    Never overwrites a :data:`RETIRED_STATUSES` account — a positive reply does not
    un-disqualify someone an eval or an opt-out already removed. An email that cannot be
    resolved to any account (missing from the CSVs, or resolving to a key the ledger does
    not have) is reported under ``unmatched`` and logged to ``history.jsonl`` rather than
    raising, mirroring :func:`set_status`'s own "report, never invent" rule.
    """
    wanted = {str(e).strip().lower() for e in emails if str(e).strip()}
    result = {
        "profile": profile,
        "matched": [],
        "retired_skipped": [],
        "unmatched": sorted(wanted),
        "changed": 0,
    }
    if not wanted:
        return result

    try:
        current = ps.load_latest(profile, content_root)
        items = [dict(it) for it in current.get("items", [])]

        ledger_keys: set[str] = {k for item in items for k in _identity_keys(item)}
        csv_paths = _collect_csv_paths(profile, content_root)
        email_candidates = _find_email_candidates(wanted, items, csv_paths)
        email_to_key = _resolve_email_to_key(wanted, email_candidates, ledger_keys)

        changed, retired_emails, truly_unmatched = _apply_reply_updates(
            profile, email_to_key, items, source, content_root
        )

        matched_emails = set(email_to_key) - set(retired_emails) - truly_unmatched
        unmatched_emails = (wanted - set(email_to_key)) | truly_unmatched

        for email in sorted(unmatched_emails):
            _log_reply_mark_history(
                profile, "reply_mark_unmatched", email, source, content_root=content_root
            )

        result["matched"] = sorted(matched_emails)
        result["retired_skipped"] = sorted(retired_emails)
        result["unmatched"] = sorted(unmatched_emails)
        result["changed"] = changed
        return result

    except Exception as exc:
        for email in sorted(wanted):
            _log_reply_mark_history(
                profile,
                "reply_mark_failed",
                email,
                source,
                content_root=content_root,
                error=str(exc),
            )
        raise
