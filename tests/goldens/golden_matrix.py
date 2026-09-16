"""Shared fixtures + invocation matrices for the Phase-2 CLI goldens (PRD 2026-09-01 §6.2 V2).

Everything the golden test modules and ``capture_exact.py`` share lives here, so the pre-split
capture and the post-split assertion run the SAME invocations on the SAME synthetic fixtures.
Nothing is committed as a binary: every clip is synthesized with ffmpeg's lavfi sources, every
image with Pillow, every kit/spec/shot list from a literal below — the recipe is the fixture.
Fixture data is fictional throughout (profile ``acme``, invented copy, no addresses/emails/URLs).

Two properties every invocation keeps:

* **Relative paths, ``cwd`` = the fixture root.** Stdout and every text artifact then carry no
  temp-dir path, so an exact SHA-256 of them means the same thing in two capture directories.
  A verb that echoes ``_confined_output``'s RESOLVED path is the one exception; :func:`normalize`
  rewrites the root to ``<ROOT>`` for those.
* **Encoded media is fingerprinted structurally, never byte-hashed** (ffprobe stream layout,
  codecs, dimensions, rates, duration to 0.01 s, bitrate in 0.5 Mbps buckets): ffmpeg output is
  not bit-reproducible across builds. Text/JSON/PNG/JPEG are, so those get SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_TOKEN = "<ROOT>"
PROFILE = "acme"  # a fictional tenant — never a real profile slug
OUT = "content/out"
FONT_REL = "fonts/DejaVuSans.ttf"
VIDEO_FINISH = "gtm_core.video_finish"
SCREEN_UI = "gtm_core.screen_ui"
#: argparse wraps help to the terminal width; pin it so a transcript is the same on any runner.
CLI_ENV = {"COLUMNS": "80", "LINES": "24", "PYTHONIOENCODING": "utf-8"}
MEDIA_SUFFIXES = frozenset({".mp4", ".m4a", ".wav", ".mov"})
TEXT_SUFFIXES = frozenset({".json", ".txt", ".md", ".csv"})

#: Every scene the matrix drives. The registry-equality tripwire (test_screen_ui_golden) pins
#: ``_SCENES`` against its own literal; this tuple keeps the matrix honest against that literal
#: so a dropped registration shows up as a failing invocation, never a quietly smaller matrix.
SCENES = (
    "call-ui-bank",
    "call-ui-clinic",
    "call-ui-hotel",
    "call-ui-telco",
    "caller-row-bank",
    "caller-row-clinic",
    "caller-row-hotel",
    "caller-row-telco",
    "chat-bubble",
    "checkpoint-flow",
    "class-booking",
    "hero-reveal",
    "message-card",
    "phone-walkthrough",
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
)
#: Scenes that RENDER at 9:16 with the fixture face; every other scene refuses there (exit 2).
#: A refusal is as much a contract as a render — the 9:16 pass pins both.
SCENES_RENDERING_AT_9X16 = frozenset(
    {
        "chat-bubble",
        "class-booking",
        "hero-reveal",
        "message-card",
        "phone-walkthrough",
        "record-agent-identity",
        "record-bank",
        "record-clean",
        "record-clinic",
        "record-hotel",
        "record-telco",
        "request-inspector",
        "still-push",
    }
)
#: Tenant-neutral fixture palette (deliberately NOT any tenant's deck colours).
PALETTE = {
    "canvas": "#101418",
    "surface": "#1C2229",
    "ink": "#F4F4F4",
    "muted": "#93A1AD",
    "accent": "#4C8DFF",
    "warn": "#E0A030",
    "good": "#37C27A",
}
KIT = {
    "palette": PALETTE,
    "typography": {"font_files": {"caption": FONT_REL}},
    # The kit names the SVG; screen_ui rasterises the sibling .png (see _load_logo_asset).
    "assets": {"logo_horizontal_dark": "src/logo.svg"},
}
TIMING = {
    "DROP": [0.145, 0.215],
    "REROUTE": [0.225, 0.32],
    "NOTE": [0.40, 0.52],
    "STREAM": [0.645, 0.745],
    "SUB1": [0.79, 0.87],
    "SUB2": [0.90, 0.98],
}
DOCS: dict[str, dict] = {
    "kit.json": KIT,
    "kit-nofont.json": {"palette": PALETTE},
    "json/spec.json": {"caption_text": "one clear line then a second line to read", "total_s": 3.0},
    "json/cuts.json": {
        "cuts": [
            {"shot_id": "beat-1", "start_s": 0.0, "end_s": 1.5},
            {"shot_id": "beat-2", "start_s": 1.5, "end_s": 3.25},
        ]
    },
    "json/cuts-past-end.json": {"cuts": [{"shot_id": "beat-1", "start_s": 0.0, "end_s": 9.0}]},
    "json/cuts-empty.json": {"cuts": []},
    "json/cuts-malformed.json": {"cuts": [{"shot_id": "beat-1"}]},
    "json/segments.json": {
        "segments": [{"path": "src/clip-a.mp4"}, {"path": "src/clip-b.mp4", "reframed": False}]
    },
    "json/segments-one.json": {"segments": [{"path": "src/clip-b.mp4"}]},
    "json/segments-empty.json": {"segments": []},
    "json/cues.json": {
        "cues": [
            {"path": "src/click.wav", "at_s": 1.0, "gain_db": -6.0, "trim_s": 0.11, "label": "tick"}
        ]
    },
    "json/cues-empty.json": {"cues": []},
    "json/cues-malformed.json": {"cues": [{"path": "src/click.wav"}]},
    "json/cues-past-end.json": {"cues": [{"path": "src/click.wav", "at_s": 9.0, "label": "late"}]},
    "json/timing.json": TIMING,
    # A one-second walkthrough: the phone arrives, a card is ringed, the ring is tapped. Rects are
    # in `src/screen.png`'s own pixels (240x520), the space an operator measures on a screenshot.
    "json/walkthrough.json": {
        "version": 1,
        "actions": [
            {"type": "enter", "start_s": 0.0, "end_s": 0.4, "from": "bottom"},
            {"type": "highlight", "start_s": 0.4, "end_s": 1.0, "rect": [16, 40, 224, 130]},
            {"type": "tap", "start_s": 0.5, "end_s": 0.9, "point": [48, 86]},
        ],
    },
    "json/walkthrough-bad.json": {"actions": [{"type": "hover", "start_s": 0.0, "end_s": 0.5}]},
    # hero-reveal's own one-second beat over the same still (0=0,0 corner it reads its canvas
    # colour from): enter, ring a region, no exit — exercised at the matrix's standard 1.0s/fps 2.
    "json/hero-reveal.json": {
        "version": 1,
        "actions": [
            {"type": "enter", "start_s": 0.0, "end_s": 0.4, "from": "bottom"},
            {"type": "highlight", "start_s": 0.4, "end_s": 1.0, "rect": [50, 50, 350, 350]},
        ],
    },
    "json/words-mismatch.json": {
        "words": [{"word": "one", "start_s": 0.0, "end_s": 0.4}],
        "audio_duration_s": 9.0,
    },
    # Shot files resolve relative to the shot list's own folder, so this one sits at the root.
    "shots.json": {
        "slug": "film",
        "shots": [
            {"id": "s1", "file": "src/clip-a.mp4", "spoken": "hello there friend"},
            {
                "id": "s2",
                "file": "src/clip-b.mp4",
                "spoken": "second shot line",
                "caption_text_override": "second SHOT line",
            },
            {"id": "s3", "file": "src/silent.mp4"},
        ],
    },
    "shots-none.json": {"slug": "film", "shots": []},
}


@dataclass(frozen=True)
class Invocation:
    name: str
    module: str
    argv: tuple[str, ...]
    exit_code: int
    out_dir: str | None = None

    @property
    def artifact_dir(self) -> str:
        return self.out_dir or f"{OUT}/{self.name}"


@dataclass
class Outcome:
    invocation: Invocation
    exit_code: int
    stdout: str
    stderr: str
    artifacts: dict[str, dict] = field(default_factory=dict)

    def json(self) -> object:
        return json.loads(self.stdout)

    def artifact(self, rel: str) -> dict:
        return self.artifacts[f"{self.invocation.artifact_dir}/{rel}"]


# ── prerequisites ─────────────────────────────────────────────────────────────────────────


def bundled_font() -> Path | None:
    """matplotlib's DejaVuSans.ttf — a declared dependency that carries no tenant identity."""
    try:
        import matplotlib
    except ImportError:
        return None
    return next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)


