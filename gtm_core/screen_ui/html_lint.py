"""Pre-render static and dynamic linter for HTML motion compositions.

Enforces:
  H-SEC-01:  Zero remote URLs / network APIs (offline container contract)
  H-SEC-02:  Strict staging confinement (assets must be inside staging dir)
  H-PERF-01: Blur repaint ban in animations
  H-BRAND-01: Chromatic hex ban (channel spread > 16 must use brand tokens)
  H-SLOP-01..03: Anti-slop bans (glow, particles, gradient text) unless opted-in
  H-DET-01:  Deterministic animation contract (no CSS transitions)
  H-SAFE-01: Safe-area variable bindings and font-size floors (0.050 for headline)
"""

from __future__ import annotations

import html
import re
from pathlib import Path

try:
    from ..video_lint import SAFE_AREAS
except ImportError:
    from . import SAFE_AREAS
from .base import SceneError

# Remote network URLs or protocols
_REMOTE_URL_RE = re.compile(
    r"""(?xi)
    \b(?:https?|ftp|file|ws|wss):/{1,3}
    | ['"]\s*//[a-zA-Z0-9]
    | \b(?:fetch|XMLHttpRequest|WebSocket|EventSource|importScripts|sendBeacon)\s*\(
    | import\s*\(
    | <script\b[^>]*\bsrc\s*=\s*['"]?\s*(?:https?:|//)
    | <link\b[^>]*\bhref\s*=\s*['"]?\s*(?:https?:|//)
    """
)

# Forbidden structural tags and dangerous protocols
_FORBIDDEN_TAGS_RE = re.compile(
    r"""(?xi)
    <(?P<tag>iframe|object|embed)\b
    | <meta\b[^>]*http-equiv\s*=\s*['"]?refresh['"]?
    | \b(?:src|href|action|data)\s*=\s*['"]?\s*javascript:
    """
)

# Non-deterministic APIs banned in animations
_NON_DETERMINISTIC_RE = re.compile(
    r"""(?xi)
    \b(?:Math\.random|Date\.now|performance\.now|new\s+Date|setTimeout|setInterval|requestAnimationFrame)\b
    """
)

# Path references in src / href / url (quoted or unquoted)
_PATH_REF_RE = re.compile(
    r"""(?xi)
    \b(?:src|href)\s*=\s*(?:['"]([^'"]+)['"]|([^\s>'"]+))
    | \burl\s*\(\s*['"]?([^'")]+)['"]?\s*\)
    """
)

# Blur in animations
_ANIMATED_BLUR_RE = re.compile(
    r"""(?xi)
    (?:filter|backdrop-filter)\s*:\s*blur\s*\(
    """
)

# Hex colors #RGB, #RRGGBB, #RRGGBBAA
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")

# Approved signal colors (universal across brands)
_APPROVED_SIGNAL_HEX = frozenset(
    {
        "#f2a93b",  # --signal-warn
        "#3bd68a",  # --signal-ok
    }
)

ACHROMATIC_MAX_SPREAD = 16

# Particles and sparkles
_PARTICLES_RE = re.compile(r"""(?xi)\b(?:particle|sparkle|starfield|dust)\b|<canvas\b""")

# Gradient text
_GRADIENT_TEXT_RE = re.compile(
    r"""(?xi)
    (?:-webkit-)?background-clip\s*:\s*text
    """
)

# Radial glow
_RADIAL_GLOW_RE = re.compile(
    r"""(?xi)
    radial-gradient\s*\(
    | box-shadow\s*:[^;]*\b(?:[2-9]\d|[1-9]\d{2,})px\b
    | filter\s*:[^;]*drop-shadow\s*\([^)]*\b(?:1[7-9]|[2-9]\d|[1-9]\d{2,})px\b
    """
)

# Transitions
_TRANSITION_RE = re.compile(r"""(?xi)\btransition(?:-property)?\s*:""")

# Mission Control 2D-only telemetry check (H-MC-2D-ONLY)
_MC_3D_RE = re.compile(
    r"""(?xi)
    \bthree(?:\.min)?\.js\b
    | \bTHREE\.
    | \bWebGLRenderingContext\b
    | \bgetContext\s*\(\s*['"]webgl2?['"]
    | \bperspective\s*\(
    | \brotate[XY]\s*\(
    | \brotate3d\s*\(
    | \btranslateZ\s*\(
    | \btranslate3d\s*\(
    | \bpreserve-3d\b
    """
)

