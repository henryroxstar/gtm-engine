"""Deterministic CSS custom-properties compiler for resolved brand kits."""

from __future__ import annotations

import re
from typing import Any

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_RGBA_COLOR_RE = re.compile(
    r"^rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*(?:0(?:\.\d+)?|1(?:\.0+)?|\.\d+)\s*)?\)$"
)


def _clean_hex(val: Any, default: str) -> str:
    """Return a validated hex color string, falling back if invalid or containing CSS syntax delimiters."""
    if not isinstance(val, str):
        return default
    s = val.strip().lower()
    if not s or not _HEX_COLOR_RE.match(s):
        return default
    return s


def _clean_rgba(val: Any, default: str) -> str:
    """Return a validated rgba color string, falling back if invalid or containing CSS syntax delimiters."""
    if not isinstance(val, str):
        return default
    s = val.strip()
    if not s or not _RGBA_COLOR_RE.match(s):
        return default
    return s


def _hex_rgb_tuple(
    hex_str: str, fallback: tuple[int, int, int] = (255, 255, 255)
) -> tuple[int, int, int]:
    """Parse a ``#RRGGBB`` or ``#RGB`` string into an ``(r, g, b)`` tuple."""
    clean = str(hex_str or "").strip().lstrip("#")
    if len(clean) == 3 and all(c in "0123456789abcdefABCDEF" for c in clean):
        clean = "".join(c * 2 for c in clean)
    if len(clean) >= 6 and all(c in "0123456789abcdefABCDEF" for c in clean[:6]):
        return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))
    return fallback


def _css_font_token(name: str) -> str:
    clean = str(name or "").strip()
    clean = re.sub(r"[;{}@\r\n]", "", clean).strip().strip("'\"")
    if not clean or "url(" in clean.lower():
        return "sans-serif"
    if " " in clean:
        return f"'{clean}'"
    return clean


def _format_font_stack(
    primary_face: str,
    stack: list[str],
    default_tail: list[str],
    *,
    lower_first: bool = False,
) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    clean_primary = str(primary_face or "").strip().strip("'\"")
    if clean_primary and not clean_primary.startswith("<"):
        if lower_first and " " not in clean_primary:
            low = clean_primary.lower()
            tokens.append(low)
            seen.add(low)
        tok = _css_font_token(clean_primary)
        if tok not in seen:
            tokens.append(tok)
            seen.add(tok)
    for item in list(stack) + list(default_tail):
        clean_item = str(item or "").strip().strip("'\"")
        if not clean_item or clean_item.startswith("<"):
            continue
        tok = _css_font_token(clean_item)
        if tok not in seen and tok.lower() not in seen:
            tokens.append(tok)
            seen.add(tok)
    return ", ".join(tokens) if tokens else "system-ui, sans-serif"


