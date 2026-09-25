"""The live scorecard migration: every score the 2026-09-21 rebuild produced must survive the move.

THIS IS A REFACTOR, NOT A RE-SCORING. The weights, the sufficiency gate and the modulator moved
from a run's working memory into ``profiles/<tenant>/knowledge/scorecard.toml``. If any of the 445
scored rows moves by a point, the migration is wrong until proven otherwise.

WHAT THIS PROVES, AND WHAT IT DOES NOT
--------------------------------------
Each row's *semantic inputs* are recovered from the derivation the v12 run recorded alongside its
score — ``"company-specific agent evidence on the row"`` becomes the token ``present``, not the
number 30. The engine then does its own weight lookup, modulation, summation, rounding and
tiering. So this proves the **rubric** migrated faithfully.

It does **not** re-prove the 2026-09-21 run's per-row judgement of each account — whether that row
really had company-specific agent evidence. Re-deriving those would be a re-scoring, which the PRD
puts out of scope. Every recovered token is cross-checked against the points the run recorded for
it, so a mis-parse fails loudly rather than quietly agreeing with itself.

Reads live tenant data, which the OSS carve does not ship — guarded, per the house pattern in
``tests/lint/test_third_party_roster.py``.
"""

from __future__ import annotations

import json
import re
import statistics
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

from gtm_core.scorecard import Categorised, Scored, load, score_row

ROOT = Path(__file__).resolve().parents[2]
#: The tenant is DISCOVERED, never named here (§R9): this file ships and `content/` does not, so
#: a literal slug would carry a real company's name into the carve with no data to justify it.
_RUN_NAME = "xlsx-run-20260921"
_RUNS = sorted(ROOT.glob(f"content/*/prospects/{_RUN_NAME}"))
RUN = _RUNS[0] if _RUNS else ROOT / "content" / "_absent" / "prospects" / _RUN_NAME
TENANT = RUN.parent.parent.name
CELLS = RUN / "cells.jsonl"
WORKBOOK = RUN / "output" / "enriched-v12.xlsx"
BATCH = "v12-score100"

pytestmark = pytest.mark.skipif(
    not CELLS.is_file(),
    reason="reads live tenant data, which the OSS carve does not ship",
)

#: The run's own recorded outcome (``run.json`` -> ``v12_priority_score_0_100.result``). These are
#: the numbers the PRD's headline claim rests on.
EXPECTED_SCORED = 445
EXPECTED_CATEGORISED = 558
EXPECTED_TIERS = {"A": 45, "B": 155, "C": 208, "D": 37}
EXPECTED_CATEGORIES = {
    "Unscored — no buyer-intent reading": 168,
    "Unscored — agent activity not assessed": 167,
    "Partner — channel (not a buyer)": 99,
    "Unscored — no research on file": 74,
    "Blocked — outside target markets": 20,
    "Unscored — no ICP label": 20,
    "Excluded — by Strategy": 7,
    "Excluded — not a company": 3,
}

#: Recorded derivation phrase -> the token the card declares. Identity comes from the phrase;
#: the points are then used to VERIFY the choice, so a wrong mapping cannot pass quietly.
_AGENT = {
    "company-specific agent evidence on the row": "present",
    "agent activity in the sector but nothing company-specific": "industry_only",
    "researched and no agent evidence found": "absent",
}


#: The record kind each recovered token needs (gtm_core.scorecard.record.COMPATIBLE).
_KIND_FOR = {"present": "ai", "industry_only": "none", "absent": "none"}


def _intent_prefixes(card) -> tuple[tuple[str, str], ...]:
    """(prefix, token) for the buyer-intent axis, longest prefix first.

    READ FROM THE CARD, not restated: the phrases are the tenant's own (one of them names their
    product category), so `profiles/` is their only home. Two normalisations make a card phrase
    usable as a prefix of what the run actually recorded. A parenthetical qualifier is dropped
    (the card says "elevated buyer intent (60-74)"; the run wrote "elevated buyer intent, top
    topic score 65"), the same `" ("` split the agent axis above already relies on. And longest
    first, because one prefix extends another — the category-topic one extends the plain high
    one — so a shorter sibling tried first would swallow every extension.
    """
    axis = next(a for a in card.axes if a.name == "buyer_intent")
    prefixes = ((p.split(" (")[0], t) for t, p in axis.phrases.items())
    return tuple(sorted(prefixes, key=lambda pair: -len(pair[0])))


