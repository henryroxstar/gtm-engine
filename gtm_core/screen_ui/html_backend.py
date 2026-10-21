"""HTML/WAAPI motion backend for programmatic screen_ui scenes.

Renders parameterised library scenes and confined one-off compositions to PNG
sequences via headless Chromium (HyperFrames sidecar / container / local CLI),
which are then encoded to MP4 by ``video_finish.mux.frames_to_video``.
"""

from __future__ import annotations

import contextlib
import errno
import html
import json
import os
import re
import shutil
import subprocess  # nosec B404 — hyperframes CLI and Docker orchestration, arg list only, never shell=True
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PIL import Image

from ..brandkit_css import render_tokens_css
from ..paths import resolve_content_root
from . import SAFE_AREAS  # guarded copy: video_lint is withheld from the OSS carve
from .base import SceneError
from .html_lint import HtmlLintError, lint_composition
from .registry import _HTML_SCENES, html_scene_template
from .scene_payload import ScenePayload, apply
from .scene_prepare import prepare_page, require_html_enabled

HYPERFRAMES_VERSION = "0.8.162"
_RATIO_DIMS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
_DIMS_TO_RATIO: dict[tuple[int, int], str] = {v: k for k, v in _RATIO_DIMS.items()}


def _normalize_kit_for_tokens(kit: dict | None) -> dict[str, Any]:
    """Ensure partial brand kit dicts (such as test fixtures) populate default fallback sections."""
    if not isinstance(kit, dict):
        kit = {}
    norm = dict(kit)
    palette = dict(norm.get("palette", {})) if isinstance(norm.get("palette"), dict) else {}
    typography = (
        dict(norm.get("typography", {})) if isinstance(norm.get("typography"), dict) else {}
    )
    motion = dict(norm.get("motion", {})) if isinstance(norm.get("motion"), dict) else {}
    layout = dict(norm.get("layout", {})) if isinstance(norm.get("layout"), dict) else {}

    # Bridge typography.headline -> typography.display if display not set
    if "headline" in typography and "display" not in typography:
        typography["display"] = typography["headline"]

    # Ensure palette status and families dicts exist
    if "status" not in palette or not isinstance(palette["status"], dict):
        palette["status"] = {}
    if "families" not in palette or not isinstance(palette["families"], dict):
        palette["families"] = {}

    norm["palette"] = palette
    norm["typography"] = typography
    norm["motion"] = motion
    norm["layout"] = layout
    return norm


class HtmlBackendError(SceneError):
    """Refusal or failure in the HTML motion rendering backend."""


@contextlib.contextmanager
def motion_render_lock(content_root: Path, *, timeout_s: float = 180.0) -> Iterator[Path]:
    """Acquire exclusive advisory flock on ``content_root/.locks/motion-render.lock``."""
    import fcntl  # POSIX only

    locks_dir = content_root / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / "motion-render.lock"
    fh = lock_path.open("a+")
    start = time.monotonic()
    logged_wait = False

    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                if time.monotonic() - start >= timeout_s:
                    fh.close()
                    raise HtmlBackendError(
                        f"Timed out after {timeout_s:.1f}s waiting on motion-render.lock"
                    ) from None
                if not logged_wait:
                    logged_wait = True
                    sys.stderr.write(
                        f"Waiting on motion-render.lock (timeout {timeout_s:.0f}s)...\n"
                    )
                    sys.stderr.flush()
                time.sleep(0.5)
            else:
                fh.close()
                raise

    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        yield lock_path
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _get_scene_source(scene: str | None, composition: Path | None) -> Path:
    if scene and scene in _HTML_SCENES:
        template = html_scene_template(scene)
        if not template.is_file():
            raise HtmlBackendError(f"HTML scene template not found: {template}")
        return template
    if composition:
        comp_path = Path(composition).resolve()
        if not comp_path.is_file():
            raise HtmlBackendError(f"One-off composition file not found: {comp_path}")
        return comp_path
    raise HtmlBackendError(
        f"Either a known HTML scene ({sorted(_HTML_SCENES.keys())}) or composition path required"
    )


