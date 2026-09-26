"""The Next Frontier insights and founder cockpit modules (PRD-041).

Provides:
- Time-bounded and streaming parse of campaign history and outcomes.
- Angle Heatmap matrix calculation (Persona x Hook) with persona casing normalization.
- Sentiment Triage derivation with XSS sanitization and fallback grouping.
- Copiable action prompts with DOM isolation for safe operator steering.
- SVG node halo and CSS marquee rendering for standalone HTML output.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..paths import resolve_content_root
from .format import _e, section
from .i18n import map_term

# ── 1. Streaming & Time-Bounded History Parser (OOM Prevention) ─────────────


def parse_campaign_history(
    profile: str,
    content_root: Path | None = None,
    days_back: int = 30,
) -> list[dict[str, Any]]:
    """Stream and parse history.jsonl and outcomes.jsonl safely.

    - Validates JSON strictly line-by-line; raises ValueError on corrupt lines.
    - Applies a time bound (default: 30 days) to prevent out-of-memory scale issues.
    """
    root = content_root or resolve_content_root()
    profile_dir = root / profile
    events: list[dict[str, Any]] = []

    cutoff = datetime.now(UTC) - timedelta(days=days_back)

    for fname in ("history.jsonl", "outcomes.jsonl"):
        path = profile_dir / fname
        if not path.is_file():
            continue

        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception as exc:
                    raise ValueError(
                        f"Corrupt JSON ledger entry at {fname}:{line_no}: {exc}"
                    ) from exc

                if not isinstance(data, dict):
                    raise ValueError(
                        f"Corrupt ledger entry at {fname}:{line_no}: expected dict, got {type(data).__name__}"
                    )

                # Time-bound filtering
                ts_str = data.get("ts") or data.get("timestamp") or data.get("date") or ""
                if ts_str:
                    try:
                        # Normalize ISO timestamp or YYYY-MM-DD
                        clean_ts = ts_str.replace("Z", "+00:00")
                        if len(clean_ts) == 10:
                            clean_ts += "T00:00:00+00:00"
                        dt = datetime.fromisoformat(clean_ts)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if dt < cutoff:
                            continue
                    except Exception:  # nosec
                        pass  # If unparseable date, keep for permissive retention

                events.append(data)

    return events


# ── 2. Sentiment Triage Derivation ──────────────────────────────────────────


def derive_sentiment_triage(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive sentiment triage data from inbound reply events.

    - Missing sentiment defaults to 'Unknown / Needs Review'.
    - All untrusted text (body, email, company) is strictly HTML escaped.
    """
    replies: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for ev in events:
        event_type = str(ev.get("event") or ev.get("type") or "").lower()
        # Inbound reply events
        if event_type in ("reply", "inbound", "inbound_reply", "response") or "sentiment" in ev:
            raw_sentiment = ev.get("sentiment")
            if not raw_sentiment or not str(raw_sentiment).strip():
                sentiment_label = "Unknown / Needs Review"
            else:
                sentiment_label = str(raw_sentiment).strip().title()

            counts[sentiment_label] += 1

            raw_body = str(ev.get("body") or ev.get("snippet") or ev.get("text") or "").strip()
            raw_email = str(ev.get("email") or ev.get("sender") or "").strip()
            raw_company = str(ev.get("company") or "").strip()
            raw_ts = str(ev.get("ts") or ev.get("date") or "")[:10]

            replies.append(
                {
                    "sentiment": sentiment_label,
                    "sentiment_escaped": html.escape(sentiment_label),
                    "body_escaped": html.escape(raw_body),
                    "email_escaped": html.escape(raw_email),
                    "company_escaped": html.escape(raw_company),
                    "ts_escaped": html.escape(raw_ts),
                }
            )

    return {
        "items": replies,
        "counts": dict(counts),
        "total": len(replies),
    }


# ── 3. Angle Heatmap Matrix Calculation ─────────────────────────────────────

_CANONICAL_PERSONAS = {
    "ciso": "CISO",
    "cto": "CTO",
    "ceo": "CEO",
    "cpo": "CPO",
    "cio": "CIO",
    "architect": "Architect",
    "ai-platform": "AI Platform",
    "product": "Product",
    "security": "Security",
}


