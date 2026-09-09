"""The emails themselves, and how the judge read them.

Split out of :mod:`~gtm_core.email_campaign_dashboard.views_what` on 2026-09-06 under §R10.
This half answers "what does a recipient actually receive, and what did the judge say about
it" — the bodies, the verdict tally, and the two queues a rejection routes to. The rest of
`views_what` answers "how is a message built": the template, the persona axis, the capability
spread. Different questions, and this one grew the machinery (a defect-class tally, a
destination split, a per-class breakdown) that pushed the file over the cap.

Every number in here is derived at render time — §R14. The prose this replaced carried five
hand-typed counts, two of which disagreed with the file they claimed to read and with each
other three paragraphs apart.
"""

from __future__ import annotations

from .format import _e


def _later_touches(m: dict) -> str:
    """When the follow-ups land, read from the specs rather than restated.

    This sentence said "Touch 2 follows on day 5" — true of one spec on one day, and a
    sentence nobody re-reads when a cadence changes.
    """
    days = sorted(
        {(t["n"], t["day"]) for t in ((m.get("samples") or {}).get("touches") or []) if t["n"] > 1}
    )
    if not days:
        return "Only the first touch is shown; later ones are in the templates below."
    phrase = ", ".join(f"touch {n} on day {d}" for n, d in days[:4])
    return (
        f"{phrase[0].upper()}{phrase[1:]} — the same for everyone; both are in the templates below."
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
        f'<p class="note"><strong>Every drafted email has been judged: {rows} rows, '
        f"{_e(verdicts)}.</strong> Scored on the {_e(backend)} transport{batch_note}. All of them are filed "
        f"in <code>evals/{_e(src)}</code> — {_e(dests)} — because a batch's send count is not "
        "its outcome while the rejections sit unfiled.</p>"
    )


def _ranking_note(m: dict) -> str:
    """Why a verdict here is an ordering, and which half of it is not about the copy."""
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

    return (
        '<p class="note"><strong>Read those verdicts as a ranking, not a decision.</strong> The '
        "judge is UNCALIBRATED for this profile — no sealed holdout has ever passed — so the "
        "verdicts have no known error rate, and a <code>drop</code> here usually means the COPY "
        f"is wrong rather than the account. <strong>{retarget} of the {rows} are TARGETING "
        f"defects</strong> ({_classes('prospect:re-target')}), which independently corroborates "
        "the matrix-persona finding on Who we're emailing: we are arguing agent governance at "
        "people who do not own it. Those route to the <code>prospect</code> skill to re-target, "
        "never to the copy gate.</p>"
        f'<p class="note"><strong>One caveat on the other {reargue}</strong> '
        f"({_classes('spec:re-argue')}). These fire because the row carries no per-row signal — "
        "which is what a generic lane <em>is</em>. The judge's rubric assumes a researched fact "
        "per recipient, so it will rate any generic lane poorly by construction, and it rejects "
        "a well-formed category claim on the same grounds. Both are a rubric mismatch to weigh "
        "against the operator's standing instruction, not a copy defect left to fix.</p>"
        + (
            f'<p class="note"><strong>{revised} of the {rows} have been re-argued and '
            "re-scored since the first pass.</strong> Every body below is the version the judge "
            f"read, so a verdict here describes the copy as it stands: <strong>{retarget}</strong> "
            f"rows route to re-targeting and <strong>{reargue}</strong> to a rewrite. "
            "<strong>Read the split, not a trend.</strong> This judge is uncalibrated and its "
            "self-agreement is about 0.39, so a re-score of unchanged copy can move a verdict on "
            "nothing; a re-score of CHANGED copy moves both at once and the two are not "
            "separable here. What each revision was for, and what it actually cost, is in the "
            "campaign's own notes under What we'll learn.</p>"
            if revised
            else '<p class="note"><strong>Nothing below has been re-angled since that '
            "scoring.</strong> Every body on this page is the version the judge read, so a "
            f"verdict here describes the copy as it stands. The work outstanding is the {retarget} "
            "re-targets — a prospecting run to find a seat that owns the problem — not a "
            "rewrite.</p>"
        )
    )