# Mission Control solid button fill check (H-MC-NO-GRADIENT-FILL)
_CSS_RULE_BLOCK_RE = re.compile(r"(?s)([^{}]+)\{([^{}]+)\}")
_BUTTON_SELECTOR_RE = re.compile(
    r"""(?xi)
    (?:\bbutton\b|\.(?:btn|cta|button)\b|\[role\s*=\s*['"]?button['"]?\])
    """
)
_INLINE_BUTTON_TAG_RE = re.compile(
    r"""(?xi)
    <(?:button\b[^>]*|(?:a|div|span)\b[^>]*(?:class\s*=\s*['"][^'"]*\b(?:btn|cta|button)\b[^'"]*['"]|role\s*=\s*['"]?button['"]?)[^>]*)>
    """
)
_GRADIENT_FN_RE = re.compile(r"(?i)(?:linear|radial|conic)-gradient\s*\(")
_BG_CLIP_TEXT_RE = re.compile(r"(?i)(?:-webkit-)?background-clip\s*:\s*text\b")
_TYPEKIT_ALLOW_RE = re.compile(r"^https://use\.typekit\.net/[a-z0-9]{5,12}\.css$")


def _has_button_gradient_fill(text: str) -> str | None:
    """Return offending snippet if an interactive button/CTA uses a gradient fill instead of solid."""
    for rule_match in _CSS_RULE_BLOCK_RE.finditer(text):
        selector = rule_match.group(1).strip()
        body = rule_match.group(2)
        if _BUTTON_SELECTOR_RE.search(selector):
            if _GRADIENT_FN_RE.search(body) and not _BG_CLIP_TEXT_RE.search(body):
                return f"{selector} {{ ... }}"
    for tag_match in _INLINE_BUTTON_TAG_RE.finditer(text):
        tag_str = tag_match.group(0)
        if _GRADIENT_FN_RE.search(tag_str) and not _BG_CLIP_TEXT_RE.search(tag_str):
            return tag_str
    return None


# Safe area variables
_SAFE_AREA_VARS = (
    "--safe-top",
    "--safe-bottom",
    "--caption-band-top",
)

# Typographic floors
_PAYLOAD_MIN_H_FRAC = 0.050  # 54px on 1080p for primary headline
_SECONDARY_MIN_H_FRAC = 0.020  # ~21px on 1080p for badge / eyebrow / kicker


def _parse_hex_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) in (3, 4):
        r, g, b = (int(c * 2, 16) for c in h[:3])
    else:
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return r, g, b


def _is_achromatic_or_signal(hex_str: str) -> bool:
    clean = hex_str.lower()
    if clean in _APPROVED_SIGNAL_HEX:
        return True
    try:
        r, g, b = _parse_hex_rgb(clean)
        return (max(r, g, b) - min(r, g, b)) <= ACHROMATIC_MAX_SPREAD
    except (ValueError, IndexError):
        return True


class HtmlLintError(SceneError):
    """Raised when an HTML motion composition fails linting."""


