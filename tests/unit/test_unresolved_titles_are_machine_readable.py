"""The unresolved-title counter must be readable in full, not as three exemplars (P4).

`render` caps unresolved titles at `EXEMPLARS` (three). That is right for a person reading a
report and wrong for the one use this counter has — harvesting title synonyms — because three
out of a long tail is a sample, and an operator who adds the three cues they were shown leaves
the fourth title failing silently. `--json` is the complete counter.

Not hypothetical: the PRD that asked for this flag named THREE unresolved engineering titles,
read off the capped text. The full counter on the same live campaign holds five.
"""

from __future__ import annotations

import json
from collections import Counter

from gtm_core.hook_coverage.config import EXEMPLARS
from gtm_core.hook_coverage.coverage import Coverage
from gtm_core.hook_coverage.render import render, unresolved_json


def _cov(distinct: int) -> Coverage:
    cov = Coverage()
    cov.rows = distinct
    cov.unresolved = Counter({f"Some Very Long Job Title Number {i}": 1 for i in range(distinct)})
    cov.personas = Counter({"cto": 4})
    cov.seats = Counter({"technical": 4})
    return cov


def test_the_rendered_text_still_caps_its_exemplars():
    """The cap is not a bug and is not removed — a report a human reads must stay readable.
    Both surfaces come from one `Coverage`; there is no second walk of the rows."""
    text = render(_cov(EXEMPLARS + 5))
    shown = [ln for ln in text.splitlines() if "e.g. Some Very Long Job Title" in ln]
    assert len(shown) == EXEMPLARS


def test_the_json_carries_every_distinct_title():
    n = EXEMPLARS + 5
    payload = unresolved_json(_cov(n))
    assert len(payload["unresolved_titles"]) == n, (
        "the machine-readable counter was capped too — then it is the same sample the "
        "rendered text already gives, and the flag buys nothing"
    )
    assert payload["unresolved_rows"] == n
    assert payload["exemplars_shown_in_text"] == EXEMPLARS


def test_it_is_ordered_most_common_first():
    """A synonym harvest works down from the most frequent title; arbitrary order makes the
    operator sort it themselves or, more likely, act on whatever is at the top."""
    cov = Coverage()
    cov.rows = 10
    cov.unresolved = Counter({"rare": 1, "common": 7, "middling": 2})
    assert list(unresolved_json(cov)["unresolved_titles"]) == ["common", "middling", "rare"]


def test_unresolved_and_unassignable_stay_separate_numbers():
    """Different problems with different fixes: an unresolved title needs a CUE, an
    unassignable persona needs a grid ROW (and the spec that row owes). Summing them would
    send an operator to fix the wrong one."""
    cov = Coverage()
    cov.rows = 6
    cov.unresolved = Counter({"VP of Something": 2})
    cov.unassignable = Counter({"cio/enterprise": 4})
    payload = unresolved_json(cov)
    assert payload["unresolved_rows"] == 2
    assert payload["unassignable_rows"] == 4
    assert payload["unassignable"] == {"cio/enterprise": 4}


def test_the_cli_emits_parseable_json(tmp_path, monkeypatch, capsys):
    from gtm_core.hook_coverage.cli import main

    profiles = tmp_path / "profiles" / "acme" / "knowledge"
    profiles.mkdir(parents=True)
    (profiles / "hook-matrix.md").write_text(
        "# Hook matrix\n\n## Startup\n\n| id | Persona | Signal to open on | Hook angle |\n"
        "|---|---|---|---|\n| h1 | CTO | Questionnaire arrived | A. |\n",
        encoding="utf-8",
    )
    seq = tmp_path / "content" / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    (seq / "cells.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))

    main(["--profile", "acme", "--json", "--warn-only"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"rows", "unresolved_rows", "unresolved_titles", "unassignable"}
