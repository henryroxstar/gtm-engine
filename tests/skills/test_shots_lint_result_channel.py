"""The shot-list lint's third result channel and craft hook (PRD 2026-10-19 R8).

``lint_shotlist`` keeps its ``(errors, warnings)`` shape byte-for-byte; ``lint_shotlist_detailed``
adds ``not_checked`` and rule-id'd ``findings`` from an optional ``gtm_core.video_craft.hook``.
That package does not exist yet (and is withheld from the public cut), so the hook-present
cases inject a fake module through ``sys.modules``; the hook-absent cases pin the public cut's
CLI to the committed tripwire goldens, in-process.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types

import pytest

from gtm_core import shots_lint as sl
from gtm_core.shots_lint import Finding, LintResult, lint_shotlist_detailed
from tests.tripwire import support

CORPUS = support.HERE / "shots_lint"
HOOK = "gtm_core.video_craft.hook"
CLEAN_KW = {"voice_grade": "professional", "voice_id": "voice-1"}


def _doc(name: str) -> object:
    return json.loads((CORPUS / name).read_text(encoding="utf-8"))


@pytest.fixture
def no_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, HOOK, None)


@pytest.fixture
def fake_hook(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def craft_findings(doc, *, require_craft, base_dir=None):
        calls.append({"doc": doc, "require_craft": require_craft})
        findings = [
            Finding("craft.static_share", "warning", "craft: static share 60% over 40%", None),
            Finding("craft.words_per_shot", "error", "craft: shot[2] carries 31 words", 2),
        ]
        return findings, ([] if require_craft else ["craft"])

    pkg = types.ModuleType("gtm_core.video_craft")
    pkg.__path__ = []  # a package, so the child import resolves
    mod = types.ModuleType(HOOK)
    mod.craft_findings = craft_findings
    monkeypatch.setitem(sys.modules, "gtm_core.video_craft", pkg)
    monkeypatch.setitem(sys.modules, HOOK, mod)
    return calls


# ── API ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", sorted(p.name for p in CORPUS.glob("*.shots.json")))
def test_absent_hook_is_lint_shotlist_exactly(no_hook, fixture: str) -> None:
    doc = _doc(fixture)
    result = lint_shotlist_detailed(doc, **CLEAN_KW)
    assert isinstance(result, LintResult)
    assert (result.errors, result.warnings) == sl.lint_shotlist(doc, **CLEAN_KW)
    assert result.not_checked == [] and result.findings == []


def test_present_hook_merges_findings_by_severity(fake_hook) -> None:
    doc = _doc("clean.shots.json")
    base_errors, base_warnings = sl.lint_shotlist(doc, **CLEAN_KW)
    result = lint_shotlist_detailed(doc, **CLEAN_KW)
    assert result.errors == [*base_errors, "craft: shot[2] carries 31 words"]
    assert result.warnings == [*base_warnings, "craft: static share 60% over 40%"]
    assert [f.rule_id for f in result.findings] == ["craft.static_share", "craft.words_per_shot"]
    assert result.not_checked == ["craft"]
    assert fake_hook == [{"doc": doc, "require_craft": False}]


def test_present_hook_receives_require_craft(fake_hook) -> None:
    lint_shotlist_detailed(_doc("clean.shots.json"), require_craft=True, **CLEAN_KW)
    assert fake_hook[-1]["require_craft"] is True


def test_unknown_severity_fails_closed_as_error(monkeypatch, fake_hook) -> None:
    sys.modules[HOOK].craft_findings = lambda doc, *, require_craft: (
        [Finding("craft.x", "fatal", "craft: odd severity", None)],  # type: ignore[arg-type]
        [],
    )
    result = lint_shotlist_detailed(_doc("clean.shots.json"), **CLEAN_KW)
    assert "craft: odd severity" in result.errors


def test_a_broken_import_inside_the_hook_is_not_swallowed(monkeypatch, tmp_path) -> None:
    """Only the hook's OWN absence means "public cut"; a missing dependency inside it surfaces."""
    (tmp_path / "hook.py").write_text("import gtm_tripwire_missing_dependency\n")
    pkg = types.ModuleType("gtm_core.video_craft")
    pkg.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, "gtm_core.video_craft", pkg)
    monkeypatch.delitem(sys.modules, HOOK, raising=False)
    with pytest.raises(ModuleNotFoundError, match="gtm_tripwire_missing_dependency"):
        lint_shotlist_detailed(_doc("clean.shots.json"))


def test_lint_shotlist_still_returns_a_bare_tuple() -> None:
    out = sl.lint_shotlist(_doc("clean.shots.json"), **CLEAN_KW)
    assert out == ([], [])


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────


def _cli(monkeypatch, capsys, args: list[str]) -> tuple[int, str]:
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.chdir(CORPUS)
    code = sl.main(args)
    return code, capsys.readouterr().out


_HAS_VIDEO_FINISH = importlib.util.find_spec("gtm_core.video_finish") is not None


