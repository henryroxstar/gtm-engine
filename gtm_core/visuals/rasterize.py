"""Offline Playwright rasterizer for Composable Visual Engine (CVE).

Renders brand-locked artboards to high-DPI PNG or vector PDF with:
- Zero external egress (all network routes blocked except data:/about:)
- Advisory POSIX locking to serialize concurrent renders
- Content-addressable SHA256 cache marker bypass
- Audit logging appended to content/<profile>/history.jsonl
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from gtm_core.confine import confined_output_path
from gtm_core.ledgers import Ledgers
from gtm_core.paths import PathConfig
from gtm_core.visuals.brand_embed import BrandEmbedder
from gtm_core.visuals.compiler import LayoutCompiler
from gtm_core.visuals.lock import visuals_render_lock
from gtm_core.visuals.render_html import HTMLArtboardBuilder
from gtm_core.visuals.schema import CVESpec


def route_filter(route: Any, *, allow_typekit: bool = False) -> None:
    """Abort external requests, allowing local data:/about: and validated Typekit CSS/fonts when enabled."""
    url = getattr(getattr(route, "request", None), "url", "")
    if url.startswith("data:") or url.startswith("about:"):
        route.continue_()
        return
    offline = os.environ.get("GTM_OFFLINE_RENDER", "").strip().lower() in {"1", "true", "yes"}
    if (
        allow_typekit
        and not offline
        and (url.startswith("https://use.typekit.net/") or url.startswith("https://p.typekit.net/"))
    ):
        route.continue_()
        return
    route.abort()


def rasterize_cve_spec(
    spec: CVESpec,
    output_path: Path,
    content_root: Path,
    profiles_root: Path | None = None,
    device_scale_factor: int = 2,
    strict_brand: bool = False,
) -> dict[str, Any]:
    """Rasterize a CVESpec to PNG or PDF with zero network egress and content caching."""
    # Kill switch check
    if os.getenv("GTM_DETERMINISTIC_VISUALS_ENABLED", "true").lower() == "false":
        raise RuntimeError("GTM_DETERMINISTIC_VISUALS_ENABLED is false; visual rendering disabled.")

    output_path = Path(output_path)
    content_root = Path(content_root)

    # Determine global content root vs profile content root
    if content_root.name == spec.profile:
        global_content_root = content_root.parent
        profile_content_root = content_root
    else:
        global_content_root = content_root
        profile_content_root = content_root / spec.profile

    # Validate output path is confined within profile_content_root
    confined_output_path(output_path, content_root=profile_content_root)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile_content_root.mkdir(parents=True, exist_ok=True)

    embedder = BrandEmbedder(
        profile=spec.profile,
        profiles_root=profiles_root,
        strict_brand=strict_brand,
    )
    compiler = LayoutCompiler(spec)
    builder = HTMLArtboardBuilder(spec=spec, embedder=embedder)
    html_content = builder.build()
    html_digest = hashlib.sha256(html_content.encode("utf-8")).hexdigest()

    # Content-addressable cache check incorporating brand tokens, rendered HTML/assets, and device scale factor
    tokens_digest = hashlib.sha256(
        f"{json.dumps(embedder.config, sort_keys=True, default=str)}:html={html_digest}:dsf={device_scale_factor}".encode()
    ).hexdigest()
    cache_key = compiler.compute_cache_key(tokens_digest)

    cache_marker = output_path.with_suffix(output_path.suffix + ".cache")
    if output_path.is_file() and cache_marker.is_file():
        try:
            if cache_marker.read_text(encoding="utf-8").strip() == cache_key:
                existing_digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
                return {"path": str(output_path), "sha256": existing_digest, "cached": True}
        except OSError:
            pass

    width = builder.compiled["viewport"]["width"]
    height = builder.compiled["viewport"]["height"]

    # Global host lock to prevent concurrent Chromium instances from exceeding VPS memory limits
    with visuals_render_lock(global_content_root, timeout_s=60.0):
        # Re-check cache after acquiring lock in case another worker just rendered it
        if output_path.is_file() and cache_marker.is_file():
            try:
                if cache_marker.read_text(encoding="utf-8").strip() == cache_key:
                    existing_digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
                    return {"path": str(output_path), "sha256": existing_digest, "cached": True}
            except OSError:
                pass

        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"]
            )
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=device_scale_factor,
                )
                allow_tk = bool(embedder.get_typekit_link_html())
                context.route("**/*", lambda route: route_filter(route, allow_typekit=allow_tk))

                page = context.new_page()
                page.set_default_timeout(30000)
                page.set_content(html_content, wait_until="load", timeout=30000)
                page.evaluate("() => document.fonts.ready")

                if output_path.suffix.lower() == ".pdf":
                    page.pdf(
                        path=str(output_path),
                        print_background=True,
                        width=f"{width}px",
                        height=f"{height}px",
                    )
                else:
                    page.screenshot(path=str(output_path), full_page=True, timeout=30000)
            finally:
                browser.close()

        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        cache_marker.write_text(cache_key, encoding="utf-8")

        # Record in history.jsonl via hash-chained Ledgers
        event_entry = {
            "event": "visual_rendered",
            "tool": "gtm_core.visuals",
            "profile": spec.profile,
            "viewport": spec.viewport,
            "output_path": str(output_path),
            "sha256": digest,
        }
        cfg = PathConfig(
            content_root=global_content_root,
            profiles_root=profiles_root or global_content_root,
            default_profile=spec.profile,
        )
        Ledgers(cfg, spec.profile).append_history(event_entry)

    return {"path": str(output_path), "sha256": digest, "cached": False}