def missing_prerequisite() -> str | None:
    """Why the media matrices cannot run here, or None. Tests skip with this reason."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            return f"{tool} is not on PATH"
    try:
        import PIL  # noqa: F401
    except ImportError:
        return "Pillow is not installed"
    if bundled_font() is None:
        return "matplotlib's bundled DejaVuSans.ttf is not available"
    return None


# ── fixtures ──────────────────────────────────────────────────────────────────────────────


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-v", "error", *args], check=True, capture_output=True
    )


def _clip(path: Path, *, seconds: float, hz: int | None) -> None:
    """testsrc picture (320x240 @ 24fps) with an optional 48 kHz stereo sine, H.264 + AAC."""
    args = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=24"]
    if hz is not None:
        args += ["-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}:sample_rate=48000"]
        args += ["-ac", "2"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-g", "48"]
    if hz is not None:
        args += ["-c:a", "aac", "-shortest"]
    _ffmpeg(*args, "-f", "mp4", str(path))


def _tone(path: Path, *, seconds: float, hz: int) -> None:
    src = f"sine=frequency={hz}:duration={seconds}:sample_rate=48000"
    _ffmpeg("-f", "lavfi", "-i", src, "-ac", "2", "-c:a", "aac", "-f", "ipod", str(path))


def _click(path: Path, *, onset_s: float = 0.12, peak: float = 0.5, seconds: float = 0.6) -> None:
    """An exponentially decaying click at a KNOWN onset — what makes a transient assertable."""
    expr = f"if(gt(t,{onset_s}),{peak}*exp(-60*(t-{onset_s}))*sin(2*PI*1400*t),0)"
    src = f"aevalsrc=exprs='{expr}':sample_rate=48000:duration={seconds}:channel_layout=stereo"
    _ffmpeg("-f", "lavfi", "-i", src, str(path))


def _images(root: Path) -> None:
    from PIL import Image, ImageDraw

    still = Image.new("RGB", (400, 400), (0, 0, 0))
    draw = ImageDraw.Draw(still)
    draw.rectangle((0, 0, 199, 199), fill=(200, 40, 40))
    draw.rectangle((200, 200, 399, 399), fill=(40, 200, 90))
    still.save(root / "src" / "still.png")
    # A phone-shaped capture for `phone-walkthrough` (240 / 0.46 = 522, so it fills the screen).
    screen = Image.new("RGB", (240, 520), (246, 246, 244))
    sd = ImageDraw.Draw(screen)
    for i in range(4):
        sd.rounded_rectangle(
            (16, 40 + i * 120, 224, 130 + i * 120), radius=10, fill=(200, 210, 214)
        )
    screen.save(root / "src" / "screen.png")
    logo = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((8, 8, 231, 71), fill=(244, 244, 244, 255))
    logo.save(root / "src" / "logo.png")
    for i in range(4):
        Image.new("RGB", (320, 180), (40 + 50 * i, 60, 90)).save(
            root / "frames" / f"ui-{i:04d}.png"
        )


def _brand_toml() -> str:
    lines = ["[palette]"] + [f'{k} = "{v}"' for k, v in PALETTE.items()]
    lines += ["", "[typography.font_files]", f'caption = "{FONT_REL}"', ""]
    return "\n".join(lines)


def build_fixtures(root: Path) -> None:
    """Synthesize the whole fixture tree under ``root`` (idempotent, ~2 s)."""
    for sub in ("src", "frames", "json", "fonts", "content", f"profiles/{PROFILE}/knowledge"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    font = bundled_font()
    if font is None:
        raise RuntimeError("no bundled DejaVuSans.ttf")
    shutil.copyfile(font, root / FONT_REL)
    (root / "profiles" / PROFILE / "knowledge" / "BRAND.toml").write_text(_brand_toml(), "utf-8")
    for rel, doc in DOCS.items():
        (root / rel).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _images(root)
    src = root / "src"
    _clip(src / "clip-a.mp4", seconds=3.0, hz=440)
    _clip(src / "clip-b.mp4", seconds=2.0, hz=660)
    _clip(src / "silent.mp4", seconds=2.0, hz=None)
    _clip(src / "take.mp4", seconds=4.0, hz=330)
    _clip(src / "long.mp4", seconds=16.0, hz=330)  # past predictor-trim's 14.9 s cap
    _tone(src / "vo-short.m4a", seconds=1.8, hz=330)
    _tone(src / "vo-long.m4a", seconds=2.2, hz=330)
    _tone(src / "vo-toolong.m4a", seconds=2.6, hz=330)
    _click(src / "click.wav")
    _ffmpeg(
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.5", str(src / "silence.wav")
    )


# ── matrices ──────────────────────────────────────────────────────────────────────────────


def _vf(name: str, *argv: str, exit_code: int = 0, out_dir: str | None = None) -> Invocation:
    return Invocation(name, VIDEO_FINISH, tuple(argv), exit_code, out_dir)


def _su(name: str, *argv: str, exit_code: int = 0, out_dir: str | None = None) -> Invocation:
    return Invocation(name, SCREEN_UI, tuple(argv), exit_code, out_dir)


def _o(name: str) -> str:
    return f"{OUT}/{name}"


def video_finish_matrix() -> tuple[Invocation, ...]:  # noqa: PLR0915 — a data table, not logic
    """Every subcommand, its happy path(s), its text and ``--json`` faces, and its refusals."""
    run = ("run", "--profile", PROFILE, "--slug", "asset", "--ratio", "9:16", "--spec")
    run += ("json/spec.json", "--source", "src/clip-a.mp4", "--profiles-root", "profiles")
    run += ("--repo-root", ".")
    exe = _o("vf-run-execute")
    mux = ("mux", "--video", "src/silent.mp4", "--content-root", "content")
    burn = ("burn-captions", "--shots", "shots.json", "--ratio", "16:9", "--profile", PROFILE)
    burn += ("--profiles-root", "profiles", "--content-root", "content", "--repo-root", ".")
    sfx = ("mix-sfx", "--in", "src/clip-a.mp4", "--content-root", "content")
    tone = ("room-tone", "--duration-s", "2", "--content-root", "content")
    return (
        _vf("vf-help", "--help"),
        _vf("vf-no-subcommand", exit_code=2),
        _vf("vf-unknown-subcommand", "frobnicate", exit_code=2),
        _vf("vf-run-dry-run", *run, "--out-dir", _o("vf-run-dry-run"), "--dry-run"),
        _vf("vf-run-execute", *run, "--out-dir", exe, "--workdir", f"{exe}/_work", "--json"),
        _vf(
            "vf-run-execute-again",
            *run,
            "--out-dir",
            exe,
            "--workdir",
            f"{exe}/_work",
            "--json",
            out_dir=exe,
        ),
        _vf("vf-run-text", *run, "--out-dir", _o("vf-run-text")),
        _vf(
            "vf-run-bad-ratio",
            *run[:6],
            "3:2",
            *run[7:],
            "--out-dir",
            _o("vf-run-bad-ratio"),
            exit_code=2,
        ),
        _vf(
            "vf-run-unsafe-profile",
            *run[:2],
            "../x",
            *run[3:],
            "--out-dir",
            _o("vf-run-unsafe-profile"),
            exit_code=2,
        ),
        _vf(
            "vf-run-unknown-profile",
            *run[:2],
            "nobody",
            *run[3:],
            "--out-dir",
            _o("vf-run-unknown-profile"),
            exit_code=1,
        ),
        _vf(
            "vf-predictor-trim",
            "predictor-trim",
            "--in",
            "src/long.mp4",
            "--out",
            f"{_o('vf-predictor-trim')}/trim.mp4",
        ),
        _vf(
            "vf-frames-to-video",
            "frames-to-video",
            "--frames-glob",
            "frames/ui-%04d.png",
            "--fps",
            "12",
            "--out",
            f"{_o('vf-frames-to-video')}/ui.mp4",
            "--content-root",
            "content",
            "--json",
        ),
        _vf(
            "vf-frames-to-video-text",
            "frames-to-video",
            "--frames-glob",
            "frames/ui-%04d.png",
            "--fps",
            "12",
            "--out",
            f"{_o('vf-frames-to-video-text')}/ui.mp4",
            "--content-root",
            "content",
        ),
        _vf(
            "vf-frames-to-video-outside-root",
            "frames-to-video",
            "--frames-glob",
            "frames/ui-%04d.png",
            "--fps",
            "12",
            "--out",
            "elsewhere/ui.mp4",
            "--content-root",
            "content",
            exit_code=2,
        ),
        _vf(
            "vf-mux-truncate",
            *mux,
            "--audio",
            "src/vo-short.m4a",
            "--out",
            f"{_o('vf-mux-truncate')}/shot.mp4",
            "--json",
        ),
        _vf(
            "vf-mux-atempo",
            *mux,
            "--audio",
            "src/vo-long.m4a",
            "--out",
            f"{_o('vf-mux-atempo')}/shot.mp4",
            "--json",
        ),
        _vf(
            "vf-mux-text",
            *mux,
            "--audio",
            "src/vo-short.m4a",
            "--out",
            f"{_o('vf-mux-text')}/shot.mp4",
        ),
        _vf(
            "vf-mux-refused",
            *mux,
            "--audio",
            "src/vo-toolong.m4a",
            "--out",
            f"{_o('vf-mux-refused')}/shot.mp4",
            exit_code=4,
        ),
        _vf(
            "vf-mux-lip-synced-refused",
            *mux,
            "--audio",
            "src/vo-long.m4a",
            "--lip-synced",
            "--out",
            f"{_o('vf-mux-lip-synced-refused')}/shot.mp4",
            exit_code=4,
        ),
        _vf(
            "vf-mux-outside-root",
            *mux,
            "--audio",
            "src/vo-short.m4a",
            "--out",
            "elsewhere/shot.mp4",
            exit_code=2,
        ),
        _vf(
            "vf-split",
            "split",
            "--in",
            "src/take.mp4",
            "--cuts",
            "json/cuts.json",
            "--out-dir",
            _o("vf-split"),
            "--content-root",
            "content",
            "--json",
        ),
        _vf(
            "vf-split-text",
            "split",
            "--in",
            "src/take.mp4",
            "--cuts",
            "json/cuts.json",
            "--out-dir",
            _o("vf-split-text"),
            "--content-root",
            "content",
        ),
        _vf(
            "vf-split-past-end",
            "split",
            "--in",
            "src/take.mp4",
            "--cuts",
            "json/cuts-past-end.json",
            "--out-dir",
            _o("vf-split-past-end"),
            "--content-root",
            "content",
            exit_code=4,
        ),
        _vf(
            "vf-split-empty",
            "split",
            "--in",
            "src/take.mp4",
            "--cuts",
            "json/cuts-empty.json",
            "--out-dir",
            _o("vf-split-empty"),
            "--content-root",
            "content",
            exit_code=2,
        ),
        _vf(
            "vf-split-malformed",
            "split",
            "--in",
            "src/take.mp4",
            "--cuts",
            "json/cuts-malformed.json",
            "--out-dir",
            _o("vf-split-malformed"),
            "--content-root",
            "content",
            exit_code=2,
        ),
        _vf(
            "vf-split-outside-root",
            "split",
            "--in",
            "src/take.mp4",
            "--cuts",
            "json/cuts.json",
            "--out-dir",
            "elsewhere",
            "--content-root",
            "content",
            exit_code=2,
        ),
        _vf(
            "vf-stitch-hard-cut",
            "stitch",
            "--segments",
            "json/segments.json",
            "--ratio",
            "1:1",
            "--out",
            f"{_o('vf-stitch-hard-cut')}/master.mp4",
            "--content-root",
            "content",
            "--json",
        ),
        _vf(
            "vf-stitch-crossfade",
            "stitch",
            "--segments",
            "json/segments.json",
            "--ratio",
            "1:1",
            "--crossfade-s",
            "0.5",
            "--out",
            f"{_o('vf-stitch-crossfade')}/master.mp4",
            "--content-root",
            "content",
            "--json",
        ),
        _vf(
            "vf-stitch-normalize-bitrate",
            "stitch",
            "--segments",
            "json/segments.json",
            "--ratio",
            "1:1",
            "--normalize",
            "--video-bitrate",
            "8M",
            "--out",
            f"{_o('vf-stitch-normalize-bitrate')}/master.mp4",
            "--content-root",
            "content",
            "--json",
        ),
        _vf(
            "vf-stitch-single-crossfade",
            "stitch",
            "--segments",
            "json/segments-one.json",
            "--ratio",
            "1:1",
            "--crossfade-s",
            "0.5",
            "--out",
            f"{_o('vf-stitch-single-crossfade')}/master.mp4",
            "--content-root",
            "content",
            "--json",
        ),
        _vf(
            "vf-stitch-text",
            "stitch",
            "--segments",
            "json/segments.json",
            "--ratio",
            "1:1",
            "--out",
            f"{_o('vf-stitch-text')}/master.mp4",
            "--content-root",
            "content",
        ),
        _vf(
            "vf-stitch-empty",
            "stitch",
            "--segments",
            "json/segments-empty.json",
            "--ratio",
            "1:1",
            "--out",
            f"{_o('vf-stitch-empty')}/master.mp4",
            "--content-root",
            "content",
            exit_code=2,
        ),
        _vf(
            "vf-stitch-crossfade-too-long",
            "stitch",
            "--segments",
            "json/segments.json",
            "--ratio",
            "1:1",
            "--crossfade-s",
            "5",
            "--out",
            f"{_o('vf-stitch-crossfade-too-long')}/master.mp4",
            "--content-root",
            "content",
            exit_code=4,
        ),
        _vf(
            "vf-stitch-bad-ratio",
            "stitch",
            "--segments",
            "json/segments.json",
            "--ratio",
            "3:2",
            "--out",
            f"{_o('vf-stitch-bad-ratio')}/master.mp4",
            "--content-root",
            "content",
            exit_code=2,
        ),
        _vf("vf-burn-captions", *burn, "--out-dir", _o("vf-burn-captions"), "--json"),
        _vf("vf-burn-captions-text", *burn, "--out-dir", _o("vf-burn-captions-text")),
        _vf(
            "vf-burn-captions-shot-ids",
            *burn,
            "--shot-ids",
            "s2",
            "--out-dir",
            _o("vf-burn-captions-shot-ids"),
            "--json",
        ),
        _vf(
            "vf-burn-captions-unknown-shot-id",
            *burn,
            "--shot-ids",
            "nope",
            "--out-dir",
            _o("vf-burn-captions-unknown-shot-id"),
            exit_code=4,
        ),
        _vf(
            "vf-burn-captions-no-shots",
            *burn[:2],
            "shots-none.json",
            *burn[3:],
            "--out-dir",
            _o("vf-burn-captions-no-shots"),
            exit_code=2,
        ),
        _vf(
            "vf-burn-captions-unsafe-profile",
            *burn[:6],
            "../x",
            *burn[7:],
            "--out-dir",
            _o("vf-burn-captions-unsafe-profile"),
            exit_code=2,
        ),
        _vf("vf-burn-captions-outside-root", *burn, "--out-dir", "elsewhere", exit_code=2),
        _vf("vf-find-transient", "find-transient", "--in", "src/click.wav", "--json"),
        _vf("vf-find-transient-text", "find-transient", "--in", "src/click.wav"),
        _vf("vf-find-transient-silence", "find-transient", "--in", "src/silence.wav", exit_code=4),
        _vf(
            "vf-mix-sfx",
            *sfx,
            "--cues",
            "json/cues.json",
            "--out",
            f"{_o('vf-mix-sfx')}/mixed.mp4",
            "--json",
        ),
        _vf(
            "vf-mix-sfx-text",
            *sfx,
            "--cues",
            "json/cues.json",
            "--out",
            f"{_o('vf-mix-sfx-text')}/mixed.mp4",
        ),
        _vf(
            "vf-mix-sfx-no-verify",
            *sfx,
            "--cues",
            "json/cues.json",
            "--no-verify",
            "--out",
            f"{_o('vf-mix-sfx-no-verify')}/mixed.mp4",
            "--json",
        ),
        _vf(
            "vf-mix-sfx-empty",
            *sfx,
            "--cues",
            "json/cues-empty.json",
            "--out",
            f"{_o('vf-mix-sfx-empty')}/mixed.mp4",
            exit_code=2,
        ),
        _vf(
            "vf-mix-sfx-malformed",
            *sfx,
            "--cues",
            "json/cues-malformed.json",
            "--out",
            f"{_o('vf-mix-sfx-malformed')}/mixed.mp4",
            exit_code=2,
        ),
        _vf(
            "vf-mix-sfx-cue-past-end",
            *sfx,
            "--cues",
            "json/cues-past-end.json",
            "--out",
            f"{_o('vf-mix-sfx-cue-past-end')}/mixed.mp4",
            exit_code=4,
        ),
        _vf(
            "vf-room-tone-default",
            *tone,
            "--out",
            f"{_o('vf-room-tone-default')}/tone.m4a",
            "--json",
        ),
        _vf(
            "vf-room-tone-0p09",
            *tone,
            "--amplitude",
            "0.09",
            "--out",
            f"{_o('vf-room-tone-0p09')}/tone.m4a",
            "--json",
        ),
        _vf("vf-room-tone-text", *tone, "--out", f"{_o('vf-room-tone-text')}/tone.m4a"),
        _vf("vf-room-tone-wav", *tone, "--out", f"{_o('vf-room-tone-wav')}/tone.wav", exit_code=4),
        _vf(
            "vf-room-tone-zero-duration",
            "room-tone",
            "--duration-s",
            "0",
            "--content-root",
            "content",
            "--out",
            f"{_o('vf-room-tone-zero-duration')}/tone.m4a",
            exit_code=4,
        ),
        _vf("vf-room-tone-outside-root", *tone, "--out", "elsewhere/tone.m4a", exit_code=2),
        _vf(
            "vf-contact-sheet",
            "contact-sheet",
            "--in",
            "src/clip-a.mp4",
            "--out",
            f"{_o('vf-contact-sheet')}/sheet.jpg",
            "--count",
            "4",
            "--tile-w",
            "160",
        ),
        _vf(
            "vf-contact-sheet-defaults",
            "contact-sheet",
            "--in",
            "src/clip-a.mp4",
            "--out",
            f"{_o('vf-contact-sheet-defaults')}/sheet.jpg",
        ),
        _vf(
            "vf-contact-sheet-zero-count",
            "contact-sheet",
            "--in",
            "src/clip-a.mp4",
            "--out",
            f"{_o('vf-contact-sheet-zero-count')}/sheet.jpg",
            "--count",
            "0",
            exit_code=1,
        ),
    )


def scene_argv(scene: str, ratio: str, out_dir: str) -> tuple[str, ...]:
    argv = (scene, "--kit-json", "kit.json", "--ratio", ratio, "--fps", "2", "--duration-s", "1.0")
    argv += ("--out-dir", out_dir, "--repo-root", ".")
    if scene == "still-push":
        argv += ("--image", "src/still.png")
    if scene == "phone-walkthrough":
        # The action list is as much a scene input as the screenshot: without one the scene
        # refuses, so the matrix's happy path carries both.
        argv += ("--image", "src/screen.png", "--actions", "json/walkthrough.json")
    if scene == "hero-reveal":
        argv += ("--image", "src/still.png", "--actions", "json/hero-reveal.json")
    if scene.startswith("caller-row-"):
        # Refuses with anything but exactly four stills, in row order — the fixture still stands
        # in for all four callers, which is enough to pin the composition, not the faces.
        argv += ("--still", "src/still.png") * 4
    return argv


def screen_ui_matrix() -> tuple[Invocation, ...]:
    """Every scene at 16:9 (all render) and 9:16 (some refuse), ``audit-fit``, every flag path."""
    base = ("--kit-json", "kit.json", "--ratio", "16:9", "--repo-root", ".")
    cf = ("checkpoint-flow", *base, "--fps", "2", "--duration-s", "1.0", "--out-dir")
    sp = ("still-push", *base, "--fps", "2", "--duration-s", "1.0", "--out-dir")
    pw = ("phone-walkthrough", *base, "--fps", "2", "--duration-s", "1.0", "--out-dir")
    scenes: list[Invocation] = []
    for scene in SCENES:
        name = f"su-{scene}-16x9"
        scenes.append(_su(name, *scene_argv(scene, "16:9", _o(name))))
    for scene in SCENES:
        name = f"su-{scene}-9x16"
        code = 0 if scene in SCENES_RENDERING_AT_9X16 else 2
        scenes.append(_su(name, *scene_argv(scene, "9:16", _o(name)), exit_code=code))
    return (
        *scenes,
        _su("su-help", "--help", exit_code=0),  # was the unescaped-% crash; escaped 2026-09-03
        _su("su-no-args", exit_code=2),
        _su("su-bad-scene", "bogus", *base, exit_code=2),
        _su("su-bad-ratio", "title-claim", "--kit-json", "kit.json", "--ratio", "3:2", exit_code=2),
        _su("su-missing-duration-and-out-dir", "title-claim", *base, exit_code=2),
        _su(
            "su-kit-without-font",
            "title-claim",
            "--kit-json",
            "kit-nofont.json",
            "--ratio",
            "16:9",
            "--duration-s",
            "1.0",
            "--out-dir",
            _o("su-kit-without-font"),
            exit_code=2,
        ),
        _su("su-audit-fit-16x9", "audit-fit", *base),
        _su(
            "su-audit-fit-9x16-min-tolerance",
            "audit-fit",
            "--kit-json",
            "kit.json",
            "--ratio",
            "9:16",
            "--repo-root",
            ".",
            "--min-tolerance",
            "1.15",
            exit_code=1,
        ),
        _su(
            "su-checkpoint-flow-timing-json",
            *cf,
            _o("su-checkpoint-flow-timing-json"),
            "--timing-json",
            "json/timing.json",
        ),
        _su(
            "su-checkpoint-flow-words-mismatch",
            *cf,
            _o("su-checkpoint-flow-words-mismatch"),
            "--words-json",
            "json/words-mismatch.json",
            exit_code=2,
        ),
        _su(
            "su-checkpoint-flow-both-timings",
            *cf,
            _o("su-checkpoint-flow-both-timings"),
            "--words-json",
            "json/words-mismatch.json",
            "--timing-json",
            "json/timing.json",
            exit_code=2,
        ),
        _su(
            "su-title-claim-logo",
            "title-claim",
            *base,
            "--fps",
            "2",
            "--duration-s",
            "1.0",
            "--out-dir",
            _o("su-title-claim-logo"),
            "--logo",
        ),
        _su(
            "su-record-bank-logo-ignored",
            "record-bank",
            *base,
            "--fps",
            "2",
            "--duration-s",
            "1.0",
            "--out-dir",
            _o("su-record-bank-logo-ignored"),
            "--logo",
        ),
        _su(
            "su-still-push-crop",
            *sp,
            _o("su-still-push-crop"),
            "--image",
            "src/still.png",
            "--crop-frac",
            "0.5,0.5,1,1",
        ),
        _su(
            "su-still-push-bad-crop",
            *sp,
            _o("su-still-push-bad-crop"),
            "--image",
            "src/still.png",
            "--crop-frac",
            "0.1,0.2",
            exit_code=2,
        ),
        _su("su-still-push-no-image", *sp, _o("su-still-push-no-image"), exit_code=2),
        _su(
            "su-phone-walkthrough-no-actions",
            *pw,
            _o("su-pw-no-actions"),
            "--image",
            "src/screen.png",
            exit_code=2,
        ),
        _su(
            "su-phone-walkthrough-bad-action",
            *pw,
            _o("su-pw-bad-action"),
            "--image",
            "src/screen.png",
            "--actions",
            "json/walkthrough-bad.json",
            exit_code=2,
        ),
    )


def all_invocations() -> tuple[Invocation, ...]:
    return (*video_finish_matrix(), *screen_ui_matrix())


# ── running + fingerprinting ──────────────────────────────────────────────────────────────


def cli_env(root: Path) -> dict[str, str]:
    pythonpath = os.pathsep.join(p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p)
    return {
        **os.environ,
        **CLI_ENV,
        "PYTHONPATH": pythonpath,
        "GTM_CONTENT_ROOT": str(root / "content"),
        "GTM_PROFILES_ROOT": str(root / "profiles"),
    }


#: ffmpeg's own stderr (passed through by ``_run_ffmpeg``) carries heap addresses and progress
#: lines that differ run to run on the same machine; neither is behaviour.
_HEAP_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{6,}")
_PROGRESS_LINE_RE = re.compile(r"^(?:frame=|size=)[^\n]*\n?", re.MULTILINE)


def normalize(text: str, root: Path) -> str:
    for form in sorted({str(root.resolve()), str(root)}, key=len, reverse=True):
        text = text.replace(form, ROOT_TOKEN)
    text = _HEAP_ADDR_RE.sub("0x<ADDR>", text)
    return _PROGRESS_LINE_RE.sub("", text)


def run_invocation(root: Path, inv: Invocation) -> Outcome:
    proc = subprocess.run(
        [sys.executable, "-m", inv.module, *inv.argv],
        cwd=root,
        env=cli_env(root),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    artifacts: dict[str, dict] = {}
    art_dir = root / inv.artifact_dir
    if art_dir.is_dir():
        for path in sorted(art_dir.rglob("*")):
            if path.is_file() and path.suffix != ".part":
                artifacts[path.relative_to(root).as_posix()] = fingerprint_file(path, root)
    return Outcome(
        inv, proc.returncode, normalize(proc.stdout, root), normalize(proc.stderr, root), artifacts
    )


def run_matrix(root: Path, invocations: tuple[Invocation, ...]) -> dict[str, Outcome]:
    return {inv.name: run_invocation(root, inv) for inv in invocations}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _round(raw: object, places: int = 2) -> float | None:
    try:
        return round(float(raw), places)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def media_fingerprint(path: Path) -> dict:
    """Structure only: what a pure-motion refactor must preserve, without the encoder's noise."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    doc = json.loads(out.stdout)
    keys = (
        "codec_type",
        "codec_name",
        "width",
        "height",
        "pix_fmt",
        "r_frame_rate",
        "sample_rate",
        "channels",
        "channel_layout",
    )
    streams = []
    for s in doc.get("streams", []):
        row = {k: s[k] for k in keys if s.get(k) is not None}
        row["duration"] = _round(s.get("duration"))
        streams.append(row)
    fmt = doc.get("format", {})
    bitrate = _round(fmt.get("bit_rate"), 0)
    return {
        "kind": "media",
        "streams": streams,
        "format": {
            "format_name": fmt.get("format_name"),
            "duration": _round(fmt.get("duration")),
            "bitrate_500k_buckets": None if bitrate is None else int(round(bitrate / 500_000)),
        },
    }


