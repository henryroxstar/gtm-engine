from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .confine import _safe_asset_path
from .constants import DEFAULT_CRF, DEFAULT_PRESET, PREDICTOR_TRIM_S
from .errors import FfmpegUnavailable, LintError, PolishError
from .ffmpeg import _probe_fps, _run_ffmpeg


def polish_with_reap(
    asset_path: Path,
    profile: str,
    *,
    ratio: str,
    content_root: Path | None = None,
    reap_caller: Callable[[Path], Any] | None = None,
    purpose: str | None = None,
) -> Any:
    """Lint-gated Reap video-polish pass.

    The file must resolve under the active profile's content root; absolute paths, ``..``
    segments, or paths outside the root are refused. The asset is probed and evaluated with
    :mod:`gtm_core.video_lint`; any shipped ERROR tier finding raises :class:`LintError` before
    the Reap caller is invoked. This keeps expensive Reap minutes from being spent on assets that
    would fail the local quality gate anyway.

    ``reap_caller`` is the skill-layer hook that actually invokes the Reap MCP tool(s). The
    signature is ``reap_caller(asset_path: Path) -> Any``. ``gtm_core`` modules do not call MCP
    tools directly (CLAUDE.md §R6), so this parameter is required — passing ``None`` is a
    configuration error and raises :class:`PolishError`.
    """
    from .. import video_lint
    from ..paths import resolve_content_root

    if reap_caller is None:
        raise PolishError(
            "reap_caller is required for polish_with_reap — gtm_core modules do not invoke MCP tools"
        )

    root = content_root if content_root is not None else resolve_content_root()
    source = _safe_asset_path(asset_path, content_root=root)

    if ratio not in video_lint.SAFE_AREAS:
        raise PolishError(f"unknown ratio {ratio!r} for lint")

    probe = video_lint.probe(source)
    findings = video_lint.evaluate(probe, ratio=ratio, purpose=purpose)
    errors = [f for f in findings if f.severity == video_lint.ERROR]
    if errors:
        summary = "; ".join(f"{f.tier} {f.rule}: {f.excerpt}" for f in errors[:3])
        raise LintError(
            f"refusing Reap polish for {source.name}: {len(errors)} lint ERROR(s) — {summary}"
        )

    return reap_caller(source)


def predictor_trim(src: Path, dst: Path, *, duration_s: float = PREDICTOR_TRIM_S) -> None:
    """Trim to <=duration_s for the virality_predictor's hard input cap. Always re-encodes — a
    stream copy at -t 14.9 lands back on a GOP boundary near the observed 15.041667s failure.

    **`-t duration_s` alone is not reliable here** — verified live 2026-08-15: `-t 14.9` on a
    24fps source re-encoded to 14.916667s (358 frames = ceil(14.9 * 24), not floor), i.e. ffmpeg
    rounds a partial final frame UP rather than dropping it, so the naive call can still exceed
    the cap it was meant to enforce. Fixed by probing the source's real fps and subtracting one
    full frame's duration from the requested target before handing it to `-t` — the rounded-up
    result then lands at or under `duration_s`, never over it."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    fps = _probe_fps(src)
    safe_duration_s = duration_s - (1.0 / fps)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-t",
        f"{safe_duration_s:.6f}",
        "-c:v",
        "libx264",
        "-crf",
        str(DEFAULT_CRF),
        "-preset",
        str(DEFAULT_PRESET),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "mp4",
        str(part),
    ]
    _run_ffmpeg(args)
    os.replace(part, dst)


#: Sidecar written beside a graded shot. "Never grade twice" is an invariant `plan()` already
#: enforces for a whole-asset finish (`exactly one grade stage is required`); this is the same
#: rule for the piecewise per-shot path, which is how every asset in this repo is actually
#: assembled. A second `eq` on an already-graded file is not a stronger grade — it is a second
#: generation of compression loss applied to a picture whose contrast has already been moved, and
#: the drift is invisible until the shot is cut beside an ungraded neighbour.
GRADE_SIDECAR_SUFFIX = ".grade.json"


def grade_shot(
    src: Path,
    dst: Path,
    *,
    eq: dict[str, float],
    color_balance: dict[str, float] | None = None,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    force: bool = False,
) -> Path:
    """Apply ONE ``eq`` grade to one finished shot, carrying its audio through untouched.

    Exists because a per-shot assembly has no whole-asset ``run`` to hang a grade on: this film's
    picture is 26 separate files that are stitched at the end, so "grade the four caller plates to
    one target" has to happen per file, before the concat, or the four never match.

    The audio is stream-copied, not re-encoded. A grade is a picture operation and a shot's VO is
    the one thing in this pipeline that must come out bit-identical to what went in.

    ``color_balance`` is the SPLIT-TONE lever ``eq`` structurally cannot offer: ``eq``'s
    ``gamma_r``/``gamma_b`` move a whole channel, so cooling a warm room with them cools the
    faces in it by the same amount. ``colorbalance`` takes shadows, midtones and highlights
    separately (``rs``/``gs``/``bs``, ``rm``/``gm``/``bm``, ``rh``/``gh``/``bh``, each -1..1), so
    a room can sit in a cool world while its practical light and its skin stay warm — which is
    the only version of "put this footage in the brand's palette" that does not turn a kitchen
    into a spaceship. Applied BEFORE ``eq`` in the same single pass, so this is still one grade.
    """
    import json

    if not eq:
        raise PolishError("grade_shot needs at least one eq term (e.g. brightness/contrast)")
    marker = dst.with_suffix(dst.suffix + GRADE_SIDECAR_SUFFIX)
    if marker.exists() and not force:
        raise PolishError(
            f"{dst.name} was already graded ({marker.name} exists). Grading it again stacks a "
            f"second eq on a picture that has already been moved and re-encodes it a second time. "
            f"Re-grade from the UNGRADED source instead, or pass force=True if you mean it."
        )
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    terms = ":".join(f"{k}={v}" for k, v in eq.items())
    chain = f"eq={terms}"
    if color_balance:
        cb = ":".join(f"{k}={v}" for k, v in color_balance.items())
        chain = f"colorbalance={cb},{chain}"
    _run_ffmpeg(
        [
            ffmpeg_bin, "-y", "-i", str(src),
            "-filter:v", chain,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-f", "mp4", str(part),
        ]
    )  # fmt: skip
    os.replace(part, dst)
    marker.write_text(
        json.dumps(
            {
                "source": str(src),
                "eq": eq,
                "color_balance": color_balance or {},
                "crf": crf,
                "preset": preset,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dst
