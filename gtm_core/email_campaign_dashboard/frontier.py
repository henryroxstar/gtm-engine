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
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..outcomes import REPLY_OUTCOMES
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
        # An outcomes.jsonl row records a reply as `"outcome": "reply"` (gtm_core.outcomes.
        # REPLY_OUTCOMES), never as `"event": "reply"` — the history.jsonl vocabulary this
        # used to check against alone. On a live tenant profile history.jsonl carries no
        # reply-shaped event at all, so this always read "no replies" even with replies on
        # record in outcomes.jsonl.
        outcome = str(ev.get("outcome") or "").lower()
        is_reply = (
            event_type in ("reply", "inbound", "inbound_reply", "response")
            or outcome in REPLY_OUTCOMES
            or "sentiment" in ev
        )
        if is_reply:
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


def extract_angle_title(title: str, variant: str = "") -> str:
    """Extract clean narrative opening angle title from sequence title or fallback variant slug."""
    if not title:
        parts = variant.replace("::", " - ").replace("-", " ").split()
        return " ".join(parts).title() if parts else "Standard"
    parts = [p.strip() for p in title.split("·")]
    if len(parts) >= 3:
        return parts[1]
    if len(parts) == 2:
        m = re.search(r"\((.*?)\)", parts[0])
        return m.group(1) if m else parts[0]
    return title.strip()


