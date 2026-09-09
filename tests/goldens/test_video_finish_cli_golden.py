"""video_finish CLI goldens — representative invocations through the real ``python -m`` entry
(PRD 2026-09-01 §6.2 V2, §6.1 row 7).

tests/media already pins the LIBRARY (stitch(), mux(), split() … against real ffmpeg). This
module pins the CLI CONTRACT the skills drive: the exit code of every failure class, the text
and ``--json`` faces of stdout, the write-boundary and tenant-segment refusals, and the STRUCTURE
of every produced artifact. Encoded media is never byte-hashed — ffmpeg output is not
bit-reproducible — so ffprobe layout, codecs, dimensions, rates and durations are asserted with
tolerances, next to the numbers the module itself computes (mux durations/atempo, the SFX
measurement, split cut points, the plan id). Same-machine byte determinism is the job of
``capture_exact.py``; the last test re-runs a slice of the matrix to prove that the stdout and
the text/PNG/JPEG artifacts it hashes really are deterministic.

Every subcommand is driven; nothing here needs a provider. Not exercised, because both need
tenant material: real footage from a provider render beside a local encode (the case
``--normalize`` exists for) and a ``run`` whose brand kit configures a Reap caption preset.
"""

from __future__ import annotations

import re

import golden_matrix as gm
import pytest

MISSING = gm.missing_prerequisite()
pytestmark = pytest.mark.skipif(MISSING is not None, reason=str(MISSING))

MATRIX = gm.video_finish_matrix()
BY_NAME = {inv.name: inv for inv in MATRIX}
R = gm.ROOT_TOKEN
OUT = gm.OUT
REFUSE_ROOT = "refusing to write outside the resolved content root"
MP4_FORMAT = "mov,mp4,m4a,3gp,3g2,mj2"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("vf-golden")
    gm.build_fixtures(root)
    return root, gm.run_matrix(root, MATRIX)


@pytest.fixture(scope="module")
def outcomes(run) -> dict[str, gm.Outcome]:
    return run[1]


def _video(fp: dict) -> dict:
    return next(s for s in fp["streams"] if s["codec_type"] == "video")


def _audio(fp: dict) -> dict:
    return next(s for s in fp["streams"] if s["codec_type"] == "audio")


def _assert_h264(fp: dict, *, w: int, h: int, duration: float, tol: float = 0.1, fps=None):
    assert fp["format"]["format_name"] == MP4_FORMAT
    v = _video(fp)
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"]) == ("h264", w, h, "yuv420p")
    if fps is not None:
        assert v["r_frame_rate"] == fps
    assert v["duration"] == pytest.approx(duration, abs=tol)


def _assert_aac(fp: dict, *, rate: str = "48000", duration=None, tol: float = 0.1):
    a = _audio(fp)
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", rate, 2)
    assert a["channel_layout"] == "stereo"
    if duration is not None:
        assert a["duration"] == pytest.approx(duration, abs=tol)


# ── the contract every invocation shares ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", [inv.name for inv in MATRIX])
def test_exit_code(outcomes, name):
    got = outcomes[name]
    assert got.exit_code == BY_NAME[name].exit_code, (
        f"{name}: stdout={got.stdout[-400:]!r} stderr={got.stderr[-400:]!r}"
    )


def test_matrix_drives_every_subcommand():
    driven = {inv.argv[0] for inv in MATRIX if inv.argv and not inv.argv[0].startswith("--")}
    assert driven == {
        "run",
        "predictor-trim",
        "frames-to-video",
        "mux",
        "split",
        "stitch",
        "burn-captions",
        "find-transient",
        "mix-sfx",
        "room-tone",
        "contact-sheet",
        "frobnicate",  # the unknown-verb refusal
    }


def test_argparse_refusals_name_the_choice(outcomes):
    assert "error: the following arguments are required: cmd" in outcomes["vf-no-subcommand"].stderr
    assert "invalid choice: 'frobnicate'" in outcomes["vf-unknown-subcommand"].stderr
    for name in ("vf-run-bad-ratio", "vf-stitch-bad-ratio"):
        # The BEHAVIOUR, not argparse's punctuation: CPython quotes the choices differently
        # between patch releases, and pinning that made this pass locally and fail on CI.
        err = outcomes[name].stderr
        assert "argument --ratio: invalid choice: '3:2'" in err
        assert all(ratio in err for ratio in ("16:9", "1:1", "4:5", "9:16")), err
    assert all(outcomes[n].stdout == "" for n in ("vf-no-subcommand", "vf-run-bad-ratio"))