def _samples_section(m: dict) -> str:
    """Show the emails themselves. Every other number on this page is about these."""
    s = m.get("samples") or {}
    packs, touches = s.get("packs") or [], s.get("touches") or []
    if not packs and not touches and not s.get("rendered"):
        return ""

    def _mail(subject, body, meta):
        return (
            '<div style="border:1px solid var(--line,#d0d7de);border-radius:8px;'
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
    return (
        '<div class="card"><h2>The emails themselves</h2>'
        + _judged_note(m)
        + '<p class="note"><strong>The judge reproduced the operator\'s own objection, '
        "independently.</strong> Five of the six 1:1 packs were rejected for the same reason: "
        "<em>the email asserts an architecture the evidence does not establish</em> — "
        '"infers unscoped credentials from a job-dispatch signal, but the signal only proves '
        'the pipeline exists"; "asserts build-time policy config without evidence". That is '
        "the same critique that prompted voice.md rule 9 (claim the CATEGORY, not their build), "
        "and it means rule 10 (the agent as grammatical subject) pulled the copy back toward "
        "assertion. <strong>The two rules are in tension and the current batch landed on the "
        "wrong side of it</strong> — the next revision has to satisfy both, not trade one for "
        "the other.</p>"
        '<p class="note"><strong>The judge now sees the dossier, and it changed real verdicts.</strong> '
        "Until 2026-09-05 the pack adapter fed it only the pack's one-line <code>Why-now</code>, "
        "so it rejected claims the research plainly supports — one pack for naming a customer "
        '"that does not appear in the signal evidence" (the dossier quotes it verbatim from '
        'the company\'s own site) and another for "no connection between the two companies" '
        "(the dossier records the product as that company's own). With the dossier in context the first "
        "moved <code>drop</code> to <code>re-angle</code>, and a second verdict is now grounded in "
        'the research rather than guessed: "Dossier explicitly routes this row to hold".</p>'
        '<p class="note"><strong>A conflict worth knowing about before you read any verdict.</strong> '
        'The copy now states the problem as a CATEGORY claim — <em>"typically for platforms '
        "making contractual determinations…\"</em> — on the operator's explicit instruction, so "
        "an inference drawn from a website is never asserted as fact about this reader. The judge "
        'rejects exactly that: <em>"assumes the recipient has a compliance audit problem based on '
        "what's typical for platforms, without evidence that their actual implementation "
        'has this gap."</em> Both positions are coherent and they cannot both be satisfied — a '
        "category claim has no per-recipient evidence by construction, which is the whole reason "
        "to use one. <strong>The operator's instruction wins</strong>: it is explicit and "
        "repeated, and this judge has never passed a sealed holdout. Expect "
        "<code>re-angle</code> on well-formed category copy; do not chase the verdict back into "
        "assertion, because that round-trip has already been run.</p>"
        '<p class="note"><strong>Two earlier rejections were artifacts of what the judge was '
        "shown, not of the copy.</strong> The pack adapter feeds it the pack's "
        "<code>Why-now</code> line as evidence, not the dossier behind it. One pack was dropped "
        'for naming a customer "that does not appear in the signal evidence" — the dossier '
        "names the customer verbatim from the company's own site; the Why-now field had "
        'anonymised it. A second was dropped for "no connection between the two companies" — '
        "the dossier records the product as that company's own. Both Why-now fields are corrected "
        "and rescored; the durable fix is to feed the dossier, and it is not done.</p>"
        + _ranking_note(m)
        + '<p class="note">Every number on this page is about these. The 1:1 packs are shown '
        "verbatim — one named person, no merge fields, so the body below <em>is</em> what gets "
        "sent. The sequence touches are templates and are shown with their merge tags intact: "
        "filling them in here would be a second renderer beside the merge-render gate, and two "
        "renderers is how a page starts showing copy nobody sends.</p>"
        f"<h3>Hand-written, one per account ({len(packs)})</h3>{pack_html}"
        f"<h3>Generic lane, rendered per recipient ({len(t1)})</h3>"
        '<p class="note">These are the accounts whose evidence was too thin for a 1:1 — no '
        "dated why-now survived verification — so the body argues a seat-level problem and "
        "asserts nothing about the company. Rendered here with the same renderer the "
        f"merge-render gate uses, not a preview of it. {_later_touches(m)}</p>"
        f"{rendered_html}"
        f"<h3>Templates, unrendered ({len(touches)} touches)</h3>{touch_html}"
        "</div>"
    )
