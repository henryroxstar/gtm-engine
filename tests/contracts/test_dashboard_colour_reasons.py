"""PS20 T1.5 — a colour means one thing, and says which.

Two checks over the rendered page, each with negative controls that inject their own rule:

* **Tokens only.** No ``rgba()`` or hex literal in any stylesheet rule outside ``:root``, and
  none in an inline ``style=`` a view emits.
* **Every warn or risk says why.** The classes whose rule paints with ``--warn`` or ``--risk``
  are collected from the CSS itself, so a new one is checked without anyone listing it. Every
  element carrying one must hold a ``data-warn``/``data-risk`` reason from the closed lists in
  ``config`` (PRD §5).

Then the known sites (PRD P1.5's table): each renders its mapped class on one fixture that
makes every warn and risk site render at least once, in each of the three scopes the CLI can
produce. The HTML is parsed, so single- and double-quoted class attributes both count.
"""

from __future__ import annotations

import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard.config import RISK_REASONS, WARN_REASONS
from gtm_core.email_campaign_dashboard.scope import resolve
from gtm_core.email_campaign_dashboard.views_lede import _lede_block
from gtm_core.prospect_readiness import Readiness
from tests.test_email_campaign_dashboard import (
    SPEC,
    WINDOW,
    _page,
    _seed,
    _seed_operational,
    _write_lane_state,
)

REPO = Path(__file__).resolve().parents[2]
RAW = re.compile(r"rgba?\(|#[0-9a-fA-F]{3,8}\b")
_VOID = frozenset({"meta", "br", "hr", "img", "input", "link"})


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []  # list of dict(attrs)

    def handle_starttag(self, tag, attrs):
        self.tags.append({k: (v or "") for k, v in attrs})


def _tags(page):
    p = _Tags()
    p.feed(page.split("</style>", 1)[-1])
    return p.tags


def _css(page):
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def raw_colour_violations(page) -> list[str]:
    css = _css(page)
    root = re.search(r":root\s*\{[^}]*\}", css)
    rest = css.replace(root.group(0), "") if root else css
    bad = [r for r in re.findall(r"[^{}]+\{[^}]*\}", rest) if RAW.search(r)]
    bad += [t["style"] for t in _tags(page) if RAW.search(t.get("style", ""))]
    return bad


def reason_violations(page) -> list[str]:
    compounds = []
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", _css(page)):
        for kind in ("warn", "risk"):
            if f"var(--{kind}" in body:
                for part in sel.split(","):
                    words = part.strip().split()
                    classes = (
                        frozenset(re.findall(r"\.([A-Za-z][\w-]*)", words[0]))
                        if words
                        else frozenset()
                    )
                    if classes:
                        compounds.append((kind, classes))
    allowed = {"warn": set(WARN_REASONS), "risk": set(RISK_REASONS)}
    out = []
    for t in _tags(page):
        cls = set(t.get("class", "").split())
        kinds = {kind for kind, need in compounds if need <= cls}
        # A token painted inline is the same claim as one painted by a class.
        kinds |= {kind for kind in allowed if f"var(--{kind}" in t.get("style", "")}
        for kind in sorted(kinds):
            got = t.get(f"data-{kind}", "").split()
            if not got or not set(got) <= allowed[kind]:
                out.append(f"{sorted(cls)} {t.get('style', '')} data-{kind}={got}")
    return out


def style_blocks(page) -> int:
    """Both checks read the FIRST ``<style>``; a second one would escape them."""
    return len(re.findall(r"<style\b", page))


def _marks(page) -> set[tuple[str, str, str]]:
    """``(class, kind, reason)`` for every element that names why it is coloured."""
    out = set()
    for t in _tags(page):
        for kind in ("warn", "risk"):
            for reason in t.get(f"data-{kind}", "").split():
                out.add((" ".join(t.get("class", "").split()), kind, reason))
    return out


class _Pills(HTMLParser):
    """``text -> {class}`` for every element whose class list includes ``pill``."""

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.found: dict[str, set[str]] = {}

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append(" ".join((dict(attrs).get("class") or "").split()))

    def handle_endtag(self, tag):
        if tag not in _VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and "pill" in self.stack[-1].split() and data.strip():
            self.found.setdefault(" ".join(data.split()), set()).add(self.stack[-1])


