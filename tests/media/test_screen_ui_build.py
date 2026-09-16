"""``gtm_core.screen_ui.build`` — the rebuild driver (plan change #6, glittery-sleeping-yao).

Real ffmpeg, real Pillow frames, real fixture font (matplotlib's bundled DejaVuSans — a declared
dependency, no tenant identity). Tiny fixtures: 2fps, ~1s screen_ui clips, a small lavfi
declared-file clip — this directory requires ffmpeg on PATH (see conftest.py).

The standard fixture is a three-shot film: ``hero`` (screen_ui hero-reveal, its own ``actions``
file so it can be mutated), ``still2`` (screen_ui still-push — a static image and no text draw,
so it is the "other shot" a selective re-render must leave untouched), and ``clip`` (a declared
``file``, never rendered). ``still2`` can carry a declared ``dissolve`` transition into it, so the
"sidecars survive a master rename" case (#4) has a real transition to prove non-null.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib
import pytest
from PIL import Image, ImageDraw

from gtm_core.screen_ui import build

_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)
pytestmark = pytest.mark.skipif(_FONT is None, reason="no bundled TTF fixture found")

_RATIO = "1:1"
_FPS = 2
_HERO_DURATION_S = 1.0
_CLIP_DURATION_S = 1.0


def _kit() -> dict:
    return {
        "palette": {
            "canvas": "#0B1A2E",
            "surface": "#1A2B3C",
            "ink": "#FFFFFF",
            "accent": "#00C6C6",
        },
        "typography": {"font_files": {"caption": str(_FONT)}},
    }


def _passing_still(path: Path) -> Path:
    """A small centred mark on a flat background — with the fixture's ``layout: scale=0.5``
    (see :func:`_hero_actions`) this is known to CLEAR the caption band (verified against
    ``fit_layout.report`` directly while writing this fixture)."""
    img = Image.new("RGB", (300, 300), (20, 20, 24))
    ImageDraw.Draw(img).rectangle((80, 80, 220, 220), fill=(90, 140, 200))
    img.save(path)
    return path


def _clipping_still(path: Path) -> Path:
    """Full-bleed content (rows 8.5%-83.2% of height) at the scene's default layout
    (scale=1.0, centred) — the regression shape ``test_screen_ui_fit_layout.py`` already proves
    clips the caption band; reused here as the KNOWN-BAD fixture for the fit-overlap refusal."""
    w, h = 1080, 1080
    img = Image.new("RGB", (w, h), (11, 26, 46))
    top, bottom = round(h * 0.085), round(h * 0.832)
    ImageDraw.Draw(img).rectangle(
        (round(w * 0.1), top, round(w * 0.9), bottom), fill=(200, 200, 200)
    )
    img.save(path)
    return path


def _hero_actions(path: Path, *, end_s: float = 0.1) -> Path:
    doc = {
        "version": 1,
        "actions": [{"type": "enter", "start_s": 0.0, "end_s": end_s}],
        "layout": {"scale": 0.5, "center_x_frac": 0.5, "center_y_frac": 0.5},
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _lavfi_clip(path: Path, *, duration: float, size: str = "1080x1080") -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=24",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],  # fmt: skip
        check=True, capture_output=True,
    )  # fmt: skip
    return path


def _sine_audio(path: Path, *, duration: float) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
            "-c:a", "pcm_s16le", str(path),
        ],  # fmt: skip
        check=True, capture_output=True,
    )  # fmt: skip
    return path


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.shots_dir = tmp_path / "shots"
        self.shots_dir.mkdir()
        self.hero_still = _passing_still(self.shots_dir / "hero-still.png")
        self.hero_actions = _hero_actions(self.shots_dir / "hero-actions.json")
        self.clip_file = _lavfi_clip(self.shots_dir / "clip.mp4", duration=_CLIP_DURATION_S)
        self.shots_path = self.shots_dir / "film.shots.json"
        self.kit_path = tmp_path / "kit.json"
        self.kit_path.write_text(json.dumps(_kit()), encoding="utf-8")
        self.spec_path = tmp_path / "spec.json"
        self.spec_path.write_text(json.dumps({}), encoding="utf-8")
        # Three ~1.0s segments hard-cut -> a ~3.0s silent master; comfortably inside
        # MUX_TRUNCATE_MAX_S (0.35s) of that with margin for encoder jitter, and still inside it
        # for the transitions test (a 0.2s dissolve trims ~0.2s off the same total).
        self.audio_path = _sine_audio(tmp_path / "audio.wav", duration=2.7)
        self.work_dir = tmp_path / "work"
        self.out_dir = tmp_path / "out"
        self._write_shots()

    def _write_shots(self, *, transition: dict | None = None) -> None:
        # still-push: no text draw at all (unlike title-claim, whose default copy overflows the
        # 1:1 safe box at this fixture's font), so it needs only an image — a second screen_ui
        # scene that is never fit-checked, and has no input files a "which shot re-rendered" test
        # could confuse with hero's.
        still2_shot: dict = {
            "id": "still2",
            "duration_s": _CLIP_DURATION_S,
            "production": {"screen_ui": {"scene": "still-push", "image": self.hero_still.name}},
        }
        if transition is not None:
            still2_shot["production"]["transition_in"] = transition
        doc = {
            "shots": [
                {
                    "id": "hero",
                    "duration_s": _HERO_DURATION_S,
                    "production": {
                        "screen_ui": {
                            "scene": "hero-reveal",
                            "image": self.hero_still.name,
                            "actions": self.hero_actions.name,
                        }
                    },
                },
                still2_shot,
                {"id": "clip", "file": self.clip_file.name},
            ]
        }
        self.shots_path.write_text(json.dumps(doc), encoding="utf-8")

    def argv(
        self, *, force: bool = False, dry_run: bool = False, content_root: bool = True
    ) -> list[str]:
        argv = [
            "--shots", str(self.shots_path),
            "--kit-json", str(self.kit_path),
            "--ratio", _RATIO,
            "--fps", str(_FPS),
            "--audio", str(self.audio_path),
            "--profile", "acme",
            "--slug", "film",
            "--work-dir", str(self.work_dir),
            "--out-dir", str(self.out_dir),
            "--spec", str(self.spec_path),
        ]  # fmt: skip
        if content_root:
            argv += ["--content-root", str(self.root)]
        if force:
            argv.append("--force")
        if dry_run:
            argv.append("--dry-run")
        return argv

    @property
    def finish_manifest_path(self) -> Path:
        # `_RATIO` is fixed ("1:1"), so its ratio-slug is fixed too — a direct path avoids the
        # glob "finish-*.json" also matching the sibling "finish-spec-1x1.json".
        return self.out_dir / "finish-1x1.json"

    @property
    def final_asset_path(self) -> Path:
        return self.out_dir / "film-1x1-final.mp4"


@pytest.fixture
def fixture(tmp_path) -> Fixture:
    return Fixture(tmp_path)


def _tree_digest(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _spy(monkeypatch, target_module, name):
    """Records ``(args, kwargs, return_value)`` per call while delegating to the real
    function — a wrapper, never a mock, so the driver's actual behaviour is unchanged."""
    calls: list = []
    real = getattr(target_module, name)

    def wrapper(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append((args, kwargs, result))
        return result

    monkeypatch.setattr(target_module, name, wrapper)
    return calls


def _render_calls_by_scene(calls) -> list[str]:
    return [args[0] if args else kwargs["scene"] for args, kwargs, _result in calls]


# ── 1: first build renders everything, exit mirrors lint ─────────────────────────────────────


def test_first_build_renders_all_shots_and_exit_mirrors_lint(fixture, monkeypatch):
    render_calls = _spy(monkeypatch, build, "render_scene")
    code = build.main(fixture.argv())
    assert _render_calls_by_scene(render_calls) == ["hero-reveal", "still-push"]

    finals = list(fixture.work_dir.glob("master-*.mp4"))
    assert len(finals) == 1
    assert fixture.final_asset_path.is_file()
    assert fixture.finish_manifest_path.is_file()

    from gtm_core.video_lint import cli as video_lint_cli

    expected = video_lint_cli.main(
        [
            str(fixture.final_asset_path),
            "--ratio",
            _RATIO,
            "--manifest",
            str(fixture.finish_manifest_path),
        ]
    )
    # video_lint was just invoked a second time above (idempotent read-only measurement), so its
    # own exit code is the ground truth to compare the driver's against.
    assert code == expected


# ── 2: second build, no change — 0 renders, master reused, run skips ─────────────────────────


def test_second_build_with_no_change_renders_nothing_and_skips(fixture, monkeypatch):
    build.main(fixture.argv())
    final_before = list(fixture.work_dir.glob("master-*.mp4"))[0]
    mtime_before = final_before.stat().st_mtime_ns

    render_calls = _spy(monkeypatch, build, "render_scene")
    execute_calls = _spy(monkeypatch, build, "execute")
    build.main(fixture.argv())

    assert render_calls == []
    final_after = list(fixture.work_dir.glob("master-*.mp4"))[0]
    assert final_after == final_before
    assert final_after.stat().st_mtime_ns == mtime_before
    assert len(execute_calls) == 1
    _args, _kwargs, finish_result = execute_calls[0]
    assert finish_result.skipped is True


# ── 3: change one actions file — only that shot re-renders ───────────────────────────────────


def test_changing_one_actions_file_rerenders_only_that_shot(fixture, monkeypatch):
    build.main(fixture.argv())
    first_master = list(fixture.work_dir.glob("master-*.mp4"))[0]
    title_record = json.loads(
        (fixture.work_dir / "frames" / "still2" / "screen-ui-build.json").read_text()
    )

    _hero_actions(fixture.hero_actions, end_s=0.15)  # same file name, new content -> new digest

    render_calls = _spy(monkeypatch, build, "render_scene")
    build.main(fixture.argv())

    assert _render_calls_by_scene(render_calls) == ["hero-reveal"], (
        "only the shot whose input changed should re-render"
    )
    title_record_after = json.loads(
        (fixture.work_dir / "frames" / "still2" / "screen-ui-build.json").read_text()
    )
    assert title_record_after == title_record, "the untouched shot's record must be byte-identical"

    new_masters = [p for p in fixture.work_dir.glob("master-*.mp4") if p != first_master]
    assert len(new_masters) == 1, "the master's inputs changed, so must its name"


# ── 4: sidecars survive the master rename, non-null transitions ──────────────────────────────


def test_master_rename_carries_sidecars_with_real_transitions(fixture):
    fixture._write_shots(transition={"kind": "dissolve", "duration_s": 0.2})
    code = build.main(fixture.argv())
    assert code in (0, 1), "should reach the finish stage, not refuse earlier"

    manifest = json.loads(fixture.finish_manifest_path.read_text())
    assert manifest["transitions"], (
        "the dissolve must ride into the finish manifest, not just exist as a file"
    )
    assert manifest["transitions"][0]["kind"] == "dissolve"
    assert manifest["transitions"][0]["duration_s"] == 0.2


# ── 5: stale-final guard — different audio, new master name, not skipped ─────────────────────


def test_different_audio_forces_a_new_master_name(fixture):
    build.main(fixture.argv())
    first_master = list(fixture.work_dir.glob("master-*.mp4"))[0]

    fixture.audio_path = _sine_audio(fixture.root / "audio2.wav", duration=2.72)
    code = build.main(fixture.argv())
    assert code in (0, 1)

    seconds_master = [p for p in fixture.work_dir.glob("master-*.mp4") if p != first_master]
    assert len(seconds_master) == 1, "a new master must be produced, the old one left in place"


# ── 6: --force renders every shot even with matching fingerprints ────────────────────────────


def test_force_renders_every_shot_regardless_of_fingerprint(fixture, monkeypatch):
    build.main(fixture.argv())
    render_calls = _spy(monkeypatch, build, "render_scene")
    build.main(fixture.argv(force=True))
    assert sorted(_render_calls_by_scene(render_calls)) == ["hero-reveal", "still-push"]


# ── 7: --dry-run leaves the tree untouched, exits 0 ───────────────────────────────────────────


def test_dry_run_leaves_the_tree_unchanged(fixture, capsys):
    before = _tree_digest(fixture.work_dir) | _tree_digest(fixture.out_dir)
    code = build.main(fixture.argv(dry_run=True))
    assert code == 0
    after = _tree_digest(fixture.work_dir) | _tree_digest(fixture.out_dir)
    assert after == before
    assert not fixture.work_dir.exists()
    assert not fixture.out_dir.exists()

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    by_id = {row["id"]: row for row in lines if "id" in row}
    assert by_id["hero"]["action"] == "render"
    assert by_id["still2"]["action"] == "render"
    assert by_id["clip"]["action"] == "file"
    final_line = next(row for row in lines if "final_master" in row)
    assert final_line["reuse"] is False


def test_dry_run_after_a_real_build_reports_reuse(fixture, capsys):
    build.main(fixture.argv())
    capsys.readouterr()
    code = build.main(fixture.argv(dry_run=True))
    assert code == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    by_id = {row["id"]: row for row in lines if "id" in row}
    assert by_id["hero"]["action"] == "reuse"
    assert by_id["still2"]["action"] == "reuse"
    final_line = next(row for row in lines if "final_master" in row)
    assert final_line["reuse"] is True


# ── 8: mixed shot list (file + screen_ui) passes through ─────────────────────────────────────


def test_mixed_shot_list_passes_through_to_the_stitched_master(fixture):
    code = build.main(fixture.argv())
    assert code in (0, 1)
    finals = list(fixture.work_dir.glob("master-*.mp4"))
    assert len(finals) == 1 and finals[0].stat().st_size > 0


def test_a_shot_naming_neither_screen_ui_nor_file_is_refused_before_any_render(
    fixture, monkeypatch
):
    doc = json.loads(fixture.shots_path.read_text())
    doc["shots"].append({"id": "orphan"})
    fixture.shots_path.write_text(json.dumps(doc), encoding="utf-8")

    render_calls = _spy(monkeypatch, build, "render_scene")
    code = build.main(fixture.argv())
    assert code == 2
    assert render_calls == []


@pytest.mark.parametrize(
    "bad_id",
    [
        "/etc/cron.d/x",  # absolute — Path("work") / "/etc/cron.d/x" collapses to /etc/cron.d/x
        "../../escaped",  # relative traversal above --work-dir
        "..",
    ],
)
def test_a_shot_id_that_could_escape_work_dir_is_refused_before_any_render(
    fixture, monkeypatch, bad_id
):
    """``shot.id`` becomes a bare path segment under ``--work-dir`` (``frames/<id>``,
    ``clips/<id>.mp4``) — a shots.json is untrusted content (§R5), so an id shaped like an
    absolute path or a `..` climb must be refused before it is ever joined onto a Path, not
    trusted because the top-level ``--work-dir``/``--out-dir`` flags were already confined."""
    doc = json.loads(fixture.shots_path.read_text())
    doc["shots"][0]["id"] = bad_id
    fixture.shots_path.write_text(json.dumps(doc), encoding="utf-8")

    render_calls = _spy(monkeypatch, build, "render_scene")
    code = build.main(fixture.argv())
    assert code == 2
    assert render_calls == []
    assert not Path("/etc/cron.d/x").exists(), "must never actually write outside work_dir"
    assert not any(fixture.work_dir.rglob("*")), "no write of any kind before a validation refusal"


# ── 10: fit overlap → exit 2, no frames anywhere under work-dir ──────────────────────────────


def test_fit_overlap_refuses_before_any_render(fixture, monkeypatch):
    clipping = _clipping_still(fixture.shots_dir / "clip-still.png")
    # hero-reveal still needs *an* actions file; one with no "layout" override keeps the scene's
    # default (scale=1.0, centred) — the known-clipping layout the fixture still proves fails.
    bare_actions = fixture.shots_dir / "bare-actions.json"
    bare_actions.write_text(
        json.dumps({"version": 1, "actions": [{"type": "enter", "start_s": 0.0, "end_s": 0.1}]}),
        encoding="utf-8",
    )
    doc = json.loads(fixture.shots_path.read_text())
    doc["shots"][0]["production"]["screen_ui"]["image"] = clipping.name
    doc["shots"][0]["production"]["screen_ui"]["actions"] = bare_actions.name
    fixture.shots_path.write_text(json.dumps(doc), encoding="utf-8")

    render_calls = _spy(monkeypatch, build, "render_scene")
    code = build.main(fixture.argv())
    assert code == 2
    assert render_calls == []
    assert not any(fixture.work_dir.rglob("*.png")), "no frame may exist after a fit refusal"


# ── 11: mux refusal — atempo forced to 1.0 ────────────────────────────────────────────────────


def test_mux_refuses_a_vo_that_would_need_speeding_up(fixture):
    fixture.audio_path = _sine_audio(fixture.root / "long-audio.wav", duration=4.0)
    code = build.main(fixture.argv())
    assert code == 4
    assert not any(fixture.work_dir.glob("master-*.mp4"))


# ── 12: transitions/segment count mismatch → exit 4 ───────────────────────────────────────────


def test_a_transitions_count_mismatch_is_exit_4(fixture, monkeypatch):
    monkeypatch.setattr(
        build, "transitions_from_shots", lambda _doc: [("cut", 0.0), ("cut", 0.0), ("cut", 0.0)]
    )
    code = build.main(fixture.argv())
    assert code == 4


# ── 13: work/out dirs outside the content root → exit 2 ──────────────────────────────────────


def test_dirs_outside_the_content_root_are_refused(fixture, monkeypatch, tmp_path_factory):
    other_root = tmp_path_factory.mktemp("content-root")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(other_root))
    code = build.main(fixture.argv(content_root=False))
    assert code == 2
    assert not fixture.work_dir.exists()
