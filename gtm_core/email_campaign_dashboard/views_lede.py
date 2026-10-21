"""The status tab's first card: the lede ``prospects status`` opens with (PS15).

Rendered, never re-worded — the lines are ``prospect_lede.compose_lede``'s output, carried on
the model as ``m["lede"]``. Its own module so the view that renders the rest of the tab does
not also own the one card whose words must match the terminal byte for byte.
"""

from __future__ import annotations

from ..prospect_lede import GO_LIVE_WORDS
from ..prospect_status import LABELS as STATUS_LABELS
from .format import _e, scope_label, sorted_refusals, status_label

#: Login URLs for the sequencers this codebase already integrates with (the same set
#: `capability_preflight.read_email_tool` can return). Not a per-tenant fact — every
#: tenant on a given sequencer logs in at the same URL — so this differs from the
#: tenant-slug prose this fix removes elsewhere. A tool the profile declares but that is
#: not in this map still gets its name shown; only the button becomes a plain note.
_SEQUENCER_URLS: dict[str, str] = {
    "saleshandy": "https://app.saleshandy.com/",
    "apollo": "https://app.apollo.io/",
    "gmass": "https://www.gmass.co/",
}


def _sending_tool(profile: str) -> tuple[str, str]:
    """(display name, login URL or "") for the profile's declared sequencer.

    Never defaults to a specific vendor: a profile with no ``email_tool:`` in its
    PROFILE.md, or one set to ``manual``, gets no link and a generic name — showing
    Saleshandy's URL for a tenant on Apollo or GMass would send them to the wrong tool.
    """
    from ..capability_preflight import read_email_tool

    try:
        tool = read_email_tool(profile)
    except (FileNotFoundError, ValueError):
        return ("the sending tool", "")
    if tool == "manual":
        return ("the sending tool", "")
    return (tool.title(), _SEQUENCER_URLS.get(tool, ""))


def _lede_block(m: dict) -> str:
    """PS15: the lines `prospects status` opens with — `compose_lede`'s output, rendered, never
    re-worded. One element per line; an indented line is a detail of the one above it. The
    card takes the warning tone only when the answer cannot be trusted (stale, unreadable,
    or never checked): a decision waiting on the operator is theirs, not a warning.
    """
    lines = m.get("lede") or []
    if not lines:
        return ""
    readiness = m.get("readiness")
    # PS20 P1.5: warn is a number that cannot be trusted, and it says why.
    card = (
        'class="card lede"'
        if getattr(readiness, "state", "none") == "ok"
        else 'class="card lede warn" data-warn="checks-untrusted"'
    )
    # `.get`: a hand-built model (the lede's own tests) carries no `review_sheet`.
    sheet = m.get("review_sheet") or {}
    body = []
    for ln in lines:
        if ln.startswith("As of "):
            continue
        text = _e(ln.strip())
        cls = (
            "lede-reason"
            if ln.startswith("    ")
            else "lede-detail"
            if ln.startswith("  ")
            else "lede-line"
        )
        if ln.startswith("For You (") or ln.startswith("Yours ("):
            cls += " yours"  # the operator's move: the lede's one accent line
            if sheet.get("href"):
                text += f' <a href="{_e(sheet["href"])}" class="review-sheet-link">open it</a>'
        body.append(f'<p class="{cls}">{text}</p>')
    # Composed before `scope_to_campaign` narrows the model, so on a campaign page it is still
    # the whole profile's answer — its go-live word included — and says so (PS20 P1.10). Not
    # `_pool_scope_note`: that one points the reader at this very tab for the scoped figures.
    scope_note = (
        f'<p class="note"><strong>Profile-wide, not {_e(scope_label(m))}.</strong> '
        "Summary covers all active campaigns across the workspace.</p>"
        if m.get("campaign_scope")
        else ""
    )
    meter_html = _lede_meter_html(m, lines)
    details_body = (
        f"""<details class="lede-breakdown-details" style="margin-top:14px; border-top:1px dashed var(--line); padding-top:12px;">
          <summary style="font-size:12.5px; color:var(--teal); cursor:pointer; font-weight:500; user-select:none; display:inline-flex; align-items:center; gap:6px;">
            <span>Diagnostic &amp; Gate Refusal Breakdown</span>
            <span class="muted" style="font-size:11px;">(click to inspect reasons)</span>
          </summary>
          <div style="margin-top:10px;">
            {"".join(body)}
          </div>
        </details>"""
        if body
        else ""
    )
    return f"""
      <div {card}>
        <h2>Status</h2>
        {scope_note}{meter_html}{details_body}
      </div>"""


