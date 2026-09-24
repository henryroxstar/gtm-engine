"""The status PAGE must say the same numbers, in the same words, as the terminal block.

`python -m gtm_core.prospects status` and `email_campaign_status.html` are two renderings of
one pipeline, read by the same non-technical operator. On 2026-09-21 a usability audit found
the page contradicting the terminal: the terminal said "Held 3" where the page said "Held 0",
the page's banner counted "accounts" where the terminal counted "contacts", three files were
all presented as safe to load, and a tenant with nothing staged read "Go-Live Status: STAGED".

Every fixture here is fictional (`.example` domains).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospect_status, prospect_status_cli
from gtm_core.email_campaign_dashboard import loadfiles
from gtm_core.prospect_status_receipt import ACCOUNTS_HEADING, BUCKET_LABELS, BUCKETS

REPO = Path(__file__).resolve().parents[1]
PROFILE = "qa-sandbox"

#: The sentence UX-07 is about: long enough to be a paragraph when repeated under every tile.
LONG_DISCLAIMER = re.compile(r"Not filtered\s*(?:—|&mdash;)")

ACCOUNT_LABELS = [BUCKET_LABELS[b] for b in (*BUCKETS, "total_intake")]
CONTACT_LABELS = [
    prospect_status.LABELS[s] for s in prospect_status.STATUSES if s != "needs_address"
]


# ------------------------------------------------------------------ reading the page


class _Visible(HTMLParser):
    """The text a reader can SEE: drops `hidden` subtrees, scripts, styles and the title.

    ``reveal`` names classes whose `hidden` is ignored — what the page looks like once the
    client-side filter has unhidden its explanations — so a test can read both states.
    """

    _VOID = frozenset({"meta", "br", "hr", "img", "input", "link"})
    _BLOCK = frozenset({"div", "p", "li", "tr", "h1", "h2", "h3", "section", "button", "label"})

    def __init__(self, reveal: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self._reveal = reveal
        self._stack: list[bool] = []
        self._out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._VOID:
            return
        a = dict(attrs)
        hidden = "hidden" in a and not (set((a.get("class") or "").split()) & self._reveal)
        hidden = hidden or tag in ("script", "style", "title")
        self._stack.append(hidden or (self._stack[-1] if self._stack else False))

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        if self._stack:
            self._stack.pop()
        if tag in self._BLOCK:
            self._out.append("\n")

    def handle_data(self, data):
        if not (self._stack and self._stack[-1]):
            self._out.append(data)

    def lines(self) -> list[str]:
        raw = (" ".join(ln.split()) for ln in "".join(self._out).splitlines())
        return [ln for ln in raw if ln]


def visible_lines(html: str, reveal: frozenset[str] = frozenset()) -> list[str]:
    parser = _Visible(reveal)
    parser.feed(html)
    return parser.lines()


def page_numbers(html: str) -> dict[str, int]:
    """``label -> number`` for the account steps and the contact tiles, as rendered."""
    out: dict[str, int] = {}
    for label, n in re.findall(
        r'class="funnel-label"[^>]*>([^<]+)</div>\s*<div class="funnel-count"[^>]*>([\d,]+)<',
        html,
    ):
        out[label.strip()] = int(n.replace(",", ""))
    for n, label in re.findall(
        r'<div class="stat-value">([\d,]+)</div><div class="stat-label">([^<]+)</div>', html
    ):
        out.setdefault(label.strip(), int(n.replace(",", "")))
    return out


def cli_numbers(block: str) -> dict[str, int]:
    """``label -> number`` from the terminal block (a label, 2+ spaces, the count)."""
    out: dict[str, int] = {}
    for line in block.splitlines():
        hit = re.match(r"^\s*(\S.*?)\s{2,}(\d+)\s{3}", line)
        if hit:
            out.setdefault(hit.group(1), int(hit.group(2)))
    return out


# ------------------------------------------------------------------ hand-built tenants


def _account(company: str, email: str = "", **extra) -> dict:
    dom = company.lower().replace(" ", "") + ".example"
    return {"company": company, "domain": dom, "contact_email": email, "status": "new", **extra}


def _routed(email: str, company: str, lane: str, reason: str, stamp: str = "2026-09-21") -> dict:
    return {
        "email": email,
        "company": company,
        "company_domain": company.lower().replace(" ", "") + ".example",
        "lane": lane,
        "reason": reason,
        "stamp": stamp,
    }


def _tenant(root: Path, accounts: list[dict], state: list[dict] | None) -> Path:
    pros = root / PROFILE / "prospects"
    (pros / "evals").mkdir(parents=True, exist_ok=True)
    (pros / "sequences").mkdir(parents=True, exist_ok=True)
    (pros / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": accounts}), encoding="utf-8"
    )
    if state is not None:
        (pros / "evals" / "lanes-state.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in state), encoding="utf-8"
        )
    return pros


MIXED_ACCOUNTS = [
    _account("Northwind Robotics", "kai.moss@northwindrobotics.example"),
    _account("Litware Pay", "oma.reyes@litwarepay.example"),
    _account("Contoso Freight", "rowan.pike@contosofreight.example"),
    _account("Fabrikam Foods", contact_name="Uma Hale"),  # named, but nowhere to write to
    _account("Lucerne Media", "ira.bloom@lucernemedia.example", verdict="re-angle"),
    _account("Tailspin Hotels", "ana.voss@tailspinhotels.example"),  # never sorted
    _account("Woodgrove Labs", "lee.tan@woodgrovelabs.example"),
]
MIXED_STATE = [
    _routed("kai.moss@northwindrobotics.example", "Northwind Robotics", "personalised", "send"),
    _routed("oma.reyes@litwarepay.example", "Litware Pay", "hold", "tier-a-generic"),
    _routed("jo.lind@litwarepay.example", "Litware Pay", "hold", "tier-a-generic"),
    _routed("rowan.pike@contosofreight.example", "Contoso Freight", "excluded", "optout"),
    _routed("lee.tan@woodgrovelabs.example", "Woodgrove Labs", "hold", "not-a-real-trigger"),
]


def _render(root: Path) -> str:
    return gd.render_html(gd.build_model(PROFILE, content_root=root))


def _terminal(monkeypatch, capsys, root: Path) -> str:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    capsys.readouterr()
    assert prospect_status_cli.main(["--profile", PROFILE]) == 0
    return capsys.readouterr().out


# ------------------------------------------------------------------ UX-02: parity


def test_every_number_on_the_page_is_the_number_the_terminal_prints(
    tmp_path, monkeypatch, capsys
) -> None:
    """THE parity test, on a tenant where every bucket is populated — so agreement cannot be
    the vacuous kind where both sides print zero."""
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    cli = cli_numbers(_terminal(monkeypatch, capsys, tmp_path))
    page = page_numbers(_render(tmp_path))

    expected = {
        "Not a fit / excluded": 1,
        "Needs a new angle (queued)": 1,
        "No usable contact yet": 1,
        "Not yet routed": 2,  # never sorted + the unrecognised one
        "Held": 1,
        "Ready": 1,
        "All accounts": 7,
        "Waiting on you": 2,
        "Routed — not yet checked": 1,
        "Being fixed": 0,
        "In the sending tool": 0,
        "Not emailing": 1,
        "Unrecognised": 1,
        "Needs an address": 1,
    }
    assert {k: cli.get(k) for k in expected} == expected, "fixture drifted from the terminal"
    assert {k: page.get(k) for k in expected} == expected


def test_the_account_steps_use_the_terminal_words_and_add_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    html = _render(tmp_path)
    text = "\n".join(visible_lines(html))

    assert ACCOUNTS_HEADING in text
    assert "Contacts — by status (people, not companies)" in text
    for old in ("Attrition", "Failed Fit", "Failed Intent", "Enrichment Miss", "Total Intake"):
        assert old not in text
    for label in ACCOUNT_LABELS:
        assert label in text
    numbers = page_numbers(html)
    assert sum(numbers[BUCKET_LABELS[b]] for b in BUCKETS) == numbers["All accounts"]


def test_the_banner_is_the_waiting_contact_count_in_contact_words(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    text = "\n".join(visible_lines(_render(tmp_path)))
    assert "2 contacts are waiting on your decision" in text
    assert "waiting on a routing decision" not in text

    one = [r for r in MIXED_STATE if r["email"] != "jo.lind@litwarepay.example"]
    _tenant(tmp_path, MIXED_ACCOUNTS, one)
    assert "1 contact is waiting on your decision" in "\n".join(visible_lines(_render(tmp_path)))


def test_no_banner_when_nobody_is_waiting_even_if_the_ledger_says_held(
    tmp_path, monkeypatch
) -> None:
    """The contradiction itself: the banner used to fall back to the ledger's own "held"
    wording when no contact was waiting, so it announced work the contact table denied."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    accounts = [_account("Litware Pay", "oma.reyes@litwarepay.example", stage="held")]
    state = [_routed("oma.reyes@litwarepay.example", "Litware Pay", "personalised", "send")]
    _tenant(tmp_path, accounts, state)
    html = _render(tmp_path)
    assert "ACTION REQUIRED" not in html
    assert page_numbers(html)["Held"] == 0 and page_numbers(html)["Ready"] == 1

    # ...and with no sorted list at all, a ledger that says "held" is still not a decision
    # anybody is waiting on.
    shutil.rmtree(tmp_path / PROFILE)
    _tenant(tmp_path, accounts, None)
    assert "ACTION REQUIRED" not in _render(tmp_path)