def _prepare_wrapper(  # noqa: C901  # PRD 2026-10-16-html-motion-scenes
    html_src: str,
    *,
    kit: dict,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    extras: dict[str, Any] | None = None,
    ratio: str | None = None,
    payload: ScenePayload | None = None,
) -> str:
    if ratio is None:
        ratio = _DIMS_TO_RATIO.get((width, height), "9:16")
    area = SAFE_AREAS.get(ratio, SAFE_AREAS["9:16"])
    safe_top = round(height * area.top)
    safe_bottom = round(height * area.bottom)
    safe_left = round(width * area.left)
    safe_right = round(width * area.right)
    caption_band_top = round(height * (1.0 - area.bottom))

    norm_kit = _normalize_kit_for_tokens(kit)
    brand_css = render_tokens_css(norm_kit, width=width, height=height)
    total_frames = int(round(fps * duration_s))
    extra_tokens = (
        f"  --safe-top: {safe_top}px;\n"
        f"  --safe-bottom: {safe_bottom}px;\n"
        f"  --safe-left: {safe_left}px;\n"
        f"  --safe-right: {safe_right}px;\n"
        f"  --caption-band-top: {caption_band_top}px;\n"
        f"  --shot-width: {width}px;\n"
        f"  --shot-height: {height}px;\n"
        f"  --shot-fps: {fps};\n"
        f"  --shot-duration-s: {duration_s}s;\n"
        f"  --shot-total-frames: {total_frames};\n"
    )
    last_brace_idx = brand_css.rfind("}")
    if last_brace_idx != -1:
        brand_css = brand_css[:last_brace_idx] + extra_tokens + brand_css[last_brace_idx:]
    tokens_style = f'<style id="gtm-brand-tokens">\n{brand_css}\n</style>'

    csp_meta = (
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline' file:; img-src data: file: 'self'; font-src data: file:; "
        "script-src 'unsafe-inline' file:; connect-src 'none'; object-src 'none'; frame-src 'none';\">"
    )
    dim_meta = f'<meta name="viewport" content="width={width}, height={height}, initial-scale=1">'

    text = html_src

    # 1. Inline shared styles and scripts BEFORE tokens_style so tenant tokens win cascade
    shared_dir = Path(__file__).resolve().parent / "html" / "_shared"
    for fname in ("tokens.css", "animations.css"):
        fp = shared_dir / fname
        if fp.is_file():
            c = fp.read_text(encoding="utf-8")
            text = re.sub(
                rf'<link\s+rel=["\']stylesheet["\']\s+href=["\'][^"\']*_shared/{fname}["\']\s*/?>',
                lambda _m, c=c, fn=fname: f"<style>/* {fn} */\n{c}\n</style>",
                text,
            )
    mp = shared_dir / "motion.js"
    if mp.is_file():
        mc = mp.read_text(encoding="utf-8")
        text = re.sub(
            r'<script\s+src=["\'][^"\']*_shared/motion\.js["\']\s*></script>',
            lambda _m, mc=mc: f"<script>/* motion.js */\n{mc}\n</script>",
            text,
        )
    text = text.replace("../_shared/", "./_shared/").replace("/_shared/", "./_shared/")

    # 2. Inject CSP and viewport meta into head
    head_top = f"\n  {csp_meta}\n  {dim_meta}\n"
    if "<head>" in text:
        text = text.replace("<head>", f"<head>{head_top}", 1)
    else:
        text = head_top + text

    # 3. Inject tokens_style immediately before </head> to guarantee cascade dominance
    if "</head>" in text:
        text = text.replace("</head>", f"  {tokens_style}\n</head>", 1)
    else:
        text = text + f"\n{tokens_style}\n"

    # Stamp root element with dimensions and data-no-timeline
    def _stamp_root(match: re.Match[str]) -> str:
        tag = match.group(0)
        if 'data-no-timeline="true"' not in tag and "data-no-timeline" not in tag:
            tag = tag[:-1] + ' data-no-timeline="true">'
        if "data-width=" in tag:
            tag = re.sub(r'data-width="[^"]*"', f'data-width="{width}"', tag)
        else:
            tag = tag[:-1] + f' data-width="{width}">'
        if "data-height=" in tag:
            tag = re.sub(r'data-height="[^"]*"', f'data-height="{height}"', tag)
        else:
            tag = tag[:-1] + f' data-height="{height}">'
        if "data-duration=" in tag:
            tag = re.sub(r'data-duration="[^"]*"', f'data-duration="{duration_s}"', tag)
        else:
            tag = tag[:-1] + f' data-duration="{duration_s}">'
        if "data-ratio=" in tag:
            tag = re.sub(r'data-ratio="[^"]*"', f'data-ratio="{ratio}"', tag)
        else:
            tag = tag[:-1] + f' data-ratio="{ratio}">'
        return tag

    text = re.sub(r'<div[^>]*id=["\']root["\'][^>]*>', _stamp_root, text, count=1)

    # Inject string-copy extras into matching element IDs using lambda to prevent backslash issues
    if extras:
        for key in ("headline", "subline", "eyebrow", "badge", "kicker"):
            val = extras.get(key)
            if isinstance(val, str) and val.strip():
                safe_val = html.escape(val.strip())
                pattern = (
                    rf'(<(?P<tag>[a-zA-Z0-9]+)[^>]*id=["\']{key}["\'][^>]*>)(.*?)(</(?P=tag)>)'
                )
                text = re.sub(
                    pattern,
                    lambda m, repl=safe_val: f"{m.group(1)}{repl}{m.group(4)}",
                    text,
                    flags=re.DOTALL,
                )
        clean_extras = {k: v for k, v in extras.items() if isinstance(v, (str, int, float, bool))}
        props_json = json.dumps(clean_extras).replace("</", "<\\/")
        props_script = (
            f'<script id="gtm-scene-props" type="application/json">{props_json}</script>\n'
        )
        if "</head>" in text:
            text = text.replace("</head>", f"{props_script}</head>", 1)
        elif "<body>" in text:
            text = text.replace("<body>", f"<body>\n{props_script}", 1)

    # Structured input goes in LAST so none of the substitutions above can reach it.
    return apply(text, payload) if payload is not None else text


