"""Per-profile people ledger — the durable, person-keyed record of engagement.

Sibling of :mod:`gtm_core.ledger_cli`/:mod:`gtm_core.ledgers`. Where the cost/history
ledgers are append-only audit, this is an **upserted** master list of *people* the
brand has engaged with (seeded by the LinkedIn skills): one record per person, with
tags, profile URL, a full engagement history (like/comment/reply_sent + when), an
engagement count, and a lifecycle status for conversion tracking (lead → … → account).

State lives under ``content/<profile>/prospects/people.json`` (the same gitignored,
per-profile volume the other ledgers use — never committed, tenant-isolated). Mutations
take the cross-process :func:`gtm_core.locks.profile_lock` (cron + cockpit share the
volume) and write atomically (temp + ``os.replace``). Pure stdlib, SDK-free, and — like
:mod:`gtm_core.ledgers` — never calls ``datetime.now()`` at import time.

CLI::

    python -m gtm_core.people upsert     --profile P --json '{...}'
    python -m gtm_core.people log-reply   --profile P --id ID --json '{...}'
    python -m gtm_core.people set-status  --profile P --id ID --status S [--account SLUG]
    python -m gtm_core.people query       --profile P [--tag T] [--status S] \
                                          [--engaged-min N] [--account SLUG] [--list-urls] [--count]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .locks import profile_lock
from .paths import PathConfig

# Lifecycle status, ordered for the "never downgrade" rule. ``disqualified`` is terminal
# and sits outside the ladder — auto-bumps never touch it.
_STATUS_RANK = {"lead": 0, "engaged": 1, "replied": 2, "opportunity": 3, "account": 4}
STATUSES = frozenset({*_STATUS_RANK, "disqualified"})
_KIND = "people"


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing ``Z`` (call-time only)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    """Lowercase kebab slug: non-alphanumerics collapse to single hyphens, trimmed."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (value or "").lower())).strip("-")


