from __future__ import annotations

# ── production.screen_ui — deterministic mockup frames or HTML motion scenes ──────────────────
#
# A shot whose picture is a deterministic screen mockup rather than a diffusion video model render
# carries `production.screen_ui`. Consumed by `gtm_core.screen_ui.build` (video-finish Step 1.7)
# and validated here at script/plan time.
#
# Shape:
#   backend?: "pillow" | "html"
#   scene?: str (name in gtm_core.screen_ui.registry)
#   composition?: str (relative path to a project-confined .html composition)
#   headline?: str (<= 120 chars)
#   subline?: str (<= 180 chars)
#   eyebrow?: str (<= 60 chars)
#   badge?: str (<= 40 chars)
#   kicker?: str (<= 60 chars)
#   duration_s?: float > 0
#   image? / stills? / actions?: paths relative to shots.json

#: Allowed backends for production.screen_ui
_ALLOWED_BACKENDS = frozenset({"pillow", "html"})

#: Ceilings on string-copy extras
_STRING_EXTRA_CEILINGS: dict[str, int] = {
    "headline": 120,
    "subline": 180,
    "eyebrow": 60,
    "badge": 40,
    "kicker": 60,
}


def _registered_scenes() -> frozenset[str] | None:
    """Every scene name the registry serves (Pillow, mc-* aliases, HTML); None if unavailable."""
    try:
        from ..screen_ui.registry import _HTML_SCENES, _SCENES
    except ImportError:
        return None
    return frozenset(_SCENES) | frozenset(_HTML_SCENES)


def _lint_screen_ui(shot: dict, prefix: str, errors: list[str]) -> None:  # noqa: C901, PLR0912  # PRD 2026-10-16-html-motion-scenes
    """Validate production.screen_ui shape, backend, mutual exclusivity, and extras."""
    production = shot.get("production")
    if not isinstance(production, dict) or "screen_ui" not in production:
        return

    spec = production.get("screen_ui")
    if not isinstance(spec, dict):
        errors.append(f"{prefix}.production.screen_ui must be an object")
        return

    backend = spec.get("backend")
    if backend is not None and backend not in _ALLOWED_BACKENDS:
        errors.append(
            f"{prefix}.production.screen_ui.backend must be 'pillow' or 'html', got {backend!r}"
        )

    has_scene = "scene" in spec and spec["scene"] is not None
    has_composition = "composition" in spec and spec["composition"] is not None

    if has_scene and has_composition:
        errors.append(
            f"{prefix}.production.screen_ui specifies both 'scene' and 'composition' — choose exactly one"
        )
    elif not has_scene and not has_composition:
        errors.append(
            f"{prefix}.production.screen_ui specifies neither 'scene' nor 'composition' — exactly one is required"
        )

    if has_composition:
        comp = spec.get("composition")
        if not isinstance(comp, str) or not comp.strip():
            errors.append(
                f"{prefix}.production.screen_ui.composition must be a non-empty string path"
            )
        else:
            comp_clean = comp.strip()
            if not comp_clean.endswith(".html"):
                errors.append(
                    f"{prefix}.production.screen_ui.composition must be an .html file, got {comp!r}"
                )
            if ".." in comp_clean.replace("\\", "/").split("/"):
                errors.append(
                    f"{prefix}.production.screen_ui.composition path traversal ('..') is refused"
                )
            if comp_clean.startswith("/") or comp_clean.startswith("\\"):
                errors.append(
                    f"{prefix}.production.screen_ui.composition must be a relative path, got {comp!r}"
                )
            if backend == "pillow":
                errors.append(
                    f"{prefix}.production.screen_ui.composition requires backend: 'html' (cannot use 'pillow')"
                )

    if has_scene:
        scene = spec.get("scene")
        if not isinstance(scene, str) or not scene.strip():
            errors.append(f"{prefix}.production.screen_ui.scene must be a non-empty string")
        elif (known := _registered_scenes()) is not None and scene not in known:
            errors.append(
                f"{prefix}.production.screen_ui.scene {scene!r} is not a registered scene"
            )
        elif scene == "hero-reveal" and ("image" not in spec or not spec["image"]):
            errors.append(
                f"{prefix}.production.screen_ui.image is required for scene 'hero-reveal'"
            )
        elif scene == "kinetic-type" and ("headline" not in spec or not spec["headline"]):
            errors.append(
                f"{prefix}.production.screen_ui.headline is required for scene 'kinetic-type'"
            )

    for key, max_chars in _STRING_EXTRA_CEILINGS.items():
        if key in spec:
            val = spec[key]
            if not isinstance(val, str):
                errors.append(f"{prefix}.production.screen_ui.{key} must be a string")
            elif len(val) > max_chars:
                errors.append(
                    f"{prefix}.production.screen_ui.{key} length ({len(val)}) exceeds ceiling of {max_chars} chars"
                )

    if "duration_s" in spec:
        dur = spec["duration_s"]
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0:
            errors.append(f"{prefix}.production.screen_ui.duration_s must be a positive number")
