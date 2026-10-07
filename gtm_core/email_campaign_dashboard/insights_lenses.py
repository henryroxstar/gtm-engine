"""Strategic intelligence lenses for the founder and founding AE cockpit.

Organizes insights into six strategic dimensions, each with a pre-send card (what the
data already shows before anything ships) and an in-flight card (what sending has
produced so far):

1. Targeting & ICP — judge routing and seat fit, then replies by seat
2. Messaging & Hooks — why-now coverage, then replies by hook/variant
3. Objections & Friction — opt-out interception, then reply-theme classification (not built)
4. Product & Solution — whatever the operator declared on a manifest question
5. Market & Geo — market-gate and roster country mix, then replies by region (not tracked)
6. GTM Machine — pre-flight gate attrition, then statistical power at the volume in hand

Every sentence here is either a number pulled from ``m``/the campaign manifests, or a
named absence ("not declared", "not measurable: <reason>"). No lens prints a conclusion
that was not computed from this run's own data — a card renders the same on an empty
profile and a full one only when it is correctly reporting that nothing is there yet.
"""

from __future__ import annotations

import html
from typing import Any

from ..power import detectable_lift, wilson
from .aggregate import _scope_figures
from .delivery import groups


def _e(val: Any) -> str:
    """Escape HTML content safely."""
    return html.escape(str(val))


def lens_toolbar_html() -> str:
    """Render the sub-pill strategic lens switcher bar."""
    return (
        '<div class="lens-toolbar">'
        '<div class="lens-label">Strategic Lens</div>'
        '<div class="lens-pills" id="insight-lens-filters">'
        '<button type="button" class="lens-pill active" data-lens="all">All Lenses</button>'
        '<button type="button" class="lens-pill" data-lens="targeting">🎯 Targeting &amp; ICP</button>'
        '<button type="button" class="lens-pill" data-lens="messaging">✍️ Messaging &amp; Hooks</button>'
        '<button type="button" class="lens-pill" data-lens="objections">🛡️ Objections &amp; Friction</button>'
        '<button type="button" class="lens-pill" data-lens="product">⚙️ Product &amp; Solution</button>'
        '<button type="button" class="lens-pill" data-lens="geo">🌍 Market &amp; Geo</button>'
        '<button type="button" class="lens-pill" data-lens="gtm">⚡ GTM Machine</button>'
        "</div>"
        "</div>"
    )


def _build_finding_card(
    *,
    lens: str,
    lens_name: str,
    title: str,
    badge: str,
    badge_class: str = "ok",
    threshold_meta: str,
    finding: str,
    context: str,
    evidence_base: str,
    note: str,
    cmd: str,
    btn: str,
) -> str:
    """A card whose body is text computed from ``m`` (or a named absence) — never a
    fixed sentence. Every caller below builds ``finding``/``context``/``evidence_base``
    from real fields; none is a string literal chosen ahead of the data."""
    return (
        f'<div class="card" data-lens="{lens}">'
        '<div class="insight-card-header">'
        f'<div><span class="pill{" " + badge_class if badge_class else ""}">{_e(badge)}</span><h2 style="margin:4px 0 0;">{_e(title)}</h2></div>'
        f'<div class="insight-card-meta"><span class="pill">{_e(lens_name)}</span>'
        f'<span class="pill" style="opacity:0.85; font-size:11px;">{_e(threshold_meta)}</span></div>'
        "</div>"
        '<div class="insight-card-body">'
        f"<p><strong>Finding:</strong> {_e(finding)}</p>"
        f"<p><strong>Strategic Context:</strong> {_e(context)}</p>"
        f'<p><strong>Evidence Base:</strong> <span class="muted">{_e(evidence_base)}</span></p>'
        "</div>"
        '<div class="action-prompt-card">'
        '<div class="action-prompt-row">'
        f'<div><strong style="color:var(--ink);">Next Move:</strong> <span class="muted">{_e(note)}</span></div>'
        '<div class="action-prompt-copy-box">'
        f'<span class="action-prompt-text">{_e(cmd)}</span>'
        f'<button class="copy-btn" onclick="copyPrompt(this)">{_e(btn)}</button>'
        "</div>"
        "</div>"
        "</div>"
        "</div>"
    )


