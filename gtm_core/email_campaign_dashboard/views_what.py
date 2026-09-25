from __future__ import annotations

from ..hook_coverage.config import MAX_SPECS_PER_CAPABILITY as _CAP
from .config import TAB_LABELS
from .format import _barlist, _e, _pct, _scoped_out
from .views_emails import _has_samples


def _pack_notes(pm: dict, m: dict) -> str:
    # On a campaign page a profile-wide pack count is the same category error as a
    # profile-wide funnel: true, and not about this campaign. Dropped, not relabelled.
    if m.get("campaign_scope"):
        pm = dict(pm, legacy=0)
    where = ""
    legacy_word = "pack" if pm.get("legacy") == 1 else "packs"
    legacy = (
        f'<p class="muted">{pm["legacy"]:,} earlier {legacy_word}{where} are not listed: they predate '
        f"the <code>capability:</code> field (added {_e(pm['since'])}), so they are not "
        "undeclared — the field did not exist when they were written.</p>"
        if pm.get("legacy")
        else ""
    )
    if pm.get("other_campaigns"):
        other_word = "pack" if pm["other_campaigns"] == 1 else "packs"
        legacy += (
            f'<p class="muted">{pm["other_campaigns"]:,} further {other_word} belong to another '
            "campaign and are listed on its own page.</p>"
        )
    undec_word = "pack" if pm.get("undeclared") == 1 else "packs"
    warn = (
        f'<p class="muted">{pm["undeclared"]} {undec_word} declare no <code>capability:</code> — '
        "the argument-monotone cap cannot see them.</p>"
        if pm.get("undeclared")
        else ""
    )
    return f"{warn}{legacy}"


def _capability_spread(pm: dict) -> str:
    spread = "".join(
        f'<tr><td><code>{_e(cap)}</code></td><td class="num-cell">{n}</td>'
        f'<td class="muted">{f"over the cap of {_CAP} per campaign" if n > _CAP else "within cap"}</td></tr>'
        for cap, n in pm.get("spread", [])
    )
    return f"<h3>Capability spread</h3><table><tr><th>Group</th><th>Packs</th><th></th></tr>{spread}</table>"


def _opening_block(m: dict) -> str:
    # Counted over the whole pool and sourced from `cells.toml`, which this campaign's spec is
    # not registered in — so on a scoped page it renders "0 subject lines across 859 people",
    # both halves wrong. Dropped there, kept on the profile-wide page where it is correct.
    opening_card = _scoped_out(
        m,
        "What the opening line is about",
        "The event-vs-capability split is counted across the whole prospect pool, so it "
        "describes the profile's opening lines rather than this campaign's."
        # A pointer only where there is something to point at (PS20 P1.7 Rule B).
        + (
            f" This campaign's emails are on the {_e(TAB_LABELS['emails'])} tab."
            if _has_samples(m)
            else ""
        ),
    )
    sup = m["supply"]
    # Said only when true (PS20 P1.7 Rule B): every enrolled row carries a researched opening
    # clause and no registered lane is generic — a generic lane's body opens on none.
    researched = (
        sup["total"]
        and not any(s["shape"] == "none" for s in sup["signals"])
        and not any(c["lane"] == "generic" for c in m["cells"]["cells"])
    )
    every = (
        "Every email opens on one researched sentence about that company. " if researched else ""
    )
    sig_rows = "".join(
        f"<tr><td>{_e(s['kind'])}</td>"
        "<td><span class='pill'>"
        f"{_e(s['shape'])}</span></td>"
        f"<td class='num-cell'>{s['n']:,}</td>"
        f"<td class='num-cell muted'>{_pct(s['n'], sup['total'])}</td></tr>"
        for s in sup["signals"]
    )

    opening_full = f"""<div class="card">
        <h2>What the opening line is about</h2>
        <p class="note">{every}An opening sentence claims either an <strong>event</strong>,
        something that happened on a date and decays, or a <strong>capability</strong>, what
        the company does, which stays true. Both are legitimate, but only an event justifies
        "why now".</p>
        {
        _barlist(
            [
                ("something happened (event)", sup["event_shaped"]),
                ("what they already do (capability)", sup["capability_shaped"]),
            ],
            sup["total"],
        )
    }
        <table><thead><tr><th>Kind</th><th>Shape</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{sig_rows}</tbody></table>
      </div>"""
    return opening_card or opening_full


