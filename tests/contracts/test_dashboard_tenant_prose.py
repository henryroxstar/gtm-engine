"""PS20 T1.7 — tenant prose under two rules.

Rule A (a count in rendered prose is derived, number words included) is the §R14 lint's job
(``tests/lint/rendered_prose_check.py``). This file holds the page-level half:

* the PRD P1.7 known sites are gone from the rendered text, on one fixture that rendered every
  one of them on the code before the fix;
* the retired identifiers are gone from the dashboard's source;
* a tenant sees only its OWN hypotheses — the engine carries none of its own (Rule B);
* the seat counts are derived from ``SEAT_COVERAGE``;
* the generic-lane finding renders only when its data condition holds.

Every name is fictional (``gtm_core.fictionalize``).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard.config import SEAT_COVERAGE, TAB_LABELS
from tests.contracts.dashboard_page import panel, section
from tests.test_email_campaign_dashboard import CSV_HEADER, SPEC, _page, _seed, _with_pool

REPO = Path(__file__).resolve().parents[2]
DATE = "20260818"
#: Dated, because a campaign claims its sample emails by its slug's date suffix
#: (``sources.samples_model``) — an undated slug renders no samples section at all.
SLUG = f"c1-{DATE}"
#: The spec declares a persona, so ``seat_fit`` has a seat to measure the lane against.
HOOK_SPEC = SPEC.replace("## 3. The copy", "hook_cell: CISO × topic surge\n\n## 3. The copy")
#: A generic lane's template: the same spec with no why-now merge — it asserts nothing about
#: the recipient's company.
GENERIC_SPEC = HOOK_SPEC.replace("> {{Why Now}}.\n>\n", "")
ROSTER_HEAD = (
    "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier,"
    "GTM_Segment,Country/Region,GTM_Why_Now,GTM_Signal_Source_URL,GTM_Verdict,Job Title\n"
)
MANIFEST = f"""slug = "{SLUG}"
title = "Campaign One"
sequences = ["S1"]
roster_globs = ["mine-hubspot.csv"]

[targets]
emails = 9
reply_rate = 0.03

[experiment]
approach = "one merge lane beside one hand-written lane"

[[experiment.will_learn]]
question = "Does the seat answer?"
how = "Replies by seat."

[[experiment.power_table]]
lift = "3x"
meaning = "one group triples the other"
n_per_arm = 40

[[experiment.hypotheses]]
id = "H1"
claim = "The seat beats the pitch"
status = "can answer"
verdict = "open"
needs = "replies"
"""
PACK = """# Outreach Pack — Copperline Labs — 2026-08-18

**Contact:** alex@copperline.example
**Capability:** identity

## Email (touch 1)

**Subject:** a question about agent audit

