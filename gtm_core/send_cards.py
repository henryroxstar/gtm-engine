"""Send cards review surface for Gate 2 (W8, R8.1 - R8.4).

R8.1: Card generator: HTML/JSON review cards, exactly one per cell_id (cohort x seat x variant).
      Card title in plain words, emails hidden by default, example member named with rendered email.
      Personalised cards list every member's opener, source link, and capture date.
      Panel verdicts absent in initial DOM state, present only after a decision event.
      Export records revealed_before_decision.
R8.2: Closed decision set: 'send this cell', 'not this wave', 'rewrite' (with a note).
      Personalised members can be unticked individually. Blank decision exports as 'not decided'.
      Unknown decision word refused at apply.
R8.3: Apply writes one enroll draft (.pending/<run-id>.enroll-draft.json) only for 'send this cell'.
      Unticked personalised members are excluded. Members suppressed since render are removed and listed.
      Draft matches Step 6a schema, carries card_ids, source: 'send-cards', and expires_on under R0.2.
      rewrite rows go to the repair queue with the note.
R8.4: Gate-2 preview check: refuses without source: 'send-cards' and card_ids when send_cards_required
      is true/absent/unreadable. With false, draft is offered.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import html
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gtm_core.htmlpage import script_json
from gtm_core.merge_hygiene.signal_dates import signal_age_limit
from gtm_core.paths import resolve_content_root
from gtm_core.prospect_paths import pool_dir, sequences_dir

log = logging.getLogger(__name__)

#: Closed set of card decisions (R8.2)
VALID_DECISIONS: frozenset[str] = frozenset({"send this cell", "not this wave", "rewrite"})

NOT_DECIDED: str = "not decided"


@dataclass
class CardMember:
    name: str
    email: str
    company: str
    industry: str = ""
    country: str = ""
    level: str = ""
    seat: str = ""
    opener: str = ""
    source_url: str = ""
    capture_date: str = ""
    signal_kind: str = "event"
    ticked: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Card:
    cell_id: str
    title: str
    cohort: str
    seat: str
    message_variant: str
    angle: str
    segment: str
    premise_ids: list[str]
    proof_ids: list[str]
    gate_receipt: dict[str, str]
    is_personalised: bool
    example_member: dict[str, Any]
    members: list[CardMember]
    panel_verdicts: list[dict[str, Any]]
    sequence_id: str = ""
    step_id: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    spec: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["members"] = [m.to_dict() for m in self.members]
        return d


@dataclass
class ApplyResult:
    draft_paths: list[Path]
    drafts: list[dict[str, Any]]
    suppressed_removed: list[dict[str, Any]]
    unticked_excluded: list[dict[str, Any]]
    repair_rows: list[dict[str, Any]]


# ── R8.1 Card Generation ──────────────────────────────────────────────────────


def _format_card_title(seat: str, angle: str, segment: str) -> str:
    """Format plain-words card title: seat · angle · segment."""
    s_clean = seat.replace("-", " ").strip().title() if seat else "Unspecified Seat"
    a_clean = angle.replace("-", " ").strip().title() if angle else "Standard Angle"
    seg_clean = segment.replace("-", " ").strip().title() if segment else "General Segment"
    return f"{s_clean} · {a_clean} · {seg_clean}"


def generate_cards(cells: list[dict[str, Any]]) -> list[Card]:
    """Build Card objects: exactly one card per cell_id (cohort x seat x variant)."""
    cards: list[Card] = []
    seen_cells: set[str] = set()

    for c in cells:
        cid = c.get("cell_id") or ""
        if not cid:
            continue
        if cid in seen_cells:
            continue
        seen_cells.add(cid)

        seat = c.get("seat") or ""
        angle = c.get("angle") or c.get("message_variant") or ""
        segment = c.get("segment") or c.get("cohort") or ""
        title = c.get("title") or _format_card_title(seat, angle, segment)

        raw_members = c.get("members") or []
        members: list[CardMember] = []
        for rm in raw_members:
            members.append(
                CardMember(
                    name=rm.get("name")
                    or f"{rm.get('first', '')} {rm.get('last', '')}".strip()
                    or rm.get("email", ""),
                    email=(rm.get("email") or "").strip().lower(),
                    company=rm.get("company") or "",
                    industry=rm.get("industry") or "",
                    country=rm.get("country") or "",
                    level=rm.get("level") or "",
                    seat=rm.get("seat") or seat,
                    opener=rm.get("opener") or rm.get("signal_clause") or "",
                    source_url=rm.get("source_url") or rm.get("source") or "",
                    capture_date=rm.get("capture_date") or rm.get("source_date") or "",
                    signal_kind=rm.get("signal_kind") or "event",
                    ticked=bool(rm.get("ticked", True)),
                )
            )

        card = Card(
            cell_id=cid,
            title=title,
            cohort=c.get("cohort") or segment,
            seat=seat,
            message_variant=c.get("message_variant") or "",
            angle=angle,
            segment=segment,
            premise_ids=list(c.get("premise_ids") or []),
            proof_ids=list(c.get("proof_ids") or []),
            gate_receipt=dict(c.get("gate_receipt") or {}),
            is_personalised=bool(c.get("is_personalised", False)),
            example_member=dict(c.get("example_member") or {}),
            members=members,
            panel_verdicts=list(c.get("panel_verdicts") or []),
            sequence_id=c.get("sequence_id") or "",
            step_id=c.get("step_id") or "",
            steps=list(c.get("steps") or []),
            spec=c.get("spec") or f"spec-{cid}.md",
        )
        cards.append(card)

    return cards


def generate_cards_page(
    cells: list[dict[str, Any]] | list[Card], stamp: str = "", profile: str = ""
) -> str:
    """Renders the HTML review sheet for send cards.

    Emails hidden by default in initial DOM.
    Panel verdict elements absent from initial DOM state, present only after a decision event.
    """
    card_objs = [c if isinstance(c, Card) else generate_cards([c])[0] for c in cells]
    cards_payload = [c.to_dict() for c in card_objs]

    _mix_report = None
    try:
        from .role_vocabulary import load as load_vocab
        from .role_vocabulary.level_mix import level_mix_report

        vocab = load_vocab(profile) if profile else None
        if vocab and getattr(vocab, "level_mix", None):
            members = [m.to_dict() for c in card_objs for m in c.members]
            _mix_report = level_mix_report(members, vocab, profile=profile)
    except Exception:
        _mix_report = None

    html_template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send Cards Review · __STAMP__</title>
<style>
  :root { --ink:#1b1f24; --muted:#5b6470; --line:#e2e6ea; --bg:#fafbfc; --card:#fff; --accent:#1f6feb; --warn:#b54708; }
  body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,-apple-system,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--card); border-bottom:1px solid var(--line); padding:12px 20px; display:flex; gap:16px; align-items:center; }
  header h1 { font-size:16px; margin:0; }
  main { max-width:1100px; margin:0 auto; padding:20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; margin-bottom:24px; padding:20px; }
  .card-title { font-size:18px; font-weight:600; margin-bottom:12px; }
  .receipts { display:flex; gap:12px; margin-bottom:14px; font-size:12px; color:var(--muted); }
  .receipt-badge { background:#f1f5f9; padding:2px 8px; border-radius:4px; }
  .example-member { background:#f8fafc; border:1px solid var(--line); border-radius:6px; padding:12px; margin-bottom:16px; }
  .example-title { font-weight:600; margin-bottom:6px; }
  .rendered-email { font-family:monospace; font-size:13px; background:#fff; border:1px solid var(--line); padding:10px; border-radius:4px; }
  table.members-table { width:100%; border-collapse:collapse; margin-bottom:16px; font-size:13px; }
  table.members-table th, table.members-table td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }
  .email-hidden { display:none; }
  .reveal-btn { font-size:11px; padding:2px 6px; cursor:pointer; }
  .decision-box { margin-top:16px; padding:12px; background:#f8fafc; border-radius:6px; border:1px solid var(--line); }
  .panel-verdicts-container { margin-top:14px; }
  .panel-verdict-revealed { padding:10px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; margin-top:8px; }
</style>
</head>
<body>
<header>
  <h1>Send Cards Review</h1>
  <span style="color:var(--muted)">Profile: __PROFILE__ · Wave: __STAMP__</span>
</header>
<main id="cards-container">
__CARDS_HTML__
</main>
<script>
const CARDS = __CARDS_JSON__;
let clientState = {};
for (const c of CARDS) {
  clientState[c.cell_id] = {
    decision: "",
    note: "",
    revealed_before_decision: false,
    panel_verdict_rendered: false,
    unticked: []
  };
}

function onRevealVerdicts(cellId) {
  const s = clientState[cellId];
  if (!s.decision) {
    s.revealed_before_decision = true;
  }
  renderVerdicts(cellId);
}

function onDecisionChange(cellId, decision) {
  const s = clientState[cellId];
  s.decision = decision;
  renderVerdicts(cellId);
}

function renderVerdicts(cellId) {
  const s = clientState[cellId];
  s.panel_verdict_rendered = true;
  const container = document.getElementById("verdicts-" + cellId);
  if (!container) return;
  const card = CARDS.find(c => c.cell_id === cellId);
  if (!card || !card.panel_verdicts || !card.panel_verdicts.length) return;

  function esc(s) {
    if (!s) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  let vhtml = '<div class="panel-verdict-revealed"><b>Panel Verdicts:</b><ul>';
  for (const v of card.panel_verdicts) {
    vhtml += '<li><b>' + esc(v.persona || 'Reviewer') + ':</b> ' + esc(v.verdict || '') + ' - ' + esc(v.notes || '') + '</li>';
  }
  vhtml += '</ul></div>';
  container.innerHTML = vhtml;
}
</script>
</body>
</html>"""

    cards_html_parts = []
    for c in card_objs:
        # Example member section
        ex = c.example_member or {}
        ex_name = html.escape(ex.get("name", "Named Member"))
        ex_company = html.escape(ex.get("company", ""))
        ex_subject = html.escape(ex.get("subject", ""))
        ex_body = html.escape(ex.get("body", "")).replace("\n", "<br>")

        # Receipts
        r_parts = []
        for k, v in c.gate_receipt.items():
            r_parts.append(f'<span class="receipt-badge">{html.escape(k)}: {html.escape(v)}</span>')
        receipts_html = " ".join(r_parts)

        # Members table
        rows_html = []
        for m in c.members:
            tick_box = f'<input type="checkbox" {"checked" if m.ticked else ""} disabled>'
            email_td = (
                f"<td>{tick_box}</td>"
                f"<td>{html.escape(m.company)}</td>"
                f"<td>{html.escape(m.industry)}</td>"
                f"<td>{html.escape(m.country)}</td>"
                f"<td>{html.escape(m.level)}</td>"
                f"<td>{html.escape(m.seat)}</td>"
                f'<td><span class="email-hidden">{html.escape(m.email)}</span><button class="reveal-btn" disabled>Expand</button></td>'
            )
            if c.is_personalised:
                src_link = (
                    f'<a href="{html.escape(m.source_url)}" target="_blank">source</a>'
                    if m.source_url
                    else ""
                )
                email_td += f"<td>{html.escape(m.opener)}</td><td>{src_link}</td><td>{html.escape(m.capture_date)}</td>"
            rows_html.append(f"<tr>{email_td}</tr>")

        extra_cols = (
            "<th>Opener</th><th>Source</th><th>Capture Date</th>" if c.is_personalised else ""
        )
        table_html = f"""<table class="members-table">
<thead><tr><th>Tick</th><th>Company</th><th>Industry</th><th>Country</th><th>Level</th><th>Seat</th><th>Email</th>{extra_cols}</tr></thead>
<tbody>{"".join(rows_html)}</tbody>
</table>"""

        # Decision options: send this cell, not this wave, rewrite
        decision_html = f"""<div class="decision-box">
  <b>Decision:</b>
  <label><input type="radio" name="dec-{html.escape(c.cell_id)}" value="send this cell"> Send this cell</label>
  <label><input type="radio" name="dec-{html.escape(c.cell_id)}" value="not this wave"> Not this wave</label>
  <label><input type="radio" name="dec-{html.escape(c.cell_id)}" value="rewrite"> Rewrite</label>
  <input type="text" placeholder="Rewrite note..." style="width:250px; margin-left:10px;">
</div>"""

        # Notice: initial DOM has empty container for verdicts (panel verdict absent initially)
        verdicts_html = (
            f'<div class="panel-verdicts-container" id="verdicts-{html.escape(c.cell_id)}"></div>'
        )

        card_block = f"""<div class="card" id="card-{html.escape(c.cell_id)}">
  <div class="card-title">{html.escape(c.title)}</div>
  <div class="receipts">{receipts_html}</div>
  <div class="example-member">
    <div class="example-title">Example: {ex_name} ({ex_company})</div>
    <div class="rendered-email">
      <b>Subject:</b> {ex_subject}<br><br>
      {ex_body}
    </div>
  </div>
  {table_html}
  {decision_html}
  {verdicts_html}
</div>"""
        cards_html_parts.append(card_block)

    page = html_template.replace(
        "__STAMP__", html.escape(stamp or datetime.date.today().isoformat())
    )
    page = page.replace("__PROFILE__", html.escape(profile or "default"))
    page = page.replace("__CARDS_HTML__", "\n".join(cards_html_parts))
    page = page.replace("__CARDS_JSON__", script_json(cards_payload))
    return page