# ------------------------------------------------------------------ UX-04: what to load

CSV_HEAD = "first,last,email,company\n"


def _write_list(seq: Path, name: str, rows: int, *, age_days: float = 0.0) -> Path:
    path = seq / name
    body = "".join(f"P{i},Q,p{i}@litwarepay.example,Litware Pay\n" for i in range(rows))
    path.write_text(CSV_HEAD + body, encoding="utf-8")
    if age_days:
        then = time.time() - age_days * 86400
        os.utime(path, (then, then))
    return path


def _downloads(html: str) -> str:
    start = html.index('class="card safe-downloads-card"')
    return html[start : html.index("</ul>", start)]


def test_each_sending_list_names_one_file_and_the_working_list_says_do_not_load(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    seq = _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE) / "sequences"
    _write_list(seq, "ready-to-load.csv", 4)
    _write_list(seq, "ready-to-load-personalised-2026-09-21.csv", 1)
    _write_list(seq, "ready-to-load-generic-2026-09-21.csv", 0)
    block = _downloads(_render(tmp_path))
    text = "\n".join(visible_lines(block))

    assert 'href="prospects/sequences/ready-to-load-personalised-2026-09-21.csv"' in block
    assert "Personalised list — 1 contact cleared to load" in text
    assert "Generic list — nobody is cleared for this list yet" in text
    assert "ready-to-load-generic" not in block, "an empty list is not a thing to load"
    assert (
        "Working list — do NOT load this file: it still contains 2 contacts waiting on a "
        "decision" in text
    )
    assert 'href="prospects/sequences/ready-to-load.csv"' not in block
    assert block.count("download>") == 1, "exactly one thing to load"