def _pct_ci(successes: int, n: int) -> str:
    """ "{rate}% (95% CI lo-hi%)", or "—" with no denominator. A CI this wide IS the
    finding at low volume — it replaces a badge that used to declare a winner early. More
    successes than ``n`` is not a proportion (a snapshot can say so): a dash, not a crash."""
    if not n or not 0 <= successes <= n:
        return "—"
    ci = wilson(successes, n)
    pct = 100 * successes / n
    if ci is None:
        return f"{pct:.1f}%"
    return f"{pct:.1f}% (95% CI {ci[0] * 100:.0f}–{ci[1] * 100:.0f}%)"


def _build_groups_card(
    *,
    lens: str,
    lens_name: str,
    title: str,
    question: str,
    groups: list[tuple[str, int, int]],
    baseline: float | None,
    note: str,
    cmd: str,
    btn: str,
) -> str:
    """An in-flight comparison across groups (seats, hooks, …): ``(label, sent, replied)``.

    Shows the observed reply rate and its Wilson interval per group — never a pre-written
    verdict about which group "wins". When no group has any sends yet, or ``groups`` is
    empty, says so instead of a table.
    """
    total_sent = sum(g[1] for g in groups)
    if not groups or not total_sent:
        body = f'<p class="muted">{_e(question)} Not measurable yet: nothing in this run has been sent.</p>'
    else:
        rows_html = "".join(
            f"<tr><td>{_e(label)}</td><td class='num-cell'>{sent:,}</td>"
            f"<td class='num-cell'>{replied:,}</td><td>{_pct_ci(replied, sent)}</td></tr>"
            for label, sent, replied in sorted(groups, key=lambda g: -g[1])
            if sent
        )
        smallest = min((g[1] for g in groups if g[1]), default=0)
        lift_note = ""
        if baseline and smallest:
            lift = detectable_lift(smallest, baseline)
            if lift:
                lift_note = (
                    f'<p class="muted" style="font-size:12px;">At the smallest group’s '
                    f"n ({smallest:,}), only a difference of {lift:.1f}× the baseline "
                    "reply rate or larger would be distinguishable from chance.</p>"
                )
        body = (
            f"<p>{_e(question)}</p>"
            "<table><thead><tr><th>Group</th><th>Sent</th><th>Replied</th>"
            "<th>Reply rate</th></tr></thead>"
            f"<tbody>{rows_html}</tbody></table>{lift_note}"
        )
    return (
        f'<div class="card" data-lens="{lens}">'
        '<div class="insight-card-header">'
        f'<div><span class="pill">In Flight</span><h2 style="margin:4px 0 0;">{_e(title)}</h2></div>'
        f'<div class="insight-card-meta"><span class="pill">{_e(lens_name)}</span>'
        f'<span class="pill" style="opacity:0.85; font-size:11px;">{total_sent:,} sent</span></div>'
        "</div>"
        f'<div class="insight-card-body">{body}</div>'
        '<div class="action-prompt-card">'
        '<div class="action-prompt-row">'
        f'<div><strong style="color:var(--ink);">Next Move:</strong> <span class="muted">{_e(note)}</span></div>'
        '<div class="action-prompt-copy-box">'
        f'<span class="action-prompt-text">{_e(cmd)}</span>'
        f'<button class="copy-btn" onclick="copyPrompt(this)">{_e(btn)}</button>'
        "</div>"
        "</div>"
        "</div>"
        "</div>"
    )


