"""F1/F7 — the page says how old its sending figures are, and nothing depends on the day
it is read.

WHY THIS EXISTS (2026-09-30). The `figures-old` warning was detectable in the model and
INVISIBLE on the page: commit ``3ff97acf`` deleted its sentence from ``render._warnings_strip``
with no reason recorded, and the trust tests were rewritten to assert the absence. Measured on
a live tenant the same day: figures fetched five days earlier, a two-day limit, and the strip
rendering nothing at all. So the page now carries the figures' date and age in its header on
every render — not only past the limit — and the strip sentence is restored above a limit that
matches the weekly publish cadence.

EVERY EXPECTED STRING HERE IS A LITERAL. Building the expectation from the same function the
renderer calls would pass on any wording, including no wording — which is how the deleted
sentence went unnoticed. The one thing read from the code is ``FIGURES_MAX_AGE_DAYS``: §R14
forbids typing a number into prose, and the copy must move with the constant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS, TAB_LABELS
from tests.contracts.test_dashboard_ps20_trust import _stats, _strip
from tests.test_email_campaign_dashboard import _seed

LIMIT = FIGURES_MAX_AGE_DAYS


def _at(days: float = 0.0):
    """``(iso stamp, YYYY-MM-DD)`` for a moment ``days`` in the past — negative for the
    future. One call site for both, so a test can never assert a date the fixture did not
    write."""
    when = datetime.now(UTC) - timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ"), when.date().isoformat()


def _model(tmp_path, *, fetched=None, rows=(({"id": "S1", "sent": 0}),), stats=True):
    profile = _seed(tmp_path)
    if stats is None:
        pc._pool_dir(profile, tmp_path).joinpath("sequence-stats.json").unlink()
    elif isinstance(stats, str):
        _stats(tmp_path, profile, stats, stamped=True)
    else:
        payload = {"sequences": list(rows)}
        if fetched is not None:
            payload["fetched"] = fetched
        _stats(tmp_path, profile, json.dumps(payload), stamped=True)
    return gd.build_model(profile, tmp_path)


def _header(page: str) -> str:
    """The one line above the tabs that carries the build stamp — not the whole head, so a
    string that happens to appear in the stylesheet cannot satisfy an assertion here."""
    return page.split('<p class="muted" style="margin:0">', 1)[1].split("</p>", 1)[0]


def _warn_card(page: str) -> str:
    head = _strip(page)
    assert 'class="card warn"' in head, "no warning strip rendered"
    return head.split('class="card warn"', 1)[1].split("</div>", 1)[0]


# --- the header (T4, T6, T7, T9) ------------------------------------------------------------


def test_the_header_states_the_build_stamp_and_the_figures_age(tmp_path):
    """T4 — five days old, under the limit: the age is shown and NO strip is raised. The age
    is the reason this line exists on a healthy page; a warning that only appears past the
    limit tells a reader nothing on the other 6 days."""
    stamp, day = _at(5)
    m = _model(tmp_path, fetched=stamp)
    page = gd.render_html(m)
    assert _header(page) == (
        f"Page built {m['generated_at']} · Sending figures from {day} (5 days old)"
    )
    assert m["warnings"] == []
    assert 'class="card warn"' not in _strip(page)


def test_the_header_replaces_the_refreshed_line(tmp_path):
    """T7 — one line, not two. The old line said only when the page was BUILT, which is the
    fact a reader is least likely to be misled by; it is the figures' date that goes stale."""
    page = gd.render_html(_model(tmp_path, fetched=_at(1)[0]))
    # The old line verbatim — "refreshed" on its own appears legitimately elsewhere on the
    # page (views_inbound's dnc-timer note), so a bare substring check would fail forever.
    assert '<p class="muted" style="margin:0">refreshed ' not in page
    assert page.count('<p class="muted" style="margin:0">Page built ') == 1


def test_the_per_campaign_line_shows_the_same_date_as_the_header(tmp_path):
    """T7 second half / §4.5 surface agreement — ``views_overview._campaign_lines`` prints its
    own "figures from" line off ``health.figures_date``. Two surfaces naming one date must
    never disagree, so both are asserted against the fixture's own value."""
    stamp, day = _at(3)
    page = gd.render_html(_model(tmp_path, fetched=stamp))
    assert f"Sending figures from {day}" in _header(page)
    assert f"figures from {day}" in page


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({"stats": None}, "No sending figures yet"),
        ({"stats": "{broken"}, "Sending figures couldn't be read"),
        ({"fetched": "yesterday"}, "Sending figures carry no date"),
        ({"fetched": None}, "Sending figures carry no date"),
    ],
)
def test_the_header_names_each_absence_distinctly(tmp_path, kw, expected):
    """T6 — four different absences, four different sentences. Collapsing them is the
    fail-open shape: "we have no figures", "we could not read them" and "they carry no date"
    are three different pieces of work, and one wording sends the reader to the wrong one."""
    m = _model(tmp_path, **kw)
    page = gd.render_html(m)
    assert _header(page) == f"Page built {m['generated_at']} · {expected}"
    assert _header(page).endswith(f" · {expected}")
    assert _header(page).startswith("Page built ")


