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


def _capability_card(m: dict) -> str:
    """Compliance preflight: the capability contract, shown plainly (PS20 T2.12)."""
    return "<div class='card'><h3>Compliance preflight</h3>" + _capability_lines(m) + "</div>"


def _inbound_card(m: dict) -> str:
    """Inbound lane health: the DNC mirror and the unreadable replies."""
    return "<div class='card'><h3>Inbound lane health</h3>" + _inbound_lines(m) + "</div>"


def _capability_lines(m: dict) -> str:
    from gtm_core.sequencers import render_summary

    health = m.get("inbound") or {}
    cap = health.get("capability")
    if cap:
        provider = str(cap.get("provider") or "")
        pill = _CAP_PILL.get(str(cap.get("status") or ""), "class='pill'")
        asserted = (
            f"<p><b>{_e(provider)}</b> capability contract last asserted "
            f"{_e((cap.get('ts') or '')[:10])} for sequence "
            f"{_e(str(cap.get('sequence_id') or '—'))} — "
            f"<span {pill}>{_e(str(cap.get('status') or '—'))}</span></p>"
        )
        rows = health.get("capability_rows") or []
        asserted += f"<div>{render_summary(rows, fmt='html')}</div>" if rows else ""
    else:
        asserted = (
            "<p>No <code>capability_asserted</code> row on this profile — the "
            "sequencer's contract has never been checked here. Run "
            "<code>python -m gtm_core.email_compliance preflight --profile &lt;p&gt; "
            "--provider &lt;tool&gt; --sequence-id &lt;id&gt;</code>.</p>"
        )
    return asserted


def _inbound_lines(m: dict) -> str:
    health = m.get("inbound") or {}
    dnc = health.get("dnc")
    if dnc and dnc.get("event") == "dnc_reconciled":
        findings = dnc.get("findings") or []
        pill = "class='pill risk' data-risk='do-not-contact'" if findings else "class='pill ok'"
        n_prov = _i(dnc.get("provider_emails"))
        prov_word = "provider address" if n_prov == 1 else "provider addresses"
        n_find = len(findings)
        div_word = "divergence" if n_find == 1 else "divergences"
        n_only = _i(dnc.get("provider_only"))
        only_word = "entry" if n_only == 1 else "entries"
        dnc_line = (
            f"<p>DNC mirror reconciled {_e((dnc.get('ts') or '')[:10])}: "
            f"{n_prov} {prov_word}, "
            f"<span {pill}>{n_find} {div_word}</span>"
            f" · {n_only} provider-only {only_word}, which are hand-added "
            "exclusions and are never written to <code>suppression.csv</code>.</p>"
        )
    elif dnc:
        dnc_line = (
            f"<p>Last DNC sync {_e(str(dnc.get('event')))} "
            f"on {_e((dnc.get('ts') or '')[:10])} — "
            f"{_e(str(dnc.get('reason') or ''))}. The mirror the send path blocks on may be stale.</p>"
        )
    else:
        dnc_line = (
            "<p>No DNC sync has ever run on this profile — the mirror the send "
            "path blocks on is not being refreshed (<code>gtm-dnc-sync.timer</code>).</p>"
        )

    unreadable = int(health.get("unreadable") or 0)
    unattributable = int(health.get("optout_unattributable") or 0)
    if unreadable or unattributable:
        recent = ", ".join(
            f"{_e(r['email'])} ({_e(r['ts'])})" for r in health.get("unreadable_recent") or []
        )
        reply_word = "reply" if unreadable == 1 else "replies"
        unattr_txt = f" ({unattributable} unattributable)" if unattributable else ""
        # A reply this system could not read may be an opt-out (PRD P1.5: `unread-reply`).
        unread_line = (
            f"<p><span class='pill risk' data-risk='unread-reply'>{unreadable}</span> inbound "
            f"{reply_word} this system could not read{unattr_txt} — the opt-out matcher is English-only, so "
            "these were escalated as ambiguous opt-outs and nothing was drafted to them. Most "
            f"recent: {recent or 'none'}.</p>"
        )
    else:
        unread_line = "<p>No unreadable inbound replies recorded.</p>"
    return dnc_line + unread_line