> Hi Alex,
>
> A short note about how an agent's actions are attributed.
>
> Sam
"""


def _visible(page: str) -> str:
    text = re.sub(r"<style>.*?</style>|<script.*?</script>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))


def _full_fixture(tmp_path, profile="acme", *, lane_why_now="", lane=None, persona=True):
    """``_seed`` plus everything a PRD P1.7 site needs to render once: a dated campaign with a
    roster, a 1:1 pack, a merge lane rendered per recipient, a judged queue carrying a revised
    row, a scored pool, a reply-rate goal and a manifest experiment.

    ``lane`` registers S1's lane in ``cells.toml`` (unregistered by default); ``"generic"``
    also gives it a template with no why-now merge. ``lane_why_now`` is the merge-lane
    recipient's why-now on the roster. ``persona=False`` drops the spec's declared persona.
    """
    _seed(tmp_path, profile)
    _with_pool(tmp_path, profile)
    pros = pc._prospects_dir(profile, tmp_path)
    base, seq = pros.parent, pros / "sequences"
    if lane:
        cells = seq / "cells.toml"
        cells.write_text(cells.read_text(encoding="utf-8") + f'lane = "{lane}"\n', encoding="utf-8")
    # The spec cells.toml names (`seat_fit` reads its persona) and its dated twin, which
    # `samples_model` renders per recipient from the dated list beside it.
    spec = GENERIC_SPEC if lane == "generic" else HOOK_SPEC
    if not persona:
        spec = spec.replace("hook_cell: CISO × topic surge\n\n", "")
    (seq / "spec-demo-2026-08-18.md").write_text(spec, encoding="utf-8")
    (seq / f"spec-demo-{DATE}.md").write_text(spec, encoding="utf-8")
    (seq / f"spec-demo-{DATE}.csv").write_text(
        CSV_HEADER + "Riley,Bello,riley@summitline.example,Chief Happiness Officer,Summitline,"
        "summitline.example,NY,United States,startup,B,,,,,,\n",
        encoding="utf-8",
    )
    (pros / "mine-hubspot.csv").write_text(
        ROSTER_HEAD
        + "Riley,Bello,riley@summitline.example,Summitline,summitline.example,verified,B,"
        f"Startup,United States,{lane_why_now},,send,Chief Happiness Officer\n"
        + "Dana,Nakamura,dana@tidewater.example,Tidewater,tidewater.example,verified,A,Builder,"
        "Singapore,Tidewater opened an agent platform,https://tidewater.example/n,send,CISO\n",
        encoding="utf-8",
    )
    (base / "plans" / "campaigns" / "c1.campaign.toml").write_text(MANIFEST, encoding="utf-8")
    pack = base / "accounts" / "copperline-labs"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / f"prospects-{DATE}-outreach-copperline-labs.md").write_text(PACK, encoding="utf-8")
    evals = pros / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    queue = [
        {
            "email": "riley@summitline.example",
            "verdict": "re-angle",
            "destination": "spec:re-argue",
            "defect_class": "no-signal-evidence",
            "copy_revised": "2026-08-20",
            "note": "no per-row evidence",
        },
        {
            "email": "dana@tidewater.example",
            "verdict": "drop",
            "destination": "prospect:re-target",
            "defect_class": "wrong-seat",
            "note": "the seat does not own the problem",
        },
    ]
    (evals / "retarget-queue-20260819.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in queue), encoding="utf-8"
    )
    return profile


def _scoped(tmp_path, profile):
    return gd.render_html(gd.scope_to_campaign(gd.build_model(profile, tmp_path), SLUG))


def _both(tmp_path, profile) -> str:
    """The rollup and the campaign page, as one visible text: some sites render only on one
    of them (the samples are per campaign; the pool panel is the rollup's)."""
    return _visible(_page(tmp_path, profile)) + " " + _visible(_scoped(tmp_path, profile))


def _fig(page, name):
    hit = re.search(rf'data-figure="{re.escape(name)}"[^>]*>(.*?)<', page, re.S)
    return hit.group(1).strip() if hit else None


# --------------------------------------------------------------------- Rule B: the known sites

#: Distinctive fragments of the PRD P1.7 known sites, as they rendered on `_full_fixture`
#: BEFORE the fix — each was checked present on that code first, because a fragment absent
#: before proves nothing. A derived site is here by its TYPED wording; the derivations
#: themselves are pinned further down.
RETIRED = [
    # views_learn: the hard-coded hypothesis and trigger story, and their scoped notices
    "Second hypothesis",
    "Superseded. This campaign's questions",
    "Not askable of this run",
    # views_samples: one campaign's judge narrative, and `_ranking_note`'s tenant claims
    "The judge reproduced the operator",
    "The judge now sees the dossier",
    "A conflict worth knowing about",
    "Two earlier rejections were artifacts",
    "no sealed holdout has ever passed",
    "matrix-persona finding",
    "One caveat on the other",
    "self-agreement",
    "0.39",
    # views_what: the persona axis, the email structure, the news-hook story, a typed count
    "Which problem we lead on",
    "How every email is built",
    "news hook was dropped",
    "Its two subjects",
    # views_learn: typed values and counts
    "startup, enterprise",
    "topic surge only",
    "Five things change",
    "four different message bodies",
    "This campaign's two lanes",
    "spread across all three enterprise messages",
    # views_who: markets, seats, feeds, qualification route
    "Three markets",
    "overwhelmingly a US motion",
    "voice guide defines seven",
    "recognises three",
    "Only one feed is actually present",
    "originally set out to answer",
    "the dominant route is a relaxed one",
    # campaigns_dashboard._experiment_block
    "148 contacts",
    "contacts on each side",
    "three times or more",
    "The five questions we set out to answer",
    # views_status: the benchmark fit claim
    "closest to who this campaign",
    "closest published comparator",
]


def test_retired_prose_is_gone_from_rendered_pages(tmp_path):
    prof = _full_fixture(tmp_path)
    text = _both(tmp_path, prof)
    scoped = _scoped(tmp_path, prof)
    em = panel(scoped, "emails")
    assert em and "<strong>Subject:</strong>" in em, (
        "the emails panel must render, or half is unread"
    )
    assert "Full experiment notes" in text, "the manifest's experiment block must render"
    for frag in RETIRED:
        assert frag not in text, frag


def test_retired_identifiers_are_gone():
    root = REPO / "gtm_core"
    files = sorted((root / "email_campaign_dashboard").glob("*.py"))
    files.append(root / "campaigns_dashboard.py")
    src = "\n".join(p.read_text(encoding="utf-8") for p in files)
    for name in ("PERSONA_AXIS", "hypothesis_full", "trigger_full"):
        assert name not in src, name


def test_a_second_tenant_sees_only_its_own_hypotheses(tmp_path):
    a = _seed(tmp_path, "alpha")
    camp = pc._prospects_dir(a, tmp_path).parent / "plans" / "campaigns"
    with (camp / "c1.campaign.toml").open("a", encoding="utf-8") as fh:
        fh.write(
            '\n[experiment]\nhypotheses = [{id = "H1", claim = "Alpha opener earns replies", '
            'status = "can answer", verdict = "Readable at this size.", needs = "nothing"}]\n'
        )
    b = _seed(tmp_path, "bravo")
    text_a, text_b = _visible(_page(tmp_path, a)), _visible(_page(tmp_path, b))
    assert "Alpha opener earns replies" in text_a
    assert "Alpha opener earns replies" not in text_b
    assert "The hypothesis" not in text_b


# ------------------------------------------------------------- Rule A: the derived sentences


def test_the_seat_counts_are_derived_from_seat_coverage(tmp_path):
    """ "seven buyer seats … recognises three" over a list of six: the count the page prints
    and the seats it names are one list, so they cannot disagree."""
    page = _page(tmp_path, _seed(tmp_path))
    note = page.split('say "other"</h3>', 1)[1].split("</p>", 1)[0]
    assert _fig(note, "seats-recognised") == str(len(SEAT_COVERAGE))
    assert len(re.findall(r"<code>[^<]+</code>", note)) == len(SEAT_COVERAGE)


def test_the_seat_counts_reflect_custom_tenant_vocabulary(tmp_path, monkeypatch):
    """When a tenant defines a custom role-vocabulary.toml with 3 seats, the page reflects 3."""
    from gtm_core.role_vocabulary import clear_cache

    clear_cache()
    try:
        profile = "custom_tenant"
        _seed(tmp_path, profile)
        prof_dir = tmp_path / "profiles" / profile / "knowledge"
        prof_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
        (prof_dir / "role-vocabulary.toml").write_text(
            'default_persona = "creator"\n'
            'segments = ["enterprise", "unspecified"]\n'
            '[[persona]]\nname = "creator"\ncues = ["creator"]\n'
            '[[persona]]\nname = "solo-founder"\ncues = ["founder"]\n'
            '[[persona]]\nname = "solutions-engineer"\ncues = ["engineer"]\n'
            '[[seat]]\nname = "creator"\npersonas = ["creator"]\nstakes = ["reach"]\n'
            '[[seat]]\nname = "founder"\npersonas = ["solo-founder"]\nstakes = ["pipeline"]\n'
            '[[seat]]\nname = "practitioner"\npersonas = ["solutions-engineer"]\nstakes = ["fit"]\n',
            encoding="utf-8",
        )
        page = _page(tmp_path, profile)
        note = page.split('say "other"</h3>', 1)[1].split("</p>", 1)[0]
        assert _fig(note, "seats-recognised") == "3"
        assert "<code>creator</code>" in note
        assert "<code>founder</code>" in note
        assert "<code>practitioner</code>" in note
    finally:
        clear_cache()


def test_the_markets_sentence_is_derived_from_the_market_data(tmp_path):
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)
    countries = m["supply"]["countries"]
    top = max(countries, key=lambda c: c["n"])
    text = _visible(gd.render_html(m))
    share = round(100 * top["n"] / m["supply"]["total"])
    assert f"{len(countries)} markets. The largest, {top['name']}, holds {share}%" in text