def test_a_future_dated_snapshot_reads_as_unknown_in_both_places(tmp_path):
    """T6's future case, and T3's other half: the header and the strip agree that the age is
    unknown. A well-formed date in the future parses, so ``figures_date`` returns it while
    ``figures_age_days`` refuses it — the header must not print "(-3 days old)" and must not
    silently read as fresh either."""
    stamp, day = _at(-3)
    m = _model(tmp_path, fetched=stamp)
    page = gd.render_html(m)
    assert _header(page).endswith(
        f" · Sending figures are dated {day}, which is in the future, so their age is unknown"
    )
    assert m["warnings"] == ["figures-old"]
    assert "their age is unknown" in _strip(page)


def test_a_bare_date_today_is_zero_days_old(tmp_path):
    """T6's bare-date case. The skill writes ``fetched`` as a bare LOCAL date, so a bare date
    is the common shape, not the edge one — it is read as its midnight UTC."""
    _, day = _at(0)
    page = gd.render_html(_model(tmp_path, fetched=day))
    assert _header(page).endswith(f" · Sending figures from {day} (0 days old)")


def test_every_date_the_header_and_strip_print_is_iso(tmp_path):
    """T9 — one date format. A page mixing ``25 Sep`` and ``2026-09-25`` for the same fact is
    how two figures get read as two dates."""
    stamp, day = _at(LIMIT + 1)
    page = gd.render_html(_model(tmp_path, fetched=stamp))
    for text in (_header(page), _warn_card(page)):
        assert day in text
        assert datetime.strptime(day, "%Y-%m-%d")  # the fixture's own value is ISO
        assert " Sep " not in text and " Oct " not in text


# --- the strip (T2, T3, T5, T11) ------------------------------------------------------------


def test_old_figures_render_the_pinned_strip_sentence(tmp_path):
    """T2 — REPLACES ``test_old_figures_alone_is_detected_but_renders_no_strip``, which pinned
    the deleted sentence's ABSENCE. One day past the limit, the strip says what is wrong, what
    it affects, the one command that fixes it, and where the sources are."""
    stamp, day = _at(LIMIT + 1)
    m = _model(tmp_path, fetched=stamp)
    assert m["warnings"] == ["figures-old"]
    card = _warn_card(gd.render_html(m))
    assert (
        f"The sending figures are from {day}, over {LIMIT} days before this page was built. "
        "Sent, replied and bounce numbers below may be behind. Refresh the sending figures, "
        "then run <code>python -m gtm_core.email_campaign_dashboard --profile acme "
        f"--refresh-all</code>. Sources are listed under {TAB_LABELS['ops']}."
    ) in card


def test_an_unusable_date_says_so_and_never_prints_the_raw_string(tmp_path):
    """T3 — REPLACES ``test_unparseable_fetched_shows_no_date_not_the_raw_string``. The raw
    value is untrusted provider/agent text (§R5): it is reported as unusable, never echoed as
    if it were a date."""
    m = _model(tmp_path, fetched="yesterday")
    assert m["warnings"] == ["figures-old"]
    page = gd.render_html(m)
    card = _warn_card(page)
    assert (
        "The sending figures carry no usable date, so their age is unknown "
        "(S1: date cannot be read). Refresh the sending figures, then run "
        "<code>python -m gtm_core.email_campaign_dashboard --profile acme "
        f"--refresh-all</code>. Sources are listed under {TAB_LABELS['ops']}."
    ) in card
    assert "re-render as above" not in card, "nothing is above the strip to point at"
    assert "from yesterday" not in page
    assert _header(page).endswith(" · Sending figures carry no date")


