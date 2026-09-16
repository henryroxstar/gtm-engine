"""The filter's payload and the JavaScript that reads it agree with the model.

WHY THIS EXISTS. Every other claim this dashboard makes is checked in Python: the tile
provenance contract re-executes each ``src`` against the model, the refusal contract pins
the sentences a scope must not print. The client-side filter broke that symmetry — once the
headline figures are recomputed in the browser, the code deciding what "23 have a verified
address" means after a country is selected is JavaScript, and no Python test can see it.

So the checks come in three layers, and none of them is a reimplementation:

1. **The payload reproduces the page.** At zero filter every predicate must equal the figure
   the server already rendered — recomputed here from the roster's SEMANTIC fields
   (``email``, ``named``, ``signal``…), never from the ``co_*`` booleans under test.
2. **The shipped JS agrees, exhaustively.** Python does not reimplement the interpreter; it
   RUNS ``filter.js`` — the same bytes inlined into the page — over every combination of
   facet values and compares. A seeded corruption proves the harness is really reaching it.
3. **The payload is safe to ship.** No company, person, address or URL (§R9), and no number,
   which would quietly re-open the fail-open in assertion 2 of the provenance contract.

NODE IS A HARD REQUIREMENT HERE, not a ``pytest.skip``. A skip goes green forever the moment
the runtime is missing, which is the "declared contract nobody runs" failure this repo keeps
rediscovering — and it would go green on exactly the layer nothing else covers.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from gtm_core import email_campaign_dashboard as gd  # noqa: E402
from gtm_core.email_campaign_dashboard import filters  # noqa: E402
from gtm_core.email_campaign_dashboard import format as fmt  # noqa: E402

JS = REPO / "gtm_core" / "email_campaign_dashboard" / "filter.js"
JS_TEST = REPO / "tests" / "js" / "filter.test.js"
FIXTURE = REPO / "tests" / "js" / "fixture.json"

HEAD = (
    "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,"
    "GTM_Tier,GTM_Segment,Country/Region,GTM_Why_Now,GTM_Signal_Source_URL,GTM_Verdict\n"
)

#: (first, email, company, tier, segment, country, why_now, source_url, verdict). Spread so
#: every predicate has both members, both facets have two values, and one account carries a
#: name with no address — the case that makes `contact_verified - named_seat` unsafe and
#: `co_role_inbox` safe.
SEED_ROWS = (
    (
        "Ada",
        "ada@analytical.example",
        "Analytical Engine",
        "A",
        "Builder",
        "Singapore",
        "shipped a gateway",
        "https://example.test/a",
        "send",
    ),
    ("", "team@loomworks.example", "Loomworks", "B", "builder", "Singapore", "", "", "send"),
    (
        "Grace",
        "grace@harborlight.example",
        "Harborlight",
        "A",
        "Startup",
        "Singapore",
        "raised a round",
        "",
        "re-angle",
    ),
    ("", "", "Quiet Mill", "B", "Builder", "United States", "opened an office", "", "drop"),
    ("Alan", "", "Bletchley Works", "B", "Startup", "United States", "", "", "send"),
)


def _seed(tmp_path, profile="acme"):
    from gtm_core import prospects_consolidate as pc

    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "sequences").mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"{first},L,{email},{co},{co.lower().replace(' ', '')}.example,verified,"
        f"{tier},{seg},{country},{why},{url},{verdict}\n"
        for first, email, co, tier, seg, country, why, url, verdict in SEED_ROWS
    )
    (pros / "mine-20260904-hubspot.csv").write_text(HEAD + body, encoding="utf-8")
    camps = pros.parent / "plans" / "campaigns"
    camps.mkdir(parents=True, exist_ok=True)
    (camps / "mine-20260904.campaign.toml").write_text(
        'slug = "mine-20260904"\ntitle = "Builders"\nstatus = "active"\n'
        'product = "Brightpath Health"\nroster_globs = ["mine-20260904-hubspot.csv"]\n'
        "[targets]\nprospects = 4\nemails = 8\nreply_rate = 0.018\n",
        encoding="utf-8",
    )
    return pros


@pytest.fixture
def page(tmp_path):
    _seed(tmp_path)
    m = gd.build_model("acme", tmp_path)
    html = gd.render_html(m)
    return m, html, filters.payload_rows(m)


# --- the independent recompute ---------------------------------------------------
#
# Derived from each row's SEMANTIC fields, exactly as the tile-provenance resolver does and
# for the same reason: reading the shipped `co_*` booleans would make every assertion below
# "the number equals the number".


def expected(rows: list[dict], sel: list[tuple[str, str]], pred: str) -> int:
    kept = [r for r in rows if all(str(r.get(k) or "") == v for k, v in sel)]
    every = {r["company"] for r in kept}

    def co(field):
        return {r["company"] for r in kept if r.get(field)}

    email, named = co("email"), co("named")
    return len(
        {
            "all": every,
            "co_has_email": email,
            "co_no_email": every - email,
            "co_named": named,
            "co_role_inbox": email - named,
            "co_signal": co("signal"),
            "co_signal_sourced": co("signal_source_url"),
        }[pred]
    )


def _selections(payload: list[dict]) -> list[list[tuple[str, str]]]:
    """Every combination of facet values, including none. ~40 cases: exhaustive, not sampled."""
    per = []
    for key, _label, _heading in filters.FACETS:
        vals = sorted({str(r.get(key) or "") for r in payload if r.get(key)})
        per.append([None, *vals])
    return [
        [(k, v) for (k, _l, _h), v in zip(filters.FACETS, combo, strict=True) if v]
        for combo in itertools.product(*per)
    ]


# --- layer 1: the payload reproduces the page ------------------------------------


def test_every_reactive_tile_equals_its_predicate_at_zero_filter(page):
    """The tile the server rendered and the count the browser will compute must start equal.

    If they do not, the runtime tripwire fires for a reader — but a reader is not a test.
    """
    m, _html, payload = page
    reactive = [t for t in fmt._tiles_recorded() if filters.reactive(t["src"])]
    assert reactive, "no tile declared a rows: derivation — the filter drives nothing"
    for t in reactive:
        pred = filters.predicate_of(t["src"])
        assert filters.count(payload, pred) == t["raw"]["value"], (
            f"tile {t['label']!r} rendered {t['raw']['value']} but its predicate {pred!r} "
            f"counts {filters.count(payload, pred)} over the shipped payload. The page and "
            "its own filter data disagree before anything has been selected."
        )


def test_the_complementary_predicates_partition_the_accounts(page):
    """What makes the two subtraction sub-lines safe to recompute."""
    _m, _html, payload = page
    for sel in _selections(payload):
        kept = [r for r in payload if all(str(r.get(k) or "") == v for k, v in sel)]
        total = filters.count(kept, "all")
        assert filters.count(kept, "co_has_email") + filters.count(kept, "co_no_email") == total
        assert filters.count(kept, "co_role_inbox") <= filters.count(kept, "co_has_email")


def test_the_payload_agrees_with_the_roster_semantics(page):
    """Layer 1 proper: every predicate, every selection, against the semantic recompute."""
    m, _html, payload = page
    rows = m["roster"]["rows"]
    for sel in _selections(payload):
        kept = [r for r in payload if all(str(r.get(k) or "") == v for k, v in sel)]
        for pred in filters.PREDICATES:
            assert filters.count(kept, pred) == expected(rows, sel, pred), (pred, sel)


# --- layer 2: the shipped JavaScript ---------------------------------------------


def _node() -> str:
    exe = shutil.which("node")
    assert exe, (
        "node is not on PATH, and this test does not skip. The filter's interpreter is "
        "JavaScript; no other test in this repo reads it, so skipping here would leave the "
        "one layer nothing else covers permanently green. Install Node (CI does so via "
        "actions/setup-node in the pytest job) or delete the filter."
    )
    return exe


def _run_js(rows: list[dict], cases: list[list[tuple[str, str]]]) -> list[dict]:
    """Run the SHIPPED filter.js over `rows` for each selection, and return its counts."""
    driver = (
        f"const f = require({json.dumps(str(JS))});\n"
        "const job = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));\n"
        "const out = job.cases.map(function (sel) {\n"
        "  const shown = f.selectRows(job.rows, sel);\n"
        "  const counts = {};\n"
        "  job.preds.forEach(function (p) { counts[p] = f.countAccounts(shown, p); });\n"
        "  return {rows_shown: shown.length, counts: counts, groups: f.groupCounts(shown)};\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    job = json.dumps({"rows": rows, "cases": cases, "preds": list(filters.PREDICATES)})
    res = subprocess.run(  # noqa: S603 - fixed argv, no shell, input is our own JSON
        [_node(), "-e", driver],
        input=job,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, f"node failed: {res.stderr[-2000:]}"
    return json.loads(res.stdout)


def test_the_shipped_javascript_agrees_with_the_model_on_every_selection(page):
    """Layer 2 — the cross-check. Not a reimplementation: this RUNS the page's own code."""
    m, _html, payload = page
    sels = _selections(payload)
    assert len(sels) >= 8, f"only {len(sels)} selections — the seed has too little variety"
    got = _run_js(payload, sels)
    rows = m["roster"]["rows"]
    for sel, res in zip(sels, got, strict=True):
        for pred in filters.PREDICATES:
            assert res["counts"][pred] == expected(rows, sel, pred), (
                f"selection {sel} predicate {pred}: the shipped filter.js counted "
                f"{res['counts'][pred]}, the model says {expected(rows, sel, pred)}"
            )


