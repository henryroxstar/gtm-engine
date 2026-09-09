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


# ═════════════════════════════════════════════════════════════════════════════
# reference-image edit depth (2026-09-06, C1) — the second surface for one number
# ═════════════════════════════════════════════════════════════════════════════


def _shotlist_with_refs(refs: list[str]) -> dict:
    return {
        "source_item": "it-1",
        "total_duration_s": 4.0,
        "style_scaffold": {"look": "clean studio light", "provider_model": "m"},
        "shots": [
            {
                "duration_s": 4.0,
                "camera": "static",
                "motion_prompt": "she turns to the lens",
                "visual": "a founder at a desk",
                "audio_bed": "room tone",
                "reference_images": refs,
            }
        ],
    }


def _storyboard(chain: list[str]) -> dict:
    """A storyboard whose stills form one delta-edit chain, hero first."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = []
    for i, name in enumerate(chain, start=1):
        entry = {
            "n": i,
            "image_job_id": f"job-{i}",
            "image_path": f"content/acme/video/run/{name}",
            "identity_anchor": anchor,
        }
        if i > 1:
            entry["derived_from"] = f"job-{i - 1}"
        entries.append(entry)
    return {"entries": entries}


def test_a_reference_past_the_depth_cap_warns():
    """hero + 5 derivatives: the last still is 5 edits down, past the cap of 4."""
    from gtm_core.shots_lint import lint_shotlist

    chain = ["hero.png", "d1.png", "d2.png", "d3.png", "d4.png", "d5.png"]
    errors, warnings = lint_shotlist(
        _shotlist_with_refs(["content/acme/video/run/d5.png"]), storyboard=_storyboard(chain)
    )
    assert errors == []
    assert any("d5.png" in w and "5 edit(s)" in w for w in warnings), warnings


def test_a_reference_at_the_depth_cap_is_clean():
    """Positive control and the other side of the boundary — depth 4 is at the cap, not past it."""
    from gtm_core.shots_lint import lint_shotlist

    chain = ["hero.png", "d1.png", "d2.png", "d3.png", "d4.png"]
    errors, warnings = lint_shotlist(
        _shotlist_with_refs(["content/acme/video/run/d4.png"]), storyboard=_storyboard(chain)
    )
    assert errors == []
    assert not any("edit(s) from its hero" in w for w in warnings), warnings


def test_the_rule_is_inert_without_a_storyboard():
    """Depth lives on the storyboard entry and nowhere else. With none supplied the rule says
    nothing rather than guessing — a lint that fires on absent evidence gets ignored."""
    from gtm_core.shots_lint import lint_shotlist

    errors, warnings = lint_shotlist(_shotlist_with_refs(["content/acme/video/run/d5.png"]))
    assert errors == []
    assert not any("edit(s) from its hero" in w for w in warnings), warnings


def test_the_depth_warning_is_a_warning_and_never_an_error():
    """Asserted on the channel, so a later well-meaning promotion trips this test rather than
    blocking an operator mid-run."""
    from gtm_core.shots_lint import lint_shotlist

    chain = ["hero.png"] + [f"d{i}.png" for i in range(1, 7)]
    errors, warnings = lint_shotlist(
        _shotlist_with_refs(["content/acme/video/run/d6.png"]), storyboard=_storyboard(chain)
    )
    assert errors == [], "the reference-depth rule must never reach the errors channel"
    assert warnings


def test_a_broken_storyboard_does_not_fail_the_lint():
    """A circular lineage is `storyboard approve`'s refusal to make, not this rule's."""
    from gtm_core.shots_lint import lint_shotlist

    broken = {
        "entries": [
            {"n": 1, "image_job_id": "a", "derived_from": "b", "image_path": "x/a.png"},
            {"n": 2, "image_job_id": "b", "derived_from": "a", "image_path": "x/b.png"},
        ]
    }
    errors, warnings = lint_shotlist(_shotlist_with_refs(["x/a.png"]), storyboard=broken)
    assert errors == []


def test_the_cap_has_one_home():
    """`shots_lint` imports MAX_EDIT_DEPTH; it does not declare a second copy that can drift."""
    import inspect

    from gtm_core import storyboard as sb
    from gtm_core.shots_lint import api, camera

    assert camera.MAX_EDIT_DEPTH is sb.MAX_EDIT_DEPTH
    assert api._active_constants()["max_edit_depth"] == sb.MAX_EDIT_DEPTH
    for module in (camera, api):
        assert "MAX_EDIT_DEPTH =" not in inspect.getsource(module), (
            f"{module.__name__} declares its own copy of the depth cap"
        )


def test_the_rule_publishes_itself_in_active_rules():
    """`--rules` is derived by AST-walking lint_shotlist, so a new rule must appear there."""
    from gtm_core.shots_lint import active_rules

    names = {r["name"] for r in active_rules()}
    assert "_lint_reference_edit_depth" in names


def test_the_storyboard_flag_refuses_a_path_outside_the_content_root(tmp_path, monkeypatch, capsys):
    """A caller-supplied read path gets the same confinement as every other one."""
    import json as _json

    from gtm_core.shots_lint.cli import main

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content").mkdir()
    shots = tmp_path / "content" / "x.shots.json"
    shots.write_text(_json.dumps(_shotlist_with_refs(["a.png"])), encoding="utf-8")
    outside = tmp_path / "elsewhere.json"
    outside.write_text(_json.dumps(_storyboard(["hero.png"])), encoding="utf-8")

    # Positive control: a storyboard INSIDE the root is accepted.
    inside = tmp_path / "content" / "storyboard.json"
    inside.write_text(_json.dumps(_storyboard(["hero.png"])), encoding="utf-8")
    assert main([str(shots), "--storyboard", str(inside), "--no-schema"]) == 0

    assert main([str(shots), "--storyboard", str(outside), "--no-schema"]) == 2
    payload = _json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "outside the resolved content root" in payload["error"]
