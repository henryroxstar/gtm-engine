"""Email Portfolio & Distribution Analysis (Emails Tab Meta-Data Analysis).

Surfaces the distribution and strategic variety of outbound emails across 7 dimensions:
1. Persona & Role (including buyer committee role: Champion, Economic Buyer, Evaluator, Governance)
2. Public Anchor (market trigger, why-now signal, or operational perimeter event)
3. Core Argument (core thesis, capability premise, or mechanism of action)
4. Offer (give-first artifact, teardown, or architecture brief in closing CTA)
5. Industry (target industry vertical / cohort)
6. Contacts (enrolled volume count)
7. % Current (share of people in this profile's current, non-archived sequences)

Invariants:
- INV1: render_email_portfolio_html performs zero disk I/O. All spec and CSV metadata is read
  during build_model() via enrich_portfolio_metadata().
- INV2: Design tokens only. Zero hex/rgba literals in styles or inline attributes.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from .format import _e, _seat_label


def normalize_industry(raw: str) -> str:
    """Normalize industry names into consistent market cohorts."""
    if not raw or raw.strip() in ("(blank)", "—", "-"):
        return "Cross-Industry"
    r = raw.lower()
    kw_map = {
        (
            "software",
            "programming",
            "computer systems",
            "devtools",
            "ai platform",
            "developer",
        ): "DevTools & AI Platforms",
        (
            "finance",
            "banking",
            "lending",
            "capital markets",
            "fintech",
            "wealth",
        ): "Financial Services",
        (
            "health",
            "medical",
            "hospital",
            "clinic",
            "life science",
            "biotech",
            "pharma",
        ): "Healthcare & Life Sciences",
        ("insurance", "reinsurance"): "Insurance",
        ("trade", "logistics", "supply", "shipping", "freight"): "Trade & Logistics",
    }
    return next(
        (ind for kws, ind in kw_map.items() if any(k in r for k in kws)), raw.strip().title()
    )


def derive_committee_role(seat: str) -> str:
    """Map buyer seat to buying committee role.

    A seat this map does not name (e.g. ``partnerships``, ``innovation``, ``unknown`` —
    all live on this profile's own role vocabulary) used to fall back to "Evaluator",
    silently filing an unrecognised seat under a role that means something specific.
    "Unclassified" says the map has no entry for it, which is a fact about the map, not
    a guess about the buyer.
    """
    s = (seat or "").lower()
    mapping = {
        ("ai-platform", "cto", "cpo"): "Champion",
        ("security", "ciso", "cio", "ceo"): "Economic Buyer",
        ("architect", "platform-engineer"): "Technical Evaluator",
        ("risk-compliance", "cro", "compliance", "dpo"): "Governance Co-signer",
        ("founder-operator", "builder"): "Technical Founder",
    }
    return next((role for seats, role in mapping.items() if s in seats), "Unclassified")


def extract_offer(copy: list[dict], declared_offer: str = "") -> tuple[str, str]:
    """The give-first offer's label and its raw CTA question from Touch 1 copy.

    The label is ONLY ``declared_offer`` (the spec's own ``offer:`` front-matter line, if
    it declares one) — never guessed from the CTA's wording. A ten-entry keyword table used
    to match phrases like "one-pager" or "write-up" against this tenant's own copy idioms
    and label the result "Admission Review Teardown"/"A2A Identity Wiring Spec"/etc., which
    (a) put this tenant's argument vocabulary inside the de-branded engine and (b) invented
    a label for offers no spec ever declared as such. The CTA question itself is real —
    read straight from the body — and is shown regardless of whether a label exists.
    """
    first_body = copy[0].get("body", "") if copy else ""
    lines = [ln.strip() for ln in first_body.splitlines() if ln.strip()]
    questions = [ln for ln in lines if ln.endswith("?")]
    raw_q = questions[-1] if questions else ""
    return declared_offer or "Offer not labelled", raw_q


def extract_public_anchor(meta: dict) -> str:
    """The public anchor / market trigger, read ONLY from the spec's own declared metadata.

    ``slot_signal`` and ``hook_cell`` are real front-matter fields every spec on disk
    already declares; a fallback chain used to match this tenant's OWN title wording
    ("admitting a partner", "governance as a revenue decision", a literal "run 500") and
    an ``anchor_map`` of five tenant-specific phrases, both hardcoded in this de-branded
    engine. When neither field yields anything, this says so rather than guessing from
    the title.
    """
    slot_signal = meta.get("slot_signal", "")
    if slot_signal:
        first_clause = re.split(r"[:(;]", slot_signal)[0].strip()
        if len(first_clause) > 8:
            return first_clause[:55]

    hook_cell = meta.get("hook_cell", "")
    if "×" in hook_cell:
        raw_anchor = hook_cell.split("×")[-1].strip()
        return raw_anchor.replace("-", " ").replace("_", " ").title()

    stakes = meta.get("stakes", "")
    if stakes:
        return stakes.replace("-", " ").title()
    return "Not declared in the spec"


def _extract_argument_from_copy(copy: list[dict]) -> str:
    """Extract lead argument thesis sentence from Touch 1 copy."""
    if not copy:
        return ""
    first_body = copy[0].get("body", "")
    lead_prefixes = (
        "hi ",
        "hello ",
        "hey ",
        "dear ",
        "one thing i left",
        "last note from",
        "quick note",
        "assuming this isn't",
        "stood out in the",
        "is the reason for this note",
        "reason i picked you out",
    )
    for raw_ln in first_body.splitlines():
        ln = raw_ln.strip().lstrip("> ").strip()
        if not ln or ln.endswith("?"):
            continue
        ln_low = ln.lower()
        # A valediction line — "Henry", "Best,", "Regards," — is short and carries no
        # sentence-ending punctuation. Detected by SHAPE rather than a literal name list
        # (which named this tenant's own sender), so it generalises to any signer.
        is_valediction = len(ln.split()) <= 2 and not ln.endswith((".", "!", "?"))
        if any(ln_low.startswith(p) for p in lead_prefixes) or is_valediction:
            continue
        if ln.startswith("{{") and (ln.endswith("}}") or ln.endswith("}}.")):
            continue
        cleaned = re.sub(
            r"(?i)\s*\b(tell me if this is already handled|you may already have this covered|if that is already covered)[.!]?$",
            "",
            ln,
        ).strip()
        cleaned = (
            cleaned.replace("{{Company}}", "the enterprise").replace("{{First Name}}", "").strip()
        )
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
        if sentences and len(sentences[0]) < 35 and len(sentences) > 1:
            return f"{sentences[0]} {sentences[1]}"[:110]
        if sentences:
            return sentences[0][:110]
        return cleaned[:110]
    return ""


def extract_core_argument(spec_text: str, copy: list[dict], meta: dict, title: str) -> str:
    """Extract primary thesis or mechanism of action from spec or Touch 1 copy."""
    m_arg = re.search(r"##\s+\d+\.\s+The argument\s*\n+([^\n]+)", spec_text)
    if m_arg:
        first_line = m_arg.group(1).strip()
        m_bold = re.match(r"^\*\*(.+?)\*\*", first_line)
        if m_bold:
            return m_bold.group(1).strip()
        if "The argument is" in first_line:
            part = first_line.split("The argument is", 1)[-1].strip()
            clean_part = re.split(r"[.:]", part)[0].strip()
            if len(clean_part) > 10:
                return f"The argument is {clean_part}"
        clean_headline = re.sub(r"^\*\*|\*\*$", "", first_line).strip()
        if clean_headline and not clean_headline.startswith("Regenerated from"):
            return clean_headline[:90]

    if copy_arg := _extract_argument_from_copy(copy):
        return copy_arg

    premise = meta.get("premise") or meta.get("argument_id")
    if premise:
        return premise.replace("-", " ").replace("_", " ").title()

    return title or "Runtime Policy & Delivery Architecture"


def enrich_portfolio_metadata(
    messages: list[dict],
    seq_dir: Path,
    content_root: Path | None = None,
    profile: str = "",
) -> None:
    """Pre-extract all 7 portfolio dimensions during model build (INV1 compliant)."""
    master_csv = seq_dir / ".pool" / "master-list.csv"
    email_to_industry: dict[str, str] = {}
    if master_csv.is_file():
        try:
            with master_csv.open(newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    e = (r.get("email") or "").strip().lower()
                    ind = (r.get("industry") or r.get("cohort") or "").strip()
                    if e and ind:
                        email_to_industry[e] = ind
        except (
            OSError,
            csv.Error,
            UnicodeDecodeError,
        ):  # a missing/corrupt file is no data, not a crash
            pass

    for msg in messages:
        spec_path = seq_dir / msg.get("spec", "")
        spec_text = ""
        meta: dict[str, str] = {}
        if spec_path.is_file():
            try:
                spec_text = spec_path.read_text(encoding="utf-8")
                for line in spec_text.splitlines()[:30]:
                    mf = re.match(r"^([a-zA-Z0-9_\-]+):\s*(.+)$", line.strip())
                    if mf:
                        meta[mf.group(1).lower()] = mf.group(2).strip()
            except (
                OSError,
                csv.Error,
                UnicodeDecodeError,
            ):  # a missing/corrupt file is no data, not a crash
                pass

        aud = msg.get("audience", [])
        enrolled = sum(c.get("enrolled", 0) for c in aud)
        seats = sorted({c.get("seat") for c in aud if c.get("seat")})
        lead_seat = seats[0] if seats else "other"
        role = derive_committee_role(lead_seat)
        seat_label = _seat_label(lead_seat)

        ind_counter: Counter[str] = Counter()
        csv_path = seq_dir / msg.get("csv", "")
        if csv_path.is_file():
            try:
                with csv_path.open(newline="", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        e = (r.get("email") or r.get("Email") or "").strip().lower()
                        ind = r.get("industry") or email_to_industry.get(e) or ""
                        if ind:
                            ind_counter[ind] += 1
            except (
                OSError,
                csv.Error,
                UnicodeDecodeError,
            ):  # a missing/corrupt file is no data, not a crash
                pass

        top_raw_ind = ind_counter.most_common(1)[0][0] if ind_counter else ""
        norm_ind = normalize_industry(top_raw_ind)

        anchor = extract_public_anchor(meta)
        core_arg = extract_core_argument(spec_text, msg.get("copy", []), meta, msg.get("title", ""))
        offer_label, offer_quote = extract_offer(msg.get("copy", []), meta.get("offer", ""))

        msg["portfolio"] = {
            "persona_type": role,
            "seat": lead_seat,
            "seat_label": seat_label,
            "public_anchor": anchor,
            "core_argument": core_arg,
            "offer": offer_label,
            "offer_quote": offer_quote,
            "industry": norm_ind,
            "contacts": enrolled,
            "spec": msg.get("spec", ""),
            "sequence_id": msg.get("sequence_id", ""),
        }


def render_email_portfolio_html(m: dict) -> str:
    """Render the executive Email Portfolio & Distribution Analysis card.

    Zero file I/O (INV1). Uses design tokens only (INV2).
    """
    messages = m.get("messages") or []
    if not messages:
        return ""
    # Exclude archived/retired sequences (a campaign's own `archived_sequences` list): a
    # portfolio analysis of the CURRENT email mix should not count a sequence deleted from
    # the sending tool months ago. Until now it did — the denominator below summed every
    # source `cells.toml` has ever registered, so a profile rollup with 359 current
    # recipients reported "% of Pool" against roughly 1,170.
    archived_ids = {
        s["sequence_id"]
        for c in (m.get("campaigns") or {}).get("campaigns", [])
        for s in c.get("archived", [])
    }
    messages = [msg for msg in messages if msg.get("sequence_id") not in archived_ids]
    if not messages:
        return ""

    total_pool_contacts = sum(msg.get("portfolio", {}).get("contacts", 0) for msg in messages)

    role_counts: Counter[str] = Counter()
    ind_counts: Counter[str] = Counter()
    anchor_counts: Counter[str] = Counter()
    offer_counts: Counter[str] = Counter()

    for msg in messages:
        p = msg.get("portfolio") or {}
        contacts = p.get("contacts", 0)
        role = p.get("persona_type", "Unclassified")
        ind = p.get("industry", "Cross-Industry")
        anchor = p.get("public_anchor", "Not declared")
        offer = p.get("offer", "Offer not labelled")

        role_counts[role] += contacts
        ind_counts[ind] += contacts
        anchor_counts[anchor] += contacts
        offer_counts[offer] += 1

    top_role, top_role_cnt = role_counts.most_common(1)[0] if role_counts else ("Champion", 0)
    top_ind, top_ind_cnt = ind_counts.most_common(1)[0] if ind_counts else ("DevTools", 0)
    top_anchor = anchor_counts.most_common(1)[0][0] if anchor_counts else "Not declared"
    top_offer = offer_counts.most_common(1)[0][0] if offer_counts else "Offer not labelled"

    role_pills = []
    for r_name in ("Champion", "Economic Buyer", "Technical Evaluator", "Governance Co-signer"):
        cnt = role_counts.get(r_name, 0)
        pct = (cnt / total_pool_contacts * 100) if total_pool_contacts else 0
        role_pills.append(
            f'<div style="display:flex; justify-content:space-between; align-items:center; padding:5px 10px; background:var(--surface); border:1px solid var(--line); border-radius:6px; font-size:12px; margin-bottom:4px;">'
            f'<span style="color:var(--muted); font-weight:500;">{_e(r_name)}</span>'
            f'<span style="color:var(--ink);"><strong>{cnt}</strong> ({pct:.0f}%)</span>'
            f"</div>"
        )

    summary_tiles = (
        '<div class="portfolio-summary-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:14px; margin:16px 0 20px;">'
        '<div class="summary-tile glass-panel" style="background:var(--wash); border:1px solid var(--line); border-radius:12px; padding:14px 16px; display:flex; flex-direction:column; justify-content:space-between; gap:8px;">'
        '<div style="color:var(--teal); font-size:11.5px; text-transform:uppercase; letter-spacing:0.06em; font-weight:600;">Committee Role Balance</div>'
        f'<div style="display:flex; flex-direction:column; gap:4px;">{"".join(role_pills)}</div>'
        f'<div style="font-size:12px; color:var(--muted); border-top:1px dashed var(--line); padding-top:8px; margin-top:4px;">Primary focus: <strong>{_e(top_role)}</strong> ({top_role_cnt} contacts)</div>'
        "</div>"
        '<div class="summary-tile glass-panel" style="background:var(--wash); border:1px solid var(--line); border-radius:12px; padding:14px 16px; display:flex; flex-direction:column; justify-content:space-between; gap:8px;">'
        '<div style="color:var(--teal); font-size:11.5px; text-transform:uppercase; letter-spacing:0.06em; font-weight:600;">Public Anchors &amp; Triggers</div>'
        f'<div style="font-size:18px; font-weight:700; color:var(--ink); margin:4px 0;">{len(anchor_counts)} distinct triggers</div>'
        f'<div style="font-size:12.5px; color:var(--muted); line-height:1.4;">Top trigger: <strong style="color:var(--ink);">{_e(top_anchor)}</strong></div>'
        "</div>"
        '<div class="summary-tile glass-panel" style="background:var(--wash); border:1px solid var(--line); border-radius:12px; padding:14px 16px; display:flex; flex-direction:column; justify-content:space-between; gap:8px;">'
        '<div style="color:var(--teal); font-size:11.5px; text-transform:uppercase; letter-spacing:0.06em; font-weight:600;">Give-First Offers</div>'
        f'<div style="font-size:18px; font-weight:700; color:var(--ink); margin:4px 0;">{len(offer_counts)} distinct assets</div>'
        f'<div style="font-size:12.5px; color:var(--muted); line-height:1.4;">Most offered: <strong style="color:var(--ink);">{_e(top_offer)}</strong></div>'
        "</div>"
        '<div class="summary-tile glass-panel" style="background:var(--wash); border:1px solid var(--line); border-radius:12px; padding:14px 16px; display:flex; flex-direction:column; justify-content:space-between; gap:8px;">'
        '<div style="color:var(--teal); font-size:11.5px; text-transform:uppercase; letter-spacing:0.06em; font-weight:600;">Target Industry Coverage</div>'
        f'<div style="font-size:18px; font-weight:700; color:var(--ink); margin:4px 0;">{len(ind_counts)} cohorts</div>'
        f'<div style="font-size:12.5px; color:var(--muted); line-height:1.4;">Lead vertical: <strong style="color:var(--ink);">{_e(top_ind)}</strong> ({top_ind_cnt} contacts)</div>'
        "</div>"
        "</div>"
    )

    filter_roles = [
        "all",
        "Champion",
        "Economic Buyer",
        "Technical Evaluator",
        "Governance Co-signer",
    ]
    filter_pills = [
        f'<button type="button" class="email-pill active" data-portfolio-role="all">All Roles ({total_pool_contacts:,})</button>'
    ]
    for r in filter_roles[1:]:
        cnt = role_counts.get(r, 0)
        if cnt > 0:
            filter_pills.append(
                f'<button type="button" class="email-pill" data-portfolio-role="{_e(r)}">{_e(r)} ({cnt:,})</button>'
            )

    toolbar_html = (
        '<div class="email-toolbar" style="margin:16px 0 18px;">'
        '<div class="email-search-bar" style="position:relative; max-width:480px;">'
        '<span class="email-search-icon">🔍</span>'
        '<input type="text" id="portfolio-search" class="email-search-input" placeholder="Search personas, public anchors, core arguments, offers, industries..." autocomplete="off">'
        '<button type="button" id="portfolio-search-clear" class="email-search-clear" hidden>×</button>'
        "</div>"
        f'<div id="portfolio-role-filters" class="email-pills">{"".join(filter_pills)}</div>'
        "</div>"
    )

    rows = []
    for msg in messages:
        p = msg.get("portfolio") or {}
        contacts = p.get("contacts", 0)
        pct = (contacts / total_pool_contacts * 100) if total_pool_contacts else 0
        role = p.get("persona_type", "Unclassified")
        seat_lbl = p.get("seat_label") or _seat_label(p.get("seat", "other"))
        ind = p.get("industry", "Cross-Industry")
        anchor = p.get("public_anchor", "Not declared")
        arg = p.get("core_argument", "")
        offer = p.get("offer", "Offer not labelled")
        quote = p.get("offer_quote", "")
        sid = p.get("sequence_id", "")

        role_badge = f'<span class="pill accent">{_e(role)}</span>'
        persona_cell = (
            f'<div style="font-weight:600; margin-bottom:4px;">{role_badge}</div>'
            f'<div style="color:var(--ink); font-size:13px;">{_e(seat_lbl)}</div>'
        )

        anchor_cell = (
            f'<div style="font-size:13.5px; font-weight:500; color:var(--ink);">{_e(anchor)}</div>'
        )

        arg_cell = (
            f'<div style="font-size:13px; line-height:1.45; color:var(--ink);">{_e(arg)}</div>'
        )

        quote_html = (
            f'<div style="font-size:11.5px; color:var(--muted); margin-top:4px; font-style:italic;">"{_e(quote[:80])}{"..." if len(quote) > 80 else ""}"</div>'
            if quote
            else ""
        )
        offer_cell = f'<span class="pill" style="font-size:11.5px;">{_e(offer)}</span>{quote_html}'

        ind_cell = f'<span class="pill" style="font-size:11.5px; background:var(--wash); border-color:var(--line);">{_e(ind)}</span>'

        contacts_cell = (
            f'<div style="font-weight:700; font-size:14px; color:var(--ink);">{contacts}</div>'
        )

        pct_width = min(max(pct, 2.0), 100.0) if pct > 0 else 0
        pct_cell = (
            f'<div style="font-weight:600; font-size:13px; color:var(--ink);">{pct:.1f}%</div>'
            f'<div style="height:4px; border-radius:2px; background:var(--wash); margin-top:4px; overflow:hidden;">'
            f'<div style="width:{pct_width:.1f}%; height:100%; background:var(--accent);"></div>'
            f"</div>"
        )

        search_corpus = f"{role} {seat_lbl} {anchor} {arg} {offer} {quote} {ind} {sid}".lower()

        rows.append(
            f'<tr data-portfolio-row="{_e(sid)}" data-role="{_e(role)}" data-industry="{_e(ind)}" data-search="{_e(search_corpus)}">'
            f"<td>{persona_cell}</td>"
            f"<td>{anchor_cell}</td>"
            f"<td>{arg_cell}</td>"
            f"<td>{offer_cell}</td>"
            f"<td>{ind_cell}</td>"
            f'<td class="num-cell">{contacts_cell}</td>'
            f'<td class="num-cell">{pct_cell}</td>'
            f"</tr>"
        )

    table_html = (
        '<table id="portfolio-table" class="data-table">'
        "<thead><tr>"
        '<th style="min-width:140px;">Persona &amp; Role</th>'
        '<th style="min-width:180px;">Public Anchor</th>'
        '<th style="min-width:240px;">Core Argument</th>'
        '<th style="min-width:180px;">Offer</th>'
        '<th style="min-width:130px;">Industry</th>'
        '<th class="num-cell" style="width:70px;">Contacts</th>'
        '<th class="num-cell" style="width:80px;" title="Share of people in this profile\'s current (non-archived) sequences">% Current</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )

    script_html = (
        "<script>"
        "(function initPortfolioControls() {"
        '  var table = document.getElementById("portfolio-table");'
        "  if (!table) return;"
        '  var searchInput = document.getElementById("portfolio-search");'
        '  var clearBtn = document.getElementById("portfolio-search-clear");'
        '  var roleContainer = document.getElementById("portfolio-role-filters");'
        '  var activeRole = "all";'
        "  function filterPortfolio() {"
        '    var query = (searchInput ? searchInput.value : "").toLowerCase().trim();'
        '    var rows = table.querySelectorAll("tbody tr[data-portfolio-row]");'
        "    rows.forEach(function(row) {"
        '      var rRole = row.getAttribute("data-role") || "";'
        '      var rSearch = row.getAttribute("data-search") || "";'
        '      var roleMatch = (activeRole === "all") || (rRole === activeRole);'
        "      var queryMatch = !query || (rSearch.indexOf(query) !== -1);"
        '      row.style.display = (roleMatch && queryMatch) ? "" : "none";'
        "    });"
        "    if (clearBtn) clearBtn.hidden = !query;"
        "  }"
        "  if (searchInput) {"
        '    searchInput.addEventListener("input", filterPortfolio);'
        "  }"
        "  if (clearBtn) {"
        '    clearBtn.addEventListener("click", function() {'
        '      searchInput.value = "";'
        "      filterPortfolio();"
        "      searchInput.focus();"
        "    });"
        "  }"
        "  if (roleContainer) {"
        '    roleContainer.addEventListener("click", function(e) {'
        '      var btn = e.target.closest(".email-pill");'
        "      if (!btn) return;"
        '      roleContainer.querySelectorAll(".email-pill").forEach(function(p) { p.classList.remove("active"); });'
        '      btn.classList.add("active");'
        '      activeRole = btn.getAttribute("data-portfolio-role") || "all";'
        "      filterPortfolio();"
        "    });"
        "  }"
        "})();"
        "</script>"
    )

    return (
        '<div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 30ms;">'
        "<h2>Email Portfolio &amp; Distribution Analysis</h2>"
        '<p class="note">Strategic breakdown across buyer committee personas, why-now public anchors, core thesis mechanisms, and give-first offers.</p>'
        f"{summary_tiles}"
        f"{toolbar_html}"
        f"{table_html}"
        f"{script_html}"
        "</div>"
    )