def _pills(page) -> dict[str, set[str]]:
    p = _Pills()
    p.feed(page.split("</style>", 1)[-1])
    return p.found


# --- the fixture: every warn and risk site, once ------------------------------------------

ROSTER_HEAD = (
    "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,"
    "GTM_Tier,GTM_Segment,Country/Region,GTM_Why_Now,GTM_Signal_Source_URL,GTM_Verdict\n"
)

EXPERIMENT = """
[experiment]
approach = "one generic lane"

[[experiment.will_learn]]
question = "Does the seat answer?"
how = "Replies by seat."

[[experiment.measurement]]
stage = "reply"
instrumented = true
note = "the sending tool counts it"

[[experiment.measurement]]
stage = "meeting"
instrumented = false
note = "nobody tags it"

[[experiment.hypotheses]]
id = "H1"
claim = "The seat beats the pitch"
status = "can answer"
verdict = "open"
needs = "replies"

[[experiment.hypotheses]]
id = "H2"
claim = "Surge predicts replies"
status = "not set up"
verdict = "open"
needs = "a second arm"
"""


#: Dated, because a campaign claims its sample emails by the date suffix of its slug
#: (`sources.samples_model`): an undated slug renders no samples section at all.
SLUG = "c1-20260818"


def _seed_every_site(tmp_path, profile="acme"):
    """One page on which every warn and risk site renders at least once.

    Re-push drift comes from `_seed_operational` (checked 08-18, pushed 08-17). On top: a
    blocking copy check; a capability FAIL, a DNC divergence and an unreadable reply in the
    history; one routed row this build cannot name and one waiting on a decision, with the
    working list on disk (do not load); a two-country roster, so the filter bar and its
    hidden tripwire render; and one of its rows on S1's list while the provider holds three —
    the list-vs-provider gap. Readiness never ran, so the lede cannot be trusted. A spec
    carrying the slug's date gives the scoped pages a sample email, whose frame is an
    inline style.
    """
    _seed_operational(tmp_path, profile)
    pros = pc._prospects_dir(profile, tmp_path)
    base = pros.parent
    (base / "plans" / "campaigns" / "c1.campaign.toml").write_text(
        f'slug = "{SLUG}"\ntitle = "Campaign One"\nstatus = "active"\nsequences = ["S1"]\n'
        'roster_globs = ["mine-hubspot.csv"]\n\n[targets]\nemails = 9\n' + WINDOW + EXPERIMENT,
        encoding="utf-8",
    )
    (pros / "mine-hubspot.csv").write_text(
        ROSTER_HEAD
        + "Ada,Byte,ada@acme.example,Acme,acme.example,verified,A,Builder,Singapore,,,send\n"
        + "Grace,L,grace@harborlight.example,Harborlight,harborlight.example,verified,B,"
        "Startup,United States,,,send\n",
        encoding="utf-8",
    )
    _write_lane_state(
        tmp_path,
        profile,
        [
            {"email": "ada@acme.example", "lane": "hold", "reason": "not-a-real-trigger"},
            {"email": "grace@harborlight.example", "lane": "hold", "reason": "tier-a-generic"},
        ],
    )
    (pros / "sequences" / f"spec-demo-{SLUG[-8:]}.md").write_text(SPEC, encoding="utf-8")
    (pros / "sequences" / "ready-to-load.csv").write_text(
        "first,last,email,company\nGrace,L,grace@harborlight.example,Harborlight\n",
        encoding="utf-8",
    )
    (pros / "evals" / "retarget-queue-2026-09-20.jsonl").write_text(
        json.dumps(
            {"email": "ada@acme.example", "verdict": "drop", "destination": "prospect:re-target"}
        )
        + "\n"
        + json.dumps({"email": "grace@harborlight.example", "verdict": "send"})
        + "\n",
        encoding="utf-8",
    )
    pool = pc._pool_dir(profile, tmp_path)
    rec = json.loads((pool / "lint-S1.json").read_text(encoding="utf-8"))
    rec.update(verdict="FAIL", errors=2)
    (pool / "lint-S1.json").write_text(json.dumps(rec), encoding="utf-8")
    with (base / "history.jsonl").open("a", encoding="utf-8") as fh:
        for ev in (
            {
                "event": "capability_asserted",
                "provider": "saleshandy",
                "sequence_id": "S1",
                "status": "FAIL",
                "ts": "2026-09-20T09:00:00Z",
            },
            {
                "event": "dnc_reconciled",
                "ts": "2026-09-20T09:00:00Z",
                "provider_emails": 4,
                "provider_only": 1,
                "findings": ["gone@acme.example is on the ledger but not at the provider"],
            },
            {"event": "optout_unreadable", "email": "quiet@acme.example", "ts": "2026-09-20"},
            {"event": "prospect_run", "ts": "2026-08-01T09:00:00Z", "market": "SG", "total": 12},
        ):
            fh.write(json.dumps(ev) + "\n")
    return profile


