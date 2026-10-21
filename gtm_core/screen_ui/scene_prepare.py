"""Building the page an HTML library scene renders, and refusing bad input before it is rendered.

Kept out of ``html_backend`` (which is at its §R10 length ceiling) and out of ``build``. Both import
this module; this module reaches back into ``html_backend`` only inside functions, so the import
order is never circular.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .registry import _HTML_SCENE_PREPARE, html_scene_is_external
from .scene_payload import check_prepared


def require_html_enabled(scene: str | None) -> None:
    """HTML-only scenes have no Pillow twin to fall back to, so the kill switch refuses them on
    every entry path (CLI, build driver, backend) rather than only in the build driver."""
    if html_scene_is_external(scene) and os.environ.get("GTM_SCREEN_UI_HTML_ENABLED", "1") == "0":
        from .html_backend import HtmlBackendError

        raise HtmlBackendError(
            f"scene {scene!r} renders through the HTML backend, which is disabled via "
            "GTM_SCREEN_UI_HTML_ENABLED=0"
        )


def prepare_page(
    scene: str | None,
    raw_html: str,
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    extras: dict[str, Any] | None,
    content_root: Path,
) -> str:
    """The page the renderer loads: tokens, safe areas, string copy and, for a scene with a
    ``prepare`` hook, its structured payload, which then gets the lint the raw template cannot."""
    from .html_backend import _RATIO_DIMS, _prepare_wrapper

    width, height = _RATIO_DIMS[ratio]
    prepare = _HTML_SCENE_PREPARE.get(scene or "")
    payload = (
        prepare(extras or {}, kit, content_root=content_root, ratio=ratio) if prepare else None
    )
    page = _prepare_wrapper(
        raw_html,
        kit=kit,
        width=width,
        height=height,
        fps=fps,
        duration_s=duration_s,
        extras=extras,
        ratio=ratio,
        payload=payload,
    )
    if payload is not None:
        check_prepared(page, kit=kit, ratio=ratio, fps=fps, duration_s=duration_s)
    return page


def preflight_shots(
    shots: list[Any], *, kit: dict, ratio: str, fps: int, content_root: Path
) -> None:
    """Prepare, never render, every HTML shot whose scene has a prepare hook, so a malformed
    diagram, an unknown build id or a denied font is refused before the first shot spends.

    Raises ``SceneError`` naming the shot.
    """
    from .base import SceneError
    from .html_backend import _get_scene_source

    for shot in shots:
        if getattr(shot, "backend", None) != "html" or shot.scene not in _HTML_SCENE_PREPARE:
            continue
        raw = _get_scene_source(shot.scene, None).read_text(encoding="utf-8")
        try:
            prepare_page(
                shot.scene,
                raw,
                kit=kit,
                ratio=ratio,
                fps=fps,
                duration_s=shot.duration_s,
                extras=shot.extras,
                content_root=content_root,
            )
        except SceneError as exc:
            raise SceneError(f"shot {shot.id}: {exc}") from exc