def _shared_last_subject(messages: list) -> str:
    """The shared-subject note, said only when the data holds it (PS20 PRD out-of-scope Rule B).

    It named whichever subject two groups shared as "the final email for every group" and then
    called it the least tailored, the riskiest for spam and the easiest to vary. On one-touch
    sequences there is no final email, and nothing on disk backs the other three claims. It
    renders now only when there are at least two groups, each has at least two subject-bearing
    emails, every group's last subject is the same one, and every group's final email carries
    it — a group ending on a threaded reply has no final-email subject to name.
    """
    if not all(msg["copy"] and msg["copy"][-1]["subject"] for msg in messages):
        return ""
    lasts = [[t["subject"] for t in msg["copy"] if t["subject"]] for msg in messages]
    if len(lasts) < 2 or any(len(s) < 2 for s in lasts) or len({s[-1] for s in lasts}) != 1:
        return ""
    return (
        f"<p class='note'><strong>{_e(lasts[0][-1])}</strong> is the subject of the final email "
        "in every group, so everyone on these lists sees the same last subject line.</p>"
    )


def _subjects_block(m: dict) -> str:
    # "Not registered" only when the registration check fails (PS20 P1.7 Rule B): a scoped
    # page keeps the cells.toml messages of this campaign's own sequences, so none means none.
    unregistered = ", which this campaign's sequence spec is not registered in"
    subjects_card = _scoped_out(
        m,
        "Every subject line in the campaign",
        "Subject lines are counted across the whole prospect pool and read from cells.toml"
        + ("" if m["messages"] else unregistered)
        + f". This campaign's subject lines are on the {_e(TAB_LABELS['emails'])} tab, one row per sequence.",
    )
    sup = m["supply"]

    subjects: dict[str, int] = {}
    subj_variants: dict[str, set[str]] = {}
    slots = 0
    threaded = 0
    for msg in m["messages"]:
        n = sum(c["enrolled"] for c in msg["audience"])
        for touch in msg["copy"]:
            if not touch["subject"]:
                threaded += 1
                continue
            slots += 1
            subjects[touch["subject"]] = subjects.get(touch["subject"], 0) + n
            subj_variants.setdefault(touch["subject"], set()).add(
                msg.get("title") or msg["sequence_id"]
            )
    shared = {k: v for k, v in subj_variants.items() if len(v) > 1}
    subj_rows = "".join(
        f"<tr><td><code>{_e(k)}</code></td><td class='num-cell'>{n:,}</td>"
        f"<td class='muted'>{'shared by every group' if k in shared else 'one group only'}</td></tr>"
        for k, n in sorted(subjects.items(), key=lambda kv: -kv[1])
    )
    shared_note = _shared_last_subject(m["messages"])
    reuse = slots - len(subjects)

    subjects_full = f"""      <div class="card">
        <h2>Every subject line in the campaign</h2>
        <p class="note"><strong>{len(subjects)} different subject line{
        "" if len(subjects) == 1 else "s"
    } across {sup["total"]:,} people.</strong>
        {slots} of the emails {
        "carries a subject of its own" if slots == 1 else "carry a subject of their own"
    }, of which {reuse}
        {"reuses" if reuse == 1 else "reuse"} a subject another group also gets.{
        f" The other {threaded} {'arrives as a reply' if threaded == 1 else 'arrive as replies'}"
        " inside the first email's own thread, so "
        f"{'it carries' if threaded == 1 else 'they carry'}"
        " no new subject at all — the reader sees the conversation, not a fresh pitch."
        if threaded
        else ""
    }</p>
        {shared_note}
        <table><thead><tr><th>Subject</th><th>People who receive it</th><th>Used by</th></tr>
        </thead><tbody>{subj_rows}</tbody></table>
      </div>"""
    return subjects_card or subjects_full


def _tally(m: dict) -> dict:
    return ((m.get("roster") or {}).get("judge_tally")) or {}


