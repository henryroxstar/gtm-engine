from __future__ import annotations

from ..hook_coverage.config import MAX_SPECS_PER_CAPABILITY as _CAP
from .config import PERSONA_AXIS, SEAT_COVERAGE
from .format import _barlist, _e, _pct, _scoped_out, _seat_label
from .views_samples import _samples_section


def _packs_section(m: dict) -> str:
    """The 1:1 lane. Absent from this page until 2026-09-04 — see :func:`packs_model`."""
    pm = m.get("packs") or {}
    rows = pm.get("packs") or []
    if not rows:
        return ""
    spread = "".join(
        f'<tr><td><code>{_e(cap)}</code></td><td class="num-cell">{n}</td>'
        f'<td class="muted">{f"over the cap of {_CAP} per campaign" if n > _CAP else "within cap"}</td></tr>'
        for cap, n in pm.get("spread", [])
    )
    body = "".join(
        f"<tr><td><strong>{_e(r['account'])}</strong></td>"
        f"<td class='muted'>{_e(r['date'])}</td>"
        + (
            f"<td><code>{_e(r['capability'])}</code></td>"
            if r["capability"]
            else '<td><span class="pill warn">undeclared</span></td>'
        )
        + f"<td class='muted'>{_e(r['rules_version'] or '—')}</td></tr>"
        for r in rows
    )
    # On a campaign page a profile-wide pack count is the same category error as a
    # profile-wide funnel: true, and not about this campaign. Dropped, not relabelled.
    if m.get("campaign_scope"):
        pm = dict(pm, legacy=0)
    where = ""
    legacy = (
        f'<p class="muted">{pm["legacy"]:,} earlier pack(s){where} are not listed: they predate '
        f"the <code>capability:</code> field (added {_e(pm['since'])}), so they are not "
        "undeclared — the field did not exist when they were written.</p>"
        if pm.get("legacy")
        else ""
    )
    if pm.get("other_campaigns"):
        legacy += (
            f'<p class="muted">{pm["other_campaigns"]:,} further pack(s) belong to another '
            "campaign and are listed on its own page.</p>"
        )
    warn = (
        f'<p class="muted">{pm["undeclared"]} pack(s) declare no <code>capability:</code> — '
        "the argument-monotone cap cannot see them.</p>"
        if pm.get("undeclared")
        else ""
    )
    return (
        '<div class="card"><h2>1:1 packs — the hand-written lane</h2>'
        "<p>One named person per pack, so these enrol nobody and carry no sequence id. Every "
        "other source on this page reads <code>cells.toml</code>, campaigns or outcomes, and a "
        "pack is in none of them — so until 2026-09-04 this page described the merge lane only."
        "</p>"
        f"<table><tr><th>Account</th><th>Date</th><th>Capability argued</th><th>Rules</th></tr>{body}</table>"
        f"{warn}{legacy}"
        f"<h3>Capability spread</h3><table><tr><th>Group</th><th>Packs</th><th></th></tr>{spread}</table>"
        "</div>"
    )


