"""The in-page staleness banner — a page opened a week after it was built says so itself.

WHY THIS EXISTS. ``--check-fresh`` is the only thing that convicts an old page, and it runs
only when somebody asks. A page that sits in a folder or an email attachment is read days later
with nothing on it to say its sending figures have aged. The banner is the one thing on the page
that depends on the day it is READ, so it lives in the browser: Python emits the exact instant the
figures were fetched (normalised to UTC) and the limit as attributes and a hidden card, and
``banner.js`` unhides it.

What a test must prove, because nothing server-side can: that the shipped script, run against a
clock, shows the card when it should and **fails closed** when it cannot read the date. Every
case here runs the script exactly as the page ships it, extracted from the rendered HTML, under
node. The DOM is the only stub; ``Date`` is frozen to the instant under test.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard import banner, health
from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS
from gtm_core.page_inputs import digest
from tests.contracts.test_dashboard_ps20_trust import _stats
from tests.test_email_campaign_dashboard import _seed

_CARD = re.compile(r'<div class="card" id="stale-banner"[^>]*>.*?</div>', re.S)
_SCRIPT = re.compile(r"<script data-stale-banner>(.*?)</script>", re.S)

_DRIVER = r"""
const fs = require('node:fs'); const vm = require('node:vm');
const jobs = JSON.parse(fs.readFileSync(0, 'utf8'));
const one = (job) => {
  const node = () => ({ hidden: true, textContent: '' });
  const slots = { '[data-age]': node(), '[data-known]': node(), '[data-unknown]': node() };
  const el = { hidden: job.startsHidden, getAttribute: (n) => (n in job.attrs ? job.attrs[n] : null),
               querySelector: (s) => slots[s] || null };
  const document = { getElementById: (id) => (id === 'stale-banner' ? el : null) };
  const Real = Date; const T = Real.parse(job.now);
  function Fake(...a) { return new Real(...a); }
  Fake.now = () => T; Fake.UTC = Real.UTC; Fake.parse = Real.parse;
  vm.runInNewContext(job.script, { document, Date: Fake });
  return { shown: !el.hidden, age: slots['[data-age]'].textContent,
           known: !slots['[data-known]'].hidden, unknown: !slots['[data-unknown]'].hidden };
};
process.stdout.write(JSON.stringify(jobs.map(one)));
"""


def _node() -> str:
    exe = shutil.which("node")
    assert exe, (
        "node is not on PATH, and this test does not skip. The banner is JavaScript and "
        "node is the only way to run it; install node or delete the banner."
    )
    return exe


def _page(tmp_path, fetched: str, profile: str = "acme") -> str:
    _seed(tmp_path, profile)
    _stats(tmp_path, profile, {"fetched": fetched, "sequences": [{"id": "S1", "sent": 1}]})
    return gd.render_html(gd.build_model(profile, tmp_path))


def _fresh_page(tmp_path, days_old: int = 3) -> tuple[str, str]:
    day = (datetime.now(UTC) - timedelta(days=days_old)).date().isoformat()
    return _page(tmp_path, day), day


def _attrs(card: str) -> dict[str, str]:
    return dict(re.findall(r'(data-[a-z-]+)="([^"]*)"', card.split(">", 1)[0]))


def _job(page: str, now: str, attrs: dict | None) -> dict:
    card = _CARD.search(page)
    assert card, "no stale-banner card on the page"
    script = _SCRIPT.search(page)
    assert script, "no stale-banner script on the page"
    return {
        "script": script.group(1),
        "now": now,
        "attrs": attrs if attrs is not None else _attrs(card.group(0)),
        "startsHidden": " hidden" in card.group(0).split(">", 1)[0],
    }


def _run_many(jobs: list[dict]) -> list[dict]:
    res = subprocess.run(
        [_node(), "-e", _DRIVER], input=json.dumps(jobs), capture_output=True, text=True, timeout=60
    )
    assert res.returncode == 0, f"node failed: {res.stderr[-2000:]}"
    return json.loads(res.stdout)


def _run(page: str, now: str, *, attrs: dict | None = None) -> dict:
    return _run_many([_job(page, now, attrs)])[0]


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def _banner_for(fetched: str) -> str:
    """The banner fragment for a snapshot stamped ``fetched``, built an hour after the instant it
    names so it is dated and inside the limit — a fixed historic stamp, never the wall clock, so
    these cases cannot drift as real time passes."""
    instant = health._parse_fetched(fetched)
    figures = health.figures_state(
        {"snapshot": {"fetched": fetched}, "sequences": [{"id": "S1"}]},
        instant + timedelta(hours=1),
    )
    out = banner.stale_banner({"figures": figures})
    assert out, f"no banner emitted for {fetched!r}: {figures}"
    return out


def _after(fetched: str, **delta) -> str:
    """The instant ``delta`` after the instant ``fetched`` names (whatever zone it was written in)."""
    return _iso(health._parse_fetched(fetched) + timedelta(**delta))


def _at(day: str, plus_hours: float) -> str:
    base = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return (base + timedelta(hours=plus_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_card_is_emitted_hidden_and_carries_the_figures_instant_not_the_build_date(tmp_path):
    page, day = _fresh_page(tmp_path)
    card = _CARD.search(page).group(0)
    attrs = _attrs(card)
    assert attrs["data-figures"] == f"{day}T00:00:00Z"  # a bare date is its UTC midnight
    assert attrs["data-limit"] == str(FIGURES_MAX_AGE_DAYS)
    assert " hidden" in card.split(">", 1)[0], "a static capture must show nothing"
    built = re.search(r"Page built (\d{4}-\d{2}-\d{2})", page).group(1)
    assert built != day, "fixture must tell the build date from the figures date"
    assert built not in card


def test_on_the_limit_day_the_card_stays_hidden_and_a_day_later_it_shows(tmp_path):
    page, day = _fresh_page(tmp_path)
    at_limit = _run(page, _at(day, 24 * FIGURES_MAX_AGE_DAYS))
    assert at_limit["shown"] is False
    over = _run(page, _at(day, 24 * (FIGURES_MAX_AGE_DAYS + 1)))
    assert over["shown"] is True
    assert over["age"] == str(FIGURES_MAX_AGE_DAYS + 1)
    assert over["known"] is True and over["unknown"] is False


def test_the_browser_agrees_with_health_py_on_the_strict_boundary(tmp_path):
    """health.figures_state says over the limit the moment the fractional age passes it, and
    the page header says whole days, so an hour past the limit is 'over' with a count equal to
    the limit. The browser must reach the same verdict, not its own rounding of it."""
    page, day = _fresh_page(tmp_path)
    just_over = _run(page, _at(day, 24 * FIGURES_MAX_AGE_DAYS + 1))
    assert just_over["shown"] is True
    assert just_over["age"] == str(FIGURES_MAX_AGE_DAYS)


def test_the_count_is_whole_days_rounded_down_never_up(tmp_path):
    """health.figures_state shows age_days rounded down so it never overstates; 8.5 days reads
    as 8, not 9."""
    page, day = _fresh_page(tmp_path)
    got = _run(page, _at(day, 24 * (FIGURES_MAX_AGE_DAYS + 1) + 13))
    assert got["shown"] is True
    assert got["age"] == str(FIGURES_MAX_AGE_DAYS + 1)


def test_a_page_built_fresh_and_opened_a_week_later_shows_the_banner(tmp_path):
    page, day = _fresh_page(tmp_path, days_old=5)
    built = re.search(r"Page built (\d{4}-\d{2}-\d{2})", page).group(1)
    opened = _at(built, 24 * 8)
    assert _run(page, opened)["shown"] is True
    assert _run(page, _at(built, 1))["shown"] is False


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "garbage",
        "2026-13-40",
        "2026-02-30",
        "26-09-22",
        " 2026-09-22",
        "2026-09-22T25:00:00Z",
        "2026-09-22T00:60:00Z",
        "2026-09-22T00:00:00",  # no zone: Python never writes one, so it is not ours to guess
        "2026-09-22T00:00:00+25:00",
        "2026-09-22 00:00:00Z",
        "2026-09-22T00:00:00Zjunk",
    ],
)
def test_an_unreadable_figures_date_fails_closed(tmp_path, bad):
    page, day = _fresh_page(tmp_path)
    attrs = {**_attrs(_CARD.search(page).group(0)), "data-figures": bad}
    got = _run(page, _at(day, 24), attrs=attrs)
    assert got["shown"] is True and got["unknown"] is True and got["known"] is False
    assert got["age"] == ""


@pytest.mark.parametrize("bad", ["", "seven", "-1", "7.5"])
def test_an_unreadable_limit_fails_closed(tmp_path, bad):
    page, day = _fresh_page(tmp_path)
    attrs = {**_attrs(_CARD.search(page).group(0)), "data-limit": bad}
    got = _run(page, _at(day, 24), attrs=attrs)
    assert got["shown"] is True and got["unknown"] is True


def test_a_missing_attribute_fails_closed(tmp_path):
    page, day = _fresh_page(tmp_path)
    attrs = _attrs(_CARD.search(page).group(0))
    del attrs["data-figures"]
    assert _run(page, _at(day, 24), attrs=attrs)["unknown"] is True


def test_a_figures_date_in_the_future_fails_closed(tmp_path):
    """Same rule as health.figures_age_days: more than a day ahead is as untrustworthy as
    unparseable. A day ahead (a local date in UTC+8) reads as today and stays hidden."""
    page, day = _fresh_page(tmp_path)
    assert _run(page, _at(day, -12))["shown"] is False
    assert _run(page, _at(day, -36))["unknown"] is True
    assert _run(page, _at(day, -72))["unknown"] is True


def test_the_script_does_nothing_when_the_card_is_absent(tmp_path):
    page, day = _fresh_page(tmp_path)
    script = _SCRIPT.search(page).group(1)
    probe = (
        "const vm = require('node:vm');"
        f"vm.runInNewContext({json.dumps(script)}, {{document: {{getElementById: () => null}}, Date}});"
        "process.stdout.write('ok');"
    )
    res = subprocess.run([_node(), "-e", probe], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0 and res.stdout == "ok", res.stderr[-1000:]


def test_the_script_writes_numbers_and_unhides_but_ships_no_prose():
    """§R14's derived-prose lint reads ``*.py`` and nothing else, so a sentence that moves into
    the JS is a sentence no lint can read. Python renders every sentence, hidden."""
    js = (Path(banner.__file__).parent / "banner.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    literals = re.findall(r"'([^']*)'|\"([^\"]*)\"", code)
    words = [a or b for a, b in literals if re.search(r"[A-Za-z]+ [A-Za-z]+", a or b)]
    assert words == [], f"reader-facing prose in banner.js: {words}"


def test_the_card_says_what_the_reader_should_do_and_names_the_date(tmp_path):
    page, day = _fresh_page(tmp_path)
    card = _CARD.search(page).group(0)
    text = re.sub(r"<[^>]+>", " ", card)
    assert day in text
    assert "Ask for the latest before relying on it" in text
    assert re.search(r"\d+ days? ago", card) is None, "the age is the browser's to write"


def test_a_page_already_over_the_limit_at_build_says_so_statically_and_has_no_banner(tmp_path):
    """The warnings strip already carries that sentence with no script needed, so a second,
    script-dependent copy would only repeat it."""
    old = (datetime.now(UTC) - timedelta(days=FIGURES_MAX_AGE_DAYS + 3)).date().isoformat()
    page = _page(tmp_path, old)
    assert "stale-banner" not in page
    assert "figures-old" in page


@pytest.mark.parametrize("payload", [None, "{broken", '{"sequences": [{"id": "S1"}]}'])
def test_no_banner_when_the_figures_have_no_date_to_age(tmp_path, payload):
    """An undated, unreadable or never-refreshed snapshot has no age to compare against; the
    strip names the first two statically and the third is a setup step, not an old page."""
    _seed(tmp_path, "acme")
    if payload is not None:
        _stats(tmp_path, "acme", payload)
    assert "stale-banner" not in gd.render_html(gd.build_model("acme", tmp_path))


def test_the_grep_for_relative_day_counts_excludes_script_text(tmp_path):
    page, _ = _fresh_page(tmp_path)
    prose = re.sub(r"<script\b.*?</script>", "", page, flags=re.S)
    assert re.search(r"\b\d+ days? ago\b", prose) is None
    assert "stale-banner" in prose


def test_page_sha256_matches_the_file_that_carries_the_banner(tmp_path):
    """The banner's attributes and script are part of the page bytes, and the inventory's
    digest is taken of the written file — so a banner added AFTER the digest would read as a
    hand-edit on the very next check."""
    _seed(tmp_path, "acme")
    day = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    _stats(tmp_path, "acme", {"fetched": day, "sequences": [{"id": "S1", "sent": 1}]})
    path = gd.render_dashboard("acme", tmp_path)
    text = path.read_text(encoding="utf-8")
    assert 'id="stale-banner"' in text and "<script data-stale-banner>" in text
    inv = json.loads(path.with_suffix(".inputs.json").read_text(encoding="utf-8"))
    assert inv["page_sha256"] == digest(path)
    report = gd.check_fresh("acme", tmp_path)
    assert not report.page_edited


def test_editing_the_banner_attributes_in_the_file_is_a_page_edit(tmp_path):
    _seed(tmp_path, "acme")
    day = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    _stats(tmp_path, "acme", {"fetched": day, "sequences": [{"id": "S1", "sent": 1}]})
    path = gd.render_dashboard("acme", tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'data-figures="{day}T00:00:00Z"', 'data-figures="2030-01-01T00:00:00Z"'
        ),
        encoding="utf-8",
    )
    assert gd.check_fresh("acme", tmp_path).page_edited is True


def test_rendering_the_same_model_twice_is_byte_identical_with_the_banner(tmp_path):
    _seed(tmp_path, "acme")
    day = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    _stats(tmp_path, "acme", {"fetched": day, "sequences": [{"id": "S1", "sent": 1}]})
    m = gd.build_model("acme", tmp_path)
    assert gd.render_html(m) == gd.render_html(m)
    assert "stale-banner" in gd.render_html(m)


def test_the_card_sits_above_the_tabs_and_the_script_follows_it(tmp_path):
    page, _ = _fresh_page(tmp_path)
    assert page.index('id="stale-banner"') < page.index('<div class="tabs">')
    assert page.index('id="stale-banner"') < page.index("<script data-stale-banner>")


# --- the instant, not the date (verification audit B-F3, red-team F7) -------------------------
#
# The writer stamps `%Y-%m-%dT%H:%M:%SZ`. The banner used to receive only the calendar date, so
# the browser counted from UTC midnight while Python counted from the full instant: at 6.58 days
# the header said "6 days old" and the card said "7 days ago", and with a `+hh:mm` offset the card
# could stay hidden after `--check-fresh` said STALE. Every case here is a FIXED historic stamp
# with the build clock pinned beside it, so none of them move as real time passes.

_FETCHED = "2026-09-25T20:00:00Z"


def _text(page: str) -> str:
    card = _CARD.search(page).group(0)
    return re.sub(r"<[^>]+>", " ", card)


def test_a_timestamp_is_carried_as_its_exact_instant_in_utc():
    """Mutation caught: writing ``fig["date"]`` into ``data-figures`` again."""
    assert _attrs(_CARD.search(_banner_for(_FETCHED)).group(0))["data-figures"] == _FETCHED
    assert (
        _attrs(_CARD.search(_banner_for("2026-09-25T20:00:00+08:00")).group(0))["data-figures"]
        == "2026-09-25T12:00:00Z"
    )
    assert (
        _attrs(_CARD.search(_banner_for("2026-09-25T20:00:00-05:00")).group(0))["data-figures"]
        == "2026-09-26T01:00:00Z"
    )
    assert (
        _attrs(_CARD.search(_banner_for("2026-09-25T20:00:00.250Z")).group(0))["data-figures"]
        == "2026-09-25T20:00:00.250Z"
    )


def test_the_card_still_prints_the_iso_date_of_the_stamp_not_the_instant():
    """One date format on the page, and it is the date the header prints (the stamp's own
    calendar day): `+14:00` on the 25th is the 24th in UTC, and the card must say the 25th."""
    page = _banner_for("2026-09-25T00:30:00+14:00")
    assert "from 2026-09-25," in _text(page)
    assert _attrs(_CARD.search(page).group(0))["data-figures"] == "2026-09-24T10:30:00Z"


def test_the_b05_case_a_z_timestamp_at_6_58_days_stays_hidden():
    """Python: 6.58 days, not over the limit. The old banner read the date alone and showed "7
    days ago". Mutation caught: any return to measuring from the date's midnight."""
    page = _banner_for(_FETCHED)
    now = "2026-10-02T10:00:00.000Z"
    assert health._figures_age_exact_days(_FETCHED, datetime(2026, 10, 2, 10, tzinfo=UTC)) < 7
    assert _run(page, now)["shown"] is False