def test_write_boundary_refusals(outcomes):
    """Every confined verb exits 2 on an ``--out``/``--out-dir`` outside ``--content-root``."""
    for name in (
        "vf-frames-to-video-outside-root",
        "vf-mux-outside-root",
        "vf-split-outside-root",
        "vf-burn-captions-outside-root",
        "vf-room-tone-outside-root",
    ):
        got = outcomes[name]
        assert REFUSE_ROOT in got.stderr and f"(root: {R}/content)" in got.stderr, name
        assert got.artifacts == {}, name


# ── run ───────────────────────────────────────────────────────────────────────────────────


def test_run_dry_run_prints_the_plan_and_writes_nothing(outcomes):
    got = outcomes["vf-run-dry-run"]
    plan = got.json()
    assert got.artifacts == {}
    assert [s["name"] for s in plan["stages"]] == [
        "normalize",
        "upscale",
        "grade",
        "captions",
        "loudnorm",
        "encode",
    ]
    assert plan["plan_id"] == "46935c51d4f5c662"  # sha256 of the canonical plan — relative source
    assert plan["source"] == "src/clip-a.mp4" and plan["ratio"] == "9:16"
    assert plan["census"] == {"grade": 1, "overlay": 3, "loudnorm": 1, "concat": 0}
    assert plan["stages"][1]["args"] == {"width": 1080, "height": 1920}
    assert plan["stages"][3]["args"]["num_screens"] == 3
    assert plan["stages"][5]["args"] == {"crf": 20, "preset": "medium", "gop": 48, "threads": 4}


def test_run_executes_then_short_circuits_on_the_same_plan(outcomes):
    first, again = outcomes["vf-run-execute"], outcomes["vf-run-execute-again"]
    out = f"{OUT}/vf-run-execute/asset-9x16-final.mp4"
    assert first.json() == {"out_path": out, "skipped": False}
    assert again.json() == {"out_path": out, "skipped": True}
    assert again.artifacts == first.artifacts  # the skip left every byte alone
    text = outcomes["vf-run-text"]
    assert (
        text.stdout
        == f"video-finish: wrote {OUT}/vf-run-text/asset-9x16-final.mp4 (skipped=False)\n"
    )


def test_run_final_asset_and_sidecars(outcomes):
    got = outcomes["vf-run-execute"]
    final = got.artifact("asset-9x16-final.mp4")
    _assert_h264(final, w=1080, h=1920, duration=3.0, fps="24/1")
    # loudnorm resamples to 192 kHz internally and nothing pins the rate back down, so the
    # final carries the AAC encoder's ceiling — pinned as-is, it is what ships today.
    _assert_aac(final, rate="96000", duration=3.1, tol=0.2)
    assert got.artifact("finish-9x16.json")["kind"] == "text"
    assert got.artifact("captions.json")["kind"] == "text"
    for i in range(3):
        png = got.artifact(f"_work/captions/caption_{i:02d}.png")
        assert (png["size"], png["mode"]) == ([1080, 1920], "RGBA")
    assert len(got.artifacts) == 6


def test_run_refuses_an_unsafe_profile_but_tracebacks_on_a_missing_kit(outcomes):
    """The traversal guard reaches the CLI as a clean exit 2; an unknown profile resolves to an
    EMPTY kit and dies deep in captions.load_face with a traceback (exit 1) — pinned as-is."""
    assert outcomes["vf-run-unsafe-profile"].stderr == (
        "video-finish: could not resolve brand kit: unsafe profile: '../x'\n"
    )
    unknown = outcomes["vf-run-unknown-profile"]
    assert "gtm_core.captions.FontMissing: BRAND.toml has no [typography.font_files].caption" in (
        unknown.stderr
    )
    assert unknown.artifacts == {}


