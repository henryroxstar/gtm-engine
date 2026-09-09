"""screen_ui CLI goldens — every registered scene through the real ``python -m`` entry
(PRD 2026-09-01 §6.2 V2; §6.1 row 2 registry tripwire; §6.1 row 7 CLI drift).

Three layers, each catching what the previous cannot:

1. **Registry equality.** ``set(_SCENES)`` equals an explicit literal — set EQUALITY, not
   non-empty — so a scene whose registration stops running after the split is a NAMED failure.
   Same for ``SAFE_AREAS``. The matrix in ``golden_matrix`` is checked against the same literal.
2. **CLI contract.** Every scene at 16:9 (all render with the fixture face) and at 9:16 (nine
   render, the rest refuse — a refusal is a contract too), ``audit-fit``, and every flag path:
   exit codes and the JSON stdout.
3. **Frame fingerprints.** Each PNG's dimensions, mode and a coarse structural fingerprint
   (16x16 box-filtered grayscale, 4 bits per cell) against ``screen_ui/scene_fingerprints.json``,
   tolerating one quantisation level per cell — the bundled DejaVuSans is byte-identical
   everywhere, but a different Pillow/FreeType may antialias a hair differently. A raw byte hash
   is deliberately NOT committed; same-machine byte identity is ``capture_exact.py``'s job.

Regenerate the fingerprint golden deliberately, in the same PR as an intentional scene change:

    uv run python tests/goldens/test_screen_ui_golden.py --write
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import golden_matrix as gm
import pytest

FINGERPRINTS = Path(__file__).resolve().parent / "screen_ui" / "scene_fingerprints.json"
MISSING = gm.missing_prerequisite()
EXPECTED_SCENES = frozenset(
    {
        "call-ui-bank",
        "call-ui-clinic",
        "call-ui-hotel",
        "call-ui-telco",
        "caller-row-bank",
        "caller-row-clinic",
        "caller-row-hotel",
        "caller-row-telco",
        "checkpoint-flow",
        "class-booking",
        "message-card",
        "record-agent-identity",
        "record-bank",
        "record-clean",
        "record-clinic",
        "record-grid",
        "record-hotel",
        "record-telco",
        "request-inspector",
        "rows-claim",
        "rows-evidence",
        "rows-identity",
        "rows-layers",
        "rows-scope",
        "still-push",
        "title-claim",
        "title-close",
    }
)
EXPECTED_RATIOS = frozenset({"16:9", "1:1", "4:5", "9:16"})
#: scenes whose strings go through ``_fit_or_refuse`` and so appear in an audit-fit report
FITTED_SCENES = frozenset(
    {
        "checkpoint-flow",
        "message-card",
        "record-grid",
        "rows-claim",
        "rows-evidence",
        "rows-identity",
        "rows-layers",
        "rows-scope",
        "title-claim",
        "title-close",
    }
)
DIMS = {"16x9": [1920, 1080], "9x16": [1080, 1920]}
MATRIX = gm.screen_ui_matrix()
BY_NAME = {inv.name: inv for inv in MATRIX}
#: A render is a SCENE invocation. Selecting on ``exit_code == 0`` alone was only ever right
#: because the one non-scene success — ``--help`` — happened to crash; when that was fixed on
#: 2026-09-03 it silently joined this list and was asked to parse help text as JSON.
RENDERS = tuple(inv.name for inv in MATRIX if inv.exit_code == 0 and inv.argv[0] in gm.SCENES)


# ── layer 1: registries ───────────────────────────────────────────────────────────────────


def test_scene_registry_is_exactly_the_expected_set():
    from gtm_core.screen_ui import _SCENES

    assert set(_SCENES) == EXPECTED_SCENES


def test_safe_area_registry_is_exactly_the_expected_set():
    from gtm_core.video_lint import SAFE_AREAS

    assert set(SAFE_AREAS) == EXPECTED_RATIOS


def test_the_matrix_drives_exactly_the_registered_scenes():
    assert set(gm.SCENES) == EXPECTED_SCENES
    for suffix in ("16x9", "9x16"):
        assert {n for n in BY_NAME if n.endswith(f"-{suffix}") and n[3:-5] in EXPECTED_SCENES} == {
            f"su-{scene}-{suffix}" for scene in EXPECTED_SCENES
        }


# ── layers 2 + 3 need the toolchain ───────────────────────────────────────────────────────

needs_toolchain = pytest.mark.skipif(MISSING is not None, reason=str(MISSING))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    if MISSING is not None:
        pytest.skip(str(MISSING))
    root = tmp_path_factory.mktemp("su-golden")
    gm.build_fixtures(root)
    return root, gm.run_matrix(root, MATRIX)


@pytest.fixture(scope="module")
def outcomes(run) -> dict[str, gm.Outcome]:
    return run[1]


@needs_toolchain
@pytest.mark.parametrize("name", [inv.name for inv in MATRIX])
def test_exit_code(outcomes, name):
    got = outcomes[name]
    assert got.exit_code == BY_NAME[name].exit_code, (
        f"{name}: stdout={got.stdout[-400:]!r} stderr={got.stderr[-400:]!r}"
    )


@needs_toolchain
@pytest.mark.parametrize("name", RENDERS)
def test_a_render_reports_its_frame_count_and_timing_source(outcomes, name):
    inv = BY_NAME[name]
    expected = {
        "scene": inv.argv[0],
        "frames": 2,  # fps 2 x 1.0 s; also the floor _frame_count enforces
        "out_dir": inv.artifact_dir,
        "fps": 2,
        "duration_s": 1.0,
    }
    if inv.argv[0] == "checkpoint-flow":
        expected["timing_source"] = (
            "timing-json:json/timing.json"
            if "--timing-json" in inv.argv
            else "default-literals (audio/vo/h15.wav, 26.59s)"
        )
    assert outcomes[name].json() == expected
    pngs = [k for k, v in outcomes[name].artifacts.items() if v["kind"] == "png"]
    assert len(pngs) == 2, pngs


@needs_toolchain
@pytest.mark.parametrize("scene", sorted(EXPECTED_SCENES - gm.SCENES_RENDERING_AT_9X16))
def test_a_9x16_refusal_is_an_error_json_on_stdout_and_no_frames(outcomes, scene):
    got = outcomes[f"su-{scene}-9x16"]
    doc = got.json()
    assert doc["scene"] == scene and doc["error"]
    assert got.artifacts == {} and got.stderr == ""


@needs_toolchain
def test_frames_match_the_committed_fingerprints(outcomes):
    assert FINGERPRINTS.is_file(), f"missing golden {FINGERPRINTS.name} — see the module docstring"
    golden = json.loads(FINGERPRINTS.read_text(encoding="utf-8"))
    live = _fingerprints(outcomes)
    assert sorted(live) == sorted(golden), "the set of rendered frames changed"
    problems = []
    for key, row in golden.items():
        got = live[key]
        if (got["size"], got["mode"]) != (row["size"], row["mode"]):
            problems.append(f"{key}: {got['size']}/{got['mode']} vs {row['size']}/{row['mode']}")
            continue
        worst, cells = gm.fingerprint_distance(row["coarse"], got["coarse"])
        if worst > 1:
            problems.append(f"{key}: {cells} cells differ, worst by {worst} levels")
    assert not problems, "\n".join(problems) + (
        "\nIf the scene change is intentional, regenerate in the same PR:\n"
        "  uv run python tests/goldens/test_screen_ui_golden.py --write"
    )


@needs_toolchain
def test_every_frame_is_the_ratios_pixel_size_and_only_call_ui_keeps_alpha(outcomes):
    for name in RENDERS:
        for key, png in outcomes[name].artifacts.items():
            assert png["size"] == DIMS[name.rsplit("-", 1)[-1] if name[-4:] in DIMS else "16x9"], (
                key
            )
            # Overlay scenes — composited onto footage by `video_finish.overlay_frames` — keep
            # their alpha: the `call-ui-*` family and `message-card` (masked to its own card).
            scene = BY_NAME[name].argv[0]
            assert png["mode"] == (
                "RGBA" if scene.startswith("call-ui-") or scene == "message-card" else "RGB"
            )


@needs_toolchain
def test_audit_fit(outcomes):
    wide = outcomes["su-audit-fit-16x9"].json()
    assert (wide["ratio"], wide["strings"], wide["below_min_tolerance"]) == ("16:9", 40, 0)
    assert len(wide["rows"]) == 40 and {r["scene"] for r in wide["rows"]} == FITTED_SCENES
    tolerances = [r["tolerance"] for r in wide["rows"]]
    assert tolerances == sorted(tolerances), "the fragile strings must come first"
    assert all(t > 1.0 for t in tolerances), "at 16:9 every string survives the fixture face"
    assert set(wide["rows"][0]) == {
        "scene", "ratio", "tolerance", "where", "text", "box_w", "resolved_px", "floor_px",
        "at_floor", "width_at_floor_px",
    }  # fmt: skip
    assert outcomes["su-audit-fit-16x9"].artifacts == {}  # throwaway frames go to a temp dir
    tall = outcomes["su-audit-fit-9x16-min-tolerance"]
    doc = tall.json()
    assert doc["ratio"] == "9:16" and doc["strings"] >= 1
    # `message-card` is a phone-shaped composition, so its strings survive 9:16 with room to
    # spare; every OTHER fitted scene's strings are fragile there. It is those fragile rows that
    # make this invocation exit 1 — so pin which side of the threshold each scene lands on, not
    # the pre-message-card claim that no survivor is comfortable.
    argv = BY_NAME["su-audit-fit-9x16-min-tolerance"].argv
    min_tolerance = float(argv[argv.index("--min-tolerance") + 1])
    roomy = {r["scene"] for r in doc["rows"] if r["tolerance"] >= min_tolerance}
    assert roomy == {"message-card"}
    assert doc["below_min_tolerance"] == sum(r["scene"] != "message-card" for r in doc["rows"])
    assert doc["below_min_tolerance"] >= 1  # the exit-1 path still fires
    assert {r["scene"] for r in doc["rows"]} <= FITTED_SCENES


@needs_toolchain
def test_flag_paths(outcomes):
    def png_hashes(name: str) -> list[str]:
        return [v["sha256"] for _, v in sorted(outcomes[name].artifacts.items())]

    # --logo on a scene outside _SCENE_EXTRAS is silently DROPPED (the help text says
    # "refused") — the frames are byte-identical to the plain render. Pinned as-is.
    assert png_hashes("su-record-bank-logo-ignored") == png_hashes("su-record-bank-16x9")
    # on title-claim the mark is composited (fades in, so frame 0 still matches the plain render)
    plain, logo = png_hashes("su-title-claim-16x9"), png_hashes("su-title-claim-logo")
    assert plain[0] == logo[0] and plain[1] != logo[1]
    # a timing map equal to the literals draws the same frames; only the provenance differs
    assert png_hashes("su-checkpoint-flow-timing-json") == png_hashes("su-checkpoint-flow-16x9")
    assert png_hashes("su-still-push-crop") != png_hashes("su-still-push-16x9")
    assert outcomes["su-checkpoint-flow-words-mismatch"].json() == {
        "scene": "checkpoint-flow",
        "error": "json/words-mismatch.json was measured against a 9.000s VO but this scene is "
        "being rendered for 1.000s — that is a different cut. Re-ingest the timings for the VO "
        "you are rendering.",
    }
    assert outcomes["su-checkpoint-flow-both-timings"].json() == {
        "scene": "checkpoint-flow",
        "error": "pass --words-json or --timing-json, not both",
    }
    assert outcomes["su-still-push-bad-crop"].json() == {
        "scene": "still-push",
        "error": "--crop-frac must be 4 numbers",
    }
    assert outcomes["su-still-push-no-image"].json() == {
        "scene": "still-push",
        "error": "still-push needs --image: the asset to animate",
    }
    assert outcomes["su-missing-duration-and-out-dir"].json() == {
        "scene": "title-claim",
        "error": "--duration-s and --out-dir required",
    }
    nofont = outcomes["su-kit-without-font"].json()
    assert nofont["scene"] == "title-claim"
    assert nofont["error"].startswith("BRAND.toml has no [typography.font_files].caption entry")


@needs_toolchain
def test_usage_errors_go_to_stderr_with_exit_2(outcomes):
    for name, needle in (
        ("su-no-args", "error: the following arguments are required: scene, --kit-json, --ratio"),
        ("su-bad-scene", "error: argument scene: invalid choice: 'bogus'"),
        ("su-bad-ratio", "error: argument --ratio: invalid choice: '3:2'"),
    ):
        got = outcomes[name]
        assert got.stdout == "" and got.stderr.startswith("usage: gtm_core.screen_ui "), name
        assert needle in got.stderr, name


# ── golden maintenance ────────────────────────────────────────────────────────────────────


def _fingerprints(outcomes: dict[str, gm.Outcome]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for name in RENDERS:
        for key, png in outcomes[name].artifacts.items():
            if png["kind"] == "png":
                rows[key] = {"size": png["size"], "mode": png["mode"], "coarse": png["coarse"]}
    return rows


def write_golden() -> None:
    import tempfile

    if MISSING is not None:
        raise SystemExit(f"cannot render: {MISSING}")
    with tempfile.TemporaryDirectory(prefix="su-golden-") as tmp:
        root = Path(tmp)
        gm.build_fixtures(root)
        rows = _fingerprints(gm.run_matrix(root, tuple(BY_NAME[n] for n in RENDERS)))
    FINGERPRINTS.parent.mkdir(parents=True, exist_ok=True)
    FINGERPRINTS.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FINGERPRINTS} ({len(rows)} frames)")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_golden()
    else:
        print(__doc__)