def test_a_z_timestamp_one_minute_past_the_limit_shows_and_says_the_whole_days():
    page = _banner_for(_FETCHED)
    inside, over = _run_many(
        [
            _job(page, _after(_FETCHED, days=7), None),
            _job(page, _after(_FETCHED, days=7, minutes=1), None),
        ]
    )
    assert inside["shown"] is False
    assert over["shown"] is True and over["age"] == "7" and over["known"] is True


def test_seven_and_a_half_days_reads_seven_days_ago_never_seven_days_old():
    """The strict `>` is on fractional days and the count is whole days rounded down, so the
    sentence is "7 days ago" — and the card never uses the header's "days old" wording."""
    page = _banner_for(_FETCHED)
    got = _run(page, _after(_FETCHED, days=7, hours=12))
    assert got["shown"] is True and got["age"] == "7"
    assert "days old" not in _text(page)
    assert re.search(r"<span data-age></span> days ago", page)


def test_a_bare_date_keeps_its_behaviour_at_the_limit_a_minute_over_and_a_day_over():
    page = _banner_for("2026-09-25")
    at, minute, day = _run_many(
        [
            _job(page, _after("2026-09-25", days=7), None),
            _job(page, _after("2026-09-25", days=7, minutes=1), None),
            _job(page, _after("2026-09-25", days=8), None),
        ]
    )
    assert at["shown"] is False
    assert minute["shown"] is True and minute["age"] == "7"
    assert day["shown"] is True and day["age"] == "8"
    # and a page that carries the BARE date in the attribute (an older or hand-built page) is
    # read exactly as before: UTC midnight
    bare = {**_attrs(_CARD.search(page).group(0)), "data-figures": "2026-09-25"}
    assert _run(page, _after("2026-09-25", days=7), attrs=bare)["shown"] is False
    assert _run(page, _after("2026-09-25", days=7, minutes=1), attrs=bare)["shown"] is True