# ── the per-shot verbs ────────────────────────────────────────────────────────────────────


def test_predictor_trim_never_exceeds_the_cap(outcomes):
    got = outcomes["vf-predictor-trim"]
    assert got.stdout == f"video-finish: wrote {OUT}/vf-predictor-trim/trim.mp4\n"
    trim = got.artifact("trim.mp4")
    _assert_h264(trim, w=320, h=240, duration=14.86, tol=0.06)
    assert _video(trim)["duration"] <= 14.9
    _assert_aac(trim)


def test_frames_to_video(outcomes):
    got = outcomes["vf-frames-to-video"]
    assert got.json() == {"out_path": f"{R}/{OUT}/vf-frames-to-video/ui.mp4", "fps": 12}
    ui = got.artifact("ui.mp4")
    _assert_h264(ui, w=320, h=180, duration=0.33, tol=0.05, fps="12/1")
    _assert_aac(ui, duration=0.33, tol=0.05)  # the silent anullsrc track mux/stitch rely on
    assert outcomes["vf-frames-to-video-text"].stdout == (
        f"video-finish: wrote {R}/{OUT}/vf-frames-to-video-text/ui.mp4\n"
    )


def test_mux(outcomes):
    truncate = outcomes["vf-mux-truncate"].json()
    assert truncate["strategy"] == "truncate-video" and truncate["atempo"] is None
    assert truncate["out_path"] == f"{R}/{OUT}/vf-mux-truncate/shot.mp4"
    assert truncate["video_duration_s"] == pytest.approx(2.0, abs=0.05)
    assert truncate["audio_duration_s"] == pytest.approx(1.8, abs=0.05)
    shot = outcomes["vf-mux-truncate"].artifact("shot.mp4")
    _assert_h264(shot, w=320, h=240, duration=1.8, tol=0.1)
    _assert_aac(shot, duration=1.8)

    atempo = outcomes["vf-mux-atempo"].json()
    assert atempo["strategy"] == "atempo"
    assert atempo["atempo"] == pytest.approx(1.1, abs=0.02)
    _assert_h264(outcomes["vf-mux-atempo"].artifact("shot.mp4"), w=320, h=240, duration=2.0)

    assert re.fullmatch(
        rf"video-finish: wrote {re.escape(R)}/{OUT}/vf-mux-text/shot\.mp4 "
        r"\(video 2\.00s, audio 1\.8\ds, truncate-video\)\n",
        outcomes["vf-mux-text"].stdout,
    )
    refused = outcomes["vf-mux-refused"].stderr
    assert "would need atempo=1.300, past the 1.15 transparency ceiling" in refused
    assert "for a LIP-SYNCED shot" in outcomes["vf-mux-lip-synced-refused"].stderr
    assert "past the 1.02 transparency ceiling" in outcomes["vf-mux-lip-synced-refused"].stderr


def test_split(outcomes):
    got = outcomes["vf-split"]
    doc = got.json()
    assert doc["out_paths"] == [f"{R}/{OUT}/vf-split/beat-{i}.mp4" for i in (1, 2)]
    assert doc["source_duration_s"] == pytest.approx(4.0, abs=0.05)
    assert doc["cuts"] == [
        {"shot_id": "beat-1", "start_s": 0.0, "end_s": 1.5, "duration_s": 1.5},
        {"shot_id": "beat-2", "start_s": 1.5, "end_s": 3.25, "duration_s": 1.75},
    ]
    for beat, dur in (("beat-1.mp4", 1.5), ("beat-2.mp4", 1.75)):
        _assert_h264(got.artifact(beat), w=320, h=240, duration=dur, fps="24/1")
        _assert_aac(got.artifact(beat), duration=dur)
    assert len(got.artifacts) == 2  # no .part files, no stray work files
    assert re.fullmatch(
        rf"video-finish: wrote 2 cuts from a 4\.0\ds take into {re.escape(R)}/{OUT}/vf-split-text\n",
        outcomes["vf-split-text"].stdout,
    )
    assert "ends at 9.000s but the take is only 4.0" in outcomes["vf-split-past-end"].stderr
    assert outcomes["vf-split-empty"].stderr == (
        "video-finish: --cuts JSON has no non-empty 'cuts' array\n"
    )
    assert outcomes["vf-split-malformed"].stderr == (
        "video-finish: malformed entry in --cuts JSON: 'start_s'\n"
    )