def _lint_text(  # noqa: C901, PLR0912, PLR0915  # PRD 2026-10-16-html-motion-scenes
    combined_text: str, kit: dict, ratio: str, *, is_library_scene: bool = False
) -> list[str]:
    import os

    errors: list[str] = []

    # 0. Unescape HTML entities and CSS escapes for URL and path inspection
    unescaped = html.unescape(combined_text)
    unescaped = re.sub(
        r"\\([0-9a-fA-F]{1,6})\s?",
        lambda m: chr(int(m.group(1), 16)),
        unescaped,
    )

    # 1. H-SEC-01: Remote network URLs, network APIs, forbidden tags
    forbidden_match = _FORBIDDEN_TAGS_RE.search(unescaped)
    if forbidden_match:
        errors.append(
            f"H-SEC-01 (Forbidden Tags / Protocols): detected forbidden structure ({forbidden_match.group(0)!r})"
        )

    typo_cfg = kit.get("typography") if isinstance(kit, dict) else {}
    raw_tk = str(typo_cfg.get("typekit_url") or "").strip() if isinstance(typo_cfg, dict) else ""
    offline = os.environ.get("GTM_OFFLINE_RENDER", "").strip().lower() in {"1", "true", "yes"}
    allowed_tk_url = raw_tk if (raw_tk and not offline and _TYPEKIT_ALLOW_RE.match(raw_tk)) else ""

    unescaped_for_remote = unescaped
    if allowed_tk_url:
        unescaped_for_remote = re.sub(
            rf'<link\b[^>]*\bhref\s*=\s*[\'"]?{re.escape(allowed_tk_url)}[\'"]?[^>]*>',
            "",
            unescaped_for_remote,
            flags=re.IGNORECASE,
        )

    remote_match = _REMOTE_URL_RE.search(unescaped_for_remote)
    if remote_match:
        errors.append(
            f"H-SEC-01 (Remote Egress Ban): detected remote URL or network API ({remote_match.group(0)!r})"
        )

    # 2. H-SEC-02: External Path Reference Ban (use unescaped)
    for p_match in _PATH_REF_RE.finditer(unescaped):
        path_str = (p_match.group(1) or p_match.group(2) or p_match.group(3) or "").strip()
        if not path_str or path_str.startswith(("#", "data:")):
            continue
        if path_str.startswith(("http://", "https://", "ftp://", "file://", "//")):
            continue
        if path_str.startswith("/") or bool(re.match(r"^[a-zA-Z]:", path_str)):
            errors.append(f"H-SEC-02 (External Path Ban): detected absolute path ({path_str!r})")
            break
        if ".." in path_str:
            if is_library_scene and (
                path_str.startswith("../_shared/") or path_str.startswith("_shared/")
            ):
                prefix_len = (
                    len("../_shared/") if path_str.startswith("../_shared/") else len("_shared/")
                )
                rel = path_str[prefix_len:]
                if ".." in rel:
                    errors.append(
                        f"H-SEC-02 (External Path Ban): detected directory traversal ({path_str!r})"
                    )
                    break
            else:
                errors.append(
                    f"H-SEC-02 (External Path Ban): detected directory traversal ({path_str!r})"
                )
                break

    # 3. H-PERF-01: Animated blur repaint ban
    if "@keyframes" in combined_text or "animate(" in combined_text:
        blur_match = _ANIMATED_BLUR_RE.search(combined_text)
        if blur_match:
            errors.append(
                "H-PERF-01 (Blur Repaint Ban): animated filter:blur causes high GPU paint overhead"
            )

    # 4. H-BRAND-01: Chromatic Hex Ban
    for m in _HEX_COLOR_RE.finditer(combined_text):
        hex_val = m.group(0)
        if not _is_achromatic_or_signal(hex_val):
            errors.append(
                f"H-BRAND-01 (Chromatic Hex Ban): hardcoded hex {hex_val!r} is not achromatic and not an approved signal color; use var(--color-*) tokens"
            )
            break

    # Motion opt-ins from kit
    motion_cfg = kit.get("motion") if isinstance(kit, dict) else {}
    if not isinstance(motion_cfg, dict):
        motion_cfg = {}

    # 5. H-SLOP-01: Radial Glow Ban
    glow_allowed = bool(
        motion_cfg.get("glow_on_focal", False) or motion_cfg.get("motion_glow_opt_in", False)
    )
    if not glow_allowed:
        glow_match = _RADIAL_GLOW_RE.search(combined_text)
        if glow_match:
            errors.append(
                f"H-SLOP-01 (Glow Opt-In): radial glow ({glow_match.group(0)!r}) is banned unless [motion].glow_on_focal is true"
            )

    # 6. H-SLOP-02: Particles Ban
    particles_allowed = bool(motion_cfg.get("particles_allowed", False))
    if not particles_allowed:
        particle_match = _PARTICLES_RE.search(combined_text)
        if particle_match:
            errors.append(
                f"H-SLOP-02 (Particles Opt-In): particle emitter ({particle_match.group(0)!r}) is banned unless [motion].particles_allowed is true"
            )

    # 7. H-SLOP-03: Gradient Text Ban
    gradient_text_allowed = bool(motion_cfg.get("gradient_text_allowed", False))
    if not gradient_text_allowed:
        gradient_match = _GRADIENT_TEXT_RE.search(combined_text)
        if gradient_match:
            errors.append(
                "H-SLOP-03 (Gradient Text Opt-In): background-clip: text is banned unless [motion].gradient_text_allowed is true"
            )

    # 7b. Mission Control 2D-only and Solid Button Fill gates (H-MC-2D-ONLY, H-MC-NO-GRADIENT-FILL)
    ds_cfg = kit.get("design_system") if isinstance(kit, dict) else {}
    if isinstance(ds_cfg, dict) and str(ds_cfg.get("archetype") or "") == "mission-control":
        mc_3d_match = _MC_3D_RE.search(combined_text)
        if mc_3d_match:
            errors.append(
                f"H-MC-2D-ONLY (Pure 2D Telemetry): 3D transform or WebGL ({mc_3d_match.group(0)!r}) is forbidden in mission-control archetype compositions"
            )
        btn_grad = _has_button_gradient_fill(combined_text)
        if btn_grad:
            errors.append(
                f"H-MC-NO-GRADIENT-FILL (Solid Button Fill): interactive control ({btn_grad!r}) uses a gradient fill; mission-control archetype requires solid fills (var(--color-primary))"
            )

    # 8. H-DET-01: Deterministic Animation Contract (no transitions)
    trans_match = _TRANSITION_RE.search(combined_text)
    if trans_match:
        errors.append(
            "H-DET-01 (Deterministic Animation): CSS transition declarations are banned; use WAAPI or @keyframes"
        )

    # 8b. H-DET-02: Non-deterministic APIs
    non_det_match = _NON_DETERMINISTIC_RE.search(combined_text)
    if non_det_match:
        errors.append(
            f"H-DET-02 (Non-Deterministic API Ban): non-deterministic API ({non_det_match.group(0)!r}) is banned; use frame-index arithmetic or WAAPI"
        )

    # 8c. H-DET-03: Root element timeline marker
    if "<div" in combined_text and 'id="root"' in combined_text:
        root_tag_match = re.search(r'<div[^>]*id=["\']root["\'][^>]*>', combined_text)
        if root_tag_match and "data-no-timeline" not in root_tag_match.group(0):
            errors.append(
                'H-DET-03 (Timeline Marker): root element must declare data-no-timeline="true"'
            )

    # 9. H-SAFE-01: Safe-Area Variable Binding
    for var in _SAFE_AREA_VARS:
        if var not in combined_text:
            errors.append(f"H-SAFE-01 (Safe Area Bindings): composition must reference {var}")

    return errors