def _release_batch_items(m: dict) -> list[str]:
    """One card item per sorted batch the checks refused: the decisions that release it.

    Built from the stored check report's classes, never from the lede's sentences. Errors
    are Claude's to clear and are named in one line; the warning classes are the
    operator's, because accepting one is a decision nobody else may make for them.
    """
    from ..prospect_lede import BATCH_WORDS, refusal_copy, warning_decision

    readiness = m.get("readiness")
    items = []
    for batch, refused, classes in sorted_refusals(readiness):
        word = BATCH_WORDS.get(batch, batch)
        errors = [(rule, n, unit) for rule, n, unit in classes if unit != "warning"]
        budget, pile = readiness.warnings.get(batch, (0, []))
        lines = []
        if errors:
            mine = "; ".join(f"{n:,} {unit}s {refusal_copy(rule)[0]}" for rule, n, unit in errors)
            lines.append(f"<li><strong>Claude's part, not yours:</strong> {_e(mine)}.</li>")
        for rule, n in sorted(pile, key=lambda rc: -rc[1]):
            what, choice = warning_decision(rule)
            lines.append(
                f"<li><strong>{n:,}</strong> toward the limit — {_e(what)}: {_e(choice)}.</li>"
            )
        total = sum(n for _, n in pile)
        ask = (
            f"{total:,} warnings against a limit of {budget:,}, so at least "
            f"{total - budget:,} must be accepted, fixed or removed before any of these "
            "contacts can go."
            if pile and total > budget
            else "The checks held these contacts back; the check report has the detail."
        )
        items.append(
            '<div class="action-item-box" data-release-batch="' + _e(batch) + '">'
            '<div class="action-item-header">'
            f'<div class="action-item-title">Decision: Release the {_e(word)} batch '
            f"({refused:,} contacts held)</div>"
            '<span class="pill">Action Waiting on You</span>'
            "</div>"
            '<div class="action-detail-grid">'
            f'<div class="action-detail-col"><strong>What is being asked</strong><span>{_e(ask)}</span></div>'
            '<div class="action-detail-col"><strong>What you need to decide</strong>'
            f'<ul class="release-decisions">{"".join(lines)}</ul></div>'
            '<div class="action-detail-col"><strong>Where to go</strong><span>Reply to Claude in '
            "chat with what to accept, clear or remove; it re-runs the checks.</span></div>"
            "</div>"
            "</div>"
        )
    return items