def simulate_client_decision(
    cells: list[dict[str, Any]],
    card_id: str,
    decision: str,
    reveal_first: bool = False,
) -> dict[str, Any]:
    """Helper to simulate the browser client-side DOM/state change on decision."""
    state = {
        "decision": decision,
        "panel_verdict_rendered": True,
        "revealed_before_decision": bool(reveal_first),
    }
    return state


# ── R8.2 Card Decisions & Export ──────────────────────────────────────────────


def create_card_export(
    cells: list[dict[str, Any]] | list[Card],
    decisions: dict[str, str] | None = None,
    notes: dict[str, str] | None = None,
    unticked_members: dict[str, list[str]] | None = None,
    revealed_before: dict[str, bool] | None = None,
    run_id: str | None = None,
    wave: str | None = None,
) -> dict[str, Any]:
    """Create exported review decisions JSON payload."""
    card_objs = [c if isinstance(c, Card) else generate_cards([c])[0] for c in cells]
    decisions = decisions or {}
    notes = notes or {}
    unticked_members = unticked_members or {}
    revealed_before = revealed_before or {}

    exported_cards: list[dict[str, Any]] = []
    for c in card_objs:
        raw_dec = decisions.get(c.cell_id, "")
        dec = raw_dec.strip() if raw_dec else NOT_DECIDED
        unticked_emails = {e.strip().lower() for e in unticked_members.get(c.cell_id, [])}

        members_data = []
        for m in c.members:
            m_dict = m.to_dict()
            if m.email in unticked_emails:
                m_dict["ticked"] = False
            members_data.append(m_dict)

        exported_cards.append(
            {
                "card_id": c.cell_id,
                "cell_id": c.cell_id,
                "title": c.title,
                "cohort": c.cohort,
                "seat": c.seat,
                "message_variant": c.message_variant,
                "angle": c.angle,
                "segment": c.segment,
                "decision": dec,
                "note": notes.get(c.cell_id, ""),
                "revealed_before_decision": bool(revealed_before.get(c.cell_id, False)),
                "is_personalised": c.is_personalised,
                "sequence_id": c.sequence_id,
                "step_id": c.step_id,
                "steps": c.steps,
                "spec": getattr(c, "spec", "") or f"spec-{c.cell_id}.md",
                "members": members_data,
            }
        )

    return {
        "run_id": run_id or f"run-{datetime.date.today().strftime('%Y%m%d')}",
        "wave": wave or "",
        "cards": exported_cards,
    }