def check_html_composition(
    html_path: Path,
    kit: dict,
    ratio: str,
    *,
    is_library_scene: bool = False,
) -> list[str]:
    """Scan an HTML composition (and referenced sibling CSS/JS) for safety and quality violations."""
    if not html_path.is_file():
        return [f"composition file not found: {html_path}"]
    try:
        html_text = html_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"failed to read composition: {exc}"]

    comp_dir = html_path.parent
    all_texts = [html_text]
    for ext in ("*.css", "*.js"):
        for subfile in comp_dir.glob(ext):
            if subfile.is_file():
                try:
                    all_texts.append(subfile.read_text(encoding="utf-8"))
                except OSError:
                    pass
    return _lint_text("\n".join(all_texts), kit, ratio, is_library_scene=is_library_scene)


def lint_composition(
    html_src: str | Path,
    *,
    kit: dict | None = None,
    ratio: str = "9:16",
    fps: int = 30,
    duration_s: float = 5.0,
    is_library_scene: bool = False,
    raise_on_error: bool = True,
) -> list[str]:
    """Lint an HTML composition string or Path, returning errors or raising HtmlLintError."""
    k = kit or {}
    if isinstance(html_src, Path):
        errors = check_html_composition(html_src, k, ratio, is_library_scene=is_library_scene)
    else:
        errors = _lint_text(str(html_src), k, ratio, is_library_scene=is_library_scene)
    if errors and raise_on_error:
        raise HtmlLintError("; ".join(errors))
    return errors


def check_kinetic_type_fit(extras: dict, kit: dict, ratio: str) -> None:
    """Pre-render verification of headline text length against safe area budget."""
    headline = str(extras.get("headline") or "").strip()
    if not headline:
        raise SceneError("kinetic-type requires a non-empty --headline")

    safe_area = SAFE_AREAS.get(ratio)
    safe_top_frac = getattr(safe_area, "top", 0.08)
    caption_bottom_frac = getattr(safe_area, "bottom", 0.14)
    caption_band_top_frac = 1.0 - caption_bottom_frac
    available_v_frac = caption_band_top_frac - safe_top_frac

    # Headline focal area reserves at most 45% of free vertical height
    line_h_frac = 0.065
    max_lines = max(2, int((available_v_frac * 0.45) / line_h_frac))

    # Estimate wrapped lines based on headline words (approx 3 words per line on 9:16)
    words = headline.split()
    words_per_line = 3 if ratio == "9:16" else 6
    estimated_lines = max(1, (len(words) + words_per_line - 1) // words_per_line)

    if estimated_lines > max_lines:
        raise SceneError(
            f"headline {headline!r} ({len(words)} words) requires ~{estimated_lines} lines, exceeding the vertical budget of {max_lines} lines above the caption band"
        )