def test_stitch(outcomes):
    hard = outcomes["vf-stitch-hard-cut"]
    assert hard.json() == {
        "out_path": f"{R}/{OUT}/vf-stitch-hard-cut/master.mp4",
        "segments": 2,
        "method": "concat demuxer (stream copy)",
    }
    master = hard.artifact("master.mp4")
    _assert_h264(master, w=1080, h=1080, duration=5.0, tol=0.15)
    _assert_aac(master, duration=5.0, tol=0.15)
    # both 320x240 sources were re-encoded to the 1:1 frame before the join
    for seg, dur in (("norm_clip-a.mp4", 3.0), ("norm_clip-b.mp4", 2.0)):
        _assert_h264(hard.artifact(f"_work/normalized/{seg}"), w=1080, h=1080, duration=dur)
    assert hard.artifact("_work/concat_list.txt")["kind"] == "text"

    xfade = outcomes["vf-stitch-crossfade"]
    assert xfade.json()["method"] == "xfade re-encode"
    _assert_h264(xfade.artifact("master.mp4"), w=1080, h=1080, duration=4.5, tol=0.15, fps="24/1")

    bitrate = outcomes["vf-stitch-normalize-bitrate"]
    assert bitrate.json()["method"] == "concat demuxer (stream copy)"
    target = bitrate.artifact("master.mp4")["format"]["bitrate_500k_buckets"]
    crf = master["format"]["bitrate_500k_buckets"]
    assert 4 <= target <= 18 and target >= 4 * max(crf, 1), (target, crf)

    # One segment + crossfade takes the concat path inside stitch(), but the CLI derives the
    # reported method from the FLAG, so it says "xfade re-encode" anyway — pinned as-is.
    single = outcomes["vf-stitch-single-crossfade"]
    assert single.json() == {
        "out_path": f"{R}/{OUT}/vf-stitch-single-crossfade/master.mp4",
        "segments": 1,
        "method": "xfade re-encode",
    }
    assert single.artifact("_work/concat_list.txt")["kind"] == "text"
    _assert_h264(single.artifact("master.mp4"), w=1080, h=1080, duration=2.0)

    assert outcomes["vf-stitch-text"].stdout == (
        f"video-finish: wrote {R}/{OUT}/vf-stitch-text/master.mp4 "
        "(2 segments, concat demuxer (stream copy))\n"
    )
    assert outcomes["vf-stitch-empty"].stderr == (
        "video-finish: --segments JSON has no non-empty 'segments' array\n"
    )
    too_long = outcomes["vf-stitch-crossfade-too-long"]
    assert too_long.stderr.rstrip().endswith(
        "video-finish: requested crossfade_s=5.0s requires each segment to be at least 10.0s, "
        "but the shortest normalized segment is 2.000s"
    )
    assert f"{OUT}/vf-stitch-crossfade-too-long/master.mp4" not in too_long.artifacts