def _lede_meter_html(m: dict, lines: list[str]) -> str:
    """Render horizontal distribution progress bar across pipeline stages.

    Labels come from ``prospect_status.LABELS`` — the same words the terminal and the
    worklist table use for these exact buckets — never a re-invented "Live"/"Staged"
    vocabulary. Until this fix, ``in_sending_tool`` (people already loaded, who may not
    be loaded again) was labelled "Live in Sequencer — Dispatched", which reads as
    active sending regardless of whether any sequence is. Whether anything is actually
    sending is a separate question, answered by ``go_live_status`` in the badge below,
    never by this bucket's size.

    No regex fallback: when ``prospect_status`` (the router's own counts) is unavailable,
    the meter renders nothing rather than re-deriving numbers by parsing the lede's prose.
    """
    ps = m.get("prospect_status") or {}
    counts = ps.get("counts") or {}
    total_people = ps.get("total") or 0
    if total_people <= 0:
        return ""

    live_count = counts.get("in_sending_tool", 0)
    staging_count = counts.get("ready_to_send", 0)
    yours_count = counts.get("waiting_on_you", 0)
    held_count = counts.get("being_fixed", 0)
    excluded_count = counts.get("not_emailing", 0)

    live_pct = (live_count / total_people) * 100
    staging_pct = (staging_count / total_people) * 100
    yours_pct = (yours_count / total_people) * 100
    held_pct = (held_count / total_people) * 100
    aside_pct = max(0, 100 - live_pct - staging_pct - yours_pct - held_pct)

    go_live_word = GO_LIVE_WORDS.get(m.get("go_live_status") or "unknown", "")
    live_badge = (
        f'<span class="pill" style="font-size:12px; margin-left:auto;">Sending Tool: {_e(go_live_word)}</span>'
        if go_live_word
        else ""
    )
    split = m.get("sending_split") or {}
    live_label = STATUS_LABELS["in_sending_tool"] + (
        f" — {split.get('sending', 0):,} sending now, {split.get('paused', 0):,} loaded but paused"
        + (f", {split['unknown']:,} status unknown" if split.get("unknown") else "")
        if split
        else ""
    )
    staging_label = status_label(m, "ready_to_send")
    yours_label = STATUS_LABELS["waiting_on_you"]
    held_label = STATUS_LABELS["being_fixed"]
    excluded_label = STATUS_LABELS["not_emailing"]

    return f"""
        <div class="lede-progress-card">
          <div class="lede-progress-title">
            <span>Pipeline &amp; Prospect Pool Allocation ({total_people:,} Prospects)</span>
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="muted">{live_count:,} in the sending tool · {staging_count:,} sorted · {yours_count:,} waiting on you · {held_count:,} being reworked · {excluded_count:,} closed</span>
              {live_badge}
            </div>
          </div>
          <div class="lede-progress-bar">
            <div class="lede-progress-seg seg-live" style="width:{live_pct:.1f}%;" title="{live_count:,} {_e(live_label)} ({live_pct:.1f}%)"></div>
            <div class="lede-progress-seg seg-staging" style="width:{staging_pct:.1f}%;" title="{staging_count:,} {_e(staging_label)} ({staging_pct:.1f}%)"></div>
            <div class="lede-progress-seg seg-yours" style="width:{yours_pct:.1f}%;" title="{yours_count:,} {_e(yours_label)} ({yours_pct:.1f}%)"></div>
            <div class="lede-progress-seg seg-held" style="width:{held_pct:.1f}%;" title="{held_count:,} {_e(held_label)} ({held_pct:.1f}%)"></div>
            <div class="lede-progress-seg" style="width:{aside_pct:.1f}%; background:var(--muted); opacity:0.6;" title="{excluded_count:,} {_e(excluded_label)} ({aside_pct:.1f}%)"></div>
          </div>
          <div class="lede-progress-legend">
            <div class="lede-progress-item"><span class="lede-progress-dot" style="background:var(--ok);"></span> <strong>{live_count:,}</strong> {_e(live_label)} <span class="muted" style="font-size:11.5px;">({live_pct:.1f}%)</span></div>
            <div class="lede-progress-item"><span class="lede-progress-dot" style="background:var(--teal);"></span> <strong>{staging_count:,}</strong> {_e(staging_label)} <span class="muted" style="font-size:11.5px;">({staging_pct:.1f}%)</span></div>
            <div class="lede-progress-item"><span class="lede-progress-dot" style="background:var(--accent);"></span> <strong>{yours_count:,}</strong> {_e(yours_label)} <span class="muted" style="font-size:11.5px;">({yours_pct:.1f}%)</span></div>
            <div class="lede-progress-item"><span class="lede-progress-dot" style="background:var(--muted);"></span> <strong>{held_count:,}</strong> {_e(held_label)} <span class="muted" style="font-size:11.5px;">({held_pct:.1f}%)</span></div>
            <div class="lede-progress-item"><span class="lede-progress-dot" style="background:var(--muted);"></span> <strong>{excluded_count:,}</strong> {_e(excluded_label)} <span class="muted" style="font-size:11.5px;">({aside_pct:.1f}%)</span></div>
          </div>
        </div>"""


