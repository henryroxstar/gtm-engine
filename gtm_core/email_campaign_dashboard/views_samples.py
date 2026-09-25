"""The emails themselves, and how the judge read them.

Split out of :mod:`~gtm_core.email_campaign_dashboard.views_what` on 2026-09-06 under §R10.
This half answers "what does a recipient actually receive, and what did the judge say about
it" — the bodies, the verdict tally, and the two queues a rejection routes to. The rest of
`views_what` answers "how is a message built": the subject lines, the opening lines, the
capability spread. Different questions, and this one grew the machinery (a defect-class tally, a
destination split, a per-class breakdown) that pushed the file over the cap.

Every number in here is derived at render time — §R14. The prose this replaced carried five
hand-typed counts, two of which disagreed with the file they claimed to read and with each
other three paragraphs apart.
"""

from __future__ import annotations

from .format import _e


def _later_touches(m: dict) -> str:
    """When the follow-ups land, read from the specs rather than restated — or nothing.

    This sentence said "Touch 2 follows on day 5" — true of one spec on one day, and a
    sentence nobody re-reads when a cadence changes. Then it said "both are in the templates
    below" over any number of later touches, and "later ones are in the templates below"
    over none (PS20 P1.7).
    """
    days = sorted(
        {(t["n"], t["day"]) for t in ((m.get("samples") or {}).get("touches") or []) if t["n"] > 1}
    )
    if not days:
        return ""
    phrase = ", ".join(f"touch {n} on day {d}" for n, d in days[:4])
    return (
        f"{phrase[0].upper()}{phrase[1:]} — the same for everyone; they are in the templates below."
    )


def _tally(m: dict) -> dict:
    return ((m.get("roster") or {}).get("judge_tally")) or {}


def _judged_note(m: dict) -> str:
    """How many rows the judge scored, and where they were filed.

    Derived, not restated. The three sentences this replaces carried five hand-typed counts
    and two of them disagreed with the queue on disk and with each other — "17 re-angle,
    7 drop" for a file holding 18 and 6, and "11 re-target, 13 re-argue" in one paragraph
    against "11 re-target, 7 re-argue" three paragraphs later. Every figure below divides a
    number that is read from ``retarget-queue-*.jsonl`` at render time.
    """
    q = _tally(m)
    rows = q.get("rows")
    if not rows:
        return ""

    def _by_prefix(prefix: str) -> str:
        # Filter BEFORE sorting: the tally also carries non-numeric keys (the judge transport),
        # and `key=lambda kv: -kv[1]` over the whole dict negates a string.
        got = [(k.split(":", 1)[1], n) for k, n in q.items() if k.startswith(prefix)]
        return ", ".join(f"{n} {label}" for label, n in sorted(got, key=lambda kv: -kv[1]))

    verdicts = _by_prefix("verdict:")
    dests = _by_prefix("dest:")
    src = (m.get("roster") or {}).get("judge_source") or ""
    # The transport and its batch size are recorded per record precisely so the page does not
    # have to assert them: `backend` and `judge_batch` exist because an SDK spawn batches rows
    # and an API call does not, and a holdout scored across both is a confound worth seeing.
    backend = q.get("backend") or "recorded"
    batch = q.get("batch") or 0
    batch_note = (
        f" (batched {batch} to a prompt, so per-row independence is weaker than the "
        "one-row-per-request path)"
        if batch > 1
        else ""
    )
    return (
        # The queue's own count, never "every drafted email": a drafted 1:1 pack the judge was
        # never shown is on this page too (PS20 P1.7 Rule B).
        f'<p class="note"><strong>The judge scored {rows} rows: '
        f"{_e(verdicts)}.</strong> Scored on the {_e(backend)} transport{batch_note}. All of them are filed "
        f"in <code>evals/{_e(src)}</code> — {_e(dests)} — because a batch's send count is not "
        "its outcome while the rejections sit unfiled.</p>"
    )