def test_the_experiment_heading_counts_its_own_hypotheses(tmp_path):
    text = _visible(_page(tmp_path, _full_fixture(tmp_path)))
    assert "The 1 question we set out to answer" in text


def test_the_varies_card_derives_its_values_and_its_row_count(tmp_path):
    """ "startup, enterprise" and "topic surge only" were typed; the card now reads the cells'
    own segments and the feeds actually present, and counts its own rows."""
    profile = _full_fixture(tmp_path)
    m = gd.build_model(profile, tmp_path)
    card = gd.render_html(m).split("<h2>What varies, and by how much</h2>", 1)[1]
    card = card.split("</table>", 1)[0]
    rows = re.findall(r"<tr><td><strong>([^<]+)</strong></td><td[^>]*>(\d+)</td>", card)
    varying = sum(1 for _name, n in rows if int(n) > 1)
    assert 0 < varying < len(rows), rows  # some parameters sit on one level
    said = f"{varying} of the {len(rows)} parameters below take more than one level"
    assert said in _visible(card)
    assert "1 different bodies" not in card  # count-1 grammar
    segments = sorted({c["segment"] for c in m["cells"]["cells"]})
    assert f"<td class='muted'>{', '.join(segments)}</td>" in card
    present = [f["label"] for f in m["intent"]["feeds"] if f["present"]]
    assert ("Trigger type", str(len(present))) in rows
    assert all(label in card for label in present)