_POINTS = re.compile(r"\(([\d.]+) of (\d+)\)")
_LABEL = re.compile(r"(ICP\d) priority per the list's own ICP ordering")
_MODULATED = re.compile(
    r"ICP1 fit on the gated segment rubric, required subtotal (\d+)/(\d+) (\w+)"
)
#: Required-input name -> the category the card must emit. The counts are read from the record
#: (see the fixture-integrity test); this map is what the engine is checked against.
_MISSING_FOR = {
    "Unscored — no ICP label": "icp_label",
    "Blocked — outside target markets": "in_target_market",
    "Unscored — no research on file": "research_on_file",
    "Unscored — agent activity not assessed": "agent_evidence",
    "Unscored — no buyer-intent reading": "intent_reading",
}
_EXCLUSION_FOR = {
    "Excluded — by Strategy": "by_strategy",
    "Excluded — not a company": "not_a_company",
    "Partner — channel (not a buyer)": "partner_channel",
}


# --------------------------------------------------------------------------------------------
# Reading the recorded run
# --------------------------------------------------------------------------------------------


def _v12_cells() -> dict[int, dict[str, object]]:
    rows: dict[int, dict[str, object]] = {}
    for line in CELLS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cell = json.loads(line)
        if cell.get("batch_id") != BATCH:
            continue
        _sheet, number, column = cell["key"]
        rows.setdefault(int(number), {})[str(column)] = cell["value"]
    return rows


def _segments(rationale: str) -> list[str]:
    body = rationale.split("Built from four account axes: ")[1]
    return [s.strip() for s in body.split(". Contact availability")[0].split(";")]


def _points(segment: str) -> float:
    match = _POINTS.search(segment)
    assert match, f"no points recorded in {segment!r}"
    return float(match.group(1))


def _inputs(card, rationale: str) -> tuple[dict[str, object], dict[str, float]]:
    """The row as the engine wants it, plus the points the run recorded per axis."""
    fit, agent, intent, why_now, market = _segments(rationale)

    row: dict[str, object] = {"in_target_market": True, "research_on_file": True}

    label = _LABEL.search(fit)
    modulated = _MODULATED.search(fit)
    if label:
        row["icp_label"] = label.group(1)
    elif modulated:
        row["icp_label"] = "ICP1"
        row["icp1_rubric_subtotal"] = {
            "subtotal": int(modulated.group(1)),
            "max": int(modulated.group(2)),
            "segment": modulated.group(3),
        }
    else:
        raise AssertionError(f"unrecognised ICP segment: {fit!r}")

    phrase = agent.split(" (")[0]
    assert phrase in _AGENT, f"unrecognised agent segment: {agent!r}"
    row["agent_evidence"] = _AGENT[phrase]
    # PH15: the engine derives the word from the row's record and refuses a contradiction. The
    # v12 run recorded no kind, so the one each token requires is supplied — this proves the
    # RUBRIC migrated, not that the run's per-row agent judgement was right (see the header).
    row["signal_agent_kind"] = _KIND_FOR[row["agent_evidence"]]

    row["intent_reading"] = next(
        (token for prefix, token in _intent_prefixes(card) if intent.startswith(prefix)),
        None,
    )
    assert row["intent_reading"], f"unrecognised intent segment: {intent!r}"

    row["dated_why_now"] = _points(why_now) > 0
    assert _points(market) == 8, f"unexpected market points in {market!r}"

    recorded = {
        "icp_fit": _points(fit),
        "agent_evidence": _points(agent),
        "buyer_intent": _points(intent),
        "timing_and_market": _points(why_now) + _points(market),
    }
    return row, recorded


@pytest.fixture(scope="module")
def card():
    return load(TENANT, profiles_root=ROOT / "profiles")


@pytest.fixture(scope="module")
def recorded() -> dict[int, dict[str, object]]:
    return _v12_cells()


# --------------------------------------------------------------------------------------------
# 1. Fixture integrity — if the corpus is not what the run said it was, nothing below means
#    anything. These assert the RECORD, not the engine, and are labelled as such.
# --------------------------------------------------------------------------------------------


