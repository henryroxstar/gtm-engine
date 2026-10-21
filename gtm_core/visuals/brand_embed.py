"""Brand Kit & Asset Base64 Inlining for the Composable Visual Engine (CVE)."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

from gtm_core.brandkit import load_brand_kit
from gtm_core.design_tokens import relative_luminance
from gtm_core.paths import resolve_profiles_root

_TYPEKIT_URL_RE = re.compile(r"^https://use\.typekit\.net/[a-z0-9]{5,12}\.css$")


class FontMissingError(RuntimeError):
    """Raised when strict_brand is True and a configured font file cannot be found."""


def _font_mime_and_format(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext == ".woff2":
        return "font/woff2", "woff2"
    if ext == ".woff":
        return "font/woff", "woff"
    if ext == ".otf":
        return "font/opentype", "opentype"
    return "font/truetype", "truetype"


class BrandEmbedder:
    """Extracts, inlines, and computes brand assets, colors, and fonts for CVE artboards."""

    def __init__(
        self,
        profile: str,
        profiles_root: Path | None = None,
        strict_brand: bool = False,
    ) -> None:
        self.profile = profile
        self.profiles_root = profiles_root or resolve_profiles_root()
        self.profile_dir = self.profiles_root / profile
        self.strict_brand = strict_brand
        self.config: dict[str, Any] = load_brand_kit(self.profiles_root, profile)

    def get_color(self, key: str, default: str = "") -> str:
        """Return a palette color from the resolved brand kit."""
        palette = (
            self.config.get("palette", {}) if isinstance(self.config.get("palette"), dict) else {}
        )
        val = palette.get(key)
        return str(val) if val is not None else default

    def get_font_family(self, key: str = "display", default: str = "") -> str:
        """Return a typography family name from the resolved brand kit."""
        typo = (
            self.config.get("typography", {})
            if isinstance(self.config.get("typography"), dict)
            else {}
        )
        val = typo.get(key)
        return str(val) if val is not None else default

    def get_design_system_policy(self) -> dict[str, Any]:
        """Return the [design_system] policy table from the resolved brand kit."""
        ds = self.config.get("design_system")
        return dict(ds) if isinstance(ds, dict) else {}

    def get_motion_policy(self) -> dict[str, Any]:
        """Return the [motion] policy table from the resolved brand kit."""
        motion = self.config.get("motion")
        return dict(motion) if isinstance(motion, dict) else {}

    def get_typekit_link_html(self) -> str:
        """Return a validated Typekit <link> tag when configured and not in offline mode."""
        offline = os.environ.get("GTM_OFFLINE_RENDER", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if offline:
            return ""
        typo = (
            self.config.get("typography", {})
            if isinstance(self.config.get("typography"), dict)
            else {}
        )
        url = str(typo.get("typekit_url") or "").strip()
        if url and _TYPEKIT_URL_RE.match(url):
            return f'<link rel="stylesheet" href="{url}">'
        return ""

    def _is_confined(self, path: Path) -> bool:
        """Check whether path stays strictly within the allowed profile directory."""
        try:
            resolved = path.resolve()
            allowed_profile = self.profile_dir.resolve()
            return resolved.is_relative_to(allowed_profile)
        except (ValueError, RuntimeError):
            return False

    def _resolve_profile_asset(self, rel_path: str) -> Path:
        """Resolve an asset path relative to profile_dir, stripping leading profiles/<slug>/ if present."""
        raw = str(rel_path).strip()
        prefix = f"profiles/{self.profile}/"
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
        return self.profile_dir / raw

    def resolve_bg_plate_uri(
        self, allow_in_ui: bool = False, *, for_hero_cover: bool = False
    ) -> str | None:
        """Resolve and base64-encode a background plate URI if permitted by design_system policy."""
        ds = self.get_design_system_policy()
        if ds.get("background_plates_in_ui") is False and not (allow_in_ui or for_hero_cover):
            return None
        refs = (
            self.config.get("references", {})
            if isinstance(self.config.get("references"), dict)
            else {}
        )
        plates = refs.get("background_plates", [])
        if not isinstance(plates, list) or not plates:
            return None
        ordered_plates = sorted(
            plates,
            key=lambda p: 0 if "darker" in str(p).lower() else 1,
        )
        images_dir_raw = str(refs.get("images") or "knowledge/brand/reference-images")
        images_dir = self._resolve_profile_asset(images_dir_raw)
        for plate_rel in ordered_plates:
            candidates = [
                self._resolve_profile_asset(str(plate_rel)),
                images_dir / str(plate_rel),
            ]
            for plate_path in candidates:
                if self._is_confined(plate_path) and plate_path.is_file():
                    b64_plate = base64.b64encode(plate_path.read_bytes()).decode("ascii")
                    return f"url('data:image/png;base64,{b64_plate}')"
        return None

    def _resolve_bg_plate_css(self, is_dark_canvas: bool, allow_in_ui: bool = False) -> str:
        """Resolve and base64-encode a dark background plate if configured, confined, and allowed."""
        if not is_dark_canvas:
            return "none"
        uri = self.resolve_bg_plate_uri(allow_in_ui=allow_in_ui)
        return uri if uri is not None else "none"

    @staticmethod
    def _normalize_font_entries(font_files: Any) -> list[tuple[str | None, str]]:
        """Normalize font_files from either a list of paths or a role->path dict."""
        if isinstance(font_files, list):
            return [(None, str(p)) for p in font_files]
        if isinstance(font_files, dict):
            seen_paths: dict[str, str | None] = {}
            for role, p in font_files.items():
                p_str = str(p)
                if p_str not in seen_paths or str(role).lower() == "mono":
                    seen_paths[p_str] = str(role).lower()
            return [(role, p_str) for p_str, role in seen_paths.items()]
        return []

    @staticmethod
    def _weight_range_for_stem(stem: str, multi_font: bool, is_mono: bool) -> str:
        """Compute CSS font-weight descriptor from filename stem."""
        if not multi_font or is_mono:
            return "400 700"
        weight_match = re.search(r"[-_]([3-8]00)$", stem)
        if not weight_match:
            return "400 700"
        w = int(weight_match.group(1))
        if w <= 400:
            return "300 400"
        if w == 500:
            return "500"
        return "600 800"

    def _discover_local_brand_fonts(
        self,
        normalized_fonts: list[tuple[str | None, str]],
        clean_primary: str,
        clean_mono: str,
    ) -> list[tuple[str | None, str]]:
        """Append any licensed primary/mono font files present in knowledge/brand/fonts/."""
        out = list(normalized_fonts)
        seen_resolved: set[Path] = {
            self._resolve_profile_asset(rel_p).resolve()
            for _, rel_p in out
            if self._resolve_profile_asset(rel_p).exists()
        }
        fonts_dir = self.profile_dir / "knowledge" / "brand" / "fonts"
        if not (self._is_confined(fonts_dir) and fonts_dir.is_dir()):
            return out
        primary_token = re.sub(r"[\s_-]+", "", clean_primary).lower()
        mono_token = re.sub(r"[\s_-]+", "", clean_mono).lower()
        for fpath in sorted(fonts_dir.iterdir()):
            if not fpath.is_file() or fpath.suffix.lower() not in {
                ".woff2",
                ".woff",
                ".ttf",
                ".otf",
            }:
                continue
            resolved_f = fpath.resolve()
            if resolved_f in seen_resolved:
                continue
            stem_token = re.sub(r"[\s_-]+", "", fpath.stem).lower()
            if primary_token and stem_token.startswith(primary_token):
                out.append(("display", f"knowledge/brand/fonts/{fpath.name}"))
                seen_resolved.add(resolved_f)
            elif mono_token and stem_token.startswith(mono_token):
                out.append(("mono", f"knowledge/brand/fonts/{fpath.name}"))
                seen_resolved.add(resolved_f)
        return out

    def _build_font_face_rules(
        self, typo: dict[str, Any], clean_primary: str, clean_mono: str
    ) -> list[str]:
        """Build @font-face CSS blocks for all configured font files."""
        rules: list[str] = []
        base_fonts = self._normalize_font_entries(typo.get("font_files", []))
        normalized_fonts = self._discover_local_brand_fonts(base_fonts, clean_primary, clean_mono)
        multi_font = len(normalized_fonts) > 1
        primary_norm = re.sub(r"[\s_-]+", "", clean_primary).lower()
        mono_norm = re.sub(r"[\s_-]+", "", clean_mono).lower()

        for role, rel_path in normalized_fonts:
            font_path = self._resolve_profile_asset(rel_path)
            if self._is_confined(font_path) and font_path.is_file():
                b64 = base64.b64encode(font_path.read_bytes()).decode("ascii")
                file_stem = re.sub(
                    r"[-_](?:[0-9]{3,4}|bold|regular|italic|semibold|light|medium|black|thin)$",
                    "",
                    font_path.stem,
                    flags=re.IGNORECASE,
                )
                is_mono = role == "mono" or "mono" in file_stem.lower()
                family_name = clean_mono if is_mono else clean_primary
                target_norm = mono_norm if is_mono else primary_norm
                stem_norm = re.sub(r"[\s_-]+", "", file_stem).lower()
                matches_brand_family = bool(
                    target_norm and (stem_norm == target_norm or stem_norm.startswith(target_norm))
                )
                is_standin = (
                    font_path.stem.lower().startswith("standin") or not matches_brand_family
                )
                mime_type, fmt = _font_mime_and_format(font_path)
                weight_range = self._weight_range_for_stem(font_path.stem, multi_font, is_mono)

                aliases: list[str] = []
                if is_standin:
                    standin_family = "Standin Mono" if is_mono else "Standin Sans"
                    for name in [standin_family, file_stem]:
                        if name and name not in aliases:
                            aliases.append(name)
                else:
                    for name in [family_name, file_stem]:
                        if name and name not in aliases:
                            aliases.append(name)

                for alias in aliases:
                    rules.append(
                        f"\n@font-face {{\n"
                        f"  font-family: '{alias}';\n"
                        f"  src: url('data:{mime_type};base64,{b64}') format('{fmt}');\n"
                        f"  font-weight: {weight_range};\n"
                        f"  font-display: block;\n"
                        f"}}"
                    )
            elif self.strict_brand:
                raise FontMissingError(f"Font file {font_path} not found in profile {self.profile}")
        return rules

    def build_font_face_css(self) -> str:
        """Return only the @font-face CSS blocks for the configured typography."""
        typo = (
            self.config.get("typography", {})
            if isinstance(self.config.get("typography"), dict)
            else {}
        )
        raw_primary = str(typo.get("primary") or typo.get("display") or typo.get("body") or "Inter")
        clean_primary = re.sub(r"[^\w\s-]", "", raw_primary).strip() or "Inter"
        raw_mono = str(typo.get("mono") or "SF Mono")
        clean_mono = re.sub(r"[^\w\s-]", "", raw_mono).strip() or "SF Mono"
        return "\n".join(self._build_font_face_rules(typo, clean_primary, clean_mono))

    def generate_embedded_css(self, theme: str = "light") -> str:
        """Generate embedded CSS custom properties and @font-face rules."""
        palette = (
            self.config.get("palette", {}) if isinstance(self.config.get("palette"), dict) else {}
        )
        typo = (
            self.config.get("typography", {})
            if isinstance(self.config.get("typography"), dict)
            else {}
        )

        bg = (
            palette.get("stage_canvas")
            or palette.get("background")
            or palette.get("canvas")
            or ("#0A182A" if theme == "dark" else "#FFFFFF")
        )
        fg = (
            palette.get("text")
            or palette.get("ink")
            or ("#FFFFFF" if theme == "dark" else "#0A182A")
        )
        primary = palette.get("primary", "#3464FD")
        accent = palette.get("accent", "#72F5FA")

        accent_upper = str(accent).upper()
        if theme == "light":
            if accent_upper in {"#72F5FA", "#00FFFF", "#38BDF8", "#00FF00", "#FFFF00"}:
                accent_text = primary
            else:
                lum = relative_luminance(str(accent))
                accent_text = primary if lum > 0.4 else accent
        else:
            accent_text = accent

        is_dark_canvas = relative_luminance(str(bg)) < 0.35
        surface = (
            palette.get("surface", "#0A182A")
            if is_dark_canvas
            else palette.get("surface_light", "#F6F8FF")
        )
        muted = (
            (palette.get("ink_muted") or palette.get("muted", "#9AA3B2"))
            if is_dark_canvas
            else "#475569"
        )
        surface_panel_rgba = (
            palette.get("surface_panel_rgba", "rgba(244, 246, 251, 0.03)")
            if is_dark_canvas
            else "rgba(255, 255, 255, 0.92)"
        )
        border_hairline_rgba = (
            palette.get("border_hairline_rgba", "rgba(244, 246, 251, 0.12)")
            if is_dark_canvas
            else "rgba(15, 23, 42, 0.12)"
        )
        rule_color = border_hairline_rgba if is_dark_canvas else "rgba(15, 23, 42, 0.12)"
        canvas_accent = accent if is_dark_canvas else accent_text

        raw_primary = str(typo.get("primary") or typo.get("display") or typo.get("body") or "Inter")
        clean_primary = re.sub(r"[^\w\s-]", "", raw_primary).strip() or "Inter"
        raw_mono = str(typo.get("mono") or "SF Mono")
        clean_mono = re.sub(r"[^\w\s-]", "", raw_mono).strip() or "SF Mono"

        bg_plate_css = self._resolve_bg_plate_css(is_dark_canvas)
        grid_line = (
            palette.get("grid_line_rgba", "rgba(123, 215, 221, 0.04)")
            if is_dark_canvas
            else "rgba(15, 23, 42, 0.03)"
        )
        accent_gradient = f"linear-gradient(90deg, {primary}, {accent})"
        canvas_alt = (
            palette.get("canvas_alt", "#0A182A")
            if is_dark_canvas
            else palette.get("canvas_light_alt", "#F8FAFC")
        )

        font_face_rules = self._build_font_face_rules(typo, clean_primary, clean_mono)
        joined_faces = "\n".join(font_face_rules)
        sans_fallback = (
            f"'{clean_primary}', 'Standin Sans', -apple-system, sans-serif"
            if "'Standin Sans'" in joined_faces
            else f"'{clean_primary}', -apple-system, sans-serif"
        )
        mono_fallback = (
            f"'{clean_mono}', 'Standin Mono', 'SF Mono', ui-monospace, monospace"
            if "'Standin Mono'" in joined_faces
            else f"'{clean_mono}', 'SF Mono', ui-monospace, monospace"
        )

        css_lines = [
            ":root {",
            f"  --cve-bg: {bg};",
            f"  --cve-canvas-alt: {canvas_alt};",
            f"  --cve-fg: {fg};",
            f"  --cve-surface: {surface};",
            f"  --cve-panel-bg: {surface_panel_rgba};",
            f"  --cve-panel-border: {border_hairline_rgba};",
            f"  --cve-muted: {muted};",
            f"  --cve-rule: {rule_color};",
            f"  --cve-primary: {primary};",
            f"  --cve-accent: {accent};",
            f"  --cve-accent-text: {accent_text};",
            f"  --cve-canvas-accent: {canvas_accent};",
            f"  --cve-accent-gradient: {accent_gradient};",
            f"  --cve-grid-line: {grid_line};",
            f"  --cve-bg-plate: {bg_plate_css};",
            f"  --cve-font-family: {sans_fallback};",
            f"  --cve-font-mono: {mono_fallback};",
            "  --cve-font-scale: 1.0;",
            "}",
        ]
        css_lines.extend(font_face_rules)
        return "\n".join(css_lines)

    def get_colophon_domain(self) -> str:
        """Extract clean brand CTA domain for editorial colophon footer."""
        cta = self.config.get("cta", {}) if isinstance(self.config.get("cta"), dict) else {}
        raw_url = str(cta.get("url", "")).strip()
        return re.sub(r"[^\w.-]", "", raw_url)

    def get_logo_html(self, theme: str = "dark") -> str:
        """Render SVG logo markup or safe area placeholder according to missing_policy."""
        assets = (
            self.config.get("assets", {}) if isinstance(self.config.get("assets"), dict) else {}
        )
        palette = (
            self.config.get("palette", {}) if isinstance(self.config.get("palette"), dict) else {}
        )
        bg = (
            palette.get("background")
            or palette.get("canvas")
            or ("#0A182A" if theme == "dark" else "#FFFFFF")
        )
        is_dark_canvas = relative_luminance(str(bg)) < 0.35

        candidate_keys = (
            (
                "logo_svg",
                "logo",
                "logo_horizontal_dark",
                "company_logo_dark",
                "logo_stacked_dark",
                "logo_horizontal_light",
            )
            if is_dark_canvas
            else (
                "logo_svg",
                "logo",
                "logo_horizontal_light",
                "company_logo_light",
                "logo_horizontal_dark",
            )
        )
        for key in candidate_keys:
            logo_rel = assets.get(key)
            if not logo_rel:
                continue
            logo_path = self._resolve_profile_asset(str(logo_rel))
            if self._is_confined(logo_path) and logo_path.is_file():
                raw_svg = logo_path.read_text(encoding="utf-8")
                # Sanitize SVG to block script execution, foreign objects, and event handlers in Chromium
                cleaned_svg = re.sub(r"(?is)<script.*?>.*?</script>", "", raw_svg)
                cleaned_svg = re.sub(r"(?is)<foreignObject.*?>.*?</foreignObject>", "", cleaned_svg)
                cleaned_svg = re.sub(
                    r'(?i)\son\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', "", cleaned_svg
                )
                return f'<div class="cve-logo">{cleaned_svg}</div>'

        missing_policy = assets.get("missing_policy", "leave safe area")
        if "safe area" in missing_policy:
            return (
                '<div class="cve-logo-safe-area" '
                'style="width: 140px; height: 36px; border: 1px dashed rgba(120,120,120,0.4); '
                "display: flex; align-items: center; justify-content: center; font-size: 10px; "
                'color: var(--cve-fg); opacity: 0.6;">Official Mark Area</div>'
            )
        return ""