def _build_declared_card(lens: str, lens_name: str, questions: list[dict]) -> str:
    """A lens with no engine-computed metric: whatever the operator declared on a
    campaign manifest's ``[[experiment.questions]]`` tagged ``lens = "<this lens>"``,
    shown in the operator's own words, alongside that campaign's own sent/replied.
    Never a hardcoded hypothesis — an engine module has no business asserting one."""
    if not questions:
        return (
            f'<div class="card" data-lens="{lens}">'
            '<div class="insight-card-header">'
            f'<div><span class="pill">Not Declared</span><h2 style="margin:4px 0 0;">{_e(lens_name)}</h2></div>'
            "</div>"
            '<div class="insight-card-body"><p class="muted">No campaign has declared a '
            f'question for this lens yet. Add <code>lens = "{_e(lens)}"</code> to a '
            "<code>[[experiment.questions]]</code> entry in the campaign file to track one "
            "here.</p></div>"
            "</div>"
        )
    cards = []
    for q in questions:
        cards.append(
            f'<div class="card" data-lens="{lens}">'
            '<div class="insight-card-header">'
            f'<div><span class="pill">Declared</span><h2 style="margin:4px 0 0;">{_e(q["question"])}</h2></div>'
            f'<div class="insight-card-meta"><span class="pill">{_e(lens_name)}</span>'
            f'<span class="pill" style="opacity:0.85; font-size:11px;">{_e(q["campaign"])}</span></div>'
            "</div>"
            '<div class="insight-card-body">'
            f"<p>{_e(q['how'])}</p>"
            f'<p class="muted">{q["sent"]:,} sent · {q["replied"]:,} replied on this campaign.</p>'
            "</div>"
            "</div>"
        )
    return "".join(cards)


def _judge_routing(m: dict[str, Any]) -> tuple[str, str]:
    """(finding, evidence) for the judge's routing tally — never invents a "send" count
    the tally does not carry (it is the RE-target queue: every row on it was rejected)."""
    tally = ((m.get("roster") or {}).get("judge_tally")) or {}
    rows = tally.get("rows") or 0
    if not rows:
        return ("No judge run has been recorded for this scope yet.", "roster.judge_tally is empty")
    retarget = sum(v for k, v in tally.items() if k.startswith("dest:") and "re-target" in k)
    reargue = sum(v for k, v in tally.items() if k.startswith("dest:") and "re-argue" in k)
    settled = tally.get("reargue:settled") or 0
    open_ = tally.get("reargue:open") or 0
    return (
        f"Of {rows:,} rows the judge rejected, {retarget:,} were routed back to prospecting "
        f"(wrong seat, not wrong copy) and {reargue:,} back to the spec (right seat, copy "
        f"rejected) — of which {settled:,} are an already-settled defect class and "
        f"{open_:,} are still open.",
        f"gtm_core.email_campaign_dashboard.roster.judge_queue, {rows:,} rows tallied",
    )


def _seat_fit_finding(m: dict[str, Any]) -> tuple[str, str]:
    fit = m.get("seat_fit") or {}
    total = fit.get("total") or 0
    if not total:
        return ("No rendered emails are in scope to check seat fit against.", "seat_fit is empty")
    matched, elsewhere, unresolved = fit["matched"], fit["elsewhere"], fit["unresolved"]
    return (
        f"{matched} of {total} recipients hold the seat their spec argues to; "
        f"{elsewhere} argue a seat the recipient does not hold; {unresolved} could not be "
        "classified by title.",
        "gtm_core.hook_coverage persona classifier over the sequences' own declared seat",
    )


def _why_now_finding(m: dict[str, Any]) -> tuple[str, str]:
    roster = m.get("roster") or {}
    accounts = roster.get("accounts") or 0
    if not accounts:
        return ("No campaign roster is in scope to check for why-now coverage.", "roster is empty")
    signal, sourced = roster.get("signal") or 0, roster.get("signal_sourced") or 0
    return (
        f"{signal} of {accounts} accounts carry a dated why-now line; {sourced} of those "
        "cite a source URL for it.",
        "roster_model over this scope's declared roster exports",
    )