def test_a_plus_eight_offset_is_measured_from_its_own_instant():
    """20:00 at +08:00 is 12:00Z. Seven days after THAT instant is the limit; counting from the
    local date's UTC midnight would be 12 hours early."""
    stamp = "2026-09-25T20:00:00+08:00"
    page = _banner_for(stamp)
    inside, over = _run_many(
        [
            _job(page, "2026-10-02T11:59:00.000Z", None),
            _job(page, "2026-10-02T12:01:00.000Z", None),
        ]
    )
    assert inside["shown"] is False
    assert over["shown"] is True and over["age"] == "7"


def test_a_minus_five_offset_is_measured_from_its_own_instant():
    """20:00 at -05:00 is 01:00Z the NEXT day: the instant is later than the date suggests, so a
    date-only banner would show five hours too early."""
    stamp = "2026-09-25T20:00:00-05:00"
    page = _banner_for(stamp)
    inside, over = _run_many(
        [
            _job(page, "2026-10-03T00:59:00.000Z", None),
            _job(page, "2026-10-03T01:01:00.000Z", None),
        ]
    )
    assert inside["shown"] is False
    assert over["shown"] is True and over["age"] == "7"


def test_an_offset_in_the_attribute_itself_is_honoured_not_guessed_at():
    """Python writes only `Z`, but a page edited by hand or built by another writer may carry an
    offset; the script reads it as the instant it names rather than failing or ignoring it."""
    page = _banner_for(_FETCHED)
    attrs = {**_attrs(_CARD.search(page).group(0)), "data-figures": "2026-09-25T20:00:00+08:00"}
    assert _run(page, "2026-10-02T11:59:00.000Z", attrs=attrs)["shown"] is False
    assert _run(page, "2026-10-02T12:01:00.000Z", attrs=attrs)["shown"] is True