def test_burn_captions(outcomes):
    got = outcomes["vf-burn-captions"]
    doc = got.json()
    assert doc["ratio"] == "16:9"
    assert [(b["shot_id"], b["text"], b["text_source"], b["screens"]) for b in doc["burned"]] == [
        ("s1", "hello there friend", "spoken", 1),
        ("s2", "second SHOT line", "caption_text_override", 1),
    ]
    assert doc["burned"][0]["out_path"] == f"{R}/{OUT}/vf-burn-captions/s1-captioned.mp4"
    assert doc["burned"][0]["src_path"] == f"{R}/src/clip-a.mp4"
    assert doc["burned"][0]["duration_s"] == pytest.approx(3.0, abs=0.05)
    assert doc["skipped"] == [
        {
            "shot_id": "s3",
            "why": "no 'spoken', 'caption_text_override' or 'caption_segments' — nothing to caption",
        }
    ]
    for shot, dur in (("s1-captioned.mp4", 3.0), ("s2-captioned.mp4", 2.0)):
        _assert_h264(got.artifact(shot), w=320, h=240, duration=dur, fps="24/1")
        _assert_aac(got.artifact(shot), duration=dur)  # the VO is stream-copied, never dropped
    for shot in ("s1", "s2"):
        png = got.artifact(f"_work/captions-{shot}/caption_00.png")
        assert (png["size"], png["mode"]) == ([1920, 1080], "RGBA")
    assert outcomes["vf-burn-captions-text"].stdout == (
        f"video-finish: burned captions into 2 shots -> {R}/{OUT}/vf-burn-captions-text (1 skipped)\n"
        "  skipped s3: no 'spoken', 'caption_text_override' or 'caption_segments' — nothing to caption\n"
    )
    only = outcomes["vf-burn-captions-shot-ids"].json()
    assert [b["shot_id"] for b in only["burned"]] == ["s2"] and only["skipped"] == []
    assert (
        "--shot-ids names ['nope'] which are not in this shot list (it has ['s1', 's2', 's3'])"
        in (outcomes["vf-burn-captions-unknown-shot-id"].stderr)
    )
    assert outcomes["vf-burn-captions-no-shots"].stderr == (
        "video-finish: --shots JSON has no non-empty 'shots' array\n"
    )
    assert outcomes["vf-burn-captions-unsafe-profile"].stderr == (
        "video-finish: could not resolve brand kit: unsafe profile: '../x'\n"
    )


# ── the audio verbs ───────────────────────────────────────────────────────────────────────


def test_find_transient(outcomes):
    doc = outcomes["vf-find-transient"].json()
    assert doc["path"] == "src/click.wav"
    assert (doc["window_s"], doc["hop_s"]) == (0.05, 0.01)
    # a click at 0.12 s: the window START is reported, always a lead on the attack, never late
    assert 0.06 <= doc["onset_s"] <= 0.12 and doc["onset_s"] <= doc["peak_s"]
    assert doc["peak_dbfs"] == pytest.approx(-6.0, abs=0.5)  # peak 0.5 -> -6.02 dBFS
    assert doc["rule"] == (
        f"first 50 ms window within 6.0 dB of the file's own {doc['peak_dbfs']:.1f} dBFS peak"
    )
    assert re.fullmatch(
        r"video-finish: click\.wav onset 0\.\d{3}s \(peak -6\.\d dBFS at 0\.\d{3}s\) — first 50 ms "
        r"window within 6\.0 dB of the file's own -6\.\d dBFS peak\n",
        outcomes["vf-find-transient-text"].stdout,
    )
    silence = outcomes["vf-find-transient-silence"].stderr
    assert silence.startswith("video-finish: no transient in the first 0.5s of src/silence.wav")
    assert "below the -50.0 dBFS floor" in silence