# ------------------------------------------------ Rule B: a finding gated on its own condition

FINDING = "Being on the generic lane does not excuse that"
EMPTY = "The empty why-now column on these rows is correct"


def test_the_generic_lane_finding_follows_the_registered_lane(tmp_path):
    """The finding is about a GENERIC lane, so it follows the lane `cells.toml` registers —
    not an empty why-now column, which a personalised lane can have too."""
    gen = _visible(_scoped(tmp_path, _full_fixture(tmp_path, "gen", lane="generic")))
    assert FINDING in gen and EMPTY in gen
    for profile, lane in (("pers", "personalised"), ("unreg", None)):
        text = _visible(_scoped(tmp_path, _full_fixture(tmp_path, profile, lane=lane)))
        assert "do not hold the seat their own spec declares" in text  # the count stays
        assert FINDING not in text and EMPTY not in text, profile


def test_the_empty_why_now_sentence_needs_empty_rows(tmp_path):
    """On a registered generic lane whose row carries a why-now, the lane finding holds but
    "the empty why-now column" does not."""
    profile = _full_fixture(tmp_path, lane="generic", lane_why_now="Summitline opened an API")
    text = _visible(_scoped(tmp_path, profile))
    assert FINDING in text
    assert EMPTY not in text


# ------------------------------------ Rule B, follow-up: claims the P1.7 table did not list

#: Sentences about one campaign that the table did not list, deleted in the follow-up. Each
#: was checked present on `_full_fixture` before the change.
RETIRED_FOLLOW_UP = [
    "the single biggest gap",  # rollup: a ranking of the pool's gaps nothing computes
    "received seat-targeted copy",  # rollup: "received" asserts a send; "seat-targeted" a spec
    "Generic lane, rendered per recipient",  # campaign page: the lane's kind, asserted
    "too thin for a 1:1",  # campaign page: why these accounts are on it, asserted
    "asserts nothing about the company",  # campaign page: what its body does, asserted
    "Only the first touch is shown",  # "later ones are in the templates" with none to show
    "They are not all the same kind of claim",  # rollup: a mix it never checked
]


def test_follow_up_claims_are_gone(tmp_path):
    text = _both(tmp_path, _full_fixture(tmp_path))
    for frag in RETIRED_FOLLOW_UP:
        assert frag not in text, frag