def coarse_png_fingerprint(path: Path, grid: int = 16) -> str:
    """16x16 box-filtered grayscale, 4 bits per cell, as hex — stable across font rasterizers."""
    from PIL import Image

    with Image.open(path) as im:
        small = im.convert("L").resize((grid, grid), Image.BOX)
        return "".join(f"{v >> 4:x}" for v in small.getdata())


def fingerprint_distance(a: str, b: str) -> tuple[int, int]:
    """(largest per-cell level difference, number of differing cells) between two fingerprints."""
    if len(a) != len(b):
        return (15, max(len(a), len(b)))
    diffs = [abs(int(x, 16) - int(y, 16)) for x, y in zip(a, b, strict=True)]
    return (max(diffs, default=0), sum(1 for d in diffs if d))


def image_fingerprint(path: Path) -> dict:
    from PIL import Image

    with Image.open(path) as im:
        row = {
            "kind": im.format.lower() if im.format else "image",
            "size": list(im.size),
            "mode": im.mode,
            "sha256": _sha(path.read_bytes()),
        }
    if path.suffix.lower() == ".png":
        row["coarse"] = coarse_png_fingerprint(path)
    return row


def fingerprint_file(path: Path, root: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix in MEDIA_SUFFIXES:
        return media_fingerprint(path)
    if suffix in {".png", ".jpg", ".jpeg"}:
        return image_fingerprint(path)
    if suffix in TEXT_SUFFIXES:
        text = normalize(path.read_text(encoding="utf-8"), root)
        return {"kind": "text", "sha256": _sha(text.encode("utf-8"))}
    return {"kind": "bytes", "sha256": _sha(path.read_bytes())}


_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def mean_volume_db(path: Path) -> float:
    """ffmpeg ``volumedetect`` mean level of the first audio stream, in dBFS."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    match = _MEAN_VOLUME_RE.search(out.stderr + out.stdout)
    if match is None:
        raise RuntimeError(f"no mean_volume line for {path}")
    return float(match.group(1))


def environment() -> dict:
    """What a capture was made with — the first thing to compare when two captures differ."""
    import PIL

    ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
    font = bundled_font()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "ffmpeg": (ffmpeg.stdout.splitlines() or ["?"])[0],
        "pillow": PIL.__version__,
        "font_sha256": _sha(font.read_bytes()) if font else None,
    }