@pytest.mark.parametrize("bad", ["", "   ", "not a date", "2026-09-25T20:00:00"])
def test_an_unreadable_or_blank_instant_fails_closed(bad):
    page = _banner_for(_FETCHED)
    attrs = {**_attrs(_CARD.search(page).group(0)), "data-figures": bad}
    got = _run(page, _after(_FETCHED, days=1), attrs=attrs)
    assert got["shown"] is True and got["unknown"] is True and got["known"] is False


def test_a_future_instant_follows_the_python_tolerance():
    """Up to a day ahead is never over the limit (a local date east of UTC); further ahead is
    unknown — the same rule as `health.figures_age_days`."""
    page = _banner_for(_FETCHED)
    ahead = {**_attrs(_CARD.search(page).group(0))}
    a_bit, too_far = _run_many(
        [
            _job(page, _after(_FETCHED, hours=-12), ahead),
            _job(page, _after(_FETCHED, hours=-25), ahead),
        ]
    )
    assert a_bit["shown"] is False
    assert too_far["shown"] is True and too_far["unknown"] is True


# One list, used by BOTH sides: every (stamp, offset from its instant) pair is judged by
# `health` in Python and by the shipped `banner.js` under node, and the verdicts must agree.
_STAMPS = [
    "2026-09-25",
    "2026-09-25T20:00:00Z",
    "2026-09-25T23:59:59Z",
    "2026-09-25T20:00:00+08:00",
    "2026-09-25T20:00:00-05:00",
    "2026-09-25T00:30:00+14:00",
    "2026-09-25T20:00:00.250Z",
]
_OFFSET_DAYS = [-3, -1.5, -0.5, 0, 1, 6.58, 6.99, 7, 7 + 1 / 1440, 7.5, 8.5, 30]