def test_no_comparison_is_said_only_when_no_lane_can_show_a_difference(tmp_path):
    """The scoped lift note said the campaign "cannot power a comparison at all" whatever its
    size. It now says so only when the power computation agrees — no lane of the campaign is
    large enough for ANY lift to show (`power.detectable_lift` is None for every lane)."""
    from gtm_core.email_campaign_dashboard.forecast import _lanes
    from gtm_core.power import detectable_lift

    tiny = _full_fixture(tmp_path, "tiny")
    lint = pc._pool_dir(tiny, tmp_path) / "lint-S1.json"
    rec = json.loads(lint.read_text(encoding="utf-8"))
    lint.write_text(json.dumps({**rec, "rows": 2}), encoding="utf-8")
    sized = _full_fixture(tmp_path, "sized")  # its checked list is 3 people
    for profile, holds in ((tiny, True), (sized, False)):
        m = gd.scope_to_campaign(gd.build_model(profile, tmp_path), SLUG)
        lifts = [detectable_lift(ln["people"], m["cells"]["baseline"]) for ln in _lanes(m)]
        assert all(x is None for x in lifts) is holds, lifts  # the fixture takes its branch
        assert ("cannot power a comparison at all" in _visible(gd.render_html(m))) is holds


def test_every_email_opens_on_a_researched_sentence_only_when_every_row_carries_one(tmp_path):
    """False for a generic lane, and for any row with no clause: gated on every enrolled row
    carrying a researched opening clause, and on no registered lane being generic."""
    said = "Every email opens on one researched sentence about that company."
    assert said in _visible(_page(tmp_path, _seed(tmp_path, "held")))
    bare = _seed(tmp_path, "bare")
    listing = pc._prospects_dir(bare, tmp_path) / "sequences" / "list.csv"
    with listing.open("a", encoding="utf-8") as fh:
        fh.write(
            "Quinn,Tide,quinn@tidewater.example,CTO,Tidewater,tidewater.example,NY,"
            "United States,enterprise,A,,,,,,\n"
        )
    assert said not in _visible(_page(tmp_path, bare))
    generic = _seed(tmp_path, "generic")
    cells = pc._prospects_dir(generic, tmp_path) / "sequences" / "cells.toml"
    cells.write_text(cells.read_text(encoding="utf-8") + 'lane = "generic"\n', encoding="utf-8")
    assert said not in _visible(_page(tmp_path, generic))


