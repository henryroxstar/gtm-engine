"""The hold sheet — bulk-first review of the hold queue, one HTML file, no server.

Groups are keyed by **(trigger, seat)**: the decision logic is the same within a trigger, and
copy is per-spec with one generic spec per seat, so within a group the email a row would get
is the same. The operator reads the reason once, decides the group once, and overrides by
exception. Anti-anchoring is structural: no radio is ever pre-checked; a prior decision on
the same account is shown as text in the "Before" column.

Rendering is the labeler's own pattern (``labeler-src/build_from_sheet.py``): bare
``__TOKEN__`` placeholders in a template that ships with this package, JSON-escaped so a
``</script>`` inside a value cannot break out. The export handler writes every row —
blank decisions as ``""`` — so a row the operator never reached is exported as *held*,
never dropped.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..cells import seat_of
from .model import HOLD_COPY, HOLD_ORDER, SALVAGE_KINDS

TEMPLATE = Path(__file__).with_name("template_hold.html")


def _seat(title: str) -> str:
    try:
        return seat_of(title or "") or "unresolved"
    except Exception:  # noqa: BLE001 — a seat resolver failure must not block review
        return "unresolved"


def _json(value) -> str:
    """JSON that is safe inside a ``<script>`` block."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def build_sheet_payload(
    hold_rows: list[dict], *, bodies: dict[str, str] | None = None
) -> tuple[list[dict], list[dict]]:
    """``(groups, rows)`` from hold-CSV rows (the columns ``decisions.HOLD_COLUMNS`` writes).

    ``bodies`` maps a seat (or ``"*"``) to the shared email text to show collapsed per group.
    """
    bodies = bodies or {}
    groups: dict[tuple[str, str], list[dict]] = {}
    for i, r in enumerate(hold_rows):
        trigger = (r.get("trigger") or "").strip().lower()
        seat = _seat(r.get("title") or "")
        groups.setdefault((trigger, seat), []).append((i, r))
    order = {t: n for n, t in enumerate(HOLD_ORDER)}
    keys = sorted(groups, key=lambda k: (order.get(k[0], len(order)), k[1]))
    out_groups, out_rows = [], []
    for key in keys:
        trigger, seat = key
        title, meaning = HOLD_COPY.get(
            trigger, (trigger.replace("-", " "), {"suppress": "", "generic": "", "salvage": ""})
        )
        gkey = f"{trigger}|{seat}"
        out_groups.append(
            {
                "key": gkey,
                "trigger": trigger,
                "seat": seat,
                "title": title,
                "count": len(groups[key]),
                "meaning": meaning,
                "body": bodies.get(seat) or bodies.get("*") or "",
            }
        )
        for i, r in groups[key]:
            out_rows.append(
                {
                    "row_id": f"{i}:{(r.get('email') or '').strip().lower()}",
                    "group": gkey,
                    "email": (r.get("email") or "").strip().lower(),
                    "first": r.get("first") or "",
                    "last": r.get("last") or "",
                    "name": f"{r.get('first') or ''} {r.get('last') or ''}".strip()
                    or (r.get("email") or ""),
                    "title": r.get("title") or "",
                    "company": r.get("company") or "",
                    "domain": r.get("company_domain") or "",
                    "tier": r.get("tier") or "",
                    "clause": r.get("signal_clause") or "",
                    "evidence": r.get("evidence") or "",
                    "detail": r.get("lane_reason") or "",
                    "defect": r.get("judge_defect_class") or "",
                    "prior": r.get("prior_decision") or "",
                    "trigger": trigger,
                    "account_key": r.get("account_key") or "",
                }
            )
    return out_groups, out_rows


def render_sheet(
    hold_rows: list[dict],
    *,
    stamp: str,
    auto: list[dict] | None = None,
    bodies: dict[str, str] | None = None,
    export_name: str | None = None,
) -> str:
    groups, rows = build_sheet_payload(hold_rows, bodies=bodies)
    export = export_name or f"hold-decisions-{stamp}.jsonl"
    html = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("__GROUPS_JSON__", _json(groups)),
        ("__ROWS_JSON__", _json(rows)),
        ("__AUTO_JSON__", _json(auto or [])),
        ("__CHIPS_JSON__", _json(list(SALVAGE_KINDS))),
        ("__STAMP__", stamp),
        ("__EXPORT_NAME__", export),
    ):
        html = html.replace(token, value)
    return html


def write_sheet(
    hold_csv: Path,
    out: Path,
    *,
    stamp: str,
    auto: list[dict] | None = None,
    bodies: dict[str, str] | None = None,
) -> Path:
    with hold_csv.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_sheet(rows, stamp=stamp, auto=auto, bodies=bodies), encoding="utf-8")
    return out