def test_a_seeded_corruption_makes_the_cross_check_fail(page):
    """SEEDED POSITIVE CONTROL for layer 2.

    A harness that never actually reached node reads identically to one that agreed with it.
    Corrupt a row whose account has no second row to mask it, and require disagreement.
    """
    m, _html, payload = page
    rows = m["roster"]["rows"]
    bad = json.loads(json.dumps(payload))
    victim = next(r for r in bad if r["co_has_email"])
    victim["co_has_email"], victim["co_no_email"] = False, True
    got = _run_js(bad, [[]])[0]["counts"]["co_has_email"]
    assert got != expected(rows, [], "co_has_email"), (
        "corrupting the payload did not change what node returned, so this cross-check "
        "proves nothing — the driver is not reading the rows it was handed."
    )


def test_the_node_unit_suite_passes():
    """The JS's own tests, run from here so one `pytest` covers the whole contract."""
    res = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [_node(), "--test", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, f"node --test failed:\n{res.stdout[-4000:]}{res.stderr[-2000:]}"


def test_the_shared_fixture_still_describes_the_real_payload(page):
    """The JS fixture and the live payload must carry the same keys.

    The fixture is hand-written, so a predicate added to ``filters.PREDICATES`` without a
    fixture column would leave ``filter.test.js`` passing on a shape the page no longer has.
    """
    _m, _html, payload = page
    fix = json.loads(FIXTURE.read_text(encoding="utf-8"))
    want = set(payload[0])
    for row in fix["rows"]:
        assert set(row) == want, (
            f"tests/js/fixture.json row keys {sorted(set(row) ^ want)} differ from the live "
            "payload's. Update the fixture in the same change as the payload."
        )


# --- layer 3: the payload is safe to ship ----------------------------------------


def test_the_payload_carries_no_identity(page):
    """§R9 — the readable row is already in the DOM; a second copy would not be."""
    m, _html, payload = page
    blob = json.dumps(payload)
    for row in m["roster"]["rows"]:
        for field in ("company", "email", "seat", "why_now", "signal_source_url"):
            val = str(row.get(field) or "").strip()
            if len(val) > 3:
                assert val not in blob, f"{field} {val!r} reached the filter payload"
    assert "@" not in blob and "http" not in blob


def test_the_payload_carries_no_tile_values(page):
    """Numbers in the payload re-open the fail-open the provenance contract just closed.

    Assertion 2 there asks whether a recorded figure appears in the document; it strips
    ``<script>`` first, but the cheaper guarantee is that there is no number to find.
    """
    _m, _html, payload = page
    for row in payload:
        for key, val in row.items():
            # `bool` subclasses `int`, so the predicate flags must be excluded by TYPE and
            # not by an `isinstance(val, int)` that silently accepts every one of them.
            if isinstance(val, bool):
                continue
            assert key in ("i", "ci") or not isinstance(val, int), (
                f"payload row carries a number under {key!r} — the only integers allowed "
                "are the row and company indices."
            )


def test_every_count_slot_in_the_page_names_a_real_predicate(page):
    """A ``data-count-pred`` the payload cannot answer renders as 0 and looks like a fact."""
    _m, html, payload = page
    slots = set(re.findall(r'data-count-pred="([^"]+)"', html))
    assert slots, "the page rendered no count slots — the filter has nothing to write into"
    for pred in slots:
        assert pred in filters.PREDICATES, f"unknown predicate {pred!r} in the page"
        if pred != "all":
            assert all(pred in r for r in payload), f"{pred!r} missing from a payload row"


def test_a_single_value_facet_states_itself_and_never_pretends_to_filter(page):
    """§R18 — a dropdown offering "All" plus exactly one option cannot discriminate.

    The operator asked (2026-09-10) to keep the product facet visible even when only one
    product is in scope, so it renders — DISABLED, carrying the value and no "All". The
    property that matters is not whether it is shown but whether it can be mistaken for a
    live control: a disabled select reports an empty value, so the filter never reads it and
    no selection can silently return the whole set under a narrowed label.
    """
    _m, html, payload = page
    fs = filters.facets(payload)
    assert fs, "no facet at all — the bar has nothing to show"
    for f in fs:
        assert f["selectable"] == (len(f["values"]) > 1), f
    rendered = set(re.findall(r'<select data-key="([^"]+)"', html))
    selectable = {f["key"] for f in fs if f["selectable"]}
    assert rendered == selectable, (
        "only a facet with something to choose between may render as a <select>. A "
        "single-value facet renders as text — a disabled select still reports its value, "
        "which is what made the page open pre-filtered on 2026-09-10."
    )
    for f in fs:
        if not f["selectable"]:
            continue
        block = re.search(
            rf'<select data-key="{re.escape(f["key"])}".*?</select>', html, re.S
        ).group(0)
        assert '<option value="">All</option>' in block, (
            f"facet {f['key']!r}: without an empty first option the control opens "
            "pre-selected, so the page loads already filtered."
        )


def test_the_filter_script_appears_exactly_once_in_the_page(page):
    """PS16 — the inert duplicate. ``script_block`` fills ``template_filter.html`` with plain
    ``str.replace()``, which rewrites EVERY occurrence of a token, not only the one inside the
    live ``<script>``. The template's own comment used to spell out both placeholder tokens
    verbatim to describe them, so each substitution also rewrote that description — folding
    the whole JSON payload and the whole filter script into what was meant to be a comment,
    byte for byte identical to the live copy right beside it.
    """
    _m, html, _payload = page
    assert html.count("function mountFilter(ROWS)") == 1, (
        "the filter script's own function definition appears more than once in the page — "
        "the template's comment is duplicating the live <script> block again"
    )
    assert html.count("var GTM_ROWS = ") == 1, (
        "the payload-assignment line appears more than once — same duplication, the payload half"
    )


def test_no_payload_ships_without_a_bar(page):
    """``bar_html`` and ``script_block`` gate on the same condition, written separately."""
    m, html, _payload = page
    from gtm_core.email_campaign_dashboard.filters import bar_html, script_block

    assert bool(bar_html(m)) == bool(script_block(m)), (
        "one of the bar and the payload rendered without the other. A payload with no bar is "
        "dead weight; a bar with no payload is a control that does nothing."
    )
    assert ("GTM_ROWS" in html) == ('id="filterbar"' in html)


def test_a_tile_that_cannot_be_recomputed_says_so(page):
    """Every non-reactive tile carries its reason, server-rendered and hidden."""
    _m, html, _payload = page
    for t in fmt._tiles_recorded():
        if not filters.reactive(t["src"]):
            assert filters.grey_reason(t["src"]), t["label"]
    assert html.count('class="stat-why" hidden') == sum(
        not filters.reactive(t["src"]) for t in fmt._tiles_recorded()
    )


def test_a_tile_rendering_an_em_dash_is_never_reactive(page):
    """A refused figure must not acquire a number the moment a filter is applied."""
    _m, _html, _payload = page
    for t in fmt._tiles_recorded():
        if "—" in str(t["value"]):
            assert not filters.reactive(t["src"]), (
                f"tile {t['label']!r} renders a refusal but declares itself filterable, so "
                "selecting a facet would replace '—' with a count. That is the refusal "
                "contract being undone by the filter."
            )


VIEWS = sorted((REPO / "gtm_core" / "email_campaign_dashboard").glob("views_*.py"))


def _no_filter_blocks() -> list[tuple[str, str]]:
    """``(module, source)`` for every ``data-no-filter`` card, read out of the VIEW SOURCE.

    Checked in the source rather than in a rendered page on purpose. The one card carrying
    this marker today is the pool-wide countries chart, and it needs a populated prospect
    pool to render at all — so a fixture-based check passes by rendering nothing, which is
    §R18's "a check that cannot discriminate" wearing a green tick. The source is always
    there.

    A block runs from the opening ``<div`` to the ``</div>`` at the same indentation.
    """
    out = []
    for path in VIEWS:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "data-no-filter" not in line:
                continue
            indent = len(line) - len(line.lstrip())
            close = f"{' ' * indent}</div>"
            end = next(
                (j for j in range(i + 1, len(lines)) if lines[j].rstrip() == close), len(lines)
            )
            assert end < len(lines), f"{path.name}:{i + 1} data-no-filter card never closes"
            out.append((path.name, "\n".join(lines[i : end + 1])))
    return out


def test_the_filter_never_touches_a_pool_wide_card():
    """``data-no-filter`` marks a block whose denominator is the shared prospect pool.

    Rescaling it to a roster selection would answer a question nobody asked, with the
    filtered roster's numerator over the pool's denominator.
    """
    blocks = _no_filter_blocks()
    assert blocks, (
        "no data-no-filter card exists in any view module. Either the marker was removed "
        "from the pool-wide countries chart — in which case the filter will happily rescale "
        "it — or it was renamed and this check now guards nothing."
    )
    for module, block in blocks:
        for hook in ("data-count-pred", "data-row", "data-group-count"):
            assert hook not in block, (
                f"{module}: a data-no-filter card contains {hook}, so the filter would "
                "rescale a pool-wide figure to a roster selection — a numerator and a "
                "denominator counted over two different sets."
            )
        assert 'class="why" hidden' in block, (
            f"{module}: a data-no-filter card must carry its own hidden reason, or it greys "
            "out under a filter saying nothing about why."
        )


def test_the_judge_tally_is_independent_of_the_roster_sources(tmp_path):
    """The judge queue is profile-wide by construction, and must stay that way.

    ``judge_queue`` takes no sources: it reads the newest ``retarget-queue-*.jsonl`` and
    counts ROWS IN THAT FILE. On the profile rollup that is the right denominator. The
    hazard is a later "fix" that joins it to the roster, which would silently rescale a
    tally of drafted emails to a set of accounts.
    """
    from gtm_core.email_campaign_dashboard.roster import roster_model, roster_sources

    _seed(tmp_path)
    m = gd.build_model("acme", tmp_path)
    none_declared = roster_model("acme", roster_sources([]), tmp_path)
    assert none_declared["judge_tally"] == m["roster"]["judge_tally"], (
        "the judge tally changed when the roster sources did. It counts rows in the "
        "retarget queue, not accounts in the roster; joining the two would report a "
        "different denominator under the same sentence."
    )


#: Phrases ``test_dashboard_aggregation_refusal.py`` asserts are ABSENT from a page that
#: refused. A grey-out reason containing one would satisfy that file's ``not in`` assertion
#: from the wrong block — the refusal would still be broken and the test would still be
#: green. Kept as literals rather than parsed out of that module: a regex over its source
#: would silently narrow the moment someone reformats an assertion.
_REFUSAL_LITERALS = (
    "the whole segment, not a slice",
    "Every account in",
    "Not shown for",
    "declare where their accounts live",
    "Not shown as one figure",
    "do not run side by side",
    "No single verdict",
    "(weighted by prospects)",
    "of —",
)


def test_no_grey_out_reason_collides_with_a_pinned_refusal_phrase():
    """The filter's sentences must not be able to satisfy the refusal contract's checks.

    ``test_dashboard_aggregation_refusal.py`` proves a scope that cannot aggregate honestly
    says so, and several of its assertions are ``phrase not in page``. Every tile on this
    page now also carries a grey-out sentence; if one of those happened to contain a pinned
    phrase, that file would go on passing while the thing it guards was broken.
    """
    reasons = [filters.grey_reason(op) for op in ("", "sum:x", "count:live", "agree:a.b.c")]
    reasons += [filters._GREY[k] for k in filters._GREY]
    for reason in reasons:
        for literal in _REFUSAL_LITERALS:
            assert literal not in reason, (
                f"the grey-out reason {reason[:60]!r}... contains {literal!r}, which "
                "test_dashboard_aggregation_refusal.py asserts is absent from a refused "
                "page. Reword the reason."
            )


def test_a_refused_figure_stays_refused_under_every_selection(page):
    """Step 33's real claim: a filter cannot turn ``of —`` into a number.

    ``_stat`` only emits a count slot for a tile whose ``src`` is a single ``rows:`` token,
    so a refused figure has nowhere for the filter to write. Asserted on the rendered page
    rather than on the rule, because the rule is what would be changed by accident.
    """
    _m, html, _payload = page
    for value_block in re.findall(r'<div class="stat-value">(.*?)</div>', html):
        if "—" in value_block:
            assert "data-count-pred" not in value_block, (
                f"a refused figure {value_block!r} carries a count slot, so selecting a "
                "facet would replace the em dash with a number the page has no basis for."
            )


def test_the_bar_opens_with_no_filter_active(page):
    """A control that reports a value the reader never set makes the page open filtered.

    THE BUG, 2026-09-10. A single-value facet rendered as ``<select disabled>`` carrying its
    one option. ``disabled`` withholds an element from form SUBMISSION — it does NOT empty
    ``HTMLSelectElement.value`` — so the page's own ``chosen()`` read a filter off it before
    the reader touched anything, decided a filter was active, and greyed out all thirteen
    unfilterable tiles with their "this figure did not move" reasons showing. The page opened
    looking like it was full of warnings.

    Nothing caught it. The node suite tests the interpreter over a fixture and never renders
    a bar; the Python suite asserted the disabled select's SHAPE and never asked what value
    it would report. Both were green. So the property under test here is the rendered
    artifact's opening state, which is the thing that was actually wrong.
    """
    _m, html, payload = page
    bar = re.search(r'<div class="filterbar".*?</div>', html, re.S)
    assert bar, "no filter bar rendered"
    bar = bar.group(0)

    assert "disabled" not in bar, (
        "a disabled control is still a control: it keeps its value and the filter reads it. "
        "A facet with nothing to choose between must render as text, not as a <select>."
    )
    for block in re.findall(r"<select\b.*?</select>", bar, re.S):
        first = re.search(r"<option\b[^>]*>", block)
        assert first and 'value=""' in first.group(0), (
            "a <select> whose first option carries a value opens pre-selected, so the page "
            f"loads already filtered: {block[:120]}"
        )
        assert "selected" not in block, "no option may be pre-selected"

    # And the statement form carries the value where a control cannot.
    for f in filters.facets(payload):
        if not f["selectable"]:
            assert f"<strong>{f['values'][0]['label']}</strong>" in bar


def test_every_scope_opens_unfiltered(tmp_path):
    """The scoped pages are where this bit hardest — campaign is single-valued there too."""
    _seed(tmp_path)
    for campaign in (None, "mine-20260904"):
        m = gd.build_model("acme", tmp_path)
        if campaign:
            m = gd.scope_to_campaign(m, campaign)
        html = gd.render_html(m)
        bar = re.search(r'<div class="filterbar".*?</div>', html, re.S)
        if not bar:
            continue
        assert "disabled" not in bar.group(0), f"scope {campaign!r} renders a disabled control"