def render_tokens_css(kit: dict[str, Any], *, width: int = 1080, height: int = 1920) -> str:
    """Compile a deterministic ``:root`` CSS custom-properties sheet from a resolved brand kit.

    Combines the tenant's ``[palette]``, ``[palette.status]``, ``[typography]``, ``[motion]``,
    and ``[layout]`` declarations with the engine's structural token scale so HTML/CDP motion
    scenes and Mission Control artboards resolve every ``--surface-*``, ``--color-*``,
    ``--text-*``, ``--font-*``, and ``--video-*`` custom property from the active tenant's
    brand kit without cross-tenant bleed.
    """
    from .design_tokens import (
        contrasting_text_color,
        css_spring_linear,
        resolve_layout,
        resolve_motion,
    )

    pal = kit.get("palette") if isinstance(kit.get("palette"), dict) else {}
    status = pal.get("status") if isinstance(pal.get("status"), dict) else {}
    families = pal.get("families") if isinstance(pal.get("families"), dict) else {}
    typo = kit.get("typography") if isinstance(kit.get("typography"), dict) else {}
    motion = resolve_motion(kit)
    layout = resolve_layout(kit)

    canvas = _clean_hex(pal.get("canvas"), "#000000")
    surface = _clean_hex(pal.get("surface"), "#111111")
    ink = _clean_hex(pal.get("ink") or pal.get("text"), "#ffffff")
    ink_muted = _clean_hex(
        pal.get("ink_muted") or pal.get("text_dim") or pal.get("secondary"), "#888888"
    )
    ink_subtle = _clean_hex(pal.get("ink_subtle") or pal.get("tertiary"), ink_muted)
    primary = _clean_hex(pal.get("primary"), "#ffffff")
    accent = _clean_hex(pal.get("accent"), primary)
    secondary = _clean_hex(pal.get("secondary"), ink_muted)
    rule = _clean_hex(pal.get("rule"), "#333333")

    computed_top = contrasting_text_color(primary)
    text_on_primary = _clean_hex(pal.get("text_on_primary"), computed_top)

    computed_toa = contrasting_text_color(accent)
    text_on_accent = _clean_hex(pal.get("text_on_accent"), computed_toa)

    teal_fam = families.get("teal") if isinstance(families.get("teal"), dict) else {}
    ice = _clean_hex(teal_fam.get("highlight"), secondary)

    orange_fam = families.get("orange") if isinstance(families.get("orange"), dict) else {}
    green_fam = families.get("green") if isinstance(families.get("green"), dict) else {}
    purple_fam = families.get("purple") if isinstance(families.get("purple"), dict) else {}
    pink_fam = families.get("pink") if isinstance(families.get("pink"), dict) else {}

    product_lines: list[str] = []
    if orange_fam.get("primary"):
        product_lines.append(
            f"  --product-elements: {_clean_hex(orange_fam.get('primary'), accent)};"
        )
    if green_fam.get("primary"):
        product_lines.append(f"  --product-radix: {_clean_hex(green_fam.get('primary'), accent)};")
    if purple_fam.get("primary"):
        product_lines.append(
            f"  --product-fabric: {_clean_hex(purple_fam.get('primary'), accent)};"
        )
    if pink_fam.get("primary"):
        product_lines.append(
            f"  --product-agentflow: {_clean_hex(pink_fam.get('primary'), accent)};"
        )
    product_lines.extend(
        [
            "  --product-core: var(--color-primary);",
            "  --product-trust: var(--color-accent);",
        ]
    )

    ds = kit.get("design_system") if isinstance(kit.get("design_system"), dict) else {}
    panel_style = str(ds.get("panel_style") or "").strip().lower()
    elevation_panel = (
        "none" if panel_style == "glass-hairline" else "0 12px 32px rgba(0, 0, 0, 0.28)"
    )
    elevation_modal = "0 24px 64px rgba(0, 0, 0, 0.55)"

    live_ok = _clean_hex(status.get("live_ok") or pal.get("good"), "#3bd68a")
    live_bad = _clean_hex(status.get("live_bad") or pal.get("risk"), "#ef4444")
    st_success = _clean_hex(status.get("success") or pal.get("good"), live_ok)
    st_warning = _clean_hex(status.get("warning") or pal.get("warn"), "#f2a93b")
    st_danger = _clean_hex(status.get("danger") or pal.get("risk"), live_bad)
    st_danger_text = _clean_hex(status.get("danger_text"), st_danger)

    ir, ig, ib = _hex_rgb_tuple(ink)
    surface_panel = _clean_rgba(pal.get("surface_panel_rgba"), f"rgba({ir}, {ig}, {ib}, 0.03)")
    surface_panel_2 = _clean_rgba(pal.get("surface_panel_2_rgba"), f"rgba({ir}, {ig}, {ib}, 0.06)")
    border_hairline = _clean_rgba(pal.get("border_hairline_rgba"), f"rgba({ir}, {ig}, {ib}, 0.12)")
    border_strong = _clean_rgba(pal.get("border_strong_rgba"), f"rgba({ir}, {ig}, {ib}, 0.20)")
    grid_line = _clean_rgba(pal.get("grid_line_rgba"), f"rgba({ir}, {ig}, {ib}, 0.08)")
    text_faint = f"rgba({ir}, {ig}, {ib}, 0.55)"

    canvas_light = _clean_hex(pal.get("canvas_light"), "#ffffff")
    surface_light = _clean_hex(pal.get("surface_light"), "#f4f5f7")
    ink_light = _clean_hex(pal.get("ink_light"), "#000000")
    rule_light = _clean_hex(pal.get("rule_light"), "#e2e5ea")

    fb_stack = typo.get("fallback_stack") if isinstance(typo.get("fallback_stack"), list) else []
    mono_stack = typo.get("mono_stack") if isinstance(typo.get("mono_stack"), list) else []
    display_font = _format_font_stack(
        str(typo.get("display") or typo.get("headline") or "Inter"),
        fb_stack,
        ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        lower_first=True,
    )
    body_font = _format_font_stack(
        str(typo.get("body") or typo.get("display") or typo.get("headline") or "Inter"),
        fb_stack,
        ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        lower_first=True,
    )
    mono_font = _format_font_stack(
        str(typo.get("mono") or "JetBrains Mono"),
        mono_stack,
        ["ui-monospace", "monospace"],
        lower_first=False,
    )

    stagger_ms = int(round(motion.default_tempo.stagger_s * 1000))
    enter_ms = int(round(motion.default_tempo.duration_s * 1000))
    glow_flag = 1 if motion.glow_on_focal else 0

    bounce = motion.bounce
    enter_s = motion.default_tempo.duration_s
    spring_easing = css_spring_linear(enter_s, bounce)
    video_easing_arrive = (
        "cubic-bezier(0.22, 1, 0.36, 1)" if bounce == 0.0 else "var(--easing-spring)"
    )

    lines = [
        "/**",
        " * Do not edit directly — compiled from BRAND.toml by `python -m gtm_core.brandkit --compile-css`.",
        " */",
        "",
        ":root {",
        "  --color-black: #000000;",
        "  --color-white: #ffffff;",
        f"  --color-canvas: {canvas};",
        f"  --color-surface: {surface};",
        f"  --color-ink: {ink};",
        f"  --color-primary: {primary};",
        f"  --color-accent: {accent};",
        f"  --color-secondary: {secondary};",
        f"  --color-rule: {rule};",
        f"  --color-blue: {primary};",
        f"  --color-cyan: {accent};",
        f"  --color-ice: {ice};",
        f"  --color-live-ok: {live_ok};",
        f"  --color-live-bad: {live_bad};",
        f"  --text-primary: {ink};",
        f"  --text-secondary: {ink_muted};",
        f"  --text-tertiary: {ink_subtle};",
        f"  --text-faint: {text_faint};",
        f"  --text-on-primary: {text_on_primary};",
        f"  --text-on-accent: {text_on_accent};",
        "  --brand-text-on-primary: var(--text-on-primary);",
        "  --brand-text-on-accent: var(--text-on-accent);",
        f"  --brand-canvas: {canvas};",
        f"  --brand-surface: {surface};",
        f"  --brand-primary: {primary};",
        f"  --brand-secondary: {secondary};",
        f"  --brand-accent: {accent};",
        f"  --brand-text: {ink};",
        f"  --brand-text-dim: {ink_muted};",
        f"  --brand-text-muted: {ink_subtle};",
        f"  --brand-warn: {st_warning};",
        f"  --brand-good: {st_success};",
        f"  --brand-font-headline: {display_font};",
        f"  --brand-font-body: {body_font};",
        f"  --brand-font-mono: {mono_font};",
        f"  --surface-canvas: {canvas};",
        f"  --surface-cell: {canvas};",
        f"  --surface-elevated: {surface};",
        f"  --surface-panel: {surface_panel};",
        f"  --surface-panel-2: {surface_panel_2};",
        f"  --border-hairline: {border_hairline};",
        f"  --border-hairline-strong: {border_strong};",
        f"  --grid-line: {grid_line};",
        f"  --surface-canvas-light: {canvas_light};",
        f"  --surface-elevated-light: {surface_light};",
        f"  --text-primary-light: {ink_light};",
        f"  --border-rule-light: {rule_light};",
        "  --status-ok: var(--color-live-ok);",
        "  --status-bad: var(--color-live-bad);",
        f"  --status-success: {st_success};",
        f"  --status-warning: {st_warning};",
        f"  --status-danger: {st_danger};",
        f"  --status-danger-text: {st_danger_text};",
        "  --state-hover-wash: var(--surface-panel-2);",
        "  --highlight-primary: var(--color-primary);",
        "  --highlight-secondary: var(--color-accent);",
        "  --accent-gradient: linear-gradient(90deg, var(--color-primary), var(--color-accent));",
        *product_lines,
        f"  --elevation-panel: {elevation_panel};",
        f"  --elevation-modal: {elevation_modal};",
        f"  --font-family-display: {display_font};",
        f"  --font-family-body: {body_font};",
        f"  --font-family-mono: {mono_font};",
        "  --font-weight-x-light: 200;",
        "  --font-weight-light: 300;",
        "  --font-weight-regular: 400;",
        "  --font-weight-medium: 500;",
        "  --font-weight-bold: 700;",
        "  --font-weight-x-bold: 800;",
        "  --font-size-display-hero: clamp(2.6rem, 1.691rem + 3.636vw, 4.6rem);",
        "  --font-size-display-lg: clamp(2.4rem, 1.673rem + 2.909vw, 4rem);",
        "  --font-size-display-md: clamp(2.2rem, 1.564rem + 2.545vw, 3.6rem);",
        "  --font-size-display-sm: clamp(2rem, 1.455rem + 2.182vw, 3.2rem);",
        "  --font-size-headline-lg: clamp(1.9rem, 1.4rem + 2vw, 3rem);",
        "  --font-size-headline-md: clamp(1.6rem, 1.236rem + 1.455vw, 2.4rem);",
        "  --font-size-headline-sm: clamp(1.4rem, 1.127rem + 1.091vw, 2rem);",
        "  --font-size-title-lg: clamp(1.25rem, 1.136rem + 0.455vw, 1.5rem);",
        "  --font-size-title-md: clamp(1.125rem, 1.068rem + 0.227vw, 1.25rem);",
        "  --font-size-title-sm: clamp(1rem, 0.943rem + 0.227vw, 1.125rem);",
        "  --font-size-title-xs: 1rem;",
        "  --font-size-body-lg: clamp(1rem, 1.5vw, 1.125rem);",
        "  --font-size-body-md: 1rem;",
        "  --font-size-body-sm: 0.875rem;",
        "  --font-size-body-xs: 0.75rem;",
        "  --font-size-label-lg: 0.875rem;",
        "  --font-size-label-md: 0.75rem;",
        "  --font-size-label-sm: 0.6875rem;",
        "  --font-size-mono-label: var(--font-size-label-sm);",
        "  --font-size-eyebrow: 0.8rem;",
        "  --font-size-code: 0.8125rem;",
        "  --line-height-display-hero: 0.98;",
        "  --line-height-display-lg: 1;",
        "  --line-height-display-md: 1.03;",
        "  --line-height-display-sm: 1.05;",
        "  --line-height-headline-lg: 1.1;",
        "  --line-height-headline-md: 1.15;",
        "  --line-height-headline-sm: 1.2;",
        "  --line-height-title-lg: 1.25;",
        "  --line-height-title-md: 1.3;",
        "  --line-height-title-sm: 1.35;",
        "  --line-height-title-xs: 1.4;",
        "  --line-height-body-lg: 1.6;",
        "  --line-height-body-md: 1.65;",
        "  --line-height-body-sm: 1.7;",
        "  --line-height-body-xs: 1.75;",
        "  --line-height-label: 1.45;",
        "  --letter-spacing-display: -0.025em;",
        "  --letter-spacing-headline: -0.015em;",
        "  --letter-spacing-title: -0.008em;",
        "  --letter-spacing-body: 0;",
        "  --letter-spacing-eyebrow: 0.18em;",
        "  --letter-spacing-mono-label: 0.1em;",
        "  --letter-spacing-caps-wide: 0.15em;",
        "  --duration-quick: 150ms;",
        "  --duration-fast: 200ms;",
        "  --duration-base: 300ms;",
        "  --duration-slow: 500ms;",
        "  --duration-reveal: 700ms;",
        "  --duration-ambient: 24000ms;",
        "  --easing-reveal: cubic-bezier(0.22, 1, 0.36, 1);",
        "  --easing-standard: cubic-bezier(0.2, 0, 0, 1);",
        "  --easing-gravity: cubic-bezier(0.123, 0.5, 0.5, 1);",
        f"  --motion-tempo: {motion.default_tempo.name};",
        f"  --motion-stagger: {stagger_ms}ms;",
        f"  --motion-enter: {enter_ms}ms;",
        f"  --motion-bounce: {bounce:.2f};",
        f"  --easing-spring: {spring_easing};",
        "  --ease-spring: var(--easing-spring);",
        f"  --motion-glow-on-focal: {glow_flag};",
        f"  --layout-density: {layout.density};",
        f"  --layout-safe-fill-min: {layout.safe_box_fill_min};",
        f"  --layout-safe-fill-max: {layout.safe_box_fill_max};",
        f"  --frame-w: {width}px;",
        f"  --frame-h: {height}px;",
        "  --video-fps: 25;",
        "  --video-title-safe: 0.05;",
        "  --video-duration-hook: 3600ms;",
        "  --video-duration-reveal: 3000ms;",
        "  --video-duration-claim: 4600ms;",
        "  --video-duration-outcome: 4500ms;",
        "  --video-duration-endcard: 5100ms;",
        "  --video-duration-cut: 400ms;",
        "  --video-duration-total: 30000ms;",
        "  --video-stagger-word: 45ms;",
        f"  --video-easing-arrive: {video_easing_arrive};",
        "  --video-easing-move: cubic-bezier(0.65, 0, 0.35, 1);",
        "  --video-type-headline: 0.067;",
        "  --video-type-headline-xl: 0.089;",
        "  --video-type-label-min: 0.014;",
        "  --radius-none: 0;",
        "  --radius-xs: 0.375rem;",
        "  --radius-sm: 0.5rem;",
        "  --radius-md: 0.75rem;",
        "  --radius-md-lg: 0.875rem;",
        "  --radius-lg: 1rem;",
        "  --radius-xl: 1.375rem;",
        "  --radius-full: 9999px;",
        "  --space-0: 0;",
        "  --space-05: 0.125rem;",
        "  --space-1: 0.25rem;",
        "  --space-2: 0.5rem;",
        "  --space-3: 0.75rem;",
        "  --space-4: 1rem;",
        "  --space-5: 1.25rem;",
        "  --space-6: 1.5rem;",
        "  --space-7: 1.75rem;",
        "  --space-8: 2rem;",
        "  --space-10: 2.5rem;",
        "  --space-12: 3rem;",
        "  --space-14: 3.5rem;",
        "  --space-16: 4rem;",
        "  --space-20: 5rem;",
        "  --space-24: 6rem;",
        "  --space-28: 7rem;",
        "  --space-32: 8rem;",
        "  --space-40: 10rem;",
        "  --space-48: 12rem;",
        "  --space-hairline: 1px;",
        "  --layout-content-max: 1200px;",
        "  --layout-measure: 62ch;",
        "  --layout-measure-lead: 50ch;",
        "  --layout-edge: clamp(1.25rem, 5vw, 4rem);",
        "  --layout-section-block: clamp(3.5rem, 7vw, 6rem);",
        "}",
        "",
    ]
    return "\n".join(lines)