@pytest.mark.parametrize(
    "stats", ["yesterday", None, "future"], ids=["unusable", "undated", "future"]
)
def test_every_undated_strip_names_one_command_that_parses(tmp_path, stats):
    """The undated and future wordings used to end "re-render as above" with nothing above, so
    the likeliest real case (a stats file with no usable date) was the one strip that named no
    command. Each unknown-age shape now ends in the same one command the dated wording does, and
    it is a command the real parser accepts.
    Catches: restoring the old tail in `freshness.figures_strip_sentence`."""
    fetched = _at(-30)[0] if stats == "future" else (stats or None)
    m = _model(tmp_path, fetched=fetched)
    card = _warn_card(gd.render_html(m))
    assert card.count("<code>") == 1
    quoted = card.split("<code>", 1)[1].split("</code>", 1)[0]
    assert quoted == "python -m gtm_core.email_campaign_dashboard --profile acme --refresh-all"
    assert f"Sources are listed under {TAB_LABELS['ops']}." in card
    from gtm_core.email_campaign_dashboard.cli import _cli

    with pytest.raises(SystemExit) as exc:
        _cli(quoted.split()[3:] + ["--help"])
    assert exc.value.code == 0


def test_the_strip_appears_only_past_the_limit(tmp_path):
    """T5, the render half. Exactly the limit is fresh; a minute past it is not. The header
    still reads the TRUNCATED count at both, which is exactly why the strip's own wording is
    "over N days" and never that count — a 7.5-day-old snapshot must not read "7 days old"
    as its justification for being flagged."""
    # EXACTLY the limit is pinned in tests/unit/test_dashboard_health.py, on a frozen clock:
    # `_at(LIMIT)` is measured a few hundred microseconds before the render reads its own
    # clock, so at the render level the exact boundary lands on the stale side by construction.
    # Here the two SIDES of it are what is asserted.
    under, under_day = _at(LIMIT - 0.01)  # ~14 minutes inside the limit
    page = gd.render_html(_model(tmp_path, fetched=under))
    assert 'class="card warn"' not in _strip(page)
    assert _header(page).endswith(f" · Sending figures from {under_day} ({LIMIT - 1} days old)")

    past, past_day = _at(LIMIT + 0.01)  # ~14 minutes over
    page = gd.render_html(_model(tmp_path, fetched=past))
    assert f"over {LIMIT} days before this page was built" in _warn_card(page)
    assert _header(page).endswith(f" · Sending figures from {past_day} ({LIMIT} days old)")

    half, _ = _at(LIMIT + 0.5)
    card = _warn_card(gd.render_html(_model(tmp_path, fetched=half)))
    assert f"over {LIMIT} days" in card
    assert f"{LIMIT} days old" not in card


def test_the_strip_names_a_real_tab_and_a_command_that_parses(tmp_path):
    """T11 — the two things the sentence points AT. A tab label that no tab carries and a
    command that does not parse are both dead ends in a card that asks for action."""
    from gtm_core.email_campaign_dashboard.config import TABS

    stamp, _ = _at(LIMIT + 1)
    card = _warn_card(gd.render_html(_model(tmp_path, fetched=stamp)))
    assert TAB_LABELS["ops"] in card
    assert TAB_LABELS["ops"] in dict(TABS).values()

    quoted = card.split("<code>", 1)[1].split("</code>", 1)[0]
    assert quoted.startswith("python -m gtm_core.email_campaign_dashboard ")
    argv = quoted.split()[3:]  # drop `python -m <module>`

    from gtm_core.email_campaign_dashboard.cli import _cli  # noqa: F401

    # The real parser, not a copy: the strip must quote flags the CLI actually accepts.
    with pytest.raises(SystemExit) as exc:
        _cli(argv + ["--help"])
    assert exc.value.code == 0


def test_a_scoped_page_shows_the_same_header_and_strip(tmp_path):
    """T8 — a scoped page that dropped the header would be the surface an operator actually
    opens (`--scope open` is what the skills render) showing no age at all. Its age is taken
    over ITS sequences and says so; the roll-up's does not need to."""
    stamp, day = _at(LIMIT + 1)
    profile = _seed(tmp_path)
    _stats(
        tmp_path, profile, json.dumps({"fetched": stamp, "sequences": [{"id": "S1"}]}), stamped=True
    )
    rollup = gd.build_model(profile, tmp_path)
    scoped = gd.scope_to_campaign(gd.build_model(profile, tmp_path), "c1")
    for m in (rollup, scoped):
        page = gd.render_html(m)
        assert f"Sending figures from {day} ({LIMIT + 1} days old" in _header(page)
        assert f"over {LIMIT} days before this page was built" in _warn_card(page)
    assert "sequence on this page" in _header(gd.render_html(scoped))
    assert "oldest of" not in _header(gd.render_html(rollup))