def _render(tmp_path, profile, mode):
    m = gd.build_model(profile, tmp_path)
    if mode != "all":
        m = gd.scope_to_campaign(m, resolve(mode, SLUG, m["campaigns"]["campaigns"]).csv)
    return gd.render_html(m)


SCOPES = ("all", "open", "campaign")

#: What the fixture must make render in every scope — so the reason check below can never
#: pass by rendering nothing.
EVERY_SITE = {
    ("card lede warn", "warn", "checks-untrusted"),
    ("card warn", "warn", "figures-old"),
    ("card warn", "warn", "tripwire"),
    ("pill warn", "warn", "unmapped"),
    ("warn", "warn", "records-disagree"),
    ("pill risk", "risk", "compliance"),
    ("pill risk", "risk", "do-not-contact"),
    ("pill risk", "risk", "unread-reply"),
    ("pill risk", "risk", "re-push"),
    ("pill risk", "risk", "do-not-load"),
    ("pill risk", "risk", "blocking-check"),
}

#: The whole palette. A new token could paint red or yellow without a reason — neither
#: check reads a token's VALUE — so adding one is a decision this set has to record.
TOKENS = {
    "--bg",
    "--panel",
    "--line",
    "--ink",
    "--muted",
    "--accent",
    "--ok",
    "--warn",
    "--risk",
    "--ok-bg",
    "--ok-line",
    "--warn-bg",
    "--warn-line",
    "--warn-wash",
    "--risk-bg",
    "--pill-bg",
    "--wash",
    "--bar-b",
    "--bar-c",
}


# --- the two checks -----------------------------------------------------------------------


