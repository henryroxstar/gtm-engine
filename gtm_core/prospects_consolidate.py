"""Fold every un-consolidated prospect-run export into one deliverability-gated,
DNC-clean master list.

Each prospect run emits its own ``prospects-<date>-<cohort>-hubspot.csv`` (see
:mod:`gtm_core.prospects_import`), but folding those into a loadable sequencer
list has always been a manual end-of-flow step — one the operator often can't
reach because a run gets interrupted or the flow is only run partway. Exports
then pile up un-consolidated and silently invisible: ``latest.json`` is
account/dashboard-level and never holds person+email rows, so nothing surfaces
the backlog. On 2026-07-23 that gap hid 370 real emails across 25+ exports
(and 26 live DNC violations inside them) behind a stale, hand-built master list.

This module is the plumbing half of the fix — it is deliberately MCP-free
(stdlib-only, matching the rest of gtm_core) so it can run standalone, on a
schedule, or mid-flow after a partial run. The judgment/MCP half (fetching the
*live* Saleshandy DNC list) belongs to the skill/agent layer, which dumps it to
a small cache file first — see ``dnc_cache_path`` below.

One-file-for-humans layout (the "make it simple" requirement): the operator was
drowning in near-identical lists. So there is exactly **one** file a human ever
looks at — ``sequences/ready-to-load.csv`` (the load-me list) — and everything
else (the full ``master-list.csv`` audit trail, the ``needs-verification.csv``
hold queue, snapshots, the blocked log) lives in a hidden ``sequences/.pool/``
dir. The mental model is then literally what the folder shows: one file.

Person-level identity, not just email (the "don't re-burn a contact" fix): the
email-only DNC/sent filter misses the same person re-resolved under a second
address (``sam@vertex.example`` on DNC, ``sortega@vertex.example`` in a later run) and
double-counts one person carrying two email formats (``robin.kraft@`` +
``rkraft@``). So the loadable outputs are additionally deduped by a person key
(normalized first+last + org token from the domain/company) — a row is dropped
if that person was already sent under *any* address, and same-person duplicates
collapse to the highest-confidence one. The full ``master-list.csv`` keeps every
address for audit; only the loadable files are person-unique.

Deliverability gate (the "make it the default" requirement): an email with no
verification signal is exactly the kind of unknown that tanks bounce rate
during mailbox ramp-up (see the ramp-up lessons in email-sequence skill docs).
So a row is classified into a confidence tier from whatever ``email_status``/
``conf`` signal its source export carried (RocketReach grade, "verified",
"account-folder-verified", or nothing at all):

  * ``high``    — RocketReach A/A-, "verified", "account-folder-verified"
                  -> written to ``ready-to-load.csv`` (safe to load today)
  * ``medium``  — RocketReach B, "found", ``conf`` in (high, med) with no grade
                  -> written to ``needs-verification.csv`` (verify before load)
  * ``unknown`` — no signal at all (the common case for a fresh raw export)
                  -> also ``needs-verification.csv``
  * ``blocked`` — RocketReach F(pattern), ``conf`` "invalid"
                  -> excluded from every loadable file, logged to
                  ``.blocked-log.jsonl`` for audit, never silently dropped

Only ``high`` confidence rows land in ``ready-to-load.csv`` by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.paths import resolve_content_root
from gtm_core.prospects_state import _norm as _norm_company

# --- schema -----------------------------------------------------------------

MASTER_COLS = [
    "first",
    "last",
    "email",
    "title",
    "company",
    "company_domain",
    "city",
    "country",
    "segment",
    "tier",
    "score",
    "conf",
    "email_status",
    "why_now",
    "case_study",
    "src",
    "conf_tier",
]

# Canonical field -> header variants seen across hubspot exports + the flat
# master-list schema. The GTM_* prefix varies by which pack generated the run.
_ALIASES = {
    "first": ("First Name", "first"),
    "last": ("Last Name", "last"),
    "name": ("Contact Name",),  # split into first/last when those are absent
    "email": ("Email", "Contact Email", "email"),
    "title": ("Job Title", "Contact Title", "title"),
    "company": ("Company Name", "Company", "company"),
    "company_domain": ("Company Domain Name", "company_domain"),
    "city": ("City", "HQ City", "city"),
    "country": ("Country/Region", "Market", "country"),
    "segment": ("GTM_Segment", "Segment", "segment"),
    "tier": ("GTM_Tier", "Tier", "tier"),
    "score": ("GTM_Score", "Score", "Lead Score", "score"),
    "conf": ("conf",),
    "email_status": ("Email Status", "email_status"),
    "why_now": ("GTM_Why_Now", "Why Now", "why_now"),
    "case_study": ("GTM_Case_Study", "Case Study", "case_study"),
}

_HIGH_STATUS = re.compile(r"rocketreach a|^verified$|account-folder-verified", re.I)
_MEDIUM_STATUS = re.compile(r"rocketreach b|^found$", re.I)
_BLOCKED_STATUS = re.compile(r"rocketreach f|pattern|^invalid$", re.I)


def _get(row: dict, field: str) -> str:
    for key in _ALIASES.get(field, ()):
        v = (row.get(key) or "").strip()
        if v:
            return v
    return ""


def _score_num(s: str) -> int:
    """``"7/12"`` -> ``7``; ``"11"`` -> ``11``; unparseable -> ``-1``."""
    s = (s or "").split("/")[0].strip()
    try:
        return int(float(s))
    except ValueError:
        return -1


def classify_confidence(email_status: str, conf: str) -> str:
    """Deliverability confidence tier from whatever verification signal a row
    carries. No signal at all (the common case for a fresh raw export) is
    ``"unknown"`` — treated as NOT safe to load by default, not as a free pass.
    """
    status = (email_status or "").strip()
    c = (conf or "").strip().lower()
    if _BLOCKED_STATUS.search(status) or c == "invalid":
        return "blocked"
    if _HIGH_STATUS.search(status):
        return "high"
    if _MEDIUM_STATUS.search(status) or c in ("high", "med"):
        return "medium"
    return "unknown"


_CONF_RANK = {"high": 3, "medium": 2, "unknown": 1, "blocked": 0}


def _org_token(company_domain: str, company: str) -> str:
    """A stable per-company token that reconciles a domain and a bare name —
    ``vertex.example`` and ``"Vertex"`` both collapse to ``vertex`` — so the same person
    matches across a run that carried a domain and one that carried only a name.
    """
    domain = re.sub(r"^www\.", "", (company_domain or "").strip().lower())
    if domain:
        return domain.split(".")[0]
    return _norm_company(company or "").replace(" ", "")


def _person_key(rec: dict) -> str:
    """Identity of a *human* across email-format changes: normalized first+last
    plus the org token. Empty when there isn't enough to identify a person (no
    name, or no org) — an empty key is never collapsed or excluded, only kept.
    """
    first = re.sub(r"[^a-z0-9]", "", (rec.get("first") or "").lower())
    last = re.sub(r"[^a-z0-9]", "", (rec.get("last") or "").lower())
    org = _org_token(rec.get("company_domain", ""), rec.get("company", ""))
    if (not first and not last) or not org:
        return ""
    return f"{first}|{last}|{org}"


def _row_to_record(row: dict, src: str) -> dict:
    first, last = _get(row, "first"), _get(row, "last")
    if not first and not last and _get(row, "name"):
        parts = _get(row, "name").split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
    email_status = _get(row, "email_status")
    conf = _get(row, "conf")
    return {
        "first": first,
        "last": last,
        "email": _get(row, "email").lower(),
        "title": _get(row, "title"),
        "company": _get(row, "company"),
        "company_domain": _get(row, "company_domain"),
        "city": _get(row, "city"),
        "country": _get(row, "country"),
        "segment": _get(row, "segment"),
        "tier": _get(row, "tier"),
        "score": _get(row, "score"),
        "conf": conf,
        "email_status": email_status,
        "why_now": _get(row, "why_now"),
        "case_study": _get(row, "case_study"),
        "src": src,
        "conf_tier": classify_confidence(email_status, conf),
    }


# --- IO -----------------------------------------------------------------


def _prospects_dir(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    return root / profile / "prospects"


def _sequences_dir(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root) / "sequences"


def _pool_dir(profile: str, content_root: Path | None = None) -> Path:
    """Hidden home for everything that isn't the one human-facing load file:
    the full master-list audit, the needs-verification hold queue, snapshots,
    and the blocked log. Keeps ``sequences/`` down to a single visible CSV."""
    return _sequences_dir(profile, content_root) / ".pool"


def ready_to_load_path(profile: str, content_root: Path | None = None) -> Path:
    """The ONE file a human (or the email-sequence skill) loads from."""
    return _sequences_dir(profile, content_root) / "ready-to-load.csv"


def needs_verification_path(profile: str, content_root: Path | None = None) -> Path:
    return _pool_dir(profile, content_root) / "needs-verification.csv"


def dnc_cache_path(profile: str, content_root: Path | None = None) -> Path:
    """Where the skill/agent layer should dump the live Saleshandy DNC emails
    (a JSON list of addresses) before calling :func:`consolidate` — this module
    never calls the Saleshandy MCP itself (plumbing vs. judgment split)."""
    return _prospects_dir(profile, content_root) / ".cache" / "dnc-emails.json"


def _load_dnc(profile: str, content_root: Path | None, dnc_file: Path | None) -> set[str]:
    path = dnc_file or dnc_cache_path(profile, content_root)
    if not path.exists():
        return set()
    return {e.strip().lower() for e in json.loads(path.read_text(encoding="utf-8")) if e.strip()}


def _load_sent(profile: str, content_root: Path | None) -> tuple[set[str], set[str]]:
    """Everyone already contacted, by ``sequences/*.csv`` rows marked ``SENT`` —
    covers manual sends that predate a formal Saleshandy sequence/DNC entry.
    Returns ``(emails, person_keys)``: the person keys catch the same human
    re-resolved under a second address (the email set alone can't)."""
    emails: set[str] = set()
    people: set[str] = set()
    seq_dir = _sequences_dir(profile, content_root)
    for path in sorted(seq_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "status" not in reader.fieldnames:
                continue
            for row in reader:
                if (row.get("status") or "").strip().upper() != "SENT":
                    continue
                email = (row.get("email") or "").strip().lower()
                if email:
                    emails.add(email)
                pk = _person_key(_row_to_record(row, path.name))
                if pk:
                    people.add(pk)
    return emails, people


def _existing_master_path(profile: str, content_root: Path | None) -> Path | None:
    """The canonical rolling file if it already exists; otherwise the newest
    dated ``master-list*.csv`` on disk (so a first run seeds from whatever the
    operator built by hand). Never assumes a fixed prior filename."""
    pool = _pool_dir(profile, content_root)
    canonical = pool / "master-list.csv"
    if canonical.exists():
        return canonical
    seq_dir = _sequences_dir(profile, content_root)
    # Migration: seed from the pre-.pool location (or any hand-built dated file)
    # the first time we run under the new layout.
    candidates = sorted(seq_dir.glob("master-list*.csv"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _load_master(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email:
            continue
        rec = {c: (r.get(c) or "") for c in MASTER_COLS}
        rec["email"] = email
        if not rec["conf_tier"]:
            rec["conf_tier"] = classify_confidence(rec["email_status"], rec["conf"])
        out.append(rec)
    return out


def _atomic_write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MASTER_COLS)
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _snapshot(path: Path, snap_dir: Path, keep: int = 30) -> Path | None:
    if not path.exists():
        return None
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")
    dest = snap_dir / f"master-list-{stamp}.csv"
    dest.write_bytes(path.read_bytes())
    snaps = sorted(snap_dir.glob("master-list-*.csv"))
    for old in snaps[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def _append_blocked_log(profile: str, content_root: Path | None, blocked: list[dict]) -> None:
    if not blocked:
        return
    path = _pool_dir(profile, content_root) / ".blocked-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for r in blocked:
            f.write(
                json.dumps(
                    {
                        "logged_at": stamp,
                        "email": r["email"],
                        "company": r["company"],
                        "src": r["src"],
                        "reason": r["email_status"] or r["conf"],
                    }
                )
                + "\n"
            )


# --- core ---------------------------------------------------------------


def consolidate(
    profile: str,
    *,
    content_root: Path | None = None,
    dnc_file: Path | None = None,
    allow_shrink: bool = False,
) -> dict:
    """Sweep every ``prospects-*-hubspot.csv`` export, fold net-new emails into
    the canonical ``sequences/master-list.csv``, gate by deliverability
    confidence, and write ``ready-to-load.csv`` / ``needs-verification.csv``.

    Idempotent: safe to call after every run (even a partial one) or on a
    schedule — re-running with no new exports reproduces the same output.
    """
    master_path = _existing_master_path(profile, content_root)
    existing = _load_master(master_path)

    by_email: dict[str, dict] = {}
    master_dups = 0
    for r in existing:
        if r["email"] in by_email:
            master_dups += 1
        else:
            by_email[r["email"]] = r

    dnc = _load_dnc(profile, content_root, dnc_file)
    sent_emails, sent_people = _load_sent(profile, content_root)
    existing_emails = {r["email"] for r in existing}

    net_new, dup_rows, already_present, dnc_hits = 0, 0, 0, 0
    for path in sorted(_prospects_dir(profile, content_root).glob("prospects-*-hubspot.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                email = _get(row, "email").lower()
                if not email or "@" not in email:
                    continue
                if email in dnc:
                    dnc_hits += 1
                    continue
                rec = _row_to_record(row, path.name)
                if email in existing_emails:
                    already_present += 1
                    continue
                if email in by_email:
                    # collision within this sweep's net-new rows
                    if _score_num(rec["score"]) > _score_num(by_email[email]["score"]):
                        by_email[email] = rec
                    dup_rows += 1
                    continue
                by_email[email] = rec
                net_new += 1

    all_rows = list(by_email.values())
    if not allow_shrink and master_path is not None and len(all_rows) < len(existing):
        raise ValueError(
            f"refusing to shrink master list: {len(existing)} -> {len(all_rows)} rows; "
            "pass allow_shrink=True to override"
        )

    pool = _pool_dir(profile, content_root)
    master_canonical = pool / "master-list.csv"
    _snapshot(master_canonical, pool / ".snapshots")
    _atomic_write_csv(master_canonical, all_rows)

    blocked = [r for r in all_rows if r["conf_tier"] == "blocked"]
    _append_blocked_log(profile, content_root, blocked)

    # Loadable = not DNC'd, not already-sent (by email), not blocked-confidence.
    loadable = [
        r
        for r in all_rows
        if r["email"] not in dnc and r["email"] not in sent_emails and r["conf_tier"] != "blocked"
    ]

    # Person-level pass: drop anyone already sent under a DIFFERENT address, and
    # collapse the same human carrying two email formats to their best row.
    sent_person_excluded = 0
    best_by_person: dict[str, dict] = {}
    person_unique: list[dict] = []
    person_dups_collapsed = 0
    for r in loadable:
        pk = _person_key(r)
        if pk and pk in sent_people:
            sent_person_excluded += 1
            continue
        if not pk:
            person_unique.append(r)  # can't identify a person — never collapse
            continue
        prev = best_by_person.get(pk)
        if prev is None:
            best_by_person[pk] = r
            person_unique.append(r)
            continue
        person_dups_collapsed += 1
        # keep the higher-confidence row, breaking ties on score
        if (_CONF_RANK[r["conf_tier"]], _score_num(r["score"])) > (
            _CONF_RANK[prev["conf_tier"]],
            _score_num(prev["score"]),
        ):
            person_unique[person_unique.index(prev)] = r
            best_by_person[pk] = r

    ready = [r for r in person_unique if r["conf_tier"] == "high"]
    needs_verification = [r for r in person_unique if r["conf_tier"] in ("medium", "unknown")]

    _atomic_write_csv(ready_to_load_path(profile, content_root), ready)
    _atomic_write_csv(needs_verification_path(profile, content_root), needs_verification)

    result = {
        "profile": profile,
        "master_total": len(all_rows),
        "master_dups_collapsed": master_dups,
        "net_new_folded": net_new,
        "net_new_dups_collapsed": dup_rows,
        "already_in_master_skipped": already_present,
        "dnc_hits_blocked": dnc_hits,
        "sent_person_excluded": sent_person_excluded,
        "person_dups_collapsed": person_dups_collapsed,
        "ready_to_load": len(ready),
        "needs_verification": len(needs_verification),
        "blocked_excluded": len(blocked),
        "master_list": str(master_canonical),
        "ready_to_load_path": str(ready_to_load_path(profile, content_root)),
        "needs_verification_path": str(needs_verification_path(profile, content_root)),
    }
    _print_banner(result)
    # Refresh the operator-facing status page on every sweep so it can never go
    # stale. Best-effort: a dashboard error must never break consolidation.
    try:
        from gtm_core.prospects_dashboard import render_dashboard

        render_dashboard(profile, content_root)
    except Exception as exc:  # noqa: BLE001 - dashboard is non-critical
        print(f"dashboard refresh skipped: {exc}", file=sys.stderr)
    return result


def _print_banner(result: dict) -> None:
    """One human-readable status line to stderr (stdout stays clean JSON) — the
    'one number, always shown' surface so readiness never needs archaeology."""
    print(
        f"pool[{result['profile']}]: {result['ready_to_load']} ready · "
        f"{result['needs_verification']} verifying · "
        f"{result['blocked_excluded']} blocked · "
        f"{result['net_new_folded']} net-new folded"
        + (
            f" · {result['sent_person_excluded']} already-contacted skipped"
            if result.get("sent_person_excluded")
            else ""
        ),
        file=sys.stderr,
    )


def pool_status(profile: str, content_root: Path | None = None) -> dict:
    """Cheap read-only summary of the last consolidation run — no MCP, no
    re-sweep. Safe for a cockpit brief or a daily heartbeat check."""
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    unconsolidated = 0
    master_emails = {r["email"] for r in master}
    for path in _prospects_dir(profile, content_root).glob("prospects-*-hubspot.csv"):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                email = _get(row, "email").lower()
                if email and "@" in email and email not in master_emails:
                    unconsolidated += 1
    by_tier = {"high": 0, "medium": 0, "unknown": 0, "blocked": 0}
    for r in master:
        by_tier[r["conf_tier"]] = by_tier.get(r["conf_tier"], 0) + 1
    return {
        "master_total": len(master),
        "by_confidence_tier": by_tier,
        "unconsolidated_in_raw_exports": unconsolidated,
    }


def next_verification_batch(
    profile: str, limit: int = 50, content_root: Path | None = None
) -> list[dict]:
    """The top ``limit`` rows from the hold queue, highest score first, shaped as
    Saleshandy prospect objects — the single call the email-sequence skill makes
    to auto-drain ``needs-verification.csv`` (import these with verify=true, let
    the sequencer's verifier grade them, survivors re-gate to ready on the next
    sweep). Rows without a usable first/last/email are skipped."""
    rows = _load_master(needs_verification_path(profile, content_root))
    rows.sort(key=lambda r: _score_num(r["score"]), reverse=True)
    out: list[dict] = []
    for r in rows:
        if not (r["first"] and r["last"] and r["email"]):
            continue
        out.append(
            {
                "First Name": r["first"],
                "Last Name": r["last"],
                "Email": r["email"],
                "Company": r["company"],
                "Job Title": r["title"],
                # Saleshandy's actual field label is "Company Domain" (confirmed via
                # list_fields 2026-07-23) — NOT "Company Domain Name", which the
                # import API rejects with a 400.
                "Company Domain": r["company_domain"],
                "Country": r["country"],
            }
        )
        if len(out) >= limit:
            break
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_consolidate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consolidate", help="sweep raw exports into the master list")
    c.add_argument("--profile", required=True)
    c.add_argument(
        "--dnc-file", help="path to a JSON list of DNC emails (default: .cache/dnc-emails.json)"
    )
    c.add_argument("--allow-shrink", action="store_true")

    s = sub.add_parser("status", help="read-only pool status, no re-sweep")
    s.add_argument("--profile", required=True)

    v = sub.add_parser("verify-batch", help="emit next N hold-queue rows as Saleshandy import JSON")
    v.add_argument("--profile", required=True)
    v.add_argument("--limit", type=int, default=50)

    args = ap.parse_args(argv)
    if args.cmd == "consolidate":
        result = consolidate(
            args.profile,
            dnc_file=Path(args.dnc_file) if args.dnc_file else None,
            allow_shrink=args.allow_shrink,
        )
    elif args.cmd == "verify-batch":
        result = next_verification_batch(args.profile, limit=args.limit)
    else:
        result = pool_status(args.profile)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