def test_python_and_the_browser_agree_over_one_table_of_fetched_and_now_pairs():
    """Mutation caught: ANY drift between `health.figures_state`/`figures_age_days` and the
    script — measuring from the date, a `>=` for a `>`, rounding up, an ignored offset. The
    expected side calls `health`; the actual side runs the shipped script."""
    jobs, expected, labels = [], [], []
    for stamp in _STAMPS:
        page = _banner_for(stamp)
        instant = health._parse_fetched(stamp)
        for days in _OFFSET_DAYS:
            now = instant + timedelta(seconds=round(days * 86400))
            exact = health._figures_age_exact_days(stamp, now)
            over = exact is None or exact > FIGURES_MAX_AGE_DAYS
            jobs.append(_job(page, _iso(now), None))
            expected.append(
                {
                    "shown": over,
                    "unknown": exact is None,
                    "age": "" if exact is None else str(health.figures_age_days(stamp, now)),
                }
            )
            labels.append((stamp, days))
    got = _run_many(jobs)
    for label, want, have in zip(labels, expected, got, strict=True):
        assert have["shown"] == want["shown"], (label, want, have)
        if want["shown"]:
            assert have["unknown"] == want["unknown"], (label, want, have)
            assert have["age"] == want["age"], (label, want, have)
    assert any(w["shown"] for w in expected) and any(not w["shown"] for w in expected)