def test_page_colours_are_tokens_and_reasons(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    assert raw_colour_violations(page) == []
    assert reason_violations(page) == []


@pytest.mark.parametrize("mode", SCOPES)
def test_every_warn_and_risk_site_names_a_listed_reason(tmp_path, mode):
    page = _render(tmp_path, _seed_every_site(tmp_path), mode)
    assert raw_colour_violations(page) == []
    assert reason_violations(page) == []
    missing = EVERY_SITE - _marks(page)
    assert not missing, f"--scope {mode}: {sorted(missing)} did not render, so nothing checked them"
    if mode != "all":  # samples are per campaign; the rollup has none to show
        assert "<strong>Subject:</strong>" in page, "the sample email's frame never rendered"


def test_checker_catches_every_shape_of_violation(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    assert any(
        "#ff0000" in v
        for v in raw_colour_violations(page.replace("</style>", ".x{color:#ff0000}</style>"))
    )
    inline = page.replace("</body>", '<div style="color:rgba(1,2,3,.5)"></div></body>')
    assert "color:rgba(1,2,3,.5)" in raw_colour_violations(inline)
    # self-contained: inject the rule too, so this control does not depend on Task 8's CSS
    probe = page.replace("</style>", ".zz{color:var(--risk)}</style>")
    for el in (
        '<span class="zz">x</span>',
        "<span class='zz'>x</span>",
        '<span class="zz" data-risk="made-up">x</span>',
    ):
        assert any("'zz'" in v for v in reason_violations(probe.replace("</body>", el + "</body>")))
    commented = page.replace("</style>", "/* c */ .zz{color:var(--risk)}</style>")
    assert any(
        "'zz'" in v
        for v in reason_violations(commented.replace("</body>", '<span class="zz">x</span></body>'))
    )
    # Positive control: a listed reason on the same element passes.
    ok = probe.replace("</body>", '<span class="zz" data-risk="re-push">x</span></body>')
    assert not any("'zz'" in v for v in reason_violations(ok))
    # A token painted inline needs a reason exactly as a class does.
    bare = page.replace("</body>", '<span style="color:var(--risk)">x</span></body>')
    assert any("color:var(--risk)" in v for v in reason_violations(bare))
    said = page.replace(
        "</body>", '<span style="color:var(--risk)" data-risk="re-push">x</span></body>'
    )
    assert not any("color:var(--risk)" in v for v in reason_violations(said))
    assert style_blocks(page.replace("</head>", "<style>.x{}</style></head>")) == 2


def test_the_palette_is_closed_and_read_whole(tmp_path):
    page = _page(tmp_path, _seed(tmp_path))
    assert style_blocks(page) == 1
    root = re.search(r":root\s*\{([^}]*)\}", _css(page)).group(1)
    assert set(re.findall(r"(--[\w-]+)\s*:", root)) == TOKENS


# --- the known sites (PRD P1.5's table) ---------------------------------------------------


def test_known_sites_carry_their_mapped_colour(tmp_path):
    page = _render(tmp_path, _seed_every_site(tmp_path), "all")
    pills = _pills(page)
    # Risk: the what-tab state AND the re-push card's heading, one reason for both.
    assert sum(1 for t in _tags(page) if t.get("data-risk") == "re-push") == 2
    assert pills["not cleared to start"] == {"pill risk"}
    assert pills["do not load"] == {"pill risk"}
    assert pills["status unmapped"] == {"pill warn"}
    # Neutral: states, design facts, verdicts and ages. `ok` is only for done or passed
    # (plan-owner decision, Task 8): a design fact is not a pass, and fewer coloured
    # pills is the point.
    for word in (
        "drop",  # judge verdicts
        "send",
        "not set up",  # _HYP_CLS
        "yes",  # can we see this step? — a design fact, both answers neutral
        "no",
        "event",  # what the opening line is about
        "capability",
        "named seat",  # seat kind
        "not on this list",
        "paused",  # the go-live badge and the sequence state
    ):
        assert pills.get(word) == {"pill"}, word
    run_age = [cls for text, cls in pills.items() if text.startswith("last run ")]
    assert run_age == [{"pill"}]
    # Done or passed.
    for word in ("verified", "can answer"):
        assert pills.get(word) == {"pill ok"}, word
    # The three readable-group boxes (each opens on its `.vn` count); `.v` alone is neutral.
    tags = _tags(page)
    verdict_boxes = sorted(
        t["class"]
        for t, nxt in zip(tags, tags[1:], strict=False)
        if t.get("class", "").split()[:1] == ["v"] and nxt.get("class") == "vn"
    )
    assert verdict_boxes == ["v", "v ok", "v ok"]
    assert 'class="lede-line yours"' in page
    assert re.search(r"\.yours\s*\{[^}]*var\(--accent\)", _css(page))
    # The old banners that carried no untrusted number are plain cards now.
    assert '<div class="card"><h2 data-figure="ops-heading">' in page
    assert '<div class="card"><h2>What this run is actually for</h2>' in page
    # Muted, never warn: filter.js hides `.stat-why` on load, so a warning cannot live there.
    assert re.search(r"\.stat-why, \.why\s*\{[^}]*color:var\(--muted\)", _css(page))


@pytest.mark.parametrize("status", ["active", "running"])
def test_a_live_sequence_and_a_present_feed_are_states_not_passes(tmp_path, status):
    """Sequence state is a state, like the neutral go-live badge; a feed being present on
    this list is a design fact. Neither is done or passed, so neither is `ok`."""
    profile = _seed(tmp_path)
    pc._pool_dir(profile, tmp_path).joinpath("sequence-stats.json").write_text(
        json.dumps({"sequences": [{"id": "S1", "name": "Demo", "status": status}]}),
        encoding="utf-8",
    )
    m = gd.build_model(profile, tmp_path)
    m["intent"]["feeds"][0].update(present=True, n=1)
    pills = _pills(gd.render_html(m))
    assert pills.get(status) == {"pill"}
    assert pills.get("present") == {"pill"}


def test_a_passing_copy_check_is_neutral_not_a_colour(tmp_path):
    """The QA verdict is risk only when a blocking check fails; a PASS is a verdict."""
    assert _pills(_page(tmp_path, _seed(tmp_path)))["PASS"] == {"pill"}


def test_a_failed_copy_check_is_a_risk_without_an_error_count(tmp_path):
    """A record that says FAIL and carries no `errors` field still failed a blocking check."""
    profile = _seed(tmp_path)
    lint = pc._pool_dir(profile, tmp_path) / "lint-S1.json"
    rec = json.loads(lint.read_text(encoding="utf-8"))
    rec["verdict"] = "FAIL"
    del rec["errors"]
    lint.write_text(json.dumps(rec), encoding="utf-8")
    page = _page(tmp_path, profile)
    assert ("pill risk", "risk", "blocking-check") in _marks(page)
    assert _pills(page)["FAIL"] == {"pill risk"}


@pytest.mark.parametrize("state", ["ok", "stale", "unreadable", "none"])
def test_only_a_trusted_lede_is_plain(state):
    """PS15: a decision waiting on the operator is theirs, not a warning — so a lede whose
    checks are current is a plain card, and only an untrusted one is warn."""
    lines = ["As of now.", "Yours (1): decide on 1 contact — the review sheet is built."]
    html = _lede_block({"lede": lines, "readiness": Readiness(state=state)})
    card = _tags(html)[0]
    if state == "ok":
        assert card == {"class": "card lede"}
    else:
        assert card == {"class": "card lede warn", "data-warn": "checks-untrusted"}
    assert 'class="lede-line yours"' in html


@pytest.mark.parametrize(
    "status,cls,risk",
    [("FAIL", "pill risk", "compliance"), ("WARN", "pill", ""), ("PASS", "pill ok", "")],
)
def test_the_capability_pill_follows_its_status(status, cls, risk):
    from gtm_core.email_campaign_dashboard.views_status import _inbound_health_block

    cap = {"provider": "", "status": status, "ts": "2026-09-20", "sequence_id": "S1"}
    html = _inbound_health_block({"inbound": {"capability": cap}})
    pill = next(t for t in _tags(html) if "pill" in t.get("class", "").split())
    assert (pill["class"], pill.get("data-risk", "")) == (cls, risk)


def test_the_dnc_pill_and_the_maintainer_notes(tmp_path):
    from gtm_core.email_campaign_dashboard.views_status import _inbound_health_block

    dnc = {"event": "dnc_reconciled", "ts": "2026-09-20", "provider_emails": 2}
    clean = _inbound_health_block({"inbound": {"dnc": dict(dnc, findings=[])}})
    split = _inbound_health_block({"inbound": {"dnc": dict(dnc, findings=["a", "b"])}})
    assert [t["class"] for t in _tags(clean) if "pill" in t.get("class", "")] == ["pill ok"]
    assert [
        (t["class"], t.get("data-risk")) for t in _tags(split) if "pill" in t.get("class", "")
    ] == [("pill risk", "do-not-contact")]
    # Nothing checked, nothing synced: notes for whoever maintains the setup — neutral text.
    notes = _inbound_health_block({"inbound": {}})
    assert "warn" not in {c for t in _tags(notes) for c in t.get("class", "").split()}


@pytest.mark.parametrize("mode", SCOPES)
def test_no_retired_colour_class_remains(tmp_path, mode):
    page = _render(tmp_path, _seed_every_site(tmp_path), mode)
    classes = {c for t in _tags(page) for c in t.get("class", "").split()}
    assert not classes & {"banner", "bad", "good"}
    untidy = [
        t["class"]
        for t in _tags(page)
        if t.get("class", "") != " ".join(t.get("class", "").split())
    ]
    assert untidy == [], "a class attribute with stray whitespace"
    assert "--bad" not in _css(page)


def test_no_retired_colour_markup_in_the_source():
    """The branches no fixture reaches: no string in the renderer spells a retired class."""
    retired = re.compile(r"\b(?:pill (?:good|bad)|card banner|v bad)\b|--bad\b")
    pkg = REPO / "gtm_core" / "email_campaign_dashboard"
    hits = []
    for path in [*sorted(pkg.glob("*.py")), REPO / "gtm_core" / "campaigns_dashboard.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if retired.search(node.value):
                    hits.append(f"{path.name}:{node.lineno}")
    assert hits == []