def test_the_unregistered_clause_renders_only_when_no_sequence_is_registered(tmp_path):
    clause = "which this campaign's sequence spec is not registered in"
    registered = _full_fixture(tmp_path, "reg")  # S1 is in cells.toml
    assert clause not in _visible(_scoped(tmp_path, registered))
    loose = _full_fixture(tmp_path, "loose")
    manifest = (
        pc._prospects_dir(loose, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    )
    manifest.write_text(MANIFEST.replace('["S1"]', '["S9"]'), encoding="utf-8")
    assert clause in _visible(_scoped(tmp_path, loose))


#: An UNCLASSIFIED judge queue (no ``defect_class``) over the two roster addresses.
QUEUE = (
    {"email": "riley@summitline.example", "verdict": "re-angle", "destination": "spec:re-argue"},
    {"email": "dana@tidewater.example", "verdict": "drop", "destination": "prospect:re-target"},
)


def _queued(tmp_path, profile, *, revised=False, rows=QUEUE):
    """`_full_fixture` with ``rows`` as its judge queue, scoped and rendered to visible text.
    ``revised`` records a copy revision on the first row."""
    p = _full_fixture(tmp_path, profile)
    rows = [dict(r) for r in rows]
    if revised:
        rows[0]["copy_revised"] = "2026-08-20"
    queue = pc._prospects_dir(p, tmp_path) / "evals" / "retarget-queue-20260819.jsonl"
    queue.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return _visible(_scoped(tmp_path, p))


def test_the_no_revision_sentence_renders_only_when_no_judged_row_records_one(tmp_path):
    """ "Neither has been worked yet" asserted both queues' state; only the copy half is on
    record (`copy_revised`), so that half is said, and only when no judged row carries one."""
    said = "No judged row records a copy revision since it was scored"
    fresh = _queued(tmp_path, "fresh")
    worked = _queued(tmp_path, "worked", revised=True)
    assert "judged rows go" in fresh and "judged rows go" in worked  # the split renders
    assert said in fresh
    assert said not in worked
    assert "Neither has been worked yet" not in fresh + worked


def test_the_uncalibrated_note_keeps_the_reading_rule_and_drops_the_prediction(tmp_path):
    """An uncalibrated judge is read by its ordering — that follows from the record. That it
    will "reject well-formed category copy" was a prediction about one tenant's copy."""
    text = _queued(tmp_path, "uncal")  # no row carries `judge_calibrated`
    assert "read the ordering, never the count" in text
    assert "category copy" not in text


def test_the_opening_notice_points_at_the_emails_only_when_the_page_shows_them(tmp_path):
    """The scoped notice said "Every email in this campaign is shown in full" — true of no
    later touch (shown as a template) and of nothing at all on a page with no samples."""
    said = f"This campaign's emails are on the {TAB_LABELS['emails']} tab."
    with_samples_page = _scoped(tmp_path, _full_fixture(tmp_path, "shown"))
    with_samples = _visible(with_samples_page)
    assert section(panel(with_samples_page, "emails"), "packs-list")  # the samples section rendered
    assert said in with_samples
    bare = _seed(tmp_path, "unshown")  # an undated slug claims no samples
    without_page = gd.render_html(gd.scope_to_campaign(gd.build_model(bare, tmp_path), "c1"))
    assert not section(panel(without_page, "emails"), "hand-sent") and not section(
        panel(without_page, "emails"), "packs-list"
    )
    without = _visible(without_page)
    assert said not in without
    assert "Every email in this campaign is shown in full" not in with_samples + without


def test_classified_judge_rows_reach_their_account_rows(tmp_path):
    """Regression: `judge_queue` reused the address variable for its per-class tally key, so a
    row carrying a `defect_class` — every real judge row — was filed under the class name and
    never joined to its account: a blank "What happens next" and no judged-rows note."""
    from gtm_core.email_campaign_dashboard.roster import judge_queue

    profile = _full_fixture(tmp_path)  # both queue rows carry a defect_class
    q = judge_queue(profile, tmp_path)
    assert set(q) - {"__tally__"} == {"riley@summitline.example", "dana@tidewater.example"}
    assert any(k.startswith("class:prospect:re-target|") for k in q["__tally__"])  # still named
    m = gd.scope_to_campaign(gd.build_model(profile, tmp_path), SLUG)
    assert all(r["judge"] for r in m["roster"]["rows"]), "a judged row missed its account"
    page = gd.render_html(m)
    acc = panel(page, "accounts")
    rows = {
        company: row
        for row in re.findall(r"<tr data-row=.*?</tr>", acc, re.S)
        for company in re.findall(r"<td><strong>([^<]+)</strong></td>", row)
    }
    assert "Email judge: find a different seat." in rows["Tidewater"]
    assert "Email judge: rewrite the argument." in rows["Summitline"]
    assert "Where the 2 judged rows go" in _visible(panel(page, "ops"))


def test_the_later_touches_line_renders_only_when_there_are_later_touches():
    from gtm_core.email_campaign_dashboard.views_emails import _later_touches

    assert _later_touches({"samples": {"touches": [{"n": 1, "day": 0}]}}) == ""
    touches = [{"n": 1, "day": 0}, {"n": 2, "day": 5}, {"n": 3, "day": 9}]
    line = _later_touches({"samples": {"touches": touches}})
    assert line.startswith("Touch 2 on day 5, touch 3 on day 9")
    assert "both" not in line  # three touches, not two


# --------------------------------------- Rule B, review round: claims nothing on disk checks

#: Deleted in the review round; each checked present on `_full_fixture` before the change.
RETIRED_REVIEW = [
    "answerable at this size",  # the learning questions: no power computation behind it
    "a reply RATE is not",
    "least varied part of the campaign",  # the subject card: never measured
    "deliberately not personalised",
    "The largest group below is",  # "Off limits" gloss: said beside any count, zero included
    "Every drafted email has been judged",  # a drafted 1:1 pack is not in the queue
    "its follow-up already happened",  # the research verdict: a routing claim
    "routed to the generic seat lane",
]


def test_review_claims_are_gone(tmp_path):
    text = _both(tmp_path, _full_fixture(tmp_path))
    for frag in RETIRED_REVIEW:
        assert frag not in text, frag


def test_the_judged_note_counts_the_rows_it_read(tmp_path):
    """The queue holds 2 rows; the page also drafts a 1:1 pack the judge never saw."""
    text = _visible(_scoped(tmp_path, _full_fixture(tmp_path)))
    assert "The judge scored 2 rows" in text


def test_the_re_angle_definition_renders_only_beside_a_re_angle_verdict(tmp_path):
    definition = "re-angle there means the evidence was too thin for a hand-written 1:1"
    assert definition not in _visible(_scoped(tmp_path, _full_fixture(tmp_path, "plain")))
    p = _full_fixture(tmp_path, "reangled")
    roster = pc._prospects_dir(p, tmp_path) / "mine-hubspot.csv"
    text = roster.read_text(encoding="utf-8")
    roster.write_text(text.replace(",send,Chief", ",re-angle,Chief"), encoding="utf-8")
    reangled = _visible(_scoped(tmp_path, p))
    assert definition in reangled
    for retired in ("its follow-up already happened", "routed to the generic seat lane"):
        assert retired not in reangled, retired


def test_the_scoped_notices_point_only_at_what_holds(tmp_path):
    """`cells.toml` is shared, but a group from ANOTHER campaign is only there when another
    campaign registered one; and each pointer needs its target to render."""
    outside = "message groups from outside this campaign"
    listed = "the numbered list above"
    limit = "the 'what it will not tell us' line"
    own = _visible(_scoped(tmp_path, _full_fixture(tmp_path, "own")))  # cells.toml: S1 only
    assert outside not in own and "another campaign's message groups" not in own
    assert listed in own  # the manifest declares will_learn
    assert limit not in own  # and no what_it_cant_tell_us
    p = _full_fixture(tmp_path, "shared")
    seq = pc._prospects_dir(p, tmp_path) / "sequences"
    (seq / "list2.csv").write_text(
        CSV_HEADER + "Ann,Oak,ann@oakridge.example,CTO,Oakridge,oakridge.example,NY,"
        "United States,startup,B,,,,,,\n",
        encoding="utf-8",
    )
    cells = seq / "cells.toml"
    cells.write_text(
        cells.read_text(encoding="utf-8") + '\n[[sequence]]\nid = "S2"\ntitle = "Other"\n'
        'csv = "list2.csv"\nspec = "spec-demo-2026-08-18.md"\n',
        encoding="utf-8",
    )
    manifest = MANIFEST.replace(
        '[[experiment.will_learn]]\nquestion = "Does the seat answer?"\nhow = "Replies by seat."\n',
        "",
    ).replace('approach = "', 'what_it_cant_tell_us = "a reply rate"\napproach = "')
    camp = pc._prospects_dir(p, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    camp.write_text(manifest, encoding="utf-8")
    shared = _visible(_scoped(tmp_path, p))
    assert outside in shared
    assert listed not in shared  # no will_learn, so no numbered list above
    assert limit in shared


def test_the_uncalibrated_note_needs_every_judged_row_uncalibrated(tmp_path):
    mixed = [dict(QUEUE[0], judge_calibrated=True), dict(QUEUE[1])]
    assert "UNCALIBRATED" not in _queued(tmp_path, "mixed", rows=mixed)
    assert "UNCALIBRATED" in _queued(tmp_path, "none")


def test_two_queues_are_named_only_when_both_are_present(tmp_path):
    said = "Those are two different queues"
    assert said in _queued(tmp_path, "both")
    one = [dict(r, destination="prospect:re-target") for r in QUEUE]
    text = _queued(tmp_path, "one", rows=one)
    assert "judged rows go" in text  # the split still renders
    assert said not in text


def test_the_denominator_gap_is_read_from_the_cited_figures(tmp_path, monkeypatch):
    """ "the gap between 0.45% and 3.4%" was typed beside the table that cites both."""
    from gtm_core.email_campaign_dashboard import views_ops

    moved = []
    for bm in views_ops.BENCHMARKS:
        bm = dict(bm)
        if bm["basis"] == "per email sent":
            bm["low"] = 0.0061
        if bm["label"] == "all industries, average":
            bm["high"] = 0.0471
        moved.append(bm)
    monkeypatch.setattr(views_ops, "BENCHMARKS", tuple(moved))
    assert "the gap between 0.61% and 4.7%" in _visible(_page(tmp_path, _seed(tmp_path)))


def test_the_judge_source_names_a_scored_date_only_when_one_is_recorded(tmp_path):
    """Once classified rows reached the table, a queue with no `filed` date read "Scored ,"."""
    assert "Scored ," not in _queued(tmp_path, "undated")
    assert "Filed in retarget-queue-20260819.jsonl" in _queued(tmp_path, "undated2")
    dated = [dict(r, filed="2026-09-19") for r in QUEUE]
    assert "Scored 2026-09-19, filed in" in _queued(tmp_path, "dated", rows=dated)


# ----------------------------------------------------------- Rule B, final round

#: Deleted or reworded in the final round; each checked present before the change.
RETIRED_FINAL = [
    "evidence is the binding constraint",  # one campaign's bottleneck, diagnosed in a tile
    "the whole segment, not a slice",  # `roster_gap` proves coverage of the scope, not this
    "goals are set on this number",  # nothing compares a manifest target to the pool figure
    "the goals are set after both",
    "is what the goals are stated on",
]


def test_final_round_claims_are_gone(tmp_path):
    text = _both(tmp_path, _full_fixture(tmp_path))
    for frag in RETIRED_FINAL:
        assert frag not in text, frag
    assert "of them cite a source" in text  # the derived half of that sub-line stays
    pool = gd.scope_to_campaign(gd.build_model(_seed(tmp_path, "pool"), tmp_path), "c1")
    scoped_pool = _visible(gd.render_html(pool))
    assert "Profile-wide, not this campaign" in scoped_pool  # the pool panel renders here
    assert "goals are on Where things stand" not in scoped_pool


def test_a_renamed_benchmark_refuses_instead_of_crashing_the_page(tmp_path, monkeypatch):
    from gtm_core.email_campaign_dashboard import views_ops

    renamed = tuple(
        dict(bm, basis="per message") if bm["basis"] == "per email sent" else bm
        for bm in views_ops.BENCHMARKS
    )
    monkeypatch.setattr(views_ops, "BENCHMARKS", renamed)
    text = _visible(_page(tmp_path, _seed(tmp_path)))
    assert "the gap between — and —" in text
    assert "no longer in the table below" in text


def test_a_mixed_lane_page_gets_no_generic_lane_finding(tmp_path):
    """EVERY sequence on the page must be registered generic — `all`, not `any`: one
    personalised sequence beside it makes the finding a claim about someone else's lane."""
    assert FINDING in _visible(_scoped(tmp_path, _full_fixture(tmp_path, "pure", lane="generic")))
    p = _full_fixture(tmp_path, "mixed", lane="generic")
    cells = pc._prospects_dir(p, tmp_path) / "sequences" / "cells.toml"
    cells.write_text(
        cells.read_text(encoding="utf-8") + '\n[[sequence]]\nid = "S2"\ntitle = "Named"\n'
        'csv = "list.csv"\nspec = "spec-demo-2026-08-18.md"\nlane = "personalised"\n',
        encoding="utf-8",
    )
    camp = pc._prospects_dir(p, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    camp.write_text(MANIFEST.replace('["S1"]', '["S1", "S2"]'), encoding="utf-8")
    text = _visible(_scoped(tmp_path, p))
    assert "do not hold the seat their own spec declares" in text  # the count still renders
    assert FINDING not in text and EMPTY not in text


def test_no_generic_lane_finding_without_a_declared_persona(tmp_path):
    """ "the spec still declares a hook_cell whose left half is a persona" needs one."""
    profile = _full_fixture(tmp_path, lane="generic", persona=False)
    text = _visible(_scoped(tmp_path, profile))
    assert "do not hold the seat their own spec declares" in text
    assert FINDING not in text and EMPTY not in text


def test_the_empty_why_now_sentence_needs_the_generic_lane(tmp_path):
    """A personalised lane's why-now column can be empty too — for want of research, which
    is not "correct"."""
    pers = _visible(_scoped(tmp_path, _full_fixture(tmp_path, "pers", lane="personalised")))
    assert EMPTY not in pers
    assert EMPTY in _visible(_scoped(tmp_path, _full_fixture(tmp_path, "gen", lane="generic")))