def _objections_finding(m: dict[str, Any]) -> tuple[str, str]:
    inbound = m.get("inbound") or {}
    detected = inbound.get("optout_detected") or 0
    dnc = inbound.get("optout_dnc_added") or 0
    unreadable = inbound.get("unreadable") or 0
    unattrib = inbound.get("optout_unattributable") or 0
    if not (detected or unreadable):
        return (
            "No opt-out or unreadable-reply event has been recorded in this profile's history yet.",
            "history.jsonl carries no optout_detected/optout_unreadable events",
        )
    parts = [f"{detected} opt-out{'s' if detected != 1 else ''} detected"]
    if dnc:
        parts.append(f"{dnc} added to the Do-Not-Contact list")
    if unattrib:
        parts.append(f"{unattrib} not attributable to a specific sender match")
    if unreadable:
        parts.append(
            f"{unreadable} reply{'ies' if unreadable != 1 else ''} could not be read at all"
        )
    return (
        "; ".join(parts) + ".",
        "history.jsonl optout_detected/optout_dnc_added/optout_unreadable events",
    )


def _market_finding(m: dict[str, Any]) -> tuple[str, str]:
    market = m.get("market") or {}
    if not market.get("gate_on"):
        return ("No market gate is configured for this profile.", "market_split: gate_on is false")
    markets = ", ".join(market.get("markets") or []) or "none declared"
    out = market.get("out_of_market") or 0
    unknown = market.get("unknown_country") or 0
    roster_rows = (m.get("roster") or {}).get("rows") or []
    by_country: dict[str, int] = {}
    for r in roster_rows:
        key = (r.get("country") or "not recorded").strip() or "not recorded"
        by_country[key] = by_country.get(key, 0) + 1
    top = sorted(by_country.items(), key=lambda kv: -kv[1])[:5]
    top_str = ", ".join(f"{c} ({n})" for c, n in top) or "no roster rows carry a country"
    return (
        f"Allowed markets: {markets}. {out} held prospects are outside them; {unknown} have "
        f"no country recorded. This scope's roster by country: {top_str}.",
        "prospects_consolidate market gate + roster_model rows",
    )


def _gtm_finding(m: dict[str, Any]) -> tuple[str, str]:
    from ..prospect_lede import _reason_lines

    readiness = m.get("readiness")
    refusals = list(getattr(readiness, "refusals", None) or [])
    if not refusals:
        return (
            "The pre-flight gate has not refused any batch on record, or has not been run.",
            "prospect_readiness.load_readiness().refusals",
        )
    classes = [c for _batch, _refused, cls in refusals for c in cls]
    held = sum(refused for _b, refused, _c in refusals)
    lines = _reason_lines(classes)
    return (
        f"{held:,} rows are held by the pre-flight gate: " + "; ".join(lines) + ".",
        "prospect_readiness.load_readiness() over the last recorded checks",
    )