def _what_view(m: dict) -> str:
    # Counted over the whole pool and sourced from `cells.toml`, which this campaign's spec is
    # not registered in — so on a scoped page it renders "0 subject lines across 859 people",
    # both halves wrong. Dropped there, kept on the profile-wide page where it is correct.
    opening_card = _scoped_out(
        m,
        "What the opening line is about",
        "The event-vs-capability split is counted across the whole prospect pool, so it "
        "describes the profile's opening lines rather than this campaign's. Every email in "
        "this campaign is shown in full under The emails themselves.",
    )
    subjects_card = _scoped_out(
        m,
        "Every subject line in the campaign",
        "Subject lines are counted across the whole prospect pool and read from cells.toml, "
        "which this campaign's sequence spec is not registered in. Its two subjects are in the "
        "spec itself; the sequence table on Where things stand names the sequence.",
    )
    sup = m["supply"]

    axis_rows = "".join(
        f"<tr><td><strong>{_e(seat)}</strong>"
        + (
            ' <span class="pill good">detected</span>'
            if seat in SEAT_COVERAGE.values()
            else ' <span class="pill warn">not detected</span>'
        )
        + f"</td><td>{_e(pain)}</td><td class='muted'>{_e(gain)}</td></tr>"
        for seat, pain, gain in PERSONA_AXIS
    )

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
    shared_note = ""
    if shared:
        names = ", ".join(f"<code>{_e(x)}</code>" for x in sorted(shared))
        shared_note = (
            f"<p class='note'><strong>{names} is the subject of the final email for every "
            f"group.</strong> It is the only subject not tailored to the reader's job, so the "
            f"last thing all {max(subjects.values()):,} people see is identical and arrives in "
            "the same window. That is the highest-risk line for spam filtering, and the single "
            "easiest thing to vary.</p>"
        )

    sig_rows = "".join(
        f"<tr><td>{_e(s['kind'])}</td>"
        f"<td><span class='pill {'good' if s['shape'] == 'event' else 'warn'}'>"
        f"{_e(s['shape'])}</span></td>"
        f"<td class='num-cell'>{s['n']:,}</td>"
        f"<td class='num-cell muted'>{_pct(s['n'], sup['total'])}</td></tr>"
        for s in sup["signals"]
    )

    seq_blocks = []
    for msg in m["messages"]:
        n = sum(c["enrolled"] for c in msg["audience"])
        touches = "".join(
            f"<tr><td>Email {c['step']}<br><span class='muted'>day {c['day']}</span></td>"
            + (
                f"<td><code>{_e(c['subject'])}</code></td>"
                if c["subject"]
                else "<td class='muted'>reply inside the first email's thread — no new subject</td>"
            )
            + f"<td class='muted'>{_e(c['opener'][:200])}</td>"
            + f"<td class='num-cell muted'>{c['words']}w</td></tr>"
            for c in msg["copy"]
        )
        lint = msg["lint"]
        if lint:
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
            ok = lint.get("verdict") == "PASS"
            drift = lint.get("drift") or []
            # The state stays here, next to the badge it qualifies — a PASS must never read
            # as "ready to send" while the checked copy and the loaded copy differ. The
            # procedure that clears it lives in the Operator notes panel.
            drift_note = (
                '<p class="note"><span class="pill warn">not cleared to start</span> '
                "The badge below describes the reviewed files, not what would go out today. "
                "See <strong>Operator notes</strong> for what is left to do.</p>"
                if drift
                else ""
            )
            qa = f"""
              {drift_note}
              <p><span class="pill {"good" if ok else "bad"}">{_e(lint.get("verdict", "?"))}</span>
              Every email was rendered against every recipient and checked —
              <strong>{lint.get("renders", 0):,} rendered emails</strong>
              ({lint.get("rows", 0):,} people × {lint.get("touches", 0)} emails),
              <strong>{len(catalogue):,} different checks</strong> per render.
              {lint.get("errors", 0)} blocking problem(s), {lint.get("warnings", 0)} cosmetic note(s).</p>
              <table><thead><tr><th>What is checked</th><th>Checks</th><th>Issues found</th>
              </tr></thead><tbody>{cat_rows}</tbody></table>
              <details><summary>The {len(fired)} check(s) that flagged something</summary>
              <table><thead><tr><th>Check</th><th>Protects against</th><th>Blocking issues</th>
              <th>Cosmetic</th></tr></thead><tbody>{finding_rows}</tbody></table></details>"""
        else:
            qa = (
                "<p class='note'>No quality record on file for this message. Never-checked and "
                "checked-and-clean look identical here — which is exactly why the record is kept.</p>"
            )
        seats = ", ".join(f"{_seat_label(c['seat'])} ({c['enrolled']})" for c in msg["audience"])
        seq_blocks.append(
            f"""<div class="card">
              <h2>{_e(msg.get("title") or msg["sequence_id"])}</h2>
              <p class="note">{n:,} people · seats: {_e(seats)}</p>
              <table><thead><tr><th>Email</th><th>Subject</th><th>The argument it opens on</th>
              <th>Length</th></tr></thead><tbody>{touches}</tbody></table>
              <h3>Quality checks</h3>{qa}
            </div>"""
        )

    subjects_full = f"""      <div class="card">
        <h2>Every subject line in the campaign</h2>
        <p class="note"><strong>{len(subjects)} different subject lines across {
        sup["total"]:,} people.</strong>
        {slots} of the emails carry a subject of their own, of which {slots - len(subjects)}
        reuse a subject another group also gets.{
        f" The other {threaded} arrive as replies inside the first email's own thread, so they"
        " carry no new subject at all — the reader sees the conversation, not a fresh pitch."
        if threaded
        else ""
    }
        Subjects are deliberately not personalised (the personalisation is the first line of the
        body), but this is the least varied part of the campaign.</p>
        {shared_note}
        <table><thead><tr><th>Subject</th><th>People who receive it</th><th>Used by</th></tr>
        </thead><tbody>{subj_rows}</tbody></table>
      </div>"""

    opening_full = f"""<div class="card">
        <h2>What the opening line is about</h2>
        <p class="note">Every email opens on one researched sentence about that company.
        They are not all the same kind of claim: an <strong>event</strong> is something that
        happened on a date and decays; a <strong>capability</strong> describes what the company
        does and stays true. Both are legitimate, but only an event justifies "why now".</p>
        {
        _barlist(
            [
                ("something happened (event)", sup["event_shaped"]),
                ("what they already do (capability)", sup["capability_shaped"]),
            ],
            sup["total"],
        )
    }
        <p class="note"><strong>{_pct(sup["capability_shaped"], sup["total"])} are capability
        statements.</strong> Worth knowing, because the sequence spec describes the clause as
        "event-shaped, not a static capability statement" — the shipped list is mostly the
        latter. The campaign's own experiment notes say the news hook was dropped deliberately;
        the spec text was not updated to match.</p>
        <table><thead><tr><th>Kind</th><th>Shape</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{sig_rows}</tbody></table>
      </div>"""

    return f"""
      <div class="card">
        <h2>How every email is built</h2>
        <p class="note">Each email follows one fixed structure. Only the first line and the
        seat-specific problem change per person.</p>
        <ol class="steps">
          <li><strong>A fact about them</strong> — one verified sentence about that specific
              company, researched and checked against a primary source.</li>
          <li><strong>Their seat's problem</strong> — the thing that job actually loses sleep
              over, never a generic pitch.</li>
          <li><strong>Why their current stack can't close it</strong> — identity tooling
              records what happened; it does not prove who authorised it.</li>
          <li><strong>Proof</strong> — one comparable customer, described by company type,
              never named.</li>
          <li><strong>One offer</strong> — we put a single artifact on the table and let them
              take it or not. We are not asking them for anything: no meeting request, no
              calendar link, no "quick call". "Want the short write up?" is the shape.</li>
        </ol>
      </div>

      <div class="card">
        <h2>Which problem we lead on, per job</h2>
        <p class="note">The rule the copy is written against. "Detected" means our automated
        check can recognise that title and verify the email matches the seat; the other four
        are written by hand and unverified.</p>
        <table><thead><tr><th>Job</th><th>What we lead on</th><th>What they get</th></tr></thead>
        <tbody>{axis_rows}</tbody></table>
      </div>

      {subjects_card}

      {subjects_card or subjects_full}

      {opening_card or opening_full}

      {_packs_section(m)}

      {_samples_section(m)}

      {"".join(seq_blocks)}"""