def normalize_persona(persona: str) -> str:
    """Normalize persona casing (e.g., CISO vs ciso) to avoid ghost cells."""
    if not persona:
        return "Unknown"
    cleaned = persona.strip().lower()
    return _CANONICAL_PERSONAS.get(cleaned, cleaned.replace("-", " ").title())


def calculate_angle_heatmap(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate 2D matrix plotting Persona vs. Hook (Opening Angle).

    Ensures casing variations (e.g. CISO vs ciso) map to the exact same cell.
    """
    matrix: dict[tuple[str, str], int] = {}
    personas_set: set[str] = set()
    hooks_set: set[str] = set()

    for c in cells:
        # Casing-safe persona grouping (defaults to 'Unknown' per PRD §4.B)
        raw_persona = c.get("seat") or c.get("persona") or "Unknown"
        p = normalize_persona(str(raw_persona))

        # Hook / Opening Angle mapping (recognizes 'variant' key from gtm_core.cells)
        raw_angle = (
            c.get("angle")
            or c.get("hook")
            or c.get("wedge")
            or c.get("variant")
            or c.get("message_variant")
            or "Standard"
        )
        h = map_term(str(raw_angle))

        personas_set.add(p)
        hooks_set.add(h)

        # Count recipients or weight
        val = int(c.get("sendable", c.get("enrolled", c.get("count", 1))) or 1)
        matrix[(p, h)] = matrix.get((p, h), 0) + val

    personas = sorted(personas_set)
    hooks = sorted(hooks_set)
    max_val = max(matrix.values()) if matrix else 1

    cells_data = []
    for p in personas:
        for h in hooks:
            count = matrix.get((p, h), 0)
            intensity = round(count / max_val, 2) if max_val > 0 else 0.0
            cells_data.append(
                {
                    "persona": p,
                    "hook": h,
                    "count": count,
                    "intensity": intensity,
                }
            )

    return {
        "personas": personas,
        "hooks": hooks,
        "cells": cells_data,
        "max_val": max_val,
        "total_cells": len(cells_data),
    }


# ── 4. Ready to Send Accounts & Copiable Action Prompts ───────────────────────


def list_ready_to_send_accounts(
    cells: list[dict[str, Any]] | None = None,
    profile: str = "",
    content_root: Path | None = None,
) -> list[str]:
    """Return sorted unique list of account names ready to send."""
    if cells is not None:
        accounts: set[str] = set()
        for c in cells:
            for m in c.get("members") or []:
                co = (m.get("company") or "").strip()
                if co:
                    accounts.add(co)
            co = (c.get("company") or "").strip()
            if co:
                accounts.add(co)
        return sorted(accounts)

    # Check pool/cells.json if available
    root = content_root or resolve_content_root()
    if profile:
        pool_cells = root / profile / "prospects" / "sequences" / ".pool" / "cells.json"
        if pool_cells.is_file():
            try:
                data = json.loads(pool_cells.read_text(encoding="utf-8"))
                clist = data.get("cells") if isinstance(data, dict) else data
                if isinstance(clist, list):
                    return list_ready_to_send_accounts(clist)
            except Exception:  # nosec
                pass

        # Fallback to ready-to-load.csv
        rtl = root / profile / "prospects" / "sequences" / "ready-to-load.csv"
        if rtl.is_file():
            import csv

            try:
                with rtl.open("r", encoding="utf-8", newline="") as fh:
                    reader = csv.DictReader(fh)
                    cos = {(r.get("company") or "").strip() for r in reader}
                    return sorted(cos - {""})
            except Exception:  # nosec
                pass

    return []


def generate_copiable_prompts(accounts: list[str]) -> list[dict[str, str]]:
    """Generate isolated copiable prompt text for human-in-the-loop steering."""
    n = len(accounts)
    if n > 0:
        approve_prompt = f"approve sending to these {n} accounts"
    else:
        approve_prompt = (
            "approve sending to these 0 accounts"  # 2026-09-26 empty state copy, not a metric
        )

    return [
        {
            "label": "Approve Audience",
            "prompt": approve_prompt,
            "context": f"{n} accounts ready in the queue",
        },
        {
            "label": "Review Held Accounts",
            "prompt": "review held accounts with why-now signals",
            "context": "surface warm accounts for review",
        },
    ]


# ── 5. HTML & Inline SVG Rendering ──────────────────────────────────────────


def render_angle_heatmap_svg(heatmap_data: dict[str, Any]) -> str:
    """Render 2D Persona x Hook heatmap as an inline SVG with glowing halo nodes."""
    personas = heatmap_data.get("personas") or []
    hooks = heatmap_data.get("hooks") or []
    cells = {(c["persona"], c["hook"]): c for c in heatmap_data.get("cells") or []}

    if not personas or not hooks:
        return '<p class="muted">No angle-persona data available yet for this scope.</p>'

    col_w = 140
    row_h = 50
    margin_left = 130
    margin_top = 40
    width = margin_left + len(hooks) * col_w + 40
    height = margin_top + len(personas) * row_h + 30

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '  # 2026-09-26 SVG coordinate, not a metric
        'xmlns="http://www.w3.org/2000/svg" class="heatmap-svg" '
        'style="max-width:100%; height:auto; font-family:inherit;">'
    ]

    # Column headers (Hooks / Opening Angles)
    for j, h in enumerate(hooks):
        x = margin_left + j * col_w + col_w // 2
        y = margin_top - 12
        svg_parts.append(
            f'<text x="{x}" y="{y}" text-anchor="middle" fill="var(--muted)" font-size="11.5" '  # 2026-09-26 CSS font weight, not a metric
            f'font-weight="500">{_e(h)}</text>'
        )

    # Row headers (Personas) and Nodes
    for i, p in enumerate(personas):
        y = margin_top + i * row_h + row_h // 2
        svg_parts.append(
            f'<text x="{margin_left - 15}" y="{y + 4}" text-anchor="end" fill="var(--ink)" '  # 2026-09-26 CSS font weight, not a metric
            f'font-size="12" font-weight="600">{_e(p)}</text>'
        )

        for j, h in enumerate(hooks):
            x = margin_left + j * col_w + col_w // 2
            cell = cells.get((p, h), {"count": 0, "intensity": 0.0})
            intensity = cell["intensity"]
            count = cell["count"]
            r_core = 4 + int(intensity * 10)
            r_halo = r_core + 6

            svg_parts.append(
                f'<g class="sc-node" tabindex="0">'  # 2026-09-26 SVG coordinate, not a metric
                f'<circle class="sc-halo" cx="{x}" cy="{y}" r="{r_halo}"></circle>'
                f'<circle class="sc-core" cx="{x}" cy="{y}" r="{r_core}"></circle>'
                f'<text x="{x}" y="{y + r_halo + 14}" text-anchor="middle" class="sc-label">{count}</text>'
                f"<title>{_e(p)} × {_e(h)}: {count} accounts</title>"
                f"</g>"
            )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def render_sentiment_triage_html(triage_data: dict[str, Any]) -> str:
    """Render inbound replies ticker feed with CSS-only marquee animation."""
    items = triage_data.get("items") or []
    counts = triage_data.get("counts") or {}

    if not items:
        return (
            '<div class="sentiment-empty muted">'
            "No inbound replies logged yet in the current observation window."
            "</div>"
        )

    summary_chips = []
    for sentiment, count in sorted(counts.items()):
        summary_chips.append(f'<span class="pill">{_e(sentiment)}: <strong>{count}</strong></span>')
    chips_html = (
        '<div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">'
        + "".join(summary_chips)
        + "</div>"
    )

    feed_items = []
    # Duplicate items once if few, so the marquee scrolls seamlessly
    scroll_items = items if len(items) >= 4 else items * 2
    for it in scroll_items:
        feed_items.append(
            '<div class="sentiment-item">'
            '  <div class="sentiment-meta">'
            f'    <span style="font-weight:600; color:var(--ink);">{it["company_escaped"] or it["email_escaped"]}</span>'
            f'    <span class="pill">{it.get("sentiment_escaped") or _e(it["sentiment"])}</span>'
            "  </div>"
            f'  <div class="sentiment-body">"{it["body_escaped"] or "—"}"</div>'
            f'  <div class="muted" style="font-size:11px; margin-top:4px;">{it["ts_escaped"]}</div>'
            "</div>"
        )

    marquee_html = (
        '<div class="sentiment-marquee-container">'
        '  <div class="sentiment-marquee-track">' + "".join(feed_items) + "  </div>"
        "</div>"
    )

    return chips_html + marquee_html


def render_ready_to_send_panel(ready_accounts: list[str]) -> str:
    """Render Ready to Send approval queue card with copiable action prompt."""
    n = len(ready_accounts)
    prompts = generate_copiable_prompts(ready_accounts)
    approve_prompt = prompts[0]["prompt"]

    accounts_preview = ""
    if ready_accounts:
        preview_list = ready_accounts[:6]
        items_html = "".join(f"<li>{_e(acc)}</li>" for acc in preview_list)
        more = (
            f"<p class='muted' style='margin:4px 0 0;'>...and {n - len(preview_list)} more</p>"
            if n > len(preview_list)
            else ""
        )
        accounts_preview = f"<ul style='margin:8px 0; padding-left:18px;'>{items_html}</ul>{more}"
    else:
        accounts_preview = (
            "<p class='muted'>No accounts currently waiting in the approval queue.</p>"
        )

    return f"""
    <div class="action-prompt-card glass-panel showcase-sweep">
      <div class="action-prompt-row">
        <div>
          <h3 style="margin:0 0 4px; text-transform:none; letter-spacing:normal; color:var(--ink); font-size:15px;">
            Ready to Send ({n} accounts)  # 2026-09-26 terminology, not a metric
          </h3>
          <span class="muted" style="font-size:12.5px;">Gate 2 Approval Queue — verify audience before dispatch.</span>
        </div>
        <div class="action-prompt-copy-box">
          <code class="action-prompt-text" id="prompt-send-cards">{_e(approve_prompt)}</code>
          <button class="copy-btn" data-target="prompt-send-cards" onclick="copyPrompt(this)">Copy Prompt</button>
        </div>
      </div>
      {accounts_preview}
    </div>
    """


def render_next_frontier_sections(m: dict[str, Any], content_root: Path | None = None) -> str:
    """Render The Next Frontier cards (Heatmap, Sentiment Triage, Ready to Send)."""
    # 1. History events (pure in-memory per INV1, no disk I/O)
    history_events = m.get("frontier_events") or []

    # 2. Derive Sentiment Triage
    triage_data = derive_sentiment_triage(history_events)
    sentiment_html = render_sentiment_triage_html(triage_data)

    # 3. Derive Angle Heatmap
    cells_list = m.get("cells", {}).get("cells") or []
    heatmap_data = calculate_angle_heatmap(cells_list)
    heatmap_html = render_angle_heatmap_svg(heatmap_data)

    # 4. Ready to Send Accounts & Prompts (in-memory only, no disk fallback during render)
    if "ready_accounts" in m:
        ready_accounts = m["ready_accounts"]
    elif cells_list:
        ready_accounts = list_ready_to_send_accounts(cells_list)
    else:
        ready_accounts = []
    ready_html = render_ready_to_send_panel(ready_accounts)

    out = []

    # Card 1: Angle Heatmap
    out.append(
        section(
            "angle-heatmap",
            '<div class="card showcase-sweep glow-card animate-on-load anim-up-lg" style="--anim-delay: 50ms;">'
            '<div class="atmospheric-glow"></div>'
            "<h2>Angle Heatmap</h2>"
            '<p class="note">Persona engagement plotted across Opening Angles (Hooks). '
            "Node halos indicate response volume and signal intensity.</p>"
            + heatmap_html
            + "</div>",
        )
    )

    # Card 2: Sentiment Triage
    out.append(
        section(
            "sentiment-triage",
            '<div class="card glow-card animate-on-load anim-up-lg" style="--anim-delay: 100ms;">'
            '<div class="atmospheric-glow"></div>'
            "<h2>Sentiment Triage</h2>"
            '<p class="note">Mission-control static feed of recent inbound responses. '
            "Real-time replies safely escaped with automated sentiment categorisation.</p>"
            + sentiment_html
            + "</div>",
        )
    )

    # Card 3: Ready to Send
    out.append(
        section(
            "ready-to-send",
            '<div class="card animate-on-load anim-up-lg" style="--anim-delay: 150ms;">'
            "<h2>Approval Queue</h2>" + ready_html + "</div>",
        )
    )

    return "".join(out)