def test_the_recorded_run_is_the_one_the_prd_describes(recorded) -> None:
    scored = [v["G"] for v in recorded.values() if isinstance(v["G"], (int, float))]
    categories = Counter(v["G"] for v in recorded.values() if isinstance(v["G"], str))
    assert len(recorded) == EXPECTED_SCORED + EXPECTED_CATEGORISED
    assert len(scored) == EXPECTED_SCORED
    assert dict(categories) == EXPECTED_CATEGORIES
    assert sum(categories.values()) == EXPECTED_CATEGORISED


# --------------------------------------------------------------------------------------------
# 2. The migration bar
# --------------------------------------------------------------------------------------------


def test_every_scored_row_reproduces_its_exact_score(card, recorded) -> None:
    """All 445, byte-identical. Any drift is a migration bug until proven otherwise."""
    drift: list[str] = []
    checked = 0
    for number, cell in sorted(recorded.items()):
        if not isinstance(cell["G"], (int, float)):
            continue
        checked += 1
        row, _ = _inputs(card, str(cell["H"]))
        result = score_row(card, row)
        assert isinstance(result, Scored), f"row {number} categorised but was scored in v12"
        if result.score != cell["G"]:
            drift.append(f"row {number}: v12={cell['G']} engine={result.score}")
    assert checked == EXPECTED_SCORED, f"only reached {checked} of {EXPECTED_SCORED} scored rows"
    assert not drift, "\n".join(drift[:25])


def test_every_axis_awards_the_points_the_run_recorded(card, recorded) -> None:
    """The per-axis check behind the total. A compensating pair of errors could reproduce a sum;
    this makes each axis answer for itself."""
    drift: list[str] = []
    for number, cell in sorted(recorded.items()):
        if not isinstance(cell["G"], (int, float)):
            continue
        row, expected = _inputs(card, str(cell["H"]))
        result = score_row(card, row)
        assert isinstance(result, Scored)
        for axis, part in zip(card.axes, result.derivation, strict=True):
            got = float(_POINTS.search(part).group(1))
            if abs(got - expected[axis.name]) > 0.05:
                drift.append(f"row {number} {axis.name}: v12={expected[axis.name]} engine={got}")
    assert not drift, "\n".join(drift[:25])


def test_the_tier_histogram_is_unchanged(card, recorded) -> None:
    tiers = Counter()
    for cell in recorded.values():
        if not isinstance(cell["G"], (int, float)):
            continue
        result = score_row(card, _inputs(card, str(cell["H"]))[0])
        assert isinstance(result, Scored)
        tiers[result.tier] += 1
    assert dict(tiers) == EXPECTED_TIERS


def test_the_modulated_icp1_rows_are_the_ones_that_needed_it(card, recorded) -> None:
    """The modulator is the subtlest part of the migration: 31 rows, and it is the only place a
    fractional intermediate reaches the total. Three of them land on exactly ``.5``, where a
    rounding-rule change would show up and nowhere else."""
    modulated = [
        str(c["H"])
        for c in recorded.values()
        if isinstance(c["G"], (int, float)) and _MODULATED.search(str(c["H"]))
    ]
    assert len(modulated) == 31
    halves = 0
    for rationale in modulated:
        row, expected = _inputs(card, rationale)
        mod = row["icp1_rubric_subtotal"]
        exact = 25 * mod["subtotal"] / mod["max"]
        assert abs(exact - expected["icp_fit"]) < 0.05
        if (exact + sum(v for k, v in expected.items() if k != "icp_fit")) % 1 == 0.5:
            halves += 1
    assert halves >= 1, "no row exercises the .5 rounding boundary — the rule is untested"


# --------------------------------------------------------------------------------------------
# 3. Categories
# --------------------------------------------------------------------------------------------


def _row_missing(name: str) -> dict[str, object]:
    """A row satisfying every required input EXCEPT ``name``."""
    full: dict[str, object] = {
        "icp_label": "ICP3",
        "in_target_market": True,
        "research_on_file": True,
        "agent_evidence": "present",
        "signal_agent_kind": "ai",
        "intent_reading": "high",
        "dated_why_now": True,
    }
    if name == "in_target_market":
        full[name] = False
    elif name == "agent_evidence":
        # The input is derived from the record, so losing it means losing the record too.
        full.pop(name)
        full.pop("signal_agent_kind")
    else:
        # A name no row carries ("nothing_is_missing") leaves the row complete — that is how the
        # positive controls below ask for a row with nothing wrong with it.
        full.pop(name, None)
    return full