def calculate_angle_heatmap(
    cells: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate 2D matrix plotting Persona vs. Hook (Opening Angle).

    Ensures casing variations (e.g. CISO vs ciso) map to the exact same cell.
    Resolves human-readable narrative opening angles when sequence sources are provided.
    """
    matrix: dict[tuple[str, str], int] = {}
    personas_set: set[str] = set()
    hooks_set: set[str] = set()
    total_replies = 0

    src_map: dict[str, dict[str, Any]] = {}
    if isinstance(sources, dict):
        src_map = sources
    elif isinstance(sources, list):
        src_map = {
            s.get("sequence_id", ""): s
            for s in sources
            if isinstance(s, dict) and s.get("sequence_id")
        }

    for c in cells:
        raw_persona = c.get("seat") or c.get("persona") or "Unknown"
        p = normalize_persona(str(raw_persona))

        sid = c.get("sequence_id")
        src = src_map.get(sid) if sid else None
        if src and src.get("title"):
            h = extract_angle_title(src["title"], str(c.get("variant") or ""))
        else:
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

        # `cells.build_cells` names this key "replied" — every live cell carries it and none
        # carry "replies"/"inbound_count", so this always read 0 total replies and the matrix
        # metric never switched from "Planned Prospects" to "Replies".
        replies = int(c.get("replied") or 0)
        total_replies += replies
        # A genuinely empty cell (sendable == 0, e.g. every recipient suppressed) must stay 0,
        # not become 1 — `or 1` on the whole expression turned a real zero into a phantom
        # planned prospect.
        val = c.get("sendable")
        if val is None:
            val = c.get("enrolled", c.get("count", 0))
        matrix[(p, h)] = matrix.get((p, h), 0) + int(val or 0)

    personas = sorted(personas_set)
    hooks = sorted(hooks_set)
    max_val = max(matrix.values()) if matrix else 1
    has_replies = total_replies > 0
    metric_label = "Replies" if has_replies else "Planned Prospects"

    cells_data = [
        {
            "persona": p,
            "hook": h,
            "count": matrix.get((p, h), 0),
            "intensity": round(matrix.get((p, h), 0) / max_val, 2) if max_val > 0 else 0.0,
            "metric_label": metric_label,
        }
        for p in personas
        for h in hooks
    ]

    return {
        "personas": personas,
        "hooks": hooks,
        "cells": cells_data,
        "max_val": max_val,
        "total_cells": len(cells_data),
        "has_replies": has_replies,
        "metric_label": metric_label,
    }


# ── 4. Ready to Send Accounts & Copiable Action Prompts ───────────────────────


def list_ready_to_send_accounts(
    cells: list[dict[str, Any]] | None = None,
    profile: str = "",
    content_root: Path | None = None,
) -> list[str]:
    """Account names named on a cell's ``members``/``company`` field, if any carry one.

    ``cells.build_cells`` cells carry neither key today — enrolment is counted, not
    listed by member — so this always returns ``[]`` on live data, and the caller
    (``render_ready_to_send_section``) correctly renders nothing rather than an empty
    panel. It used to fall back to reading ``.pool/cells.json`` (not tracked by
    ``--check-fresh``'s ``INPUT_GLOBS``, a stale-read hazard) and then
    ``ready-to-load.csv`` (every company in the pool, with no verdict or lane filter —
    "approve sending to these N accounts" over a population nobody vetted). Both
    fallbacks are removed rather than fixed: inventing a population here is worse than
    an empty panel, and a real "what's drafted and awaiting approval" view belongs on
    the roster's own classification (``views_accounts``), not a second one here.
    ``profile``/``content_root`` are kept for the existing call signature.
    """
    accounts: set[str] = set()
    for c in cells or []:
        accounts.update((m.get("company") or "").strip() for m in (c.get("members") or []))
        if co := (c.get("company") or "").strip():
            accounts.add(co)
    return sorted(accounts - {""})


def generate_copiable_prompts(accounts: list[str]) -> list[dict[str, str]]:
    """Generate isolated copiable prompt text for human-in-the-loop steering."""
    n = len(accounts)
    approve_prompt = f"approve sending to these {n} accounts"
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


def render_angle_heatmap_svg(heatmap_data: dict[str, Any], svg_id: str = "") -> str:
    """Render 2D Persona x Hook heatmap as an inline SVG with glowing halo nodes."""
    personas = heatmap_data.get("personas") or []
    hooks = heatmap_data.get("hooks") or []
    cells = {(c["persona"], c["hook"]): c for c in heatmap_data.get("cells") or []}
    metric = (heatmap_data.get("metric_label") or "planned prospects").lower()

    if not personas or not hooks:
        return '<p class="muted">No angle-persona data available yet for this scope.</p>'

    col_w = 170 if len(hooks) <= 20 else 150
    row_h, margin_left = 56, 160
    margin_top = 135 if len(hooks) <= 20 else 100
    width = margin_left + len(hooks) * col_w + 60
    height = margin_top + len(personas) * row_h + 30
    id_attr = f' id="{svg_id}"' if svg_id else ""
    vx = vy = 0

    svg_parts = [
        f'<svg{id_attr} viewBox="{vx} {vy} {width} {height}" width="{width}" height="{height}" '
        'xmlns="http://www.w3.org/2000/svg" class="heatmap-svg" '
        f'style="min-width:{width}px; height:auto; font-family:inherit;">'
    ]

    for i in range(len(personas) + 1):
        gy = margin_top + i * row_h
        svg_parts.append(
            f'<line x1="{margin_left}" y1="{gy}" x2="{width - 40}" y2="{gy}" style="stroke:var(--line); stroke-width:1px; stroke-dasharray:2px 4px;" />'
        )
    for j in range(len(hooks) + 1):
        gx = margin_left + j * col_w
        svg_parts.append(
            f'<line x1="{gx}" y1="{margin_top}" x2="{gx}" y2="{height - 30}" style="stroke:var(--line); stroke-width:1px; stroke-dasharray:2px 4px;" />'
        )

    for j, h in enumerate(hooks):
        x = margin_left + j * col_w + col_w // 2
        svg_parts.append(
            f'<text x="{x}" y="{margin_top - 16}" transform="rotate(-35, {x}, {margin_top - 16})" text-anchor="start" fill="var(--muted)" style="font-size:11.5px; font-weight:500;">{_e(h)}</text>'
        )

    for i, p in enumerate(personas):
        y = margin_top + i * row_h + row_h // 2
        svg_parts.append(
            f'<text x="{margin_left - 18}" y="{y + 4}" text-anchor="end" fill="var(--ink)" style="font-size:12px; font-weight:600;">{_e(p)}</text>'
        )
        for j, h in enumerate(hooks):
            x = margin_left + j * col_w + col_w // 2
            cell = cells.get((p, h), {"count": 0, "intensity": 0.0})
            intensity = cell["intensity"]
            count = cell["count"]
            r_core = 4 + int(intensity * 10)
            r_halo = r_core + 6
            svg_parts.append(
                f'<g class="sc-node">'
                f'<circle class="sc-halo" cx="{x}" cy="{y}" r="{r_halo}"></circle>'
                f'<circle class="sc-core" cx="{x}" cy="{y}" r="{r_core}"></circle>'
                f'<text x="{x}" y="{y + r_halo + 14}" text-anchor="middle" class="sc-label">{count}</text>'
                f"<title>{_e(p)} × {_e(h)}: {count} {metric}</title>"
                f"</g>"
            )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def render_sentiment_triage_html(triage_data: dict[str, Any]) -> str:
    """Render inbound replies ticker feed with CSS-only marquee animation."""
    items = triage_data.get("items") or []
    counts = triage_data.get("counts") or {}
    if not items:
        return '<div class="sentiment-empty muted">No inbound replies logged yet in the current observation window.</div>'

    summary_chips = [
        f'<span class="pill">{_e(s)}: <strong>{c}</strong></span>'
        for s, c in sorted(counts.items())
    ]
    chips_html = f'<div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">{"".join(summary_chips)}</div>'

    scroll_items = items if len(items) >= 4 else items * 2
    feed_items = [
        '<div class="sentiment-item">'
        f'<div class="sentiment-meta"><span style="font-weight:600; color:var(--ink);">{it["company_escaped"] or it["email_escaped"]}</span>'
        f'<span class="pill">{it.get("sentiment_escaped") or _e(it["sentiment"])}</span></div>'
        f'<div class="sentiment-body">"{it["body_escaped"] or "—"}"</div>'
        f'<div class="muted" style="font-size:11px; margin-top:4px;">{it["ts_escaped"]}</div></div>'
        for it in scroll_items
    ]
    return (
        chips_html
        + f'<div class="sentiment-marquee-container"><div class="sentiment-marquee-track">{"".join(feed_items)}</div></div>'
    )


def render_ready_to_send_panel(ready_accounts: list[str]) -> str:
    """Render Ready to Send approval queue card with copiable action prompt."""
    n = len(ready_accounts)
    prompts = generate_copiable_prompts(ready_accounts)
    approve_prompt = prompts[0]["prompt"]

    accounts_preview = (
        f"<ul style='margin:8px 0; padding-left:18px;'>{''.join(f'<li>{_e(acc)}</li>' for acc in ready_accounts[:6])}</ul>"
        + (f"<p class='muted' style='margin:4px 0 0;'>...and {n - 6} more</p>" if n > 6 else "")
        if ready_accounts
        else "<p class='muted'>No accounts currently waiting in the approval queue.</p>"
    )

    return f"""
    <div class="action-prompt-card glass-panel showcase-sweep">
      <div class="action-prompt-row">
        <div>
          <h3 style="margin:0 0 4px; text-transform:none; letter-spacing:normal; color:var(--ink); font-size:15px;">Ready to Send ({n} accounts)</h3>
          <span class="muted" style="font-size:12.5px;">Gate-2 Approval Queue — verify audience before dispatch.</span>
        </div>
        <div class="action-prompt-copy-box">
          <code class="action-prompt-text" id="prompt-send-cards">{_e(approve_prompt)}</code>
          <button class="copy-btn" data-target="prompt-send-cards" onclick="copyPrompt(this)">Copy Prompt</button>
        </div>
      </div>
      {accounts_preview}
    </div>
    """


def render_angle_heatmap_section(m: dict) -> str:
    cells_list = m.get("cells", {}).get("cells") or []
    sources = m.get("cells", {}).get("sources") or []
    campaigns = m.get("campaigns", {}).get("campaigns", [])
    active_seq_ids = {
        s["sequence_id"] for c in campaigns for s in c.get("sequences", []) if s.get("sequence_id")
    }

    active_cells = (
        [c for c in cells_list if c.get("sequence_id") in active_seq_ids]
        if active_seq_ids
        else cells_list
    )
    if not active_cells:
        active_cells = cells_list

    active_data = calculate_angle_heatmap(active_cells, sources)
    archive_data = calculate_angle_heatmap(cells_list, sources)

    has_diff = len(archive_data["hooks"]) > len(active_data["hooks"])
    has_replies = active_data.get("has_replies", False)

    if has_replies:
        desc = "Visual correlation of which hooks are resonating with which personas. Node halos indicate response volume and signal intensity."
    else:
        desc = "Pre-send audience allocation across target personas and narrative opening angles. Node size indicates planned prospect density."

    active_svg = render_angle_heatmap_svg(active_data, svg_id="svg-heatmap-active")

    if has_diff:
        archive_svg = render_angle_heatmap_svg(archive_data, svg_id="svg-heatmap-archive")
        toggle_pills = (
            '<div class="email-pills" id="heatmap-toggle-pills" style="margin:12px 0 16px;">'
            f'<button type="button" class="email-pill active" onclick="switchHeatmap(\'active\', this)">Active Campaign ({len(active_data["hooks"])} Hooks)</button>'
            f'<button type="button" class="email-pill" onclick="switchHeatmap(\'archive\', this)">All-Time Archive ({len(archive_data["hooks"])} Hooks)</button>'
            "</div>"
        )
        content_html = (
            toggle_pills
            + f'<div id="heatmap-view-active" style="overflow-x:auto; padding-bottom:1rem;">{active_svg}</div>'
            + f'<div id="heatmap-view-archive" style="display:none; overflow-x:auto; padding-bottom:1rem;">{archive_svg}</div>'
            + "<script>"
            "function switchHeatmap(m, btn) {"
            ' var p = btn.parentNode.querySelectorAll(".email-pill");'
            ' p.forEach(function(b) { b.classList.remove("active"); });'
            ' btn.classList.add("active");'
            ' var a = document.getElementById("heatmap-view-active");'
            ' var r = document.getElementById("heatmap-view-archive");'
            ' if (a) a.style.display = (m === "active") ? "block" : "none";'
            ' if (r) r.style.display = (m === "archive") ? "block" : "none";'
            "}"
            "</script>"
        )
    else:
        content_html = f'<div style="overflow-x:auto; padding-bottom:1rem;">{active_svg}</div>'

    return section(
        "angle-heatmap",
        '<div class="card showcase-sweep glow-card animate-on-load anim-up-lg" data-lens="messaging" style="--anim-delay: 50ms;">'
        '<div class="atmospheric-glow"></div>'
        "<h2>Persona vs. Hook Allocation</h2>"
        f'<p class="note">{desc}</p>' + content_html + "</div>",
    )


def render_sentiment_triage_section(m: dict) -> str:
    error = m.get("frontier_error") or ""
    if error:
        # A ledger that could not be parsed is not the same finding as an inbox with
        # nothing in it, and used to render identically ("No inbound replies logged
        # yet") — the corrupt-line case disappeared instead of being surfaced.
        body = f'<p class="note warn">The history ledger could not be read: {_e(error)}. Replies below may be incomplete.</p>'
    else:
        triage_data = derive_sentiment_triage(m.get("frontier_events") or [])
        body = render_sentiment_triage_html(triage_data)
    return section(
        "sentiment-triage",
        '<div class="card glow-card animate-on-load anim-up-lg" data-lens="objections" style="--anim-delay: 100ms;">'
        '<div class="atmospheric-glow"></div>'
        "<h2>Sentiment Triage</h2>"
        '<p class="note">Recent inbound replies this profile’s history ledger recorded, '
        "with an automated sentiment label where one was set.</p>" + body + "</div>",
    )


def render_ready_to_send_section(m: dict) -> str:
    cells_list = m.get("cells", {}).get("cells") or []
    ready_accounts = (
        m.get("ready_accounts")
        if "ready_accounts" in m
        else (list_ready_to_send_accounts(cells_list) if cells_list else [])
    )
    if not ready_accounts:
        return ""
    return section(
        "ready-to-send",
        '<div class="card animate-on-load anim-up-lg" style="--anim-delay: 150ms;">'
        f"<h2>Approval Queue</h2>{render_ready_to_send_panel(ready_accounts)}</div>",
    )
