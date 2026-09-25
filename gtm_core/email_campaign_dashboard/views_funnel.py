from __future__ import annotations

from ..prospect_lede import GO_LIVE_WORDS
from ..prospect_status_receipt import ACCOUNTS_HEADING, BUCKET_LABELS, BUCKET_NOTES, BUCKETS
from .format import _e
from .loadfiles import TTL_DAYS

_STEP = (
    "display:inline-block;padding:8px 14px;margin:4px;background:var(--panel);"
    "border:1px solid var(--line);border-radius:10px;text-align:center;"
)
_ARROW = (
    '<span class="funnel-arrow" style="margin:0 6px;color:var(--muted);font-weight:bold;">{}</span>'
)


def _attrition_funnel_block(m: dict) -> str:
    """Where each ACCOUNT stands — the same six lines, in the same words, `prospects status`
    prints. Labels and order come from ``prospect_status_receipt`` (one source of wording),
    the numbers from ``m["attrition_receipt"]``, which the model derives from the routed
    state joined to the ledger exactly as the terminal block does. The six steps add up to
    "All accounts" by construction (``verify_funnel_conservation``).

    No fallback to the CONTACT counts when the ledger is empty: a contact is not an account,
    and borrowing one for the other is how this card came to contradict the table below it.
    """
    ar = m.get("attrition_receipt") or {}
    steps = [(b, ar.get(b, 0)) for b in BUCKETS]
    parts = []
    for i, (bucket, count) in enumerate(steps):
        parts.append(
            f'<div class="funnel-step" data-bucket="{_e(bucket)}" title="{_e(BUCKET_NOTES[bucket])}" '
            f'style="{_STEP}">'
            f'<div class="funnel-label" style="font-size:12px;color:var(--muted);">'
            f"{_e(BUCKET_LABELS[bucket])}</div>"
            f'<div class="funnel-count" style="font-size:20px;font-weight:700;">{count:,}</div>'
            "</div>" + _ARROW.format("+" if i < len(steps) - 1 else "=")
        )
    parts.append(
        f'<div class="funnel-step" data-bucket="total_intake" style="{_STEP}">'
        f'<div class="funnel-label" style="font-size:12px;color:var(--muted);">'
        f"{_e(BUCKET_LABELS['total_intake'])}</div>"
        f'<div class="funnel-count" style="font-size:20px;font-weight:700;">'
        f"{ar.get('total_intake', 0):,}</div></div>"
    )
    unsorted = (
        ""
        if ar.get("from_routed_state")
        else '<p class="note">The list has not been sorted yet, so Held and Ready here are '
        "the account ledger's own wording, not the sorted list.</p>"
    )
    return f"""
      <div class="card funnel-card">
        <h2>{_e(ACCOUNTS_HEADING)}</h2>
        <p class="muted">Companies, not people. Every company in the ledger is in exactly one
        of these, so they add up to the total.</p>
        <div class="funnel-waterfall" style="display:flex;align-items:center;flex-wrap:wrap;margin:12px 0;">{"".join(parts)}</div>
        {unsorted}
      </div>"""


#: The go-live badge, by the evidence the model found — the exact words
#: ``prospect_lede.GO_LIVE_WORDS`` uses, so the badge and the lede can never say this two
#: different ways. Always a neutral pill: six words (PS20) is too many states for a
#: good/warn/bad traffic light to carry honestly.
_GO_LIVE = GO_LIVE_WORDS


def _people(n: int) -> str:
    return f"{n:,} contact{'' if n == 1 else 's'}"


def _list_line(f: dict, sorted_on: str | None) -> str:
    label = f"<strong>{_e(f['label'])}</strong>"
    if f["state"] == "load":
        return (
            f'<li><a href="{_e(f["path"])}" download>{label}</a> &mdash; '
            f"{_people(f['rows'])} cleared to load "
            f'<span class="pill ok">load this</span> '
            f'<span class="muted">{_e(f["name"])}</span></li>'
        )
    if f["state"] == "empty":
        return f'<li class="muted">{label} &mdash; nobody is cleared for this list yet.</li>'
    if f["state"] == "out_of_date":
        why = (
            f"this file is from {_e(f['stamp'])} and the list was sorted again on "
            f"{_e(sorted_on or '')}"
        )
    else:
        why = f"this file is {f['age_days']:g} days old"
    return (
        f"<li>{label} &mdash; out of date: {why}. Do not load it &mdash; sort the list "
        "again to rebuild it.</li>"
    )


def _working_line(w: dict) -> str:
    if w["state"] == "do_not_load":
        n = w["waiting"]
        return (
            "<li><strong>Working list</strong> &mdash; do NOT load this file: it still contains "
            f"{_people(n)} waiting on a decision. "
            f'<span class="pill risk" data-risk="do-not-load">do not load</span> '
            f'<span class="muted">{_e(w["name"])}, {_people(w["rows"])} in all</span></li>'
        )
    if w["state"] == "reference":
        return (
            '<li class="muted"><strong>Working list</strong> &mdash; kept for reference '
            f"({_e(w['name'])}). Load the list files above instead.</li>"
        )
    if w["state"] == "load":
        return (
            f'<li><a href="{_e(w["path"])}" download><strong>Whole list</strong></a> &mdash; '
            f"{_people(w['rows'])}. The list has not been split into sending lists, and "
            "nobody is waiting on a decision. "
            f'<span class="pill ok">load this</span> '
            f'<span class="muted">{_e(w["name"])}</span></li>'
        )
    return ""


def _safe_downloads_block(m: dict) -> str:
    """What to load: one file per sending list (see :mod:`.loadfiles` for the rule)."""
    found = m.get("load_files") or {}
    lines = [_list_line(f, found.get("sorted_on")) for f in found.get("lists") or []]
    if found.get("working"):
        lines.append(_working_line(found["working"]))
    items_html = "".join(lines) or '<li class="muted">Nothing is ready to load yet.</li>'

    word = _GO_LIVE.get(m.get("go_live_status") or "", GO_LIVE_WORDS["none"])
    go_live_badge = f'<span class="pill">{_e(word)}</span>'

    return f"""
      <div class="card safe-downloads-card">
        <h2>What to load into the sending tool</h2>
        <p>Go-Live Status: {go_live_badge}</p>
        <p class="muted">One file per sending list. Each holds only the contacts cleared for
        that list the last time the list was sorted; files older than {TTL_DAYS} days are not offered.</p>
        <ul class="steps">{items_html}</ul>
      </div>"""