@pytest.mark.parametrize("text,missing", sorted(_MISSING_FOR.items()))
def test_each_missing_input_produces_the_recorded_category(card, text, missing) -> None:
    result = score_row(card, _row_missing(missing))
    assert isinstance(result, Categorised)
    assert result.category == text
    assert result.missing_input == missing


@pytest.mark.parametrize("text,token", sorted(_EXCLUSION_FOR.items()))
def test_each_exclusion_produces_the_recorded_category(card, text, token) -> None:
    """An upstream verdict beats every sufficiency question — including on a row that would
    otherwise have scored, which is what makes it an exclusion rather than a gap."""
    row = _row_missing("nothing_is_missing") | {"exclusion": token}
    result = score_row(card, row)
    assert isinstance(result, Categorised)
    assert result.category == text
    assert result.missing_input == token


def test_a_fully_populated_row_still_scores(card) -> None:
    """Positive control. Without it every category test above is consistent with an engine that
    refuses everything."""
    result = score_row(card, _row_missing("nothing_is_missing"))
    assert isinstance(result, Scored)
    assert result.score == 20 + 30 + 19 + 12 + 8


# --------------------------------------------------------------------------------------------
# 4. §4.9 — discrimination on the real distribution, not on a fixture
# --------------------------------------------------------------------------------------------


def _team_counts() -> dict[int, float]:
    """Column O ("# teams") straight out of the sheet XML — stdlib + defusedxml, no new dep."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(WORKBOOK) as archive:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        targets = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        sheet = next(
            targets[s.get(rel)]
            for s in book.iter(f"{ns}sheet")
            if s.get("name") == "MASTER_ACCOUNTS"
        ).lstrip("/")
        grid = ET.fromstring(archive.read(sheet if sheet.startswith("xl/") else f"xl/{sheet}"))

    counts: dict[int, float] = {}
    for cell in grid.iter(f"{ns}c"):
        ref = cell.get("r") or ""
        if "".join(ch for ch in ref if ch.isalpha()) != "O" or cell.get("t") == "s":
            continue
        value = cell.find(f"{ns}v")
        if value is None or value.text is None:
            continue
        try:
            counts[int("".join(ch for ch in ref if ch.isdigit()))] = float(value.text)
        except ValueError:
            continue
    return counts


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def test_no_single_tier_holds_most_of_the_list(card, recorded) -> None:
    """806 of 1000 rows came out Tier A under the rubric this replaces, and every other check
    was green. A scale that does not separate is not a scale."""
    tiers = Counter()
    for cell in recorded.values():
        if isinstance(cell["G"], (int, float)):
            result = score_row(card, _inputs(card, str(cell["H"]))[0])
            tiers[result.tier] += 1
    total = sum(tiers.values())
    largest, count = tiers.most_common(1)[0]
    assert count / total < 0.60, f"tier {largest} holds {count}/{total} = {count / total:.1%}"


@pytest.mark.skipif(not WORKBOOK.is_file(), reason="v12 workbook not on disk")
def test_the_score_does_not_track_how_many_teams_listed_the_account(card, recorded) -> None:
    """The check the 2026-09-21 rubric failed while passing everything else: its mean climbed
    9.79 -> 11.31 -> 11.94 with team count, i.e. it measured our own coverage."""
    teams = _team_counts()
    scores: list[float] = []
    counts: list[float] = []
    for number, cell in recorded.items():
        if not isinstance(cell["G"], (int, float)) or number not in teams:
            continue
        result = score_row(card, _inputs(card, str(cell["H"]))[0])
        assert isinstance(result, Scored)
        scores.append(float(result.score))
        counts.append(teams[number])

    assert len(scores) >= 200, f"only {len(scores)} rows carry a team count — too few to judge"
    rho = _spearman(scores, counts)
    assert abs(rho) < 0.30, f"score tracks team count: Spearman rho = {rho:.3f}"