def _dispatch_render(
    staged_html: Path,
    out_dir: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    ratio: str,
    total_frames: int,
    mock: bool = False,
) -> str:
    """Render composition to out_dir, returning runtime identifier."""
    if mock or os.environ.get("GTM_MOTION_RENDERER_MOCK") == "1":
        out_dir.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (width, height), color=(18, 18, 24))
        for i in range(total_frames):
            img.save(out_dir / f"frame-{i:04d}.png", "PNG")
        return "mock"

    sidecar_host = os.environ.get("GTM_MOTION_RENDERER_HOST")
    if sidecar_host:
        endpoint = f"http://{sidecar_host}/render"
        p_dict = {
            "stage_dir": str(staged_html.parent),
            "composition_path": str(staged_html),
            "out_dir": str(out_dir),
            "ratio": ratio,
            "width": width,
            "height": height,
            "fps": fps,
            "duration_s": duration_s,
            "total_frames": total_frames,
            "format": "png-sequence",
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(p_dict).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180.0) as resp:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- endpoint is configured via internal motion renderer setting
                data = json.loads(resp.read().decode("utf-8"))
                if not (data.get("ok") or data.get("status") == "ok"):
                    raise HtmlBackendError(f"Motion renderer error: {data.get('error')}")
                return "sidecar-http"
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HtmlBackendError(f"Failed to reach motion renderer at {endpoint}: {exc}") from exc

    # Local CLI or Docker execution
    hyperframes_bin = os.environ.get("GTM_HYPERFRAMES_BIN", "hyperframes")
    if shutil.which(hyperframes_bin):
        cmd = [
            hyperframes_bin,
            "render",
            str(staged_html),
            "--output",
            str(out_dir / "frames"),
            "--fps",
            str(fps),
            "--format",
            "png-sequence",
            "--quality",
            "delivery",
            "--no-browser-gpu",
            "--quiet",
        ]
        local_env = {
            **os.environ,
            "HYPERFRAMES_NO_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "HYPERFRAMES_NO_UPDATE_CHECK": "1",
            "HYPERFRAMES_NO_AUTO_INSTALL": "1",
        }
        res = subprocess.run(cmd, env=local_env, capture_output=True, text=True, check=False)  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args -- cmd is fixed token list, never shell=True
        if res.returncode != 0:
            raise HtmlBackendError(f"Local hyperframes render failed: {res.stderr or res.stdout}")
        return "local-cli"

    # Docker fallback
    if shutil.which("docker"):
        stage_dir = staged_html.parent
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory=1536m",
            "--shm-size=512m",
            "-e",
            "DO_NOT_TRACK=1",
            "-e",
            "HYPERFRAMES_NO_TELEMETRY=1",
            "-e",
            "HYPERFRAMES_NO_UPDATE_CHECK=1",
            "-e",
            "HYPERFRAMES_NO_AUTO_INSTALL=1",
            "-v",
            f"{stage_dir.resolve()}:/project:ro",
            "-v",
            f"{out_dir.resolve()}:/output",
            "gtm-motion-renderer:0.8.162",
            "npx",
            "hyperframes",
            "render",
            "/project",
            "--output",
            "/output/frames",
            "--fps",
            str(fps),
            "--format",
            "png-sequence",
            "--no-browser-gpu",
            "--quiet",
        ]
        res = subprocess.run(docker_cmd, capture_output=True, text=True, check=False)  # nosec B603
        if res.returncode != 0:
            raise HtmlBackendError(f"Docker motion render failed: {res.stderr or res.stdout}")
        return "docker"

    raise HtmlBackendError(
        "No motion renderer available. Configure GTM_MOTION_RENDERER_HOST or install hyperframes."
    )