def _judged_note(m: dict) -> str:
    """How many rows the judge scored, and where they were filed (copied from `views_samples`).

    Every figure is read from ``retarget-queue-*.jsonl`` at render time: the prose it replaced
    carried five hand-typed counts, two of which disagreed with the queue on disk ("17 re-angle,
    7 drop" for a file holding 18 and 6) and with each other three paragraphs apart.
    """
    q = _tally(m)
    rows = q.get("rows")
    if not rows:
        return ""

    def _by_prefix(prefix: str) -> str:
        # Filter BEFORE sorting: the tally also carries non-numeric keys (the judge transport).
        got = [(k.split(":", 1)[1], n) for k, n in q.items() if k.startswith(prefix)]
        return ", ".join(f"{n} {label}" for label, n in sorted(got, key=lambda kv: -kv[1]))

    src = (m.get("roster") or {}).get("judge_source") or ""
    # Transport and batch size are recorded per record so the page does not assert them.
    backend = q.get("backend") or "recorded"
    batch = q.get("batch") or 0
    batch_note = (
        f" (batched {batch} to a prompt, so per-row independence is weaker than the "
        "one-row-per-request path)"
        if batch > 1
        else ""
    )
    return (
        # The queue's own count, never "every drafted email" (PS20 P1.7 Rule B).
        f'<p class="note"><strong>The judge scored {rows} row{"" if rows == 1 else "s"}: '
        f"{_e(_by_prefix('verdict:'))}.</strong> Scored on the {_e(backend)} transport"
        f"{batch_note}. All of them are filed in <code>evals/{_e(src)}</code> — "
        f"{_e(_by_prefix('dest:'))} — because a batch's send count is not its outcome while the "
        "rejections sit unfiled.</p>"
    )


def _ranking_note(m: dict) -> str:
    """Where the judged rows route, from the queue's counts and nothing else (copied from
    `views_samples`; PS20 P1.7 Rule B deleted one tenant's calibration and argument claims)."""
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

    carries = "carries" if revised == 1 else "carry"
    since = (
        f" {revised} of the {rows} {carries} copy revised since the first pass." if revised else ""
    )
    defect = (
        "is a targeting defect</strong> ({}) and routes"
        if retarget == 1
        else "are targeting defects</strong> ({}) and route"
    ).format(_classes("prospect:re-target"))
    return (
        f'<p class="note"><strong>{retarget} of the {rows} {defect} to the <code>prospect</code> '
        f"skill to re-target; {reargue} {'routes' if reargue == 1 else 'route'} to a rewrite "
        f"({_classes('spec:re-argue')}).{since}</p>"
    )


def _judge_notes(m: dict) -> str:
    """How the email judge read the drafts: the judged and ranking notes, or nothing when the
    judge's queue has no rows (PS20 Phase 2: Operator notes → Email quality)."""
    if not _tally(m).get("rows"):
        return ""
    return (
        '<div class="card"><h2>How the email judge read the drafts</h2>'
        + _judged_note(m)
        + _ranking_note(m)
        + "</div>"
    )


def _qa_detail(lint: dict) -> str:
    fired = lint.get("by_rule", {})
    catalogue = lint.get("checks_run", {})
    by_cat: dict[str, list[str]] = {}
    for rule, meta in sorted(catalogue.items()):
        by_cat.setdefault(meta.get("category", "other"), []).append(rule)
    cat_rows = "".join(
        f"<tr><td>{_e(cat)}</td><td class='num-cell'>{len(rules)}</td>"
        f"<td class='muted'>{_e(', '.join(r for r in rules if r in fired)) or '— all clear'}</td></tr>"
        for cat, rules in sorted(by_cat.items())
    )
    finding_rows = "".join(
        f"<tr><td>{_e(rule)}</td>"
        f"<td class='muted'>{_e(catalogue.get(rule, {}).get('protects', ''))}</td>"
        f"<td class='num-cell'>{levels.get('ERROR', 0):,}</td>"
        f"<td class='num-cell'>{levels.get('WARN', 0):,}</td></tr>"
        for rule, levels in sorted(fired.items())
    )
    check_word = "check" if len(fired) == 1 else "checks"
    errs = lint.get("errors", 0)
    warns = lint.get("warnings", 0)
    err_word = "blocking problem" if errs == 1 else "blocking problems"
    warn_word = "cosmetic note" if warns == 1 else "cosmetic notes"
    return f"""
              <p>Every email was rendered against every recipient and checked —
              <strong>{lint.get("renders", 0):,} rendered emails</strong>
              ({lint.get("rows", 0):,} people × {lint.get("touches", 0)} emails),
              <strong>{len(catalogue):,} different checks</strong> per render.
              {errs} {err_word}, {warns} {warn_word}.</p>
              <table><thead><tr><th>What is checked</th><th>Checks</th><th>Issues found</th>
              </tr></thead><tbody>{cat_rows}</tbody></table>
              <details><summary>The {len(fired)} {check_word} that flagged something</summary>
              <table><thead><tr><th>Check</th><th>Protects against</th><th>Blocking issues</th>
              <th>Cosmetic</th></tr></thead><tbody>{finding_rows}</tbody></table></details>"""