def _ranking_note(m: dict) -> str:
    """Where the judged rows route, built from the queue's counts and nothing else.

    PS20 P1.7 Rule B: this note also carried one tenant's claims — a calibration state, a
    typed self-agreement figure, the argument its copy makes and why its generic lane scores
    poorly. None of that was read from anything on disk, so it was true of one campaign on
    one day. Only the sentence built from the tally survives.
    """
    q = _tally(m)
    rows = q.get("rows")
    if not rows:
        return ""
    retarget = q.get("dest:prospect:re-target", 0)
    reargue = q.get("dest:spec:re-argue", 0)
    revised = q.get("revised", 0)

    def _classes(dest: str, n: int = 4) -> str:
        got = sorted(
            ((k.split("|", 1)[1], v) for k, v in q.items() if k.startswith(f"class:{dest}|")),
            key=lambda kv: -kv[1],
        )[:n]
        return ", ".join(f"<code>{_e(c)}</code>" for c, _n in got) or "unclassified"

    since = f" {revised} of the {rows} carry copy revised since the first pass." if revised else ""
    return (
        f'<p class="note"><strong>{retarget} of the {rows} are targeting defects</strong> '
        f"({_classes('prospect:re-target')}) and route to the <code>prospect</code> skill to "
        f"re-target; {reargue} route to a rewrite ({_classes('spec:re-argue')}).{since}</p>"
    )


def _has_samples(m: dict) -> bool:
    """Whether this page shows any email at all — the one test for "The emails themselves"."""
    s = m.get("samples") or {}
    return bool(s.get("packs") or s.get("touches") or s.get("rendered"))


def _samples_section(m: dict) -> str:
    """Show the emails themselves. Every other number on this page is about these."""
    s = m.get("samples") or {}
    packs, touches = s.get("packs") or [], s.get("touches") or []
    if not _has_samples(m):
        return ""

    def _mail(subject, body, meta):
        return (
            '<div style="border:1px solid var(--line);border-radius:8px;'
            'padding:12px 14px;margin:10px 0">'
            f'<div style="font-size:12px;opacity:.7;margin-bottom:6px">{meta}</div>'
            f"<div><strong>Subject:</strong> <code>{_e(subject)}</code></div>"
            '<pre style="white-space:pre-wrap;margin:8px 0 0;font:inherit">'
            f"{_e(body)}</pre></div>"
        )

    pack_html = "".join(
        _mail(
            x["subject"],
            x["body"],
            f"<strong>{_e(x['company'] or x['account'])}</strong> · "
            f"{_e(x['to'])} · <code>{_e(x['capability'])}</code> · {x['words']} words · "
            "1:1, sent by hand",
        )
        for x in packs
    )
    t1 = [x for x in (s.get("rendered") or []) if x["n"] == 1]
    rendered_html = "".join(
        _mail(
            x["subject"],
            x["body"],
            f"<strong>{_e(x['company'])}</strong> · {_e(x['to'])} · "
            f"<code>{_e(x['spec'])}</code> · touch 1",
        )
        for x in sorted(t1, key=lambda r: (r["company"] or "").lower())
    )
    touch_html = "".join(
        _mail(
            x["subject"],
            x["body"],
            f"<code>{_e(x['spec'])}</code> · touch {x['n']}, day {x['day']} · {x['words']} words",
        )
        for x in touches
    )
    later = _later_touches(m)
    return (
        '<div class="card"><h2>The emails themselves</h2>'
        + _judged_note(m)
        + _ranking_note(m)
        + '<p class="note">Every number on this page is about these. The 1:1 packs are shown '
        "verbatim — one named person, no merge fields, so the body below <em>is</em> what gets "
        "sent. The sequence touches are templates and are shown with their merge tags intact: "
        "filling them in here would be a second renderer beside the merge-render gate, and two "
        "renderers is how a page starts showing copy nobody sends.</p>"
        f"<h3>Hand-written, one per account ({len(packs)})</h3>{pack_html}"
        # What the section shows, and nothing about why these accounts are on it: the lane's
        # kind and its reasons were one campaign's, asserted of every merge lane (P1.7 Rule B).
        f"<h3>Merge lane, rendered per recipient ({len(t1)})</h3>"
        '<p class="note">The first email of each merge template, rendered for every recipient '
        "on its list with the same renderer the merge-render gate uses, not a preview of it."
        + (f" {later}" if later else "")
        + "</p>"
        f"{rendered_html}"
        f"<h3>Templates, unrendered ({len(touches)} touches)</h3>{touch_html}"
        "</div>"
    )
