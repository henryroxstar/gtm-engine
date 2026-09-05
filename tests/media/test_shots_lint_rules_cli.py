"""gtm_core.shots_lint --rules — the linter publishes its own rule set.

The [restated-numbers-go-stale](../../CLAUDE.md) class, caught in the act. `video-router` Step 0.5
told the script writer to target "presenter share under 60%, at least one `role: \"screen\"`" —
a hand-copied summary of what `shots_lint` enforces. On 2026-08-28 the linter grew new rules
(`audio_bed`, the look/ratio check) and the summary did not, so a script written to the documented
target was refused by rules it had never been told about.

The fix is structural, not editorial: the module that OWNS the rules prints them, and the prose
relays that output instead of paraphrasing it. Re-copying the list by hand would just restart the
clock.

The anti-drift property lives in `test_rules_cli_lists_every_active_plan_time_rule` below: it
reads which `_lint_*` helpers `lint_shotlist` actually invokes and requires every one to be
reported. A rule added without appearing here is the 2026-08-28 failure repeating, and the test
fails rather than the docs quietly going stale.

Design: the 2026-08-29 video-router hardening note, item C3a.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from gtm_core import shots_lint as sl


def _rules_invoked_by_the_entry_point() -> set[str]:
    """Every ``_lint_*`` helper called from ``lint_shotlist``, read off the AST.

    Derived, not listed — the point of the test is to notice a rule nobody wrote down.
    """
    tree = ast.parse(inspect.getsource(sl.lint_shotlist))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("_lint_")
    }


def test_rules_cli_lists_every_active_plan_time_rule(capsys):
    """Guards the C3a drift.

    Every rule the entry point runs must be reported. Adding `_lint_spoken_has_a_voice` without
    surfacing it here is exactly how the 60%-and-a-screen-shot summary went stale.
    """
    assert sl.main(["--rules"]) == 0
    payload = json.loads(capsys.readouterr().out)
    reported = {rule["name"] for rule in payload["rules"]}

    missing = _rules_invoked_by_the_entry_point() - reported
    assert not missing, (
        f"`--rules` does not report these active rules: {sorted(missing)}. A rule the linter "
        "enforces but never publishes is a rule scripts get refused by without warning."
    )


def test_every_reported_rule_explains_itself(capsys):
    """A bare rule name is not a target anyone can write to. Each carries a one-line summary,
    taken from the function's own docstring so it cannot drift from the code."""
    sl.main(["--rules"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["rules"], "no rules reported at all"
    undocumented = [
        rule["name"]
        for rule in payload["rules"]
        if not rule.get("summary", "").strip() or rule["summary"] == "(no docstring)"
    ]
    assert not undocumented, (
        f"these rules publish no explanation: {undocumented}. The summary is a docstring first "
        "line — give the function one, rather than describing it somewhere it can drift from."
    )


def test_rules_cli_reports_the_presenter_ceiling_from_the_constant(capsys):
    """The value must equal MAX_PRESENTER_SHARE, not a literal that happens to match today."""
    sl.main(["--rules"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["constants"]["MAX_PRESENTER_SHARE"] == sl.MAX_PRESENTER_SHARE


def test_rules_cli_reports_the_required_shot_role(capsys):
    """The other half of the summary the router used to hardcode."""
    sl.main(["--rules"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["constants"]["required_shot_role"] == "screen"


def test_rules_cli_is_read_only(tmp_path: Path, capsys, monkeypatch):
    """Free and side-effect-free: it takes no path, writes nothing, and touches no network.
    `video_preflight` calls it on every routing decision, so it has to stay that cheap."""
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    assert sl.main(["--rules"]) == 0
    capsys.readouterr()
    assert set(tmp_path.rglob("*")) == before, "--rules wrote to disk"


def test_rules_does_not_require_a_shot_list_path(capsys):
    """`path` is required for a lint run and must NOT be for a rules dump — otherwise every
    caller has to invent a file to ask a question about the rules."""
    assert sl.main(["--rules"]) == 0
    assert json.loads(capsys.readouterr().out)["rules"]