def _power_finding(m: dict[str, Any]) -> tuple[str, str]:
    cells = (m.get("cells") or {}).get("cells") or []
    sizes = sorted(int(c.get("sendable") or 0) for c in cells if c.get("sendable"))
    baseline = (m.get("cells") or {}).get("baseline")
    baseline_declared = (m.get("cells") or {}).get("baseline_declared", False)
    if not sizes or not baseline:
        return (
            "No sendable cell size is available yet to size a comparison against.",
            "cells.build_cells over this scope",
        )
    smallest, median = sizes[0], sizes[len(sizes) // 2]
    lift_small = detectable_lift(smallest, baseline)
    lift_med = detectable_lift(median, baseline)

    def _phrase(n: int, lift: float | None) -> str:
        if lift is None:
            return (
                f"at n={n:,}, no lift is detectable — the cell is too small to power any comparison"
            )
        return f"at n={n:,}, only a {lift:.1f}× lift or larger would be detectable"

    basis = (
        f"the {baseline * 100:.1f}% target this scope declared"
        if baseline_declared
        else f"a {baseline * 100:.1f}% reference rate (no campaign here declares its own target)"
    )
    return (
        f"Against {basis}: {_phrase(smallest, lift_small)} (smallest cell); "
        f"{_phrase(median, lift_med)} (median cell).",
        "gtm_core.power.detectable_lift over this scope's own cell sizes",
    )


def _declared_questions(m: dict[str, Any], lens: str) -> list[dict]:
    """Every ``[[experiment.questions]]`` entry across in-scope campaigns tagged
    ``lens = "<lens>"``, with that campaign's own sent/replied figures."""
    campaigns = (m.get("campaigns") or {}).get("campaigns") or []
    if not campaigns:
        return []
    fig = _scope_figures(m)
    out = []
    for c in campaigns:
        for q in (c.get("experiment") or {}).get("questions") or []:
            if q.get("lens") != lens:
                continue
            own = _campaign_contacted(fig, c)
            out.append(
                {
                    "question": q.get("question", ""),
                    "how": q.get("how", ""),
                    "campaign": c.get("title") or c.get("slug", ""),
                    "sent": own["current"] if own else 0,
                    "replied": own["replied"] if own else 0,
                }
            )
    return out


def _campaign_contacted(fig: dict, c: dict) -> dict | None:
    from .aggregate import campaign_contacted

    return campaign_contacted(fig, c)


def strategic_lenses_html(m: dict[str, Any] | None = None) -> str:
    """Six strategic lenses, each card built from ``m`` or naming what is missing."""
    if not m:
        m = {}
    baseline = (m.get("cells") or {}).get("baseline")
    cards: list[str] = []

    # 1. Targeting & ICP
    judge_find, judge_ev = _judge_routing(m)
    seat_find, seat_ev = _seat_fit_finding(m)
    cards.append(
        _build_finding_card(
            lens="targeting",
            lens_name="Targeting & ICP",
            title="Judge Routing and Seat Fit",
            badge="Pre-Send",
            threshold_meta="Pre-Send Audit",
            finding=f"{judge_find} {seat_find}",
            context="A rejection routed to prospecting means the copy is fine and the "
            "recipient is wrong; routed to the spec means the recipient is right and the "
            "copy is wrong. Those are different follow-ups.",
            evidence_base=f"{judge_ev}; {seat_ev}",
            note="Audit newly added candidate contacts with the ICP check tool before enrollment.",
            cmd=f"python -m gtm_core.prospects icp check --profile {_e(m.get('profile') or '')}",
            btn="Copy ICP Audit",
        )
    )
    cards.append(
        _build_groups_card(
            lens="targeting",
            lens_name="Targeting & ICP",
            title="Replies by Seat",
            question="Reply rate for each seat argued to, at the volume sent so far:",
            groups=groups(m, "seat"),
            baseline=baseline,
            note="Review the seat/hook breakdown below for the full cell table.",
            cmd="/draft-outreach",
            btn="Copy Outreach Prompt",
        )
    )

    # 2. Messaging & Hooks
    why_find, why_ev = _why_now_finding(m)
    cards.append(
        _build_finding_card(
            lens="messaging",
            lens_name="Messaging & Hooks",
            title="Why-Now Coverage",
            badge="Pre-Send",
            threshold_meta="Pre-Send Audit",
            finding=why_find,
            context="A hook with no dated, sourced reason to contact this account now reads "
            "as generic outreach regardless of how it is written.",
            evidence_base=why_ev,
            note="Inspect target account folder research to confirm evidence grounding before send approvals.",
            cmd='python -m gtm_core.account_folder "<company>" --profile <active>',
            btn="Inspect Account Evidence",
        )
    )
    cards.append(
        _build_groups_card(
            lens="messaging",
            lens_name="Messaging & Hooks",
            title="Replies by Hook",
            question="Reply rate for each hook sent, at the volume sent so far:",
            groups=groups(m, "angle"),
            baseline=baseline,
            note="Review the angle heatmap below for the full hook breakdown.",
            cmd="#angle-heatmap",
            btn="Jump to Heatmap",
        )
    )

    # 3. Objections & Friction
    obj_find, obj_ev = _objections_finding(m)
    cards.append(
        _build_finding_card(
            lens="objections",
            lens_name="Objections & Friction",
            title="Opt-Out and Compliance Interception",
            badge="Pre-Send" if not (m.get("inbound") or {}).get("optout_detected") else "Recorded",
            threshold_meta="From history.jsonl",
            finding=obj_find,
            context="Every detected opt-out that is not also on the Do-Not-Contact list is a "
            "gap between what was detected and what is actually suppressed.",
            evidence_base=obj_ev,
            note="Run inbound triage regularly to classify reply intent and track emerging friction themes.",
            cmd="/inbound-triage",
            btn="Run Inbound Triage",
        )
    )
    cards.append(
        _build_finding_card(
            lens="objections",
            lens_name="Objections & Friction",
            title="Objection Themes",
            badge="Not Yet Measurable",
            badge_class="",
            threshold_meta="No classifier",
            finding="Not measurable: reply text is not classified by objection theme in this build.",
            context="A theme count here would have to come from reading the replies, not from a rule this engine runs today.",
            evidence_base="No lookup exists from a reply to an objection class.",
            note="Read the raw replies for recurring themes.",
            cmd="/inbound-triage",
            btn="Run Inbound Triage",
        )
    )

    # 4. Product & Solution
    cards.append(
        _build_declared_card("product", "Product & Solution", _declared_questions(m, "product"))
    )

    # 5. Market & Geo
    market_find, market_ev = _market_finding(m)
    cards.append(
        _build_finding_card(
            lens="geo",
            lens_name="Market & Geo",
            title="Market Gate and Roster Composition",
            badge="Pre-Send",
            threshold_meta="Roster Breakdown",
            finding=market_find,
            context="A prospect outside the allowed markets or with no recorded country is a "
            "list-quality gap, not a targeting choice.",
            evidence_base=market_ev,
            note="Run a market scan to check coverage against allowed regions.",
            cmd="/market-scan",
            btn="Run Market Scan",
        )
    )
    cards.append(
        _build_finding_card(
            lens="geo",
            lens_name="Market & Geo",
            title="Replies by Region",
            badge="Not Yet Measurable",
            badge_class="",
            threshold_meta="Not tracked",
            finding="Not measurable: the outcomes ledger does not tag a reply with the "
            "recipient's country or region.",
            context="A regional reply comparison needs that field on the outcome row.",
            evidence_base="gtm_core.outcomes rows carry no country/region field.",
            note="Tag outcome rows with a region if this comparison is wanted.",
            cmd="/market-scan",
            btn="Run Market Scan",
        )
    )

    # 6. GTM Machine
    gtm_find, gtm_ev = _gtm_finding(m)
    cards.append(
        _build_finding_card(
            lens="gtm",
            lens_name="GTM Machine",
            title="Held by the Pre-Flight Gate",
            badge="Pre-Send",
            threshold_meta="Gate Audit",
            finding=gtm_find,
            context="Strict pre-flight enforcement protects domain reputation, but every held "
            "row is also a row that is not yet sendable — both are true at once.",
            evidence_base=gtm_ev,
            note="Inspect pipeline status to review held-back accounts and pipeline progression.",
            cmd="/status",
            btn="Check Pipeline Status",
        )
    )
    power_find, power_ev = _power_finding(m)
    cards.append(
        _build_finding_card(
            lens="gtm",
            lens_name="GTM Machine",
            title="Statistical Power at Current Volume",
            badge="Pre-Send",
            threshold_meta="Statistical Sizing",
            finding=power_find,
            context="A gap smaller than the detectable lift reads as chance at this volume, "
            "whatever it turns out to be.",
            evidence_base=power_ev,
            note="Refresh the campaign dashboard to monitor updated live sending metrics and reply streams.",
            cmd=f"python -m gtm_core.email_campaign_dashboard --profile {_e(m.get('profile') or '')} --scope all",
            btn="Copy Refresh Command",
        )
    )

    return "".join(cards)