def _normalize_url(url: str) -> str:
    """Canonical key form of a LinkedIn profile URL: no scheme/www/query/fragment/trailing slash."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?", 1)[0].split("#", 1)[0]
    return u.rstrip("/")


def derive_id(record: dict) -> str:
    """Stable identity for a person: normalized profile_url, else name-company slug."""
    url = record.get("profile_url")
    if url:
        norm = _normalize_url(url)
        if norm:
            return norm
    name_slug = _slug(record.get("name", ""))
    if not name_slug:
        raise ValueError("cannot derive id: record has no profile_url and no name")
    parts = [name_slug, _slug(record.get("company", ""))]
    return "-".join(p for p in parts if p)


class People:
    """Read/upsert the per-profile people ledger under ``content/<profile>/prospects/``."""

    def __init__(self, cfg: Any, profile: str) -> None:
        self._cfg = cfg
        self._profile = profile
        self._content_root: Path = cfg.content_root
        self._dir: Path = cfg.content_root / profile / "prospects"
        self._path: Path = self._dir / "people.json"

    @property
    def path(self) -> Path:
        return self._path

    # ── storage ──────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if not self._path.is_file():
            return {"kind": _KIND, "profile": self._profile, "updated_at": None, "people": []}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        data.setdefault("people", [])
        return data

    def _save(self, data: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data["kind"] = _KIND
        data["profile"] = self._profile
        data["updated_at"] = _utc_now_iso()
        tmp = self._dir / f".people.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)  # atomic on POSIX

    def read(self) -> dict:
        """Return the whole ledger (read-only; no lock needed for a snapshot read)."""
        return self._load()

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _find(people: list[dict], pid: str) -> dict | None:
        return next((p for p in people if p.get("id") == pid), None)

    @staticmethod
    def _append_engagement(person: dict, ev: dict) -> None:
        """Append an engagement, deduped on (type, post_url, date); refresh count + seen dates."""
        engagements = person.setdefault("engagements", [])
        key = (ev.get("type"), ev.get("post_url"), ev.get("date"))
        if any((e.get("type"), e.get("post_url"), e.get("date")) == key for e in engagements):
            return
        engagements.append({k: v for k, v in ev.items() if v is not None})
        person["engagement_count"] = len(engagements)
        dates = [e["date"] for e in engagements if e.get("date")]
        if dates:
            person["first_seen"] = min(dates)
            person["last_seen"] = max(dates)

    # ── mutations (locked) ───────────────────────────────────────────────────
    def upsert(self, record: dict) -> dict:
        """Add or merge a person and append its engagement. Returns the stored person."""
        pid = derive_id(record)
        ev = record.get("engagement") or {}
        with profile_lock(self._content_root, self._profile):
            data = self._load()
            person = self._find(data["people"], pid)
            if person is None:
                person = {
                    "id": pid,
                    "name": record.get("name"),
                    "headline": record.get("headline"),
                    "company": record.get("company"),
                    "profile_url": record.get("profile_url"),
                    "tags": [],
                    "engagements": [],
                    "engagement_count": 0,
                    "status": record.get("status", "lead"),
                    "linked_account": record.get("linked_account"),
                    "first_seen": None,
                    "last_seen": None,
                }
                data["people"].append(person)
            else:
                # fill only missing identity fields — never clobber existing data
                for field in ("name", "headline", "company", "profile_url"):
                    if not person.get(field) and record.get(field):
                        person[field] = record[field]
            # union tags (preserve order, drop dups)
            for tag in record.get("tags", []) or []:
                if tag not in person["tags"]:
                    person["tags"].append(tag)
            if ev:
                ev.setdefault("date", _utc_now_iso())
                self._append_engagement(person, ev)
            self._save(data)
            return person

    def log_reply(self, pid: str, event: dict) -> dict:
        """Append a ``reply_sent`` engagement (operator-confirmed) and bump status up to 'replied'."""
        with profile_lock(self._content_root, self._profile):
            data = self._load()
            person = self._find(data["people"], pid)
            if person is None:
                raise KeyError(f"no person with id {pid!r}")
            ev = dict(event)
            ev["type"] = "reply_sent"
            ev.setdefault("date", _utc_now_iso())
            self._append_engagement(person, ev)
            cur = person.get("status", "lead")
            if cur in ("lead", "engaged"):
                person["status"] = "replied"
            self._save(data)
            return person

    def set_status(self, pid: str, status: str, account: str | None = None) -> dict:
        """Set lifecycle status (and optional linked account) for conversion tracking."""
        if status not in STATUSES:
            raise ValueError(f"invalid status {status!r}; allowed: {sorted(STATUSES)}")
        with profile_lock(self._content_root, self._profile):
            data = self._load()
            person = self._find(data["people"], pid)
            if person is None:
                raise KeyError(f"no person with id {pid!r}")
            person["status"] = status
            if account is not None:
                person["linked_account"] = account
            self._save(data)
            return person

    # ── query (read-only) ────────────────────────────────────────────────────
    def query(
        self,
        *,
        tag: str | None = None,
        status: str | None = None,
        engaged_min: int | None = None,
        account: str | None = None,
    ) -> list[dict]:
        people = self._load()["people"]
        out = []
        for p in people:
            if tag is not None and tag not in (p.get("tags") or []):
                continue
            if status is not None and p.get("status") != status:
                continue
            if engaged_min is not None and (p.get("engagement_count") or 0) < engaged_min:
                continue
            if account is not None and p.get("linked_account") != account:
                continue
            out.append(p)
        return out


def _load_json(raw: str) -> dict:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[people] --json is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise SystemExit("[people] --json must be a JSON object")
    return obj


def _people(args) -> People:
    cfg = PathConfig.from_env(repo_root=args.repo_root)
    return People(cfg, args.profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.people",
        description="Per-profile people engagement ledger (upsert / log-reply / set-status / query).",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("upsert")
    up.add_argument("--profile", required=True)
    up.add_argument(
        "--json", required=True, dest="payload", help="Person record (+ optional engagement)."
    )

    lr = sub.add_parser("log-reply")
    lr.add_argument("--profile", required=True)
    lr.add_argument("--id", required=True, dest="pid")
    lr.add_argument("--json", required=True, dest="payload", help="{post_slug,post_url,date,note}")

    ss = sub.add_parser("set-status")
    ss.add_argument("--profile", required=True)
    ss.add_argument("--id", required=True, dest="pid")
    ss.add_argument("--status", required=True, choices=sorted(STATUSES))
    ss.add_argument("--account", default=None)

    q = sub.add_parser("query")
    q.add_argument("--profile", required=True)
    q.add_argument("--tag", default=None)
    q.add_argument("--status", default=None, choices=sorted(STATUSES))
    q.add_argument("--engaged-min", type=int, default=None, dest="engaged_min")
    q.add_argument("--account", default=None)
    q.add_argument("--list-urls", action="store_true", dest="list_urls")
    q.add_argument("--count", action="store_true")

    args = parser.parse_args(argv)
    ppl = _people(args)

    if args.cmd == "upsert":
        person = ppl.upsert(_load_json(args.payload))
        print(person["id"])
        return 0

    if args.cmd == "log-reply":
        try:
            person = ppl.log_reply(args.pid, _load_json(args.payload))
        except KeyError as exc:
            raise SystemExit(f"[people] {exc}") from exc
        print(
            json.dumps(
                {
                    "id": person["id"],
                    "status": person["status"],
                    "engagement_count": person["engagement_count"],
                }
            )
        )
        return 0

    if args.cmd == "set-status":
        try:
            person = ppl.set_status(args.pid, args.status, args.account)
        except KeyError as exc:
            raise SystemExit(f"[people] {exc}") from exc
        print(
            json.dumps(
                {
                    "id": person["id"],
                    "status": person["status"],
                    "linked_account": person.get("linked_account"),
                }
            )
        )
        return 0

    if args.cmd == "query":
        rows = ppl.query(
            tag=args.tag, status=args.status, engaged_min=args.engaged_min, account=args.account
        )
        if args.count:
            print(len(rows))
        elif args.list_urls:
            for p in rows:
                if p.get("profile_url"):
                    print(p["profile_url"])
        else:
            for p in rows:
                print(
                    json.dumps(
                        {
                            "id": p["id"],
                            "name": p.get("name"),
                            "tags": p.get("tags"),
                            "engagement_count": p.get("engagement_count"),
                            "status": p.get("status"),
                            "linked_account": p.get("linked_account"),
                            "profile_url": p.get("profile_url"),
                        },
                        ensure_ascii=False,
                    )
                )
        return 0

    parser.error(f"unknown command {args.cmd!r}")
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