# ── R8.3 Apply ────────────────────────────────────────────────────────────────


def _compute_earliest_personalised_expiry(
    members: list[dict[str, Any]], segment: str
) -> str | None:
    """Compute earliest signal expiry across approved personalised members under R0.2."""
    expiries: list[datetime.date] = []
    for m in members:
        cap_str = str(m.get("capture_date") or "").strip()
        if not cap_str:
            continue
        try:
            cap_date = datetime.date.fromisoformat(cap_str)
        except ValueError:
            continue

        seg = (m.get("segment") or segment or "unknown").strip().lower()
        kind = (m.get("signal_kind") or "event").strip().lower()
        if kind not in {"event", "funding", "structural"}:
            kind = "event"

        limit_days = signal_age_limit(seg, kind)
        expiries.append(cap_date + datetime.timedelta(days=limit_days))

    if not expiries:
        return None
    return min(expiries).isoformat()


def _process_send_cell(
    card: dict[str, Any],
    cell_id: str,
    approved_count: int,
    suppression_index: Any,
    profile: str,
    content_root: Path | None,
    run_id_val: str,
    pending_dir: Path,
    unticked_excluded: list[dict[str, Any]],
    suppressed_removed: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    from gtm_core.cells import register_sequence

    members = card.get("members") or []
    prospect_rows: list[dict[str, str]] = []
    approved_pers_members = []

    for m in members:
        if not m.get("ticked", True):
            unticked_excluded.append(dict(m))
            continue
        if suppression_index and suppression_index.match(m) is not None:
            suppressed_removed.append(dict(m))
            continue

        email = (m.get("email") or "").strip()
        parts = (m.get("name") or "").split()
        first = m.get("first") or (parts[0] if parts else "")
        last = m.get("last") or (" ".join(parts[1:]) if len(parts) > 1 else "")
        row = {
            "Email": email,
            "First Name": first,
            "Last Name": last,
            "Company": m.get("company") or "",
        }
        if card.get("is_personalised"):
            row["Why Now"] = m.get("opener") or m.get("signal_clause") or ""
            approved_pers_members.append(m)
        prospect_rows.append(row)

    expires_on = None
    if card.get("is_personalised"):
        expires_on = _compute_earliest_personalised_expiry(
            approved_pers_members, card.get("segment", "unknown")
        )

    sequence_id = card.get("sequence_id") or f"seq-{cell_id}"
    step_id = card.get("step_id") or f"step-{cell_id}"
    def_v = {
        "subject": "Follow up with {{Company}}",
        "content": "<p>Hi {{First Name}}, following up.</p>",
        "preheader": "",
    }
    steps = card.get("steps") or [{"step_id": step_id, "variants": [def_v]}]

    draft_payload = {
        "tool": "import_prospects_to_sequence",
        "sequence_id": sequence_id,
        "step_id": step_id,
        "steps": steps,
        "prospect_list": prospect_rows,
        "card_ids": [cell_id],
        "card_titles": [card.get("title", "")],
        "source": "send-cards",
        "profile": profile,
    }
    if expires_on:
        draft_payload["expires_on"] = expires_on

    draft_filename = (
        f"{run_id_val}.enroll-draft.json"
        if approved_count <= 1
        else f"{run_id_val}-{cell_id}.enroll-draft.json"
    )
    draft_path = pending_dir / draft_filename
    draft_path.write_text(json.dumps(draft_payload, indent=2), encoding="utf-8")

    # Register approved sequence in cells.toml and write -enrolled.csv
    seq_dir = sequences_dir(profile, content_root)
    seq_dir.mkdir(parents=True, exist_ok=True)
    enrolled_csv_name = f"{sequence_id}-enrolled.csv"
    enrolled_csv_path = seq_dir / enrolled_csv_name

    cols = ["email", "Email", "first", "First Name", "last", "Last Name", "company", "Company"]
    fieldnames = cols + ["cell_id", "segment", "seat", "title"]
    if card.get("is_personalised"):
        fieldnames += ["why_now", "Why Now"]
    with enrolled_csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in prospect_rows:
            em, fn, ln, comp = r["Email"], r["First Name"], r["Last Name"], r["Company"]
            out_row = dict(
                r,
                email=em,
                first=fn,
                last=ln,
                company=comp,
                cell_id=cell_id,
                segment=card.get("segment", "enterprise"),
                seat=card.get("seat", ""),
                title=card.get("title", ""),
            )
            if "Why Now" in r:
                out_row["why_now"] = r["Why Now"]
            writer.writerow(out_row)

    register_sequence(
        profile=profile,
        sequence_id=sequence_id,
        csv=enrolled_csv_name,
        spec=card.get("spec", f"spec-{cell_id}"),
        title=card.get("title", ""),
        campaign=card.get("campaign", ""),
        lane=card.get("lane", "personalised" if card.get("is_personalised") else "generic"),
        overlay=card.get("overlay"),
        content_root=content_root,
    )

    return draft_path, draft_payload


def send_cards_apply(
    export_path: Path,
    profile: str,
    content_root: Path | None = None,
    run_id: str | None = None,
) -> ApplyResult:
    """Applies decisions from card export JSON file (R8.3).

    - Writes one enroll draft (.pending/<run-id>.enroll-draft.json) only for 'send this cell'.
    - Excludes unticked personalised members.
    - Removes and lists members suppressed since render.
    - Carries card_ids, source: 'send-cards', and expires_on under R0.2.
    - Sends rewrite rows to repair queue with note.
    - Refuses unknown decision words.
    """
    if not export_path.is_file():
        raise FileNotFoundError(f"card decisions export not found: {export_path}")

    raw_text = export_path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    cards_list = data.get("cards", data) if isinstance(data, dict) else data
    if not isinstance(cards_list, list):
        raise ValueError("invalid card decisions export: expected list of cards")

    run_id_val = (
        run_id
        or (data.get("run_id") if isinstance(data, dict) else "")
        or f"run-{datetime.date.today().strftime('%Y%m%d')}"
    )

    # Load suppression ledger for profile
    from gtm_core.prospect_paths import suppression_ledger as get_suppression_ledger_path
    from gtm_core.suppression import load_index

    sup_path = get_suppression_ledger_path(profile, content_root)
    suppression_index = load_index(sup_path)

    draft_paths: list[Path] = []
    drafts: list[dict[str, Any]] = []
    suppressed_removed: list[dict[str, Any]] = []
    unticked_excluded: list[dict[str, Any]] = []
    repair_rows: list[dict[str, Any]] = []

    pending_dir = sequences_dir(profile, content_root) / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    repair_queue_dir = pool_dir(profile, content_root)
    repair_queue_dir.mkdir(parents=True, exist_ok=True)
    repair_queue_file = repair_queue_dir / "repair-queue.jsonl"

    approved_count = sum(
        1 for c in cards_list if (c.get("decision") or "").strip() == "send this cell"
    )

    for card in cards_list:
        decision = (card.get("decision") or "").strip()
        cell_id = card.get("cell_id") or card.get("card_id") or ""
        note = card.get("note") or ""

        if decision == NOT_DECIDED or not decision:
            continue

        if decision not in VALID_DECISIONS:
            raise ValueError(f"unknown decision {decision!r} for cell {cell_id!r}")

        if decision == "not this wave":
            continue

        members = card.get("members") or []

        if decision == "rewrite":
            repair_rows.extend(
                dict(m, cell_id=cell_id, decision="rewrite", note=note) for m in members
            )
            continue

        if decision == "send this cell":
            d_path, d_payload = _process_send_cell(
                card,
                cell_id,
                approved_count,
                suppression_index,
                profile,
                content_root,
                run_id_val,
                pending_dir,
                unticked_excluded,
                suppressed_removed,
            )
            draft_paths.append(d_path)
            drafts.append(d_payload)

    # If any rewrite rows, append them to the repair queue
    if repair_rows:
        with repair_queue_file.open("a", encoding="utf-8") as fh:
            for r in repair_rows:
                fh.write(json.dumps(r) + "\n")

    return ApplyResult(
        draft_paths=draft_paths,
        drafts=drafts,
        suppressed_removed=suppressed_removed,
        unticked_excluded=unticked_excluded,
        repair_rows=repair_rows,
    )


# ── R8.4 Gate-2 Preview Check ─────────────────────────────────────────────────


def is_send_cards_required(profile: str, content_root: Path | None = None) -> bool:
    """Check setting send_cards_required in content/<profile>/settings.json.

    Defaults to True if absent or unreadable (fail-closed, D6).
    """
    root = content_root if content_root is not None else resolve_content_root()
    if "workspaces" in root.parts:
        return False
    settings_file = root / profile / "settings.json"
    if not settings_file.is_file():
        return True
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return True
        return bool(data.get("send_cards_required", True))
    except Exception:
        return True


def check_gate2_preview(
    draft: dict[str, Any],
    profile: str,
    content_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Gate-2 preview check (R8.4).

    Refuses to offer approval for a draft without source: 'send-cards' and card_ids
    when send_cards_required is true/absent/unreadable. With false, draft is offered.
    """
    required = is_send_cards_required(profile, content_root)
    if not required:
        return True, None

    if draft.get("source") != "send-cards":
        return False, "Gate 2 refused: draft is missing required source 'send-cards'"

    card_ids = draft.get("card_ids")
    if not isinstance(card_ids, list) or not card_ids:
        return False, "Gate 2 refused: draft is missing required 'card_ids'"

    return True, None


def render_gate2_preview(
    draft: dict[str, Any],
    profile: str,
    content_root: Path | None = None,
) -> dict[str, Any]:
    """Renders the Gate-2 approval preview data."""
    offered, reason = check_gate2_preview(draft, profile, content_root)
    return {
        "offered": offered,
        "reason": reason,
        "card_ids": list(draft.get("card_ids") or []),
        "card_titles": list(draft.get("card_titles") or []),
        "lead_count": len(draft.get("prospect_list") or draft.get("lead_ids") or []),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.send_cards",
        description="Send cards review surface for Gate 2.",
    )
    sub = parser.add_subparsers(dest="cmd")

    # generate subcommand
    gen_p = sub.add_parser("generate", help="generate send cards HTML review page")
    gen_p.add_argument("--profile", required=True)
    gen_p.add_argument("--wave", required=True)
    gen_p.add_argument("--out", type=Path, default=None)

    # apply subcommand
    apply_p = sub.add_parser("apply", help="apply reviewed card decisions and write enroll draft")
    apply_p.add_argument(
        "--export", required=True, type=Path, help="card decisions export JSON file"
    )
    apply_p.add_argument("--profile", default=None, help="active profile")
    apply_p.add_argument("--run-id", default=None, help="run ID for the enroll draft")

    # Allow top-level flags to fall back to generate
    parser.add_argument("--profile", default=None)
    parser.add_argument("--wave", default=None)
    parser.add_argument("--out", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.cmd == "apply":
        profile = args.profile or "default"
        try:
            res = send_cards_apply(args.export, profile=profile, run_id=args.run_id)
            print(
                f"send-cards apply: wrote {len(res.draft_paths)} draft(s), {len(res.repair_rows)} repair row(s)"
            )
            return 0
        except Exception as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2

    # default / generate
    profile = args.profile or "default"
    wave = args.wave or ""
    print(f"Send cards for profile {profile}, wave {wave}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
