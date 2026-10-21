"""Python driver and CLI for Mission Control video rendering."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404 — node render.mjs orchestration, arg list only, never shell=True
import sys
from pathlib import Path
from typing import Any

from ...confine import ConfinementError
from ...paths import _safe_segment, resolve_content_root
from ...video_finish.mux import frames_to_video
from ..html_backend import motion_render_lock
from .validate import validate_script

ROOT = Path(__file__).resolve().parents[3]
RENDER_MJS = Path(__file__).resolve().parent / "render.mjs"


def render_mc_video(  # noqa: C901, PLR0912, PLR0915  # PRD 2026-10-16-html-motion-scenes
    script_path: str | Path,
    out_dir: str | Path,
    *,
    profile: str = "_template",
    product: str | None = None,
    scale: int = 1,
    stills: list[float] | None = None,
    poster_time: float | None = None,
    preview_only: bool = False,
    mock: bool = False,
    keep_frames: bool = False,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> dict[str, Any]:
    """Execute Mission Control video rendering end-to-end.

    Returns dict with generated asset paths:
        {"mp4": Path | None, "preview": Path, "stills": list[Path], "poster": Path}
    """
    safe_profile = _safe_segment(profile, "profile")
    safe_product = _safe_segment(product, "product") if product else None

    s_path = Path(script_path).resolve()
    if not s_path.is_file():
        raise FileNotFoundError(f"Script file not found: {s_path}")

    with open(s_path, encoding="utf-8") as f:
        script_data = json.load(f)

    # Validate before spending render time
    v_root = profiles_root.parent if profiles_root is not None else ROOT
    is_valid, errors, warnings, total_duration, _ = validate_script(
        script_data, profile=safe_profile, repo_root=v_root
    )
    for w in warnings:
        sys.stderr.write(f"⚠ [mc-video validate] {w}\n")
    if not is_valid:
        for e in errors:
            sys.stderr.write(f"✖ [mc-video validate] {e}\n")
        raise ValueError(f"Script validation failed with {len(errors)} error(s)")

    target_out = Path(out_dir).resolve()
    c_root = (content_root or resolve_content_root()).resolve()

    # Never allow writing inside repo source/profiles/hooks
    repo_forbidden = [
        ROOT / "profiles",
        ROOT / "gtm_core",
        ROOT / ".git",
        ROOT / "plugin",
        ROOT / "tests",
        ROOT / "docs",
        ROOT / "docker",
    ]
    for forbidden in repo_forbidden:
        try:
            target_out.relative_to(forbidden.resolve())
            raise ConfinementError(
                f"Refusing to write output inside protected repository directory: {target_out}"
            )
        except ValueError:
            pass

    # Confinement check: output must be inside content_root or system tmp
    is_confined = False
    try:
        target_out.relative_to(c_root)
        is_confined = True
    except ValueError:
        temp_prefixes = ("/tmp", "/private/tmp", "/var/folders", "/private/var")  # nosec B108
        if any(str(target_out).startswith(prefix) for prefix in temp_prefixes):
            is_confined = True

    if not is_confined:
        raise ConfinementError(f"Output directory {target_out} escapes content root {c_root}")

    target_out.mkdir(parents=True, exist_ok=True)

    # Pre-compile preview tokens for the exact profile + product
    preview_dir = target_out / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    typekit_url = ""
    try:
        import os
        import re as _re

        from gtm_core.brandkit import load_brand_kit
        from gtm_core.brandkit_css import render_tokens_css

        p_root = profiles_root if profiles_root is not None else (ROOT / "profiles")
        kit = load_brand_kit(p_root, safe_profile, product=safe_product)
        (preview_dir / "tokens.css").write_text(render_tokens_css(kit), encoding="utf-8")
        offline = os.environ.get("GTM_OFFLINE_RENDER", "").strip().lower() in {"1", "true", "yes"}
        if not offline:
            raw_tk = str(kit.get("typography", {}).get("typekit_url") or "").strip()
            if raw_tk and _re.match(r"^https://use\.typekit\.net/[a-z0-9]{5,12}\.css$", raw_tk):
                typekit_url = raw_tk
    except Exception as exc:
        sys.stderr.write(
            f"⚠ [mc-video] Failed to compile preview tokens for {safe_profile}/{safe_product}: {exc}\n"
        )

    script_name = (script_data.get("name") or s_path.stem).replace(" ", "-")
    fps = int(script_data.get("fps", 25))

    temp_frames_dir = target_out / ".frames_scratch"
    temp_frames_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "node",
        str(RENDER_MJS),
        str(s_path),
        "--out",
        str(target_out),
        "--scale",
        str(scale),
        "--profile",
        profile,
        "--frames-dir",
        str(temp_frames_dir),
    ]
    if safe_product:
        cmd.extend(["--product", safe_product])
    if typekit_url:
        cmd.extend(["--typekit-url", typekit_url])

    if preview_only:
        cmd.append("--preview-only")
    if mock:
        cmd.append("--mock")
    if stills:
        cmd.extend(["--stills", ",".join(str(t) for t in stills)])
    if poster_time is not None:
        cmd.extend(["--poster", str(poster_time)])

    with motion_render_lock(c_root):
        proc = subprocess.Popen(  # nosec B603 — arg list, never shell=True
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Stream stderr line by line
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()

        proc.wait()
        if proc.returncode != 0:
            out = proc.stdout.read()
            raise RuntimeError(f"render.mjs failed with exit code {proc.returncode}: {out}")

    preview_html = target_out / "preview/index.html"
    poster_png = target_out / "poster.png"
    stills_dir = target_out / "stills"

    stills_paths = (
        sorted(stills_dir.glob("still-*.png"))
        if stills_dir.exists() and list(stills_dir.glob("still-*.png"))
        else [stills_dir / f"still-{i + 1:02d}.png" for i in range(len(stills) if stills else 4)]
    )

    mp4_path = None
    try:
        if not preview_only:
            frames_glob = str(temp_frames_dir / "frame-%04d.png")
            mp4_path = target_out / f"{script_name}.mp4"
            target_res = (1920, 1080) if scale > 1 else None
            frames_to_video(
                frames_glob,
                fps=fps,
                out_path=mp4_path,
                silent_audio=True,
                target_resolution=target_res,
            )
            sys.stderr.write(f"✓ [mc-video] Output video: {mp4_path}\n")
    finally:
        if not keep_frames and temp_frames_dir.is_dir():
            shutil.rmtree(temp_frames_dir, ignore_errors=True)

    return {
        "mp4": mp4_path,
        "preview": preview_html,
        "stills": stills_paths,
        "poster": poster_png,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a Mission Control video script.")
    parser.add_argument("script", help="Path to script JSON file.")
    parser.add_argument("--out", required=True, help="Output directory.")
    parser.add_argument(
        "--scale", type=int, default=1, choices=[1, 2], help="Supersampling scale (1=1080p, 2=4K)."
    )
    parser.add_argument(
        "--profile", default="_template", help="Profile to bind assets and tokens from."
    )
    parser.add_argument("--product", default=None, help="Product slug.")
    parser.add_argument("--stills", help="Comma-separated timestamps for stills.")
    parser.add_argument("--poster", type=float, help="Poster frame timestamp.")
    parser.add_argument(
        "--preview-only", action="store_true", help="Generate preview and stills only."
    )
    parser.add_argument("--mock", action="store_true", help="Use mock renderer.")
    parser.add_argument("--keep-frames", action="store_true", help="Keep raw rendered frame PNGs.")
    args = parser.parse_args()

    stills_list = [float(s) for s in args.stills.split(",")] if args.stills else None

    try:
        res = render_mc_video(
            args.script,
            args.out,
            profile=args.profile,
            product=args.product,
            scale=args.scale,
            stills=stills_list,
            poster_time=args.poster,
            preview_only=args.preview_only,
            mock=args.mock,
            keep_frames=args.keep_frames,
        )
        if res["mp4"]:
            print(f"✓ Rendered video: {res['mp4']}")
        print(f"✓ Preview: {res['preview']}")
    except Exception as exc:
        sys.stderr.write(f"✖ Render failed: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