def _public_cut_stdout(golden: str) -> str:
    """The committed golden is the PRIVATE cut (video_craft present). The public cut prints the
    same payload minus the one key only the craft hook adds and the measured-VO duration findings only
    video_finish's mux ceilings can produce."""
    lines = golden.splitlines(keepends=True)
    out, skipping = [], False
    for line in lines:
        if not _HAS_VIDEO_FINISH and " against a measured " in line:
            continue
        if line.startswith('  "not_checked": ['):
            skipping = True
            continue
        if skipping:
            if line.startswith("  ],"):
                skipping = False
            continue
        out.append(line)
    return "".join(out)


def _public_cut_help(golden: str) -> str:
    """The private-cut help minus the --require-craft usage token and its option entry."""
    out = []
    lines = golden.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "[--require-craft]":
            i += 1
            continue
        if line.lstrip().startswith("--require-craft"):
            i += 1
            while (
                i < len(lines)
                and lines[i].startswith(" " * 24)
                and not lines[i].lstrip().startswith("-")
            ):
                i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)


@pytest.mark.parametrize("case", support.CASES["shots_lint"], ids=lambda c: c.name)
def test_cli_absent_hook_matches_the_goldens(no_hook, monkeypatch, capsys, case) -> None:
    code, out = _cli(monkeypatch, capsys, list(case.args))
    assert code == case.exit_code
    assert out == _public_cut_stdout(support.stdout_golden(case).read_text(encoding="utf-8"))


def test_help_absent_hook_matches_the_golden(no_hook, monkeypatch, capsys) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    with pytest.raises(SystemExit) as exc:
        sl.main(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == _public_cut_help(
        support.help_golden("shots_lint").read_text(encoding="utf-8")
    )


def test_cli_absent_hook_refuses_require_craft(no_hook, monkeypatch, capsys) -> None:
    monkeypatch.chdir(CORPUS)
    with pytest.raises(SystemExit) as exc:
        sl.main(["clean.shots.json", "--require-craft"])
    assert exc.value.code == 2
    assert "--require-craft" in capsys.readouterr().err


def test_cli_present_hook_reports_not_checked(fake_hook, monkeypatch, capsys) -> None:
    code, out = _cli(monkeypatch, capsys, ["clean.shots.json", *_kw_args()])
    payload = json.loads(out)
    assert list(payload)[:5] == ["path", "ok", "errors", "warnings", "not_checked"]
    assert payload["not_checked"] == ["craft"]
    assert payload["errors"] == ["craft: shot[2] carries 31 words"]
    assert payload["warnings"] == ["craft: static share 60% over 40%"]
    assert code == 1 and payload["ok"] is False
    assert fake_hook[-1]["require_craft"] is False


def test_cli_present_hook_passes_require_craft(fake_hook, monkeypatch, capsys) -> None:
    _, out = _cli(monkeypatch, capsys, ["clean.shots.json", "--require-craft", *_kw_args()])
    assert fake_hook[-1]["require_craft"] is True
    assert json.loads(out)["not_checked"] == []


def test_help_present_hook_documents_require_craft(fake_hook, monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit):
        sl.main(["--help"])
    assert "--require-craft" in capsys.readouterr().out


def _kw_args() -> list[str]:
    return ["--voice-grade", "professional", "--voice-id", "voice-1"]


# ── production.screen_ui.scene against the registry ─────────────────────────────────────────


def _scene_doc(scene: str) -> dict:
    doc = _doc("clean.shots.json")
    doc["shots"][1]["production"] = {"screen_ui": {"scene": scene, "headline": "x"}}
    return doc


@pytest.mark.parametrize("scene", ["kinetic-type", "request-inspector", "mc-gate", "gate"])
def test_registered_scene_passes(scene: str) -> None:
    assert sl.lint_shotlist(_scene_doc(scene), **CLEAN_KW) == ([], [])


@pytest.mark.parametrize(
    "scene", ["no-such-scene", "Kinetic-Type", " kinetic-type", "kinetic-type "]
)
def test_unregistered_scene_is_an_error_naming_shot_and_scene(scene: str) -> None:
    errors, _ = sl.lint_shotlist(_scene_doc(scene), **CLEAN_KW)
    assert errors == [f"shot[2].production.screen_ui.scene {scene!r} is not a registered scene"]


def test_scene_check_is_skipped_without_screen_ui(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "gtm_core.screen_ui.registry", None)
    assert sl.lint_shotlist(_scene_doc("no-such-scene"), **CLEAN_KW) == ([], [])


# ── schemas/shots.schema.json: the optional per-shot `asset` block ──────────────────────────


@pytest.mark.parametrize(
    ("asset", "ok"),
    [
        ({"class": "diagram", "must_show": "three boxes and the arrow between them"}, True),
        ({"class": "live_action"}, True),
        ({"class": "bogus"}, False),
        ({"class": "Diagram"}, False),
        ({"must_show": "x"}, False),
        ({"class": "diagram", "engine": "html"}, False),
        ("diagram", False),
    ],
)
def test_asset_block_schema(asset, ok: bool) -> None:
    from gtm_core.shots_lint.schema import schema_errors

    doc = _doc("clean.shots.json")
    doc["shots"][0]["asset"] = asset
    assert (schema_errors(doc) == []) is ok