def test_an_end_to_end_page_with_an_offset_stamp_carries_the_normalised_instant(tmp_path):
    """Through the real model, not a hand-built figures dict: a snapshot stamped with a `+08:00`
    offset renders a card whose attribute is the UTC instant and whose sentence is the ISO date."""
    when = datetime.now(UTC) - timedelta(days=2)
    local = when.astimezone(timezone(timedelta(hours=8)))
    stamp = local.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    page = _page(tmp_path, stamp)
    attrs = _attrs(_CARD.search(page).group(0))
    assert attrs["data-figures"] == when.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert f"from {local.date().isoformat()}," in _text(page)


# --- regression pins for what round 2 found HELD (2026-10-02) ---------------------------------
#
# The red team compared banner.js with `health` over 85,631 (stamp, now) pairs and found it in
# agreement. These two pins keep it that way for the two properties that comparison could not
# hold by itself: the limit the script judges against is the one `health` resolved (so a typed
# constant in either file cannot drift), and a stamp the browser's own Date cannot place fails
# toward SHOWING the card, never toward staying quiet.


def test_the_cards_limit_is_derived_from_health_never_typed(monkeypatch):
    """`data-limit` is `figures_state(...)["limit"]`, which is `config.FIGURES_MAX_AGE_DAYS`.
    Moving the constant moves the attribute AND the shipped script's verdict with it — the
    script reads the attribute, so a `7` typed anywhere in the chain would not follow."""
    stamp = "2026-09-25T20:00:00Z"
    assert _attrs(_CARD.search(_banner_for(stamp)).group(0))["data-limit"] == str(
        FIGURES_MAX_AGE_DAYS
    )
    monkeypatch.setattr(health, "FIGURES_MAX_AGE_DAYS", 3)
    page = _banner_for(stamp)
    assert _attrs(_CARD.search(page).group(0))["data-limit"] == "3"
    # three and a half days old: over a limit of 3, well inside the shipped 7
    shown, quiet = _run_many(
        [
            _job(page, _after(stamp, days=3, hours=12), None),
            _job(page, _after(stamp, days=2, hours=12), None),
        ]
    )
    assert shown["shown"] is True and shown["age"] == "3"
    assert quiet["shown"] is False