def _normalise_frames(out_dir: Path, total_frames: int, width: int, height: int) -> None:
    """Ensure contiguous 0000..N-1 PNG sequence of RGB images matching dimensions."""
    frames_sub = out_dir / "frames"
    search_dir = frames_sub if frames_sub.is_dir() else out_dir
    raw_pngs = sorted(search_dir.glob("*.png"))
    if len(raw_pngs) < total_frames:
        raise HtmlBackendError(
            f"Expected {total_frames} frames, found {len(raw_pngs)} in {search_dir}"
        )

    for i in range(total_frames):
        src, dst = raw_pngs[i], out_dir / f"frame-{i:04d}.png"
        with Image.open(src) as img:
            if img.size != (width, height):
                raise HtmlBackendError(f"Frame {i} size {img.size} != expected ({width}, {height})")
            if img.mode != "RGB":
                img.convert("RGB").save(dst, "PNG")
            elif src != dst:
                shutil.copy2(src, dst)

    if frames_sub.is_dir() and frames_sub != out_dir:
        shutil.rmtree(frames_sub, ignore_errors=True)


def render_html_scene(  # noqa: C901, PLR0912  # PRD 2026-10-16-html-motion-scenes
    scene: str | None,
    *,
    out_dir: Path,
    kit: dict,
    ratio: str = "9:16",
    fps: int = 30,
    duration_s: float = 5.0,
    composition: Path | None = None,
    extras: dict[str, Any] | None = None,
    work_dir: Path | None = None,
    content_root: Path | None = None,
    preview_frame: bool = False,
    mock_renderer: bool = False,
) -> dict[str, Any]:
    """Execute HTML motion scene render end-to-end behind the concurrency lock."""
    require_html_enabled(scene)
    if ratio not in _RATIO_DIMS:
        raise HtmlBackendError(f"Unsupported ratio {ratio!r}, expected one of {_RATIO_DIMS.keys()}")
    width, height = _RATIO_DIMS[ratio]
    total_frames = int(round(fps * duration_s))
    if total_frames <= 0:
        raise HtmlBackendError(f"Invalid duration {duration_s}s (must yield >= 1 frame)")

    c_root = (content_root or resolve_content_root()).resolve()
    resolved_out = Path(out_dir).expanduser().resolve()
    if work_dir and resolved_out.is_relative_to(Path(work_dir).resolve().parent):
        c_root = Path(work_dir).resolve().parent
    try:
        resolved_out.relative_to(c_root)
    except ValueError as exc:
        raise HtmlBackendError(
            f"refusing to write outside the resolved content root: {resolved_out} (root: {c_root})"
        ) from exc

    if composition:
        comp_path = Path(composition).expanduser().resolve()
        try:
            comp_path.relative_to(c_root)
        except ValueError as exc:
            raise HtmlBackendError(
                f"refusing to read composition outside the resolved content root: {comp_path} (root: {c_root})"
            ) from exc

    timeout = 30.0 if preview_frame else 180.0
    src_template = _get_scene_source(scene, composition)
    raw_html = src_template.read_text(encoding="utf-8")

    # Run pre-render composition lint
    try:
        is_lib = bool(scene and scene in _HTML_SCENES)
        lint_composition(
            raw_html, kit=kit, ratio=ratio, fps=fps, duration_s=duration_s, is_library_scene=is_lib
        )
    except HtmlLintError as exc:
        raise HtmlBackendError(f"Pre-render lint failed: {exc}") from exc

    prepared_html = prepare_page(
        scene, raw_html, kit=kit, ratio=ratio, fps=fps, duration_s=duration_s, extras=extras,
        content_root=(content_root or resolve_content_root()).resolve(),
    )  # fmt: skip

    t0 = time.monotonic()
    safe_out_name = re.sub(r"[^\w\-.]", "_", out_dir.name)
    if safe_out_name in ("", ".", ".."):
        safe_out_name = "shot"
    parent_dir = (work_dir or out_dir.parent).resolve()
    stage_dir = parent_dir / ".stage" / safe_out_name
    stage_dir.mkdir(parents=True, exist_ok=True)

    with motion_render_lock(c_root, timeout_s=timeout):
        staged_html = stage_dir / "index.html"
        staged_html.write_text(prepared_html, encoding="utf-8")

        # Copy shared styling and scripts
        shared_dir = Path(__file__).resolve().parent / "html" / "_shared"
        staged_shared = stage_dir / "_shared"
        staged_shared.mkdir(parents=True, exist_ok=True)
        if shared_dir.is_dir():
            for f in shared_dir.glob("*"):
                if f.is_file():
                    shutil.copy2(f, staged_shared / f.name)

        # Copy any scene-local assets
        template_assets = src_template.parent / "assets"
        if template_assets.is_dir():
            staged_assets = stage_dir / "assets"
            staged_assets.mkdir(parents=True, exist_ok=True)
            for f in template_assets.glob("*"):
                if f.is_file():
                    shutil.copy2(f, staged_assets / f.name)

        # Copy image / stills extras into assets if passed
        if extras:
            staged_assets = stage_dir / "assets"
            staged_assets.mkdir(parents=True, exist_ok=True)
            if (
                "image" in extras
                and isinstance(extras["image"], Path)
                and extras["image"].is_file()
            ):
                shutil.copy2(extras["image"], staged_assets / extras["image"].name)
                shutil.copy2(extras["image"], staged_assets / "hero.png")
                shutil.copy2(extras["image"], staged_assets / "screen.png")

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for old_png in out_dir.glob("*.png"):
                old_png.unlink(missing_ok=True)

            runtime = _dispatch_render(
                staged_html,
                out_dir,
                width=width,
                height=height,
                fps=fps,
                duration_s=duration_s,
                ratio=ratio,
                total_frames=total_frames,
                mock=mock_renderer,
            )
            _normalise_frames(out_dir, total_frames, width, height)
        finally:
            if (
                stage_dir.is_dir()
                and ".stage" in stage_dir.parts
                and stage_dir.resolve() != parent_dir
            ):
                shutil.rmtree(stage_dir, ignore_errors=True)

    elapsed_s = time.monotonic() - t0
    midpoint_frame = out_dir / f"frame-{total_frames // 2:04d}.png"

    return {
        "frames": total_frames,
        "width": width,
        "height": height,
        "fps": fps,
        "duration_s": duration_s,
        "runtime": runtime,
        "elapsed_s": round(elapsed_s, 3),
        "frame_pattern": "frame-%04d.png",
        "preview_frame": midpoint_frame if midpoint_frame.is_file() else None,
        "backend": "html",
        "hyperframes_version": HYPERFRAMES_VERSION,
    }


