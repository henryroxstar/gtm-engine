"""The hold sheet — bulk-first review of the hold queue, one HTML file, no server.

Groups are keyed by **(question, seat)** (PS12) — not by the raw trigger. Several triggers
ask the operator the same underlying question (``HOLD_QUESTION`` maps e.g.
``competitor-adjacent``/``partner``/``regulator``/``strategic-account`` all onto
``account-off-limits``), and grouping by trigger split those into near-duplicate sections
with the same three choices restated in slightly different words each time. Copy is
per-spec with one generic spec per seat, so within a group the email a row would get is the
same. The operator reads the question once, decides the group once, and overrides by
exception. Anti-anchoring is structural: no radio is ever pre-checked; a prior decision on
the same account is shown as text in the "Before" column. This is presentation-only: each
row still carries its own raw ``trigger``, unchanged, for ``hold-apply``/``decisions.py``.

Rendering is the labeler's own pattern (``labeler-src/build_from_sheet.py``): bare
``__TOKEN__`` placeholders in a template that ships with this package, JSON-escaped so a
``</script>`` inside a value cannot break out. The export handler writes every row —
blank decisions as ``""`` — so a row the operator never reached is exported as *held*,
never dropped.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ..cells import seat_of
from ..htmlpage import script_json as _json
from .decisions import _raw_detail
from .model import HOLD_ORDER, HOLD_QUESTION, QUESTION_COPY, SALVAGE_KINDS

TEMPLATE = Path(__file__).with_name("template_hold.html")


def load_default_bodies(profile: str | None, content_root: Path | None = None) -> dict[str, str]:
    """Load latest generic sequence spec email bodies for the profile, keyed by seat."""
    if not profile:
        return {}
    try:
        from ..prospect_paths import sequences_dir

        seq_dir = sequences_dir(profile, content_root)
        if not seq_dir.exists():
            return {}
        specs = sorted(seq_dir.glob("spec-generic-*.md"))
        by_seat: dict[str, Path] = {}
        for p in specs:
            m = re.search(r"spec-generic-([a-z0-9_-]+)-\d{4}-\d{2}-\d{2}\.md", p.name)
            if m:
                by_seat[m.group(1)] = p

        seat_bodies: dict[str, str] = {}
        for seat_key, path in by_seat.items():
            text = path.read_text(encoding="utf-8")
            m = re.search(r"(\*\*Step 1.*)", text, re.DOTALL)
            raw = m.group(1).strip() if m else text.strip()
            lines = [
                line[2:] if line.startswith("> ") else (line[1:] if line.startswith(">") else line)
                for line in raw.splitlines()
            ]
            seat_bodies[seat_key] = "\n".join(lines).strip()

        aliases = {
            "security": ["security", "risk-compliance", "compliance"],
            "technical": ["technical", "architect", "ai-platform", "engineering"],
            "exec": ["exec", "cto", "ceo", "product"],
            "builder": ["builder", "developer", "innovation"],
        }
        out: dict[str, str] = {}
        for base_seat, target_seats in aliases.items():
            if base_seat in seat_bodies:
                for s in target_seats:
                    out[s] = seat_bodies[base_seat]
        for k, v in seat_bodies.items():
            out.setdefault(k, v)
        if "technical" in seat_bodies:
            out["*"] = seat_bodies["technical"]
        elif seat_bodies:
            out["*"] = next(iter(seat_bodies.values()))
        return out
    except Exception:  # noqa: BLE001
        return {}


def _seat(title: str, profile: str | None = None) -> str:
    try:
        return seat_of(title or "", profile) or "unresolved"
    except Exception:  # noqa: BLE001 — a seat resolver failure must not block review
        return "unresolved"


def build_sheet_payload(
    hold_rows: list[dict],
    *,
    bodies: dict[str, str] | None = None,
    profile: str | None = None,
    content_root: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """``(groups, rows)`` from hold-CSV rows (the columns ``decisions.HOLD_COLUMNS`` writes).

    ``bodies`` maps a seat (or ``"*"``) to the shared email text to show collapsed per group.
    ``profile`` resolves seats against the tenant's own ``role-vocabulary.toml`` rather than
    the built-in default vocabulary; omitted, it falls back exactly as before.
    """
    if bodies is None and profile:
        bodies = load_default_bodies(profile, content_root)
    bodies = bodies or {}
    groups: dict[tuple[str, str], list[dict]] = {}
    for i, r in enumerate(hold_rows):
        trigger = (r.get("trigger") or "").strip().lower()
        question = HOLD_QUESTION.get(trigger, trigger)
        seat = _seat(r.get("title") or "", profile)
        groups.setdefault((question, seat), []).append((i, r))
    # A question's risk position is the earliest (riskiest) HOLD_ORDER trigger that maps to
    # it, so e.g. `account-off-limits` (earliest member `competitor-adjacent`, index 0) still
    # sorts ahead of `tier-a-would-get-generic` (index 10) exactly as the triggers did alone.
    order: dict[str, int] = {}
    for n, trig in enumerate(HOLD_ORDER):
        order.setdefault(HOLD_QUESTION.get(trig, trig), n)
    keys = sorted(groups, key=lambda k: (order.get(k[0], len(order)), k[1]))
    out_groups, out_rows = [], []
    for key in keys:
        question, seat = key
        title, meaning = QUESTION_COPY.get(
            question, (question.replace("-", " "), {"suppress": "", "generic": "", "salvage": ""})
        )
        gkey = f"{question}|{seat}"
        out_groups.append(
            {
                "key": gkey,
                "question": question,
                "seat": seat,
                "title": title,
                "count": len(groups[key]),
                "meaning": meaning,
                "body": bodies.get(seat) or bodies.get("*") or "",
            }
        )
        for i, r in groups[key]:
            trigger = (r.get("trigger") or "").strip().lower()
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
                    # The RAW detail: this key is what the export hands `hold-apply`, and the
                    # router compares it verbatim. The composed `lane_reason` never matches.
                    "detail": _raw_detail(r),
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
    profile: str | None = None,
    content_root: Path | None = None,
) -> str:
    groups, rows = build_sheet_payload(
        hold_rows, bodies=bodies, profile=profile, content_root=content_root
    )
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
    profile: str | None = None,
    content_root: Path | None = None,
) -> Path:
    with hold_csv.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_sheet(
            rows,
            stamp=stamp,
            auto=auto,
            bodies=bodies,
            profile=profile,
            content_root=content_root,
        ),
        encoding="utf-8",
    )
    return out