def test_the_whole_list_is_loadable_only_when_nothing_waits_and_nothing_was_split(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    seq = _tenant(tmp_path, MIXED_ACCOUNTS[:1], MIXED_STATE[:1]) / "sequences"
    _write_list(seq, "ready-to-load.csv", 3)
    block = _downloads(_render(tmp_path))
    assert 'href="prospects/sequences/ready-to-load.csv"' in block
    assert "Whole list — 3 contacts" in "\n".join(visible_lines(block))

    # Once the list has been split, the split files are the things to load — not this one.
    _write_list(seq, "ready-to-load-personalised-2026-09-21.csv", 1)
    block = _downloads(_render(tmp_path))
    assert 'href="prospects/sequences/ready-to-load.csv"' not in block
    assert "Working list — kept for reference" in "\n".join(visible_lines(block))


def test_a_list_file_older_than_the_last_sort_is_out_of_date_and_not_offered(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    seq = _tenant(tmp_path, MIXED_ACCOUNTS[:1], MIXED_STATE[:1]) / "sequences"
    _write_list(seq, "ready-to-load-personalised-2026-09-03.csv", 5)
    block = _downloads(_render(tmp_path))
    text = "\n".join(visible_lines(block))
    assert "href=" not in block
    assert (
        "Personalised list — out of date: this file is from 2026-09-03 and the list was "
        "sorted again on 2026-09-21. Do not load it" in text
    )


def test_load_files_model_counts_rows_and_waiting_contacts(tmp_path) -> None:
    seq = _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE) / "sequences"
    _write_list(seq, "ready-to-load.csv", 4)
    _write_list(seq, "ready-to-load-generic-2026-09-21.csv", 2)
    found = loadfiles.load_files(PROFILE, tmp_path, MIXED_STATE, waiting=2)
    assert [(f["list"], f["rows"], f["state"]) for f in found["lists"]] == [("generic", 2, "load")]
    assert found["working"]["rows"] == 4 and found["working"]["waiting"] == 2
    assert found["working"]["state"] == "do_not_load"


def test_the_sending_lists_named_here_are_the_ones_the_router_writes() -> None:
    """`loadfiles.LIST_LABELS` is hand-copied (importing the router runs a whole package for
    two strings), so it is pinned: a list added to the router must get a name on the page."""
    from gtm_core.lanes import router

    assert tuple(loadfiles.LIST_LABELS) == tuple(router._LOADABLE_LANES)


def test_a_list_file_left_untouched_past_the_limit_is_not_offered(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    seq = _tenant(tmp_path, MIXED_ACCOUNTS[:1], MIXED_STATE[:1]) / "sequences"
    _write_list(
        seq, "ready-to-load-personalised-2026-09-21.csv", 5, age_days=loadfiles.TTL_DAYS + 1
    )
    block = _downloads(_render(tmp_path))
    assert "href=" not in block
    assert "out of date: this file is 8 days old" in "\n".join(visible_lines(block))


# ------------------------------------------------------------------ UX-05: go-live evidence


def _badge(html: str) -> str:
    return re.search(r"Go-Live Status: <span[^>]*>([^<]+)</span>", html).group(1)


def test_go_live_says_nothing_staged_on_a_tenant_with_nothing_staged(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    model = gd.build_model(PROFILE, content_root=tmp_path)
    assert model["go_live_status"] == "none"
    assert _badge(gd.render_html(model)) == "Nothing staged yet"
    # The view has no constant to fall back on either.
    model.pop("go_live_status")
    assert _badge(gd.render_html(model)) == "Nothing staged yet"


def test_go_live_says_staged_only_when_a_staged_sequence_is_on_record(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    (tmp_path / PROFILE / "history.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-09-20T08:00:00Z",
                "event": "sequence_staged",
                "sequence_id": "SEQ-1",
                "name": "Builders wave one",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    model = gd.build_model(PROFILE, content_root=tmp_path)
    assert model["go_live_status"] == "staged"
    assert _badge(gd.render_html(model)) == "STAGED"


# ------------------------------------------------------------------ UX-07: the disclaimer

ROSTER_HEAD = (
    "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,"
    "GTM_Tier,GTM_Segment,Country/Region,GTM_Why_Now,GTM_Signal_Source_URL,GTM_Verdict\n"
)
ROSTER_ROWS = (
    "Kai,Moss,kai.moss@northwindrobotics.example,Northwind Robotics,northwindrobotics.example,"
    "verified,A,Builder,Singapore,shipped a gateway,https://northwindrobotics.example/a,send\n"
    "Oma,Reyes,oma.reyes@litwarepay.example,Litware Pay,litwarepay.example,verified,B,Startup,"
    "United States,raised a round,,send\n"
)


def _roster_page(root: Path) -> str:
    pros = _tenant(root, MIXED_ACCOUNTS, MIXED_STATE)
    (pros / "mine-20260904-hubspot.csv").write_text(ROSTER_HEAD + ROSTER_ROWS, encoding="utf-8")
    camps = root / PROFILE / "plans" / "campaigns"
    camps.mkdir(parents=True, exist_ok=True)
    (camps / "mine-20260904.campaign.toml").write_text(
        'slug = "mine-20260904"\ntitle = "Builders"\nstatus = "active"\n'
        'roster_globs = ["mine-20260904-hubspot.csv"]\n[targets]\nprospects = 2\n',
        encoding="utf-8",
    )
    return _render(root)


def test_the_long_disclaimer_is_invisible_unfiltered_and_appears_once_when_filtering(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    html = _roster_page(tmp_path)
    assert 'id="filterbar"' in html, "fixture must render a filter, or this test guards nothing"

    unfiltered = "\n".join(visible_lines(html))
    assert not LONG_DISCLAIMER.search(unfiltered)
    assert "not filtered" not in unfiltered.lower()

    # What the filter script unhides once a facet is selected.
    shown = frozenset({"stat-why", "why", "filter-note"})
    filtering = visible_lines(html, reveal=shown)
    assert len([ln for ln in filtering if LONG_DISCLAIMER.search(ln)]) <= 1
    assert sum(ln == "not filtered" for ln in filtering) >= 3, "each frozen figure is still marked"
    # The exact reason survives, as a tooltip on the mark.
    assert re.search(r'class="stat-why" hidden title="Not filtered — this figure [^"]+"', html)
    # ...and the one explanation sits with the filter control, before the first panel.
    assert html.index('id="filter-note"') < html.index('<section id="p-')


# ------------------------------------------------------------------ UX-06 + the e2e sandbox


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    """finalize → consolidate → `lanes route` (no judge records) on a throwaway tenant."""
    tmp = tmp_path_factory.mktemp("sandbox")
    (tmp / "content").mkdir()
    shutil.copytree(REPO / "profiles" / "_template", tmp / "profiles" / PROFILE)
    prof = tmp / "profiles" / PROFILE / "PROFILE.md"
    prof.write_text(
        "\n".join(
            "target_markets:  [Singapore]" if ln.startswith("target_markets:") else ln
            for ln in prof.read_text(encoding="utf-8").splitlines()
        ),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        GTM_CONTENT_ROOT=str(tmp / "content"),
        GTM_PROFILES_ROOT=str(tmp / "profiles"),
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", *args],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    researched = {
        "segment": "startup", "market": "Singapore", "country": "Singapore", "score": 8, "tier": "A",
        "contact_title": "Founder", "email_status": "verified", "verdict": "send",
        "signal_observed": "2026-09-10", "signal_agent_kind": "ai", "category_relation": "prospect",
        # A row carrying a SCORE must name the rubric that produced it, or `finalize` refuses
        # before writing anything (`prospects_import.require_rubric_provenance`). Supplied here
        # rather than switched off, so this fixture keeps the shape a real scored row has.
        "rubric_source": "knowledge/fixture.md#rubric", "rubric_version": "2026-01-01",
    }  # fmt: skip
    rows = [
        ("Litware Pay", "litwarepay.example", "Oma Reyes", "oma.reyes@litwarepay.example"),
        ("Lucerne Media", "lucernemedia.example", "Ira Bloom", "ira.bloom@lucernemedia.example"),
        ("Northwind Robotics", "northwindrobotics.example", "Kai Moss", "kai.moss@northwindrobotics.example"),
        ("Fabrikam Foods", "fabrikamfoods.example", "Uma Hale", ""),
    ]  # fmt: skip
    items = tmp / "items.json"
    items.write_text(
        json.dumps(
            [
                {
                    **researched,
                    "company": co,
                    "domain": dom,
                    "contact_name": name,
                    "contact_email": email,
                    "why_now": f"{co} opened its agent platform to partner organisations.",
                    "signal_evidence": f"{co} opened its agent platform to partner organisations.",
                    "signal_source_url": f"https://{dom}/news/partner-agents",
                    "signal_subject": co,
                }
                for co, dom, name, email in rows
            ]
        ),
        encoding="utf-8",
    )
    prospects = tmp / "content" / PROFILE / "prospects"
    for step in (
        ("gtm_core.prospects_import", "finalize", "--profile", PROFILE, "--items", str(items),
         "--source-run", "qa-run-1", "--standard"),
        ("gtm_core.prospects_consolidate", "consolidate", "--profile", PROFILE),
        ("gtm_core.lanes", "route", "--profile", PROFILE,
         "--csv", str(prospects / "sequences" / "ready-to-load.csv")),
    ):  # fmt: skip
        done = run(*step)
        assert done.returncode == 0, f"{step[:2]}: {done.stderr[-600:]}"
    return tmp / "content", run


def test_sandbox_page_and_terminal_agree_and_scope_open_falls_back(sandbox) -> None:
    """The run the skill actually performs. `--scope open` is the skill's mandatory last
    step, and this tenant has no campaign manifest — it must render everything, say so in
    one line, and exit 0 rather than ask an operator to edit a TOML file."""
    content, run = sandbox
    status = run("gtm_core.prospects", "status", "--profile", PROFILE)
    assert status.returncode == 0, status.stderr
    rendered = run("gtm_core.email_campaign_dashboard", "--profile", PROFILE, "--scope", "open")
    assert rendered.returncode == 0, rendered.stderr
    assert "no campaign manifest yet — showing everything" in rendered.stderr
    assert rendered.stderr.count("\n") == 1, "one plain note, not a wall"

    page_file = content / PROFILE / "email_campaign_status.html"
    assert str(page_file) in rendered.stdout
    html = page_file.read_text(encoding="utf-8")
    cli, page = cli_numbers(status.stdout), page_numbers(html)
    labels = [*ACCOUNT_LABELS, *CONTACT_LABELS, "Needs an address"]
    assert {k: page.get(k) for k in labels} == {k: cli[k] for k in labels}
    assert cli["Held"] == 3 and cli["Waiting on you"] == 3, "fixture must exercise the join"

    text = "\n".join(visible_lines(html))
    assert "3 contacts are waiting on your decision" in text
    assert "do NOT load this file: it still contains 3 contacts waiting on a decision" in text
    assert _badge(html) == "Nothing staged yet"
    assert not LONG_DISCLAIMER.search(text)

    fresh = run(
        "gtm_core.email_campaign_dashboard", "--profile", PROFILE, "--scope", "open", "--check-fresh"
    )  # fmt: skip
    assert fresh.returncode == 0, fresh.stderr


def test_scope_open_still_refuses_when_manifests_exist_but_none_is_open(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _tenant(tmp_path, MIXED_ACCOUNTS, MIXED_STATE)
    camps = tmp_path / PROFILE / "plans" / "campaigns"
    camps.mkdir(parents=True)
    (camps / "builders.campaign.toml").write_text(
        'slug = "builders"\ntitle = "Builders"\nstatus = "list too small"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit) as refusal:
        gd.render_dashboard(PROFILE, tmp_path, stubs=False, scope="open")
    said = str(refusal.value)
    assert "builders (list too small)" in said
    assert "active, live, running" in said
    assert "--scope all" in said, "name the command that works right now"
