import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.email_compliance import normalize_market
from gtm_core.minischema import validate
from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.prospects_state import fill_accounts, load_latest

FIRMO_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "domain": {"type": "string"},
            "id": {"type": "string"},
            "company": {"type": "string"},
            "industry": {"type": "string"},
            "country": {"type": "string"},
            "city": {"type": "string"},
            "employees_range": {"type": "string"},
            "description": {"type": "string"},
        },
    },
}


def _title_market(s: str) -> str:
    # Title-case for display/saving, e.g. "united states" -> "United States"
    return s.title() if s else s


def canonicalise_country(row: dict) -> dict:
    if "country" in row and row["country"]:
        normalized = normalize_market(row["country"])
        row["country"] = _title_market(normalized)
    return row


def _queue_accounts(profile: str, limit: int = None, content_root: Path = None):
    data = load_latest(profile, content_root)
    queued = []
    fields = {"industry", "country", "city", "employees_range", "description"}
    for item in data.get("items", []):
        if any(not str(item.get(f) or "").strip() for f in fields):
            queued.append(item)
    if limit is not None:
        queued = queued[:limit]
    return queued


def queue_cmd(profile: str, limit: int = None, content_root: Path = None):
    queued = _queue_accounts(profile, limit, content_root)
    # R4.1: "queue lists accounts"
    for item in queued:
        print(json.dumps(item))
    return 0


def apply_cmd(profile: str, json_payload: str, dry_run: bool = False, content_root: Path = None):
    raw = json_payload
    if raw.startswith("@"):
        raw_path = Path(raw[1:])
        if raw_path.is_file():
            raw = raw_path.read_text(encoding="utf-8")
    else:
        try:
            p = Path(raw)
            if p.is_file():
                raw = p.read_text(encoding="utf-8")
        except (OSError, ValueError):
            pass

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("Invalid JSON payload", file=sys.stderr)
        return 2

    # R4.3: validates the payload (gtm_core.minischema)
    errors = validate(payload, FIRMO_SCHEMA)
    if errors:
        print(f"Schema validation failed: {errors}", file=sys.stderr)
        return 2

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    rows = []
    for row in payload:
        r = canonicalise_country(dict(row))
        r["firmo_source"] = "apply"
        r["firmo_on"] = date_str
        rows.append(r)

    if dry_run:
        return 0

    summary = fill_accounts(profile, rows, source="apply", content_root=content_root)

    refused = summary.get("refused", [])
    conflicts = summary.get("conflicts", [])

    # Write firmographics-review-<ts>.csv
    if refused or conflicts:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        root = content_root or resolve_content_root()
        review_file = (
            root
            / _safe_segment(profile, "profile")
            / "prospects"
            / f"firmographics-review-{ts}.csv"
        )
        review_file.parent.mkdir(parents=True, exist_ok=True)

        # Sort conflicts: HQ-country first
        conflicts.sort(key=lambda c: 0 if c["field"] == "country" else 1)

        # Combine refused and conflicts for the CSV. We need an `accept` column.
        with review_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "accept",
                    "type",
                    "domain",
                    "id",
                    "company",
                    "field",
                    "existing",
                    "incoming",
                    "reason",
                ]
            )

            for c in conflicts:
                row = c["row"]
                writer.writerow(
                    [
                        "",
                        "conflict",
                        row.get("domain", ""),
                        row.get("id", ""),
                        row.get("company", ""),
                        c["field"],
                        c["existing"],
                        c["incoming"],
                        "",
                    ]
                )

            for r in refused:
                writer.writerow(
                    [
                        "",
                        "refused",
                        r.get("domain", ""),
                        r.get("id", ""),
                        r.get("company", ""),
                        "",
                        "",
                        "",
                        "matches != 1 or missing identity",
                    ]
                )

        print(f"Review written to {review_file}")

    return 0


def accept_cmd(profile: str, review_csv: str, content_root: Path = None):
    # R4.4: accept overwrites only rows the operator marked accept=yes, through the same batch writer, and records each change.
    review_path = Path(review_csv)
    if not review_path.exists():
        print(f"Review file not found: {review_csv}", file=sys.stderr)
        return 2

    accepted_rows = []
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    with review_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("accept", "").strip().lower() == "yes":
                # For conflicts, we just set the field to incoming
                if row["type"] == "conflict":
                    update_row = {
                        "domain": row["domain"],
                        "id": row["id"],
                        "company": row["company"],
                        row["field"]: row["incoming"],
                        "firmo_source": "accept",
                        "firmo_on": date_str,
                    }
                    # We might have multiple accepted fields for the same account, so we should group them,
                    # but fill_accounts takes a list of dicts. We can group them by domain/id.
                    accepted_rows.append(update_row)

    if not accepted_rows:
        return 0

    # Group by identity
    grouped = {}
    for r in accepted_rows:
        key = (r.get("domain", ""), r.get("id", ""), r.get("company", ""))
        if key not in grouped:
            grouped[key] = {}
        grouped[key].update(r)

    final_rows = list(grouped.values())

    summary = fill_accounts(
        profile, final_rows, source="accept", overwrite=True, content_root=content_root
    )
    # R4.4: "records each change" (it could be printed)
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.firmographics")
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("queue")
    q.add_argument("--profile", required=True)
    q.add_argument("--limit", type=int, default=None)

    a = sub.add_parser("apply")
    a.add_argument("--profile", required=True)
    a.add_argument("--json", required=True)
    a.add_argument("--dry-run", action="store_true")

    acc = sub.add_parser("accept")
    acc.add_argument("--profile", required=True)
    acc.add_argument("--review", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "queue":
        return queue_cmd(args.profile, args.limit)
    elif args.cmd == "apply":
        return apply_cmd(args.profile, args.json, args.dry_run)
    elif args.cmd == "accept":
        return accept_cmd(args.profile, args.review)
    return 1


if __name__ == "__main__":
    sys.exit(main())
