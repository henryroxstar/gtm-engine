"""The inbound lane's health panel, on the status tab.

Split out of :mod:`.views_status` (PS20 Task 8, §R10: that file sat at its ceiling and this
task colours three of the panel's lines). Moved, not rewritten — except that its colours now
follow PRD P1.5: a failed capability contract, a DNC divergence and an unreadable reply are
risks and say which; the maintainer notes are neutral text.
"""

from __future__ import annotations

from .format import _e, _i

#: The capability contract's pill, by status (PRD P1.5): a FAIL is a compliance risk, a PASS
#: is done, and anything else — WARN included — is a state, so neutral.
_CAP_PILL = {"FAIL": "class='pill risk' data-risk='compliance'", "PASS": "class='pill ok'"}  # nosec B105


def _inbound_health_block(m: dict) -> str:
    """The inbound lane's own status: was the sequencer's contract asserted, is the DNC
    mirror current, and how many replies could this system not read.

    THE SAME OBJECT the terminal preflight and the gate preview render. The capability
    lines come from `gtm_core.sequencers.render_summary` over
    `gtm_core.capability_preflight.capability_rows`, so the three surfaces cannot disagree
    about a capability's status — the failure test plan §4.5 exists to prevent. The model
    resolves those rows (`health.capability_rows_for`, carried as `capability_rows`), because
    resolving them opens the registry file and `render_html` opens nothing (PS20 P1.6).
    """
    from gtm_core.sequencers import render_summary

    health = m.get("inbound") or {}
    cap = health.get("capability")
    dnc = health.get("dnc")

    if cap:
        provider = str(cap.get("provider") or "")
        pill = _CAP_PILL.get(str(cap.get("status") or ""), "class='pill'")
        asserted = (
            f"<p><b>{_e(provider)}</b> capability contract last asserted "
            f"<span class='tech'>{_e((cap.get('ts') or '')[:10])}</span> for sequence "
            f"<span class='tech'>{_e(str(cap.get('sequence_id') or '—'))}</span> — "
            f"<span {pill}>{_e(str(cap.get('status') or '—'))}</span></p>"
        )
        rows = health.get("capability_rows") or []
        asserted += f"<div class='tech'>{render_summary(rows, fmt='html')}</div>" if rows else ""
    else:
        asserted = (
            "<p>No <code>capability_asserted</code> row on this profile — the "
            "sequencer's contract has never been checked here. Run "
            "<code>python -m gtm_core.email_compliance preflight --profile &lt;p&gt; "
            "--provider &lt;tool&gt; --sequence-id &lt;id&gt;</code>.</p>"
        )

    if dnc and dnc.get("event") == "dnc_reconciled":
        findings = dnc.get("findings") or []
        pill = "class='pill risk' data-risk='do-not-contact'" if findings else "class='pill ok'"
        dnc_line = (
            f"<p>DNC mirror reconciled <span class='tech'>{_e((dnc.get('ts') or '')[:10])}</span>: "
            f"{_i(dnc.get('provider_emails'))} provider address(es), "
            f"<span {pill}>{len(findings)} divergence(s)</span>"
            f" · {_i(dnc.get('provider_only'))} provider-only entr(y/ies), which are hand-added "
            "exclusions and are never written to <code>suppression.csv</code>.</p>"
        )
    elif dnc:
        dnc_line = (
            f"<p>Last DNC sync <span class='tech'>{_e(str(dnc.get('event')))}</span> "
            f"on <span class='tech'>{_e((dnc.get('ts') or '')[:10])}</span> — "
            f"{_e(str(dnc.get('reason') or ''))}. The mirror the send path blocks on may be stale.</p>"
        )
    else:
        dnc_line = (
            "<p>No DNC sync has ever run on this profile — the mirror the send "
            "path blocks on is not being refreshed (<code>gtm-dnc-sync.timer</code>).</p>"
        )

    unreadable = int(health.get("unreadable") or 0)
    if unreadable:
        recent = ", ".join(
            f"{_e(r['email'])} ({_e(r['ts'])})" for r in health.get("unreadable_recent") or []
        )
        # A reply this system could not read may be an opt-out (PRD P1.5: `unread-reply`).
        unread_line = (
            f"<p><span class='pill risk' data-risk='unread-reply'>{unreadable}</span> inbound "
            "repl(y/ies) this system could not read — the opt-out matcher is English-only, so "
            "these were escalated as ambiguous opt-outs and nothing was drafted to them. Most "
            f"recent: {recent}.</p>"
        )
    else:
        unread_line = "<p class='tech'>No unreadable inbound replies recorded.</p>"

    return (
        "<div class='card'><h3>Inbound lane health</h3>"
        + asserted
        + dnc_line
        + unread_line
        + "</div>"
    )