def test_mix_sfx(outcomes):
    got = outcomes["vf-mix-sfx"]
    doc = got.json()
    assert doc["verified"] is True and doc["base_duration_s"] == pytest.approx(3.0, abs=0.05)
    assert doc["filtergraph"] == (
        "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
        "asplit=2[base][baseout];[1:a]atrim=start=0.110000,asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,volume=-6.0dB,adelay=1000|1000[c1];"
        "[base][c1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    (cue,) = doc["cues"]
    assert (cue["label"], cue["path"], cue["at_s"], cue["verified"]) == (
        "tick",
        "src/click.wav",
        1.0,
        True,
    )
    assert cue["requested_dbfs"] == pytest.approx(cue["cue_peak_dbfs"] - 6.0, abs=0.01)
    assert abs(cue["shortfall_db"]) <= 1.0  # the cue landed at the level it asked for
    assert cue["measured_dbfs"] >= cue["null_floor_dbfs"] + 10.0  # a measurement, not the floor
    assert cue["window"] == [0.99, 1.2]
    mixed = got.artifact("mixed.mp4")
    _assert_h264(mixed, w=320, h=240, duration=3.0)  # picture stream-copied
    _assert_aac(mixed, duration=3.0)
    assert len(got.artifacts) == 1  # intermediates cleaned up

    unverified = outcomes["vf-mix-sfx-no-verify"].json()
    assert unverified["verified"] is False and unverified["cues"] == []
    assert re.fullmatch(
        rf"video-finish: wrote {re.escape(R)}/{OUT}/vf-mix-sfx-text/mixed\.mp4 "
        r"\(1 cues, worst shortfall -?0\.\d dB\)\n",
        outcomes["vf-mix-sfx-text"].stdout,
    )
    assert outcomes["vf-mix-sfx-empty"].stderr == (
        "video-finish: --cues JSON has no non-empty 'cues' array\n"
    )
    assert (
        outcomes["vf-mix-sfx-malformed"].stderr == "video-finish: malformed cue in --cues: 'at_s'\n"
    )
    assert (
        "at_s=9.0 is at or past the clip's 3.000s end" in outcomes["vf-mix-sfx-cue-past-end"].stderr
    )


def test_room_tone(run):
    root, outcomes = run
    default = outcomes["vf-room-tone-default"]
    assert default.json() == {
        "out_path": f"{R}/{OUT}/vf-room-tone-default/tone.m4a",
        "duration_s": 2.0,
    }
    for name in ("vf-room-tone-default", "vf-room-tone-0p09"):
        tone = outcomes[name].artifact("tone.m4a")
        assert [s["codec_type"] for s in tone["streams"]] == ["audio"]
        _assert_aac(tone, duration=2.0, tol=0.05)
    # The CLI's default --amplitude is 0.014 while room_tone()'s own default is 0.09, so a bed
    # made without the flag lands ~-60 dBFS — BELOW video_lint's -50 dBFS dead-air line, the
    # defect the function's docstring describes as fixed. Pinned as-is; a fix is a separate PR.
    assert -70.0 < gm.mean_volume_db(root / OUT / "vf-room-tone-default" / "tone.m4a") < -52.0
    assert -50.0 < gm.mean_volume_db(root / OUT / "vf-room-tone-0p09" / "tone.m4a") < -38.0
    assert outcomes["vf-room-tone-text"].stdout == (
        f"video-finish: wrote {R}/{OUT}/vf-room-tone-text/tone.m4a\n"
    )
    assert outcomes["vf-room-tone-wav"].stderr == (
        "video-finish: out_path must end in .m4a, got 'tone.wav'\n"
    )
    assert outcomes["vf-room-tone-zero-duration"].stderr == (
        "video-finish: duration_s must be > 0, got 0.0\n"
    )


def test_contact_sheet(outcomes):
    got = outcomes["vf-contact-sheet"]
    assert got.json() == {"out_path": f"{OUT}/vf-contact-sheet/sheet.jpg", "frames": 4}
    sheet = got.artifact("sheet.jpg")
    assert (sheet["kind"], sheet["size"], sheet["mode"]) == ("jpeg", [640, 120], "RGB")
    defaults = outcomes["vf-contact-sheet-defaults"]
    assert defaults.json()["frames"] == 8
    assert defaults.artifact("sheet.jpg")["size"] == [2560, 240]  # 8 tiles x 320, 4:3 tiles
    # --count 0 is refused by contact_sheet() but the CLI wraps nothing here: a traceback, exit 1
    zero = outcomes["vf-contact-sheet-zero-count"]
    assert zero.stderr.rstrip().endswith("ValueError: count must be >= 1, got 0")
    assert zero.artifacts == {}


# ── determinism of what the exact-capture tool hashes ─────────────────────────────────────


def test_a_second_run_reproduces_stdout_and_every_hashed_artifact(run, tmp_path):
    """The parts capture_exact.py compares byte-for-byte must be reproducible on one machine,
    or the pre/post-split diff would report noise as drift. Media stays structural."""
    _, first = run
    again_root = tmp_path / "again"
    gm.build_fixtures(again_root)
    slice_ = [
        BY_NAME[n]
        for n in (
            "vf-run-dry-run",
            "vf-frames-to-video",
            "vf-split",
            "vf-burn-captions",
            "vf-find-transient",
            "vf-mix-sfx",
            "vf-room-tone-default",
            "vf-contact-sheet",
        )
    ]
    for inv, again in gm.run_matrix(again_root, tuple(slice_)).items():
        assert again.stdout == first[inv].stdout, inv
        assert again.artifacts == first[inv].artifacts, inv