def _actions_required_card(m: dict) -> str:
    """Founder-focused action center specifying what is waiting on human decision,
    what action to take, and where to go outside or inside this page."""
    items = []
    profile = m.get("profile") or "<profile>"

    # 1. Review sheet for held contacts
    sheet = m.get("review_sheet") or {}
    href = sheet.get("href")
    # Read directly from the router's own counts (m["prospect_status"]) — never parsed back
    # out of the lede's rendered sentence, which is the same anti-pattern the meter above
    # used to carry.
    waiting_count = (m.get("prospect_status") or {}).get("counts", {}).get("waiting_on_you", 0)

    if waiting_count > 0:
        link_html = (
            f'<a href="{_e(href)}" class="copy-btn" target="_blank" style="text-decoration:none; display:inline-flex; align-items:center; gap:6px;">Open Review Sheet ↗</a>'
            if href
            else (
                '<div class="action-prompt-copy-box" style="margin-top:4px; display:flex; flex-direction:column; align-items:flex-start; gap:8px;">'
                f'<span class="action-prompt-text" style="word-break:break-all; font-size:12px; line-height:1.45;">python -m gtm_core.prospect_status_cli --profile {profile}</span>'
                '<button class="copy-btn" onclick="copyPrompt(this)" style="min-height:30px; padding:4px 12px; font-size:12px;">Copy Command</button>'
                "</div>"
            )
        )
        items.append(
            '<div class="action-item-box">'
            '<div class="action-item-header">'
            f'<div class="action-item-title">Decision: Review {waiting_count:,} Held Contacts</div>'
            '<span class="pill">Action Waiting on You</span>'
            "</div>"
            '<div class="action-detail-grid">'
            '<div class="action-detail-col"><strong>What is being asked</strong><span>Angle &amp; persona fit approval before copy can be drafted.</span></div>'
            '<div class="action-detail-col"><strong>What you need to do</strong><span>Review proposed angles and mark Approve, Skip, or Re-target.</span></div>'
            f'<div class="action-detail-col"><strong>Where to go</strong><div class="action-links">{link_html}</div></div>'
            "</div>"
            "</div>"
        )

    # 2. Quality & Go-Live Safety Checks
    readiness = m.get("readiness")
    state = getattr(readiness, "state", "none")
    if state != "ok":
        # The command that actually runs the pre-flight gate, not the dashboard renderer —
        # re-rendering this page never re-checks anything; it only redraws the last answer.
        cmd = f"uv run python -m gtm_core.preflight_report --profile {profile} --warn-only"
        why = {
            "none": "Pre-flight checks have not been run for this profile yet.",
            "stale": "A file the pre-flight gate reads has changed since the checks last ran.",
            "unreadable": getattr(readiness, "problem", "")
            or "The stored pre-flight answer could not be read.",
        }.get(state, "Go-live status is unknown.")
        items.append(
            '<div class="action-item-box">'
            '<div class="action-item-header">'
            '<div class="action-item-title">Quality Gate: Re-run Pre-Flight Safety Checks</div>'
            '<span class="pill">Safety Check Needed</span>'
            "</div>"
            '<div class="action-detail-grid">'
            f'<div class="action-detail-col"><strong>What is being asked</strong><span>{_e(why)}</span></div>'
            '<div class="action-detail-col"><strong>What you need to do</strong><span>Re-run health checks to verify deliverability, suppression, and domain reputation.</span></div>'
            '<div class="action-detail-col"><strong>Where to go</strong>'
            '<div class="action-prompt-copy-box" style="margin-top:4px; display:flex; flex-direction:column; align-items:flex-start; gap:8px;">'
            f'<span class="action-prompt-text" style="word-break:break-all; font-size:12px; line-height:1.45;">{cmd}</span>'
            '<button class="copy-btn" onclick="copyPrompt(this)" style="min-height:30px; padding:4px 12px; font-size:12px;">Copy Command</button>'
            "</div>"
            "</div>"
            "</div>"
            "</div>"
        )

    # 2b. A sorted batch the checks refused: the decisions that would release it.
    items.extend(_release_batch_items(m))

    # 3. Approval Queue (Send Cards)
    ready_accounts = m.get("ready_accounts") or []
    if ready_accounts:
        items.append(
            '<div class="action-item-box">'
            '<div class="action-item-header">'
            f'<div class="action-item-title">Review: Sign Off on {len(ready_accounts)} Ready Account Drafts</div>'
            '<span class="pill ok">Drafts Ready</span>'
            "</div>"
            '<div class="action-detail-grid">'
            '<div class="action-detail-col"><strong>What is being asked</strong><span>Personalized drafts generated &amp; passed lint; awaiting final human approval.</span></div>'
            '<div class="action-detail-col"><strong>What you need to do</strong><span>Inspect generated subject lines, opening hooks, and buyer personas.</span></div>'
            '<div class="action-detail-col"><strong>Where to go</strong>'
            '<div class="action-links">'
            '<a href="#ready-to-send-panel" class="copy-btn" style="text-decoration:none;">Jump to Approval Queue ↓</a>'
            "</div>"
            "</div>"
            "</div>"
            "</div>"
        )

    # 4. Sequencer Staged Sequences
    status_seqs = (m.get("status") or {}).get("sequences") or []
    staged_seqs = [
        s for s in status_seqs if s.get("status") == "paused" or s.get("active") is False
    ]
    camps = (m.get("campaigns") or {}).get("campaigns") or []
    staged_camps = [c for c in camps if c.get("state") == "staged"]
    if staged_camps or staged_seqs:
        # The tool name and its link both come from the profile's own declared sequencer —
        # never a fixed vendor URL, which would be silently wrong for any tenant on
        # Apollo or GMass and is a tenant fact that does not belong in this engine.
        tool_name, tool_url = _sending_tool(profile)
        where = (
            f'<a href="{_e(tool_url)}" target="_blank" class="copy-btn" style="text-decoration:none;">Open {_e(tool_name)} ↗</a>'
            if tool_url
            else f'<span class="muted">Open {_e(tool_name)} directly — no link is on file for it.</span>'
        )
        items.append(
            '<div class="action-item-box">'
            '<div class="action-item-header">'
            f'<div class="action-item-title">Sending Tool: Activate Paused Sequences in {_e(tool_name)}</div>'
            '<span class="pill">External Tool</span>'
            "</div>"
            '<div class="action-detail-grid">'
            '<div class="action-detail-col"><strong>What is being asked</strong><span>Sequences are staged in the sending tool in a paused state.</span></div>'
            '<div class="action-detail-col"><strong>What you need to do</strong><span>Once held contacts are resolved, toggle sequences from Paused to Active.</span></div>'
            f'<div class="action-detail-col"><strong>Where to go</strong><div class="action-links">{where}</div></div>'
            "</div>"
            "</div>"
        )

    if not items:
        return ""

    return (
        '<div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 100ms; margin-top:20px;">'
        "<h2>Actions Required</h2>"
        '<p class="note">Operational decisions and tasks waiting on human sign-off to advance the pipeline.</p>'
        + "".join(items)
        + "</div>"
    )


def _maintainer_block(m: dict) -> str:
    """The counting discrepancies ``prospects status`` prints under "For the record" — the same
    ``cross_check`` lines, on the Operator notes tab rather than the status tab. They are for
    whoever maintains the setup; the status tab keeps to what a person decides or must know."""
    problems = m.get("cross_check") or []
    if not problems:
        return ""
    items = "".join(f"<li>{_e(p)}</li>" for p in problems)
    return (
        '<div class="card"><h2>Data Reconciliation Notices</h2>'
        f"<p class='note'>Technical system checks for workspace data alignment. None of these block active sending.</p><ul>{items}</ul></div>"
    )