def render_html_shot(
    shot: Any,
    out_dir: Path,
    *,
    kit: dict,
    ratio: str,
    fps: int,
    work_dir: Path,
    content_root: Path | None = None,
) -> dict[str, Any]:
    """Render an HTML shot and persist its screen-ui-build.json record."""
    from . import build_record

    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*.png"):
        p.unlink(missing_ok=True)
    (out_dir / "screen-ui-build.json").unlink(missing_ok=True)

    result = render_html_scene(
        shot.scene,
        composition=shot.composition,
        out_dir=out_dir,
        kit=kit,
        ratio=ratio,
        fps=fps,
        duration_s=shot.duration_s,
        extras=shot.extras,
        work_dir=work_dir,
        content_root=content_root,
    )
    comp_hash = build_record.hash_composition_dir(shot.composition) if shot.composition else None
    rec = build_record.build_record(
        scene=shot.scene or "custom",
        ratio=ratio,
        fps=fps,
        duration_s=shot.duration_s,
        extras=shot.extras,
        kit=kit,
        font_role="caption",
        repo_root=None,
        frames=result["frames"],
        frame_pattern=result["frame_pattern"],
        backend="html",
        composition_hash=comp_hash,
        hyperframes_version=result.get("hyperframes_version"),
        runtime=result.get("runtime"),
        elapsed_s=result.get("elapsed_s"),
    )
    build_record.write(out_dir, rec)
    return result