def test_banner_py_types_no_number_of_its_own():
    """The structural half of the pin above: the card's limit comes from the model, so no integer
    literal appears in the module that renders it (an `int(...)` conversion is not a literal)."""
    import ast

    tree = ast.parse(Path(banner.__file__).read_text(encoding="utf-8"))
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int)
        and not isinstance(n.value, bool)
    ]
    assert literals == []


@pytest.mark.parametrize(
    "stamp",
    [
        "0001-01-01T00:00:00Z",  # JS reads a year below 100 as 19xx; either way far past any limit
        "0001-01-01",
        "0050-06-01T00:00:00Z",
        "9999-12-31T23:59:59Z",  # years ahead: "more than a day in the future" is unknown, shown
        "9999-12-31",
        "10000-01-01",  # five digits: not the shape the page writes
        "0000-01-01",  # year zero is not a calendar year
        "2026-02-30T00:00:00Z",  # a day the month does not have
    ],
)
def test_a_stamp_the_browsers_date_cannot_place_fails_toward_showing_the_card(stamp):
    """INTENDED, and pinned so nobody "fixes" it into silence: the banner exists to say the
    figures may be old, so every value it cannot confirm is recent shows the card. Python never
    emits any of these on a banner (a year-1 stamp is already over the limit at build and gets
    the static strip instead), so this is the browser reading a hand-edited attribute."""
    page = _banner_for(_FETCHED)
    attrs = {**_attrs(_CARD.search(page).group(0)), "data-figures": stamp}
    got = _run_many([_job(page, _after(_FETCHED, hours=1), attrs)])[0]
    assert got["shown"] is True, (stamp, got)
    assert got["known"] != got["unknown"], (stamp, got)  # exactly one sentence is unhidden
