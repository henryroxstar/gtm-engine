"""HTML/CSS Grid & SVG Artboard Generator for the Composable Visual Engine (CVE)."""

from __future__ import annotations

import html
import re
from typing import Any

from gtm_core.visuals.brand_embed import BrandEmbedder
from gtm_core.visuals.compiler import LayoutCompiler
from gtm_core.visuals.schema import (
    ContrastPairBlock,
    CVESpec,
    ScorecardGridBlock,
    StatCalloutBlock,
)


def sanitize_text(text: str) -> str:
    """Strip XML 1.0 disallowed control characters and escape HTML entities."""
    if not text:
        return ""
    # Strip XML 1.0 disallowed control characters
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
    return html.escape(cleaned, quote=True)


def _format_editorial_text(text: str) -> str:
    """Sanitize text and normalize ASCII arrow/comparison sequences into editorial glyphs."""
    escaped = sanitize_text(text)
    if not escaped:
        return ""
    escaped = re.sub(r"(?<!<!)-&gt;", "→", escaped)
    escaped = re.sub(r"&gt;=(?=\d)", "≥", escaped)
    escaped = re.sub(r"&lt;=(?=\d)", "≤", escaped)
    escaped = escaped.replace("!=", "≠")
    escaped = re.sub(r"\[([^\]\n]+)\]", r'<span class="cve-accent-gradient">\1</span>', escaped)
    return escaped


class HTMLArtboardBuilder:
    """Compiles layout and generates a self-contained brand-locked HTML artboard."""

    def __init__(self, spec: CVESpec, embedder: BrandEmbedder | None = None) -> None:
        self.spec = spec
        self.embedder = embedder or BrandEmbedder(profile=spec.profile)
        self.compiler = LayoutCompiler(spec)
        self.compiled: dict[str, Any] = self.compiler.compile()
        self.scale_factor: float = self.compiled.get("scale_factor", 1.0)

    def _derive_eyebrow(self) -> str:
        has_scorecard = any(isinstance(b, ScorecardGridBlock) for b in self.spec.blocks)
        has_stat = any(isinstance(b, StatCalloutBlock) for b in self.spec.blocks)
        if has_scorecard and not has_stat:
            return "COMPETITIVE ARCHITECTURE // EXECUTIVE BATTLECARD"
        if has_stat and has_scorecard:
            return "EXECUTIVE BRIEFING // TELEMETRY & ARCHITECTURE"
        if has_stat:
            return "BENCHMARK TELEMETRY // VERIFIED METRICS"
        return "STRATEGIC ARCHITECTURE // EXECUTIVE BRIEF"

    def _synthesize_battlecard_stat(self, scorecard: ScorecardGridBlock) -> StatCalloutBlock:
        """Synthesize an executive summary stat strip for one-pager battlecards lacking a StatCallout."""
        total_rows = len(scorecard.rows)
        yours_count = sum(
            1
            for r in scorecard.rows
            if any(c.status.value in ("yours", "shipped") for c in r.cells)
        )
        return StatCalloutBlock(
            id="synthesized-battlecard-summary",
            value=f"{yours_count} / {total_rows}",
            label=f"Verified architectural advantage across {total_rows} core enterprise evaluation dimensions",
            delta="Full-Stack Coverage",
        )

    def _synthesize_scorecard_contrast(self, scorecard: ScorecardGridBlock) -> ContrastPairBlock:
        """Synthesize a 3-point contrast summary for 2-block portrait posters lacking a ContrastPair."""
        sample_rows = scorecard.rows[:3]
        left_pts = [
            f"Unverified {r.category.lower()} across multi-hop agent execution" for r in sample_rows
        ]
        right_pts = [r.cells[-1].text for r in sample_rows if r.cells]
        return ContrastPairBlock(
            id="synthesized-governance-contrast",
            left_title="Unscoped Runtime Exposure",
            left_points=left_pts or ["Static credentials without runtime attestation"],
            right_title="Verifiable Control Enforcement",
            right_points=right_pts or ["Cryptographic policy enforcement per agent run"],
        )

    def _render_exec_strip(self, scorecard: ScorecardGridBlock) -> str:
        """Render a 3-column architectural verification strip for tall A4 one-pagers."""
        cards_html: list[str] = []
        for idx, row in enumerate(scorecard.rows[:3], start=1):
            focal_text = row.cells[0].text if row.cells else ""
            cards_html.append(
                f'<div class="cve-exec-card">'
                f'<div class="cve-exec-kicker">0{idx} // VERIFIED PILLAR · {_format_editorial_text(row.category.upper())}</div>'
                f'<div class="cve-exec-body">{_format_editorial_text(focal_text)}</div>'
                f"</div>"
            )
        return f'<div class="cve-exec-strip">{"".join(cards_html)}</div>'

    def build(self) -> str:
        """Generate the complete self-contained HTML artboard string."""
        css = self.embedder.generate_embedded_css(theme=self.spec.theme)
        logo = self.embedder.get_logo_html(theme=self.spec.theme)
        colophon_domain = (
            self.embedder.get_colophon_domain()
            if hasattr(self.embedder, "get_colophon_domain")
            else ""
        )
        width = self.compiled["viewport"]["width"]
        height = self.compiled["viewport"]["height"]
        viewport_slug = re.sub(r"[^a-z0-9-]", "-", str(self.spec.viewport).lower())
        eyebrow = self._derive_eyebrow()

        effective_blocks = list(self.spec.blocks)
        has_stat = any(isinstance(b, StatCalloutBlock) for b in effective_blocks)
        has_contrast = any(isinstance(b, ContrastPairBlock) for b in effective_blocks)
        scorecard_block = next(
            (b for b in effective_blocks if isinstance(b, ScorecardGridBlock)), None
        )
        if viewport_slug == "onepager-pdf" and not has_stat and scorecard_block is not None:
            effective_blocks.insert(0, self._synthesize_battlecard_stat(scorecard_block))
        if (
            viewport_slug == "social-4x5"
            and len(effective_blocks) == 2
            and not has_contrast
            and scorecard_block is not None
        ):
            effective_blocks.append(self._synthesize_scorecard_contrast(scorecard_block))

        block_count = len(effective_blocks)
        font_scale = (
            1.0
            if (viewport_slug in ("social-4x5", "onepager-pdf") and len(self.spec.blocks) <= 2)
            else self.scale_factor
        )

        blocks_html: list[str] = []
        for block in effective_blocks:
            if isinstance(block, ScorecardGridBlock):
                blocks_html.append(self._render_scorecard(block, viewport_slug=viewport_slug))
            elif isinstance(block, StatCalloutBlock):
                blocks_html.append(self._render_stat(block, total_blocks=block_count))
            elif isinstance(block, ContrastPairBlock):
                blocks_html.append(self._render_contrast(block))

        if (
            viewport_slug == "onepager-pdf"
            and scorecard_block is not None
            and len(scorecard_block.rows) <= 5
        ):
            blocks_html.append(self._render_exec_strip(scorecard_block))

        footnotes_html = ""
        if self.spec.footnotes:
            fn_items = "".join(
                f'<li><span class="cve-fn-id">[{sanitize_text(fn.id)}]</span> {_format_editorial_text(fn.text)}</li>'
                for fn in self.spec.footnotes
            )
            footnotes_html = f'<ul class="cve-footnotes">{fn_items}</ul>'
        else:
            footnotes_html = (
                '<div class="cve-footnotes cve-footnotes-empty">'
                "<span>VERIFIED ARCHITECTURE SPECIFICATION // FIELD ENABLEMENT EDITION</span>"
                "</div>"
            )

        domain_html = (
            f'<div class="cve-colophon-domain">{sanitize_text(colophon_domain)}</div>'
            if colophon_domain
            else f'<div class="cve-colophon-domain">{sanitize_text(self.spec.profile.upper())}</div>'
        )

        subtitle_html = (
            f'<p class="cve-subtitle">{_format_editorial_text(self.spec.subtitle)}</p>'
            if self.spec.subtitle
            else ""
        )

        ds_policy = getattr(self.embedder, "get_design_system_policy", dict)()
        motion_policy = getattr(self.embedder, "get_motion_policy", dict)()
        typekit_link = getattr(self.embedder, "get_typekit_link_html", lambda: "")()
        typekit_head = f"\n{typekit_link}" if typekit_link else ""
        is_glass_hairline = str(ds_policy.get("panel_style") or "") == "glass-hairline"
        glow_on_focal = bool(motion_policy.get("glow_on_focal", True))

        body_bg_image, body_bg_size, body_bg_pos, eyebrow_dot_shadow = (
            (
                "var(--cve-bg-plate, none),\n"
                "    radial-gradient(circle at 85% 15%, rgba(52, 100, 253, 0.14) 0%, transparent 50%),\n"
                "    radial-gradient(circle at 15% 85%, rgba(114, 245, 250, 0.05) 0%, transparent 45%)",
                "cover, 100% 100%, 100% 100%",
                "center, center, center",
                "0 0 10px var(--cve-canvas-accent, var(--cve-primary))",
            )
            if glow_on_focal
            else ("var(--cve-bg-plate, none)", "cover", "center", "none")
        )

        (
            stat_card_bg,
            stat_card_border,
            stat_card_shadow,
            scorecard_bg,
            scorecard_border,
            scorecard_shadow,
            contrast_left_bg,
            contrast_left_border,
            contrast_right_bg,
            contrast_right_border,
            contrast_right_shadow,
            th_focal_bg,
            td_focal_bg,
        ) = (
            (
                "var(--cve-panel-bg, rgba(244, 246, 251, 0.03))",
                "1px solid var(--cve-panel-border, rgba(244, 246, 251, 0.12))",
                "none",
                "var(--cve-panel-bg, rgba(244, 246, 251, 0.03))",
                "1px solid var(--cve-panel-border, rgba(244, 246, 251, 0.12))",
                "none",
                "var(--cve-panel-bg, rgba(244, 246, 251, 0.03))",
                "1px solid var(--cve-panel-border, rgba(244, 246, 251, 0.12))",
                "var(--cve-panel-bg, rgba(244, 246, 251, 0.03))",
                "1px solid var(--cve-panel-border, rgba(244, 246, 251, 0.12))",
                "none",
                "rgba(52, 100, 253, 0.08)",
                "rgba(52, 100, 253, 0.04)",
            )
            if is_glass_hairline
            else (
                "linear-gradient(135deg, rgba(52, 100, 253, 0.15) 0%, rgba(27, 37, 58, 0.72) 100%)",
                "1px solid rgba(114, 245, 250, 0.24)",
                "0 10px 28px rgba(0, 0, 0, 0.20)",
                "rgba(27, 37, 58, 0.54)",
                "1px solid var(--cve-rule, rgba(196, 207, 238, 0.18))",
                "0 12px 30px rgba(0, 0, 0, 0.20)",
                "rgba(10, 24, 42, 0.58)",
                "1px solid var(--cve-rule, rgba(196, 207, 238, 0.16))",
                "linear-gradient(160deg, rgba(52, 100, 253, 0.15) 0%, rgba(27, 37, 58, 0.72) 100%)",
                "1px solid rgba(114, 245, 250, 0.26)",
                "0 12px 30px rgba(0, 0, 0, 0.22)",
                "rgba(52, 100, 253, 0.20)",
                "rgba(52, 100, 253, 0.09)",
            )
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">{typekit_head}
<style>
{css}
:root {{
  --cve-font-scale: {font_scale};
  --cve-col1-width: 26%;
  --cve-cell-pad-x: 24px;
  --cve-section-gap: 20px;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  width: {width}px;
  height: {height}px;
  background-color: var(--cve-bg);
  background-image:
    {body_bg_image};
  background-size: {body_bg_size};
  background-position: {body_bg_pos};
  color: var(--cve-fg);
  font-family: var(--cve-font-family);
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
}}
.cve-accent-gradient {{
  background: var(--cve-accent-gradient, linear-gradient(90deg, var(--cve-primary), var(--cve-canvas-accent, var(--cve-accent))));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  -webkit-text-fill-color: transparent;
  display: inline;
}}
.cve-artboard {{
  width: 100%;
  height: 100%;
  padding: 42px 60px 34px 60px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  position: relative;
}}
/* 4 Mission Control 22px HUD corner L-bracket reticles */
.cve-artboard::before {{
  content: "";
  position: absolute;
  inset: 22px;
  pointer-events: none;
  z-index: 2;
  background:
    linear-gradient(to right, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 0 0,
    linear-gradient(to bottom, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 0 0,
    linear-gradient(to left, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 100% 0,
    linear-gradient(to bottom, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 100% 0,
    linear-gradient(to right, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 0 100%,
    linear-gradient(to top, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 0 100%,
    linear-gradient(to left, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 100% 100%,
    linear-gradient(to top, rgba(114, 245, 250, 0.42) 2px, transparent 2px) 100% 100%;
  background-repeat: no-repeat;
  background-size: 22px 22px;
}}
/* 60px x 60px Mission Control hairline grid with radial vignette mask */
.cve-artboard::after {{
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, var(--cve-grid-line, rgba(123, 215, 221, 0.18)) 1px, transparent 1px),
    linear-gradient(to bottom, var(--cve-grid-line, rgba(123, 215, 221, 0.18)) 1px, transparent 1px);
  background-size: 60px 60px;
  background-position: center center;
  -webkit-mask-image: radial-gradient(ellipse 80% 70% at 50% 50%, rgba(0, 0, 0, 0.65) 0%, rgba(0, 0, 0, 0.25) 65%, transparent 100%);
  mask-image: radial-gradient(ellipse 80% 70% at 50% 50%, rgba(0, 0, 0, 0.65) 0%, rgba(0, 0, 0, 0.25) 65%, transparent 100%);
  opacity: 0.55;
  pointer-events: none;
  z-index: 0;
}}
.cve-header, .cve-body, .cve-footer {{
  position: relative;
  z-index: 1;
}}

/* Two-Column Masthead Header */
.cve-header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 36px;
  padding-bottom: 18px;
  margin-bottom: var(--cve-section-gap);
  border-bottom: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.16));
  position: relative;
  flex-shrink: 0;
}}
.cve-header::after {{
  content: "";
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 96px;
  height: 2px;
  background: var(--cve-canvas-accent, var(--cve-primary));
}}
.cve-header-main {{
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-width: 0;
}}
.cve-eyebrow {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(11px * var(--cve-font-scale, {font_scale}));
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--cve-canvas-accent, var(--cve-accent-text));
}}
.cve-eyebrow-dot {{
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--cve-canvas-accent, var(--cve-primary));
  box-shadow: {eyebrow_dot_shadow};
  flex-shrink: 0;
}}
.cve-title {{
  font-size: calc(35px * var(--cve-font-scale, {font_scale}));
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.12;
  color: var(--cve-fg);
}}
.cve-subtitle {{
  font-size: calc(15.5px * var(--cve-font-scale, {font_scale}));
  color: var(--cve-muted, var(--cve-fg));
  opacity: 0.92;
  line-height: 1.38;
  font-weight: 400;
}}
.cve-logo {{
  display: flex;
  align-items: center;
  flex-shrink: 0;
  padding-top: 4px;
}}
.cve-logo svg {{
  height: 34px;
  width: auto;
  max-width: 190px;
  object-fit: contain;
}}

/* Body Container: equal --cve-section-gap everywhere */
.cve-body {{
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: var(--cve-section-gap);
  min-height: 0;
}}

/* Stat Callout Block — shares exact --cve-col1-width plumb line with ScorecardGrid */
.cve-stat-card {{
  position: relative;
  background: {stat_card_bg};
  border: {stat_card_border};
  border-top: 2px solid var(--cve-canvas-accent, var(--cve-primary));
  border-radius: 10px;
  display: grid;
  grid-template-columns: var(--cve-col1-width) 1fr auto;
  align-items: stretch;
  overflow: hidden;
  flex-shrink: 0;
  box-shadow: {stat_card_shadow};
}}
.cve-stat-card-single {{
  grid-template-columns: 1fr;
  padding: 40px;
  gap: 16px;
  flex: 1;
  align-content: center;
}}
.cve-stat-value-col {{
  padding: 20px var(--cve-cell-pad-x);
  border-right: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.18));
  background: rgba(10, 24, 42, 0.34);
  display: flex;
  align-items: center;
}}
.cve-stat-card-single .cve-stat-value-col {{
  border-right: none;
  background: transparent;
  padding: 0;
}}
.cve-stat-value {{
  font-size: calc(48px * var(--cve-font-scale, {font_scale}));
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.0;
  background: linear-gradient(95deg, #FFFFFF 0%, var(--cve-canvas-accent, var(--cve-primary)) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  white-space: nowrap;
}}
.cve-stat-card-single .cve-stat-value {{
  font-size: calc(88px * var(--cve-font-scale, {font_scale}));
}}
.cve-stat-center {{
  padding: 20px var(--cve-cell-pad-x);
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
}}
.cve-stat-card-single .cve-stat-center {{
  padding: 0;
}}
.cve-stat-kicker {{
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(10px * var(--cve-font-scale, {font_scale}));
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--cve-canvas-accent, #72F5FA);
  opacity: 0.9;
}}
.cve-stat-label {{
  font-size: calc(16.5px * var(--cve-font-scale, {font_scale}));
  font-weight: 500;
  color: var(--cve-fg);
  line-height: 1.35;
}}
.cve-stat-delta-col {{
  padding: 20px var(--cve-cell-pad-x);
  display: flex;
  align-items: center;
}}
.cve-stat-delta {{
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(11.5px * var(--cve-font-scale, {font_scale}));
  font-weight: 500;
  letter-spacing: 0.04em;
  color: var(--cve-canvas-accent, var(--cve-accent-text));
  background: rgba(52, 100, 253, 0.20);
  border: 1px solid rgba(114, 245, 250, 0.30);
  padding: 7px 14px;
  border-radius: 6px;
  white-space: nowrap;
}}

/* Scorecard Grid Block — strict flush-left text & right-aligned uniform status badges */
.cve-panel-scorecard {{
  flex: 1.3;
  display: flex;
  flex-direction: column;
  background: {scorecard_bg};
  border: {scorecard_border};
  border-radius: 10px;
  overflow: hidden;
  box-shadow: {scorecard_shadow};
}}
.cve-scorecard-table {{
  width: 100%;
  height: 100%;
  border-collapse: collapse;
  text-align: left;
  table-layout: fixed;
}}
.cve-scorecard-table th {{
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(11.5px * var(--cve-font-scale, {font_scale}));
  font-weight: 600;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--cve-muted, var(--cve-fg));
  background: rgba(10, 24, 42, 0.72);
  border-bottom: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.22));
  padding: 14px var(--cve-cell-pad-x);
}}
.cve-scorecard-table th + th,
.cve-scorecard-table td + td {{
  border-left: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.14));
}}
.cve-scorecard-table th.cve-col-category {{
  width: var(--cve-col1-width);
}}
.cve-scorecard-table th.cve-col-focal {{
  color: var(--cve-canvas-accent, var(--cve-fg));
  background: {th_focal_bg};
  border-top: 2px solid var(--cve-canvas-accent, var(--cve-primary));
}}
.cve-scorecard-table td {{
  border-bottom: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.12));
  padding: 15px var(--cve-cell-pad-x);
  font-size: calc(15.5px * var(--cve-font-scale, {font_scale}));
  line-height: 1.38;
  vertical-align: middle;
  color: var(--cve-muted, var(--cve-fg));
}}
.cve-scorecard-table tbody tr:last-child td {{
  border-bottom: none;
}}
.cve-scorecard-table td.cve-td-category {{
  color: var(--cve-fg);
  background: rgba(10, 24, 42, 0.34);
}}
.cve-cat-stack {{
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.cve-cat-kicker {{
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(10px * var(--cve-font-scale, {font_scale}));
  font-weight: 500;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: var(--cve-canvas-accent, var(--cve-primary));
  opacity: 0.88;
}}
.cve-cat-title {{
  font-weight: 600;
  color: var(--cve-fg);
  line-height: 1.28;
}}
.cve-scorecard-table td.cve-td-focal {{
  color: var(--cve-fg);
  font-weight: 500;
  background: {td_focal_bg};
}}
.cve-cell-inner {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}}
.cve-cell-text {{
  flex: 1;
  min-width: 0;
}}

/* Fixed-Width Monospace Status Badges */
.cve-pill {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 72px;
  padding: 4px 8px;
  border-radius: 4px;
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(10px * var(--cve-font-scale, {font_scale}));
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  text-align: center;
  flex-shrink: 0;
}}
.cve-pill-yours {{
  background: #000000;
  color: #26D07C;
  border: 1.4px solid #26D07C;
}}
.cve-pill-shipped {{
  background: #000000;
  color: #26D07C;
  border: 1.4px solid #26D07C;
}}
.cve-pill-partial {{
  background: #000000;
  color: #FBBF24;
  border: 1.4px solid #FBBF24;
}}
.cve-pill-scoped {{
  background: #000000;
  color: #9AA3B2;
  border: 1.4px solid rgba(244, 246, 251, 0.24);
}}

/* Asymmetric Agency Contrast Pair Block — flush-left rows matching ScorecardGrid padding */
.cve-contrast-pair {{
  flex: 0.95;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  align-items: stretch;
}}
.cve-contrast-card {{
  border-radius: 10px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}}
.cve-contrast-left {{
  background: {contrast_left_bg};
  border: {contrast_left_border};
  border-top: 2px solid rgba(154, 163, 178, 0.35);
}}
.cve-contrast-right {{
  background: {contrast_right_bg};
  border: {contrast_right_border};
  border-top: 2px solid var(--cve-canvas-accent, var(--cve-primary));
  box-shadow: {contrast_right_shadow};
}}
.cve-contrast-header {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px var(--cve-cell-pad-x);
  background: rgba(10, 24, 42, 0.55);
  border-bottom: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.16));
  flex-shrink: 0;
}}
.cve-contrast-title {{
  font-size: calc(15.5px * var(--cve-font-scale, {font_scale}));
  font-weight: 600;
  letter-spacing: -0.01em;
}}
.cve-contrast-left .cve-contrast-title {{
  color: var(--cve-muted, #C4CFEE);
}}
.cve-contrast-right .cve-contrast-title {{
  color: var(--cve-fg);
}}
.cve-contrast-tag {{
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(10px * var(--cve-font-scale, {font_scale}));
  letter-spacing: 0.10em;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}}
.cve-contrast-left .cve-contrast-tag {{
  color: #FDA4AF;
  background: rgba(244, 63, 94, 0.12);
  border: 1px solid rgba(244, 63, 94, 0.24);
}}
.cve-contrast-right .cve-contrast-tag {{
  color: var(--cve-canvas-accent, #72F5FA);
  background: rgba(52, 100, 253, 0.22);
  border: 1px solid rgba(114, 245, 250, 0.32);
}}
.cve-contrast-list {{
  list-style: none;
  display: flex;
  flex-direction: column;
  flex: 1;
}}
.cve-contrast-item {{
  flex: 1;
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: calc(15px * var(--cve-font-scale, {font_scale}));
  line-height: 1.38;
  padding: 12px var(--cve-cell-pad-x);
  border-bottom: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.10));
}}
.cve-contrast-item:last-child {{
  border-bottom: none;
}}
.cve-contrast-left .cve-contrast-item {{
  color: var(--cve-muted, #C4CFEE);
}}
.cve-contrast-right .cve-contrast-item {{
  color: var(--cve-fg);
  font-weight: 500;
}}
.cve-marker-left,
.cve-marker-right {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(11px * var(--cve-font-scale, {font_scale}));
  font-weight: 600;
  flex-shrink: 0;
}}
.cve-marker-left {{
  color: #FB7185;
  background: rgba(244, 63, 94, 0.14);
  border: 1px solid rgba(244, 63, 94, 0.26);
}}
.cve-marker-right {{
  color: var(--cve-canvas-accent, #72F5FA);
  background: rgba(52, 100, 253, 0.22);
  border: 1px solid rgba(114, 245, 250, 0.32);
}}

/* Editorial Colophon Footer */
.cve-footer {{
  margin-top: var(--cve-section-gap);
  padding-top: 12px;
  border-top: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.16));
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  flex-shrink: 0;
}}
.cve-footnotes {{
  font-size: calc(11px * var(--cve-font-scale, {font_scale}));
  color: var(--cve-muted, var(--cve-fg));
  opacity: 0.80;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
  line-height: 1.35;
}}
.cve-fn-id {{
  font-family: var(--cve-font-mono, monospace);
  color: var(--cve-canvas-accent, var(--cve-primary));
  margin-right: 6px;
}}
.cve-colophon-domain {{
  font-family: var(--cve-font-mono, monospace);
  font-size: calc(11px * var(--cve-font-scale, {font_scale}));
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--cve-canvas-accent, var(--cve-muted));
  white-space: nowrap;
  opacity: 0.88;
}}

/* Portrait Social 4x5 (1080x1350) Rhythm Overrides */
.cve-artboard[data-viewport="social-4x5"] {{
  --cve-col1-width: 28%;
  --cve-cell-pad-x: 22px;
  --cve-section-gap: 22px;
  padding: 46px 52px 38px 52px;
}}
.cve-artboard[data-viewport="social-4x5"] .cve-title {{
  font-size: calc(36px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="social-4x5"] .cve-subtitle {{
  font-size: calc(16px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="social-4x5"] .cve-stat-value-col,
.cve-artboard[data-viewport="social-4x5"] .cve-stat-center,
.cve-artboard[data-viewport="social-4x5"] .cve-stat-delta-col {{
  padding-top: 24px;
  padding-bottom: 24px;
}}
.cve-artboard[data-viewport="social-4x5"] .cve-scorecard-table th {{
  padding: 16px var(--cve-cell-pad-x);
}}
.cve-artboard[data-viewport="social-4x5"] .cve-scorecard-table td {{
  padding: 18px var(--cve-cell-pad-x);
  font-size: calc(15.5px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="social-4x5"] .cve-contrast-header {{
  padding: 16px var(--cve-cell-pad-x);
}}
.cve-artboard[data-viewport="social-4x5"] .cve-contrast-item {{
  padding: 14px var(--cve-cell-pad-x);
  font-size: calc(15px * var(--cve-font-scale, {font_scale}));
}}

/* Portrait Social 4x5 with 2 Blocks (e.g. StatCallout + ScorecardGrid) */
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] {{
  --cve-section-gap: 28px;
  padding: 54px 56px 44px 56px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-header {{
  padding-bottom: 24px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-title {{
  font-size: 42px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-subtitle {{
  font-size: 18.5px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-stat-value-col,
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-stat-center,
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-stat-delta-col {{
  padding-top: 34px;
  padding-bottom: 34px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-stat-value {{
  font-size: 62px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-stat-label {{
  font-size: 19px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-scorecard-table th {{
  padding: 22px var(--cve-cell-pad-x);
  font-size: 13px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-scorecard-table td {{
  padding: 28px var(--cve-cell-pad-x);
  font-size: 19px;
  line-height: 1.45;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-cat-kicker {{
  font-size: 11.5px;
}}
.cve-artboard[data-viewport="social-4x5"][data-blocks="2"] .cve-cat-title {{
  font-size: 20px;
}}

/* Portrait Executive One-Pager (1240x1754) Rhythm Overrides */
.cve-artboard[data-viewport="onepager-pdf"] {{
  --cve-col1-width: 26%;
  --cve-cell-pad-x: 28px;
  --cve-section-gap: 24px;
  padding: 54px 64px 44px 64px;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-header {{
  padding-bottom: 22px;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-logo svg {{
  height: 38px;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-title {{
  font-size: calc(44px * var(--cve-font-scale, {font_scale}));
  line-height: 1.12;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-subtitle {{
  font-size: calc(18.5px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-stat-value-col,
.cve-artboard[data-viewport="onepager-pdf"] .cve-stat-center,
.cve-artboard[data-viewport="onepager-pdf"] .cve-stat-delta-col {{
  padding-top: 26px;
  padding-bottom: 26px;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-stat-value {{
  font-size: calc(56px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-stat-label {{
  font-size: calc(18.5px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-panel-scorecard {{
  flex: 1.25;
}}
.cve-panel-bar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 13px var(--cve-cell-pad-x);
  background: rgba(10, 24, 42, 0.85);
  border-bottom: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.18));
  font-family: var(--cve-font-mono, monospace);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--cve-canvas-accent, #72F5FA);
  flex-shrink: 0;
}}
.cve-panel-bar-right {{
  color: var(--cve-muted, #C4CFEE);
  opacity: 0.85;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-scorecard-table th {{
  padding: 18px var(--cve-cell-pad-x);
  font-size: calc(12.5px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-scorecard-table td {{
  padding: 20px var(--cve-cell-pad-x);
  font-size: calc(17.5px * var(--cve-font-scale, {font_scale}));
  line-height: 1.42;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-cat-kicker {{
  font-size: calc(11px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-cat-title {{
  font-size: calc(18.5px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-contrast-pair {{
  flex: 0.72;
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-contrast-header {{
  padding: 16px var(--cve-cell-pad-x);
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-contrast-title {{
  font-size: calc(18px * var(--cve-font-scale, {font_scale}));
}}
.cve-artboard[data-viewport="onepager-pdf"] .cve-contrast-item {{
  font-size: calc(17px * var(--cve-font-scale, {font_scale}));
  padding: 16px var(--cve-cell-pad-x);
  line-height: 1.42;
}}
.cve-exec-strip {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  flex-shrink: 0;
}}
.cve-exec-card {{
  background: rgba(10, 24, 42, 0.56);
  border: 1px solid var(--cve-rule, rgba(196, 207, 238, 0.16));
  border-top: 2px solid var(--cve-canvas-accent, var(--cve-primary));
  border-radius: 10px;
  padding: 18px var(--cve-cell-pad-x);
  display: flex;
  flex-direction: column;
  gap: 8px;
}}
.cve-exec-kicker {{
  font-family: var(--cve-font-mono, monospace);
  font-size: 10.5px;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: var(--cve-canvas-accent, #72F5FA);
}}
.cve-exec-body {{
  font-size: 15.5px;
  font-weight: 500;
  color: var(--cve-fg);
  line-height: 1.38;
}}
</style>
</head>
<body>
<div class="cve-artboard" data-viewport="{viewport_slug}" data-blocks="{block_count}">
  <div class="cve-header">
    <div class="cve-header-main">
      <div class="cve-eyebrow">
        <span class="cve-eyebrow-dot"></span>
        <span>{sanitize_text(eyebrow)}</span>
      </div>
      <h1 class="cve-title">{_format_editorial_text(self.spec.title)}</h1>
      {subtitle_html}
    </div>
    {logo}
  </div>
  <div class="cve-body">
    {"".join(blocks_html)}
  </div>
  <div class="cve-footer">
    {footnotes_html}
    {domain_html}
  </div>
</div>
</body>
</html>"""

    def _render_category_cell(self, category: str, row_idx: int) -> str:
        """Render Column 1 category cell with stacked monospace kicker and flush-left title."""
        if " · " in category:
            kicker, title = category.split(" · ", 1)
            kicker_html = _format_editorial_text(kicker.strip())
            title_html = _format_editorial_text(title.strip())
        else:
            kicker_html = f"0{row_idx} // DIMENSION"
            title_html = _format_editorial_text(category.strip())
        return (
            f'<td class="cve-td-category">'
            f'<div class="cve-cat-stack">'
            f'<span class="cve-cat-kicker">{kicker_html}</span>'
            f'<strong class="cve-cat-title">{title_html}</strong>'
            f"</div>"
            f"</td>"
        )

    def _render_scorecard(self, block: ScorecardGridBlock, viewport_slug: str = "") -> str:
        max_cells = max((len(r.cells) for r in block.rows), default=0)
        # Ensure column headers align with [category_col, *cell_cols]
        if len(block.columns) == max_cells and max_cells > 0:
            col_headers = ["Stage / Dimension", *block.columns]
        else:
            col_headers = list(block.columns)

        # Identify which cell column (0-indexed among row.cells) is the focal ("ours" / "now") column
        focal_cell_idx: int | None = None
        if max_cells > 0:
            best_score = -1
            for c_idx in range(max_cells):
                header_text = (
                    col_headers[c_idx + 1].lower() if (c_idx + 1) < len(col_headers) else ""
                )
                score = 0
                if any(
                    kw in header_text for kw in ("ours", "now", "focal", "target", "mockplatform")
                ):
                    score += 3
                for r in block.rows:
                    if c_idx < len(r.cells) and r.cells[c_idx].status.value in ("yours", "shipped"):
                        score += 2
                if score > best_score and score > 0:
                    best_score = score
                    focal_cell_idx = c_idx

        th_parts: list[str] = []
        for idx, col_name in enumerate(col_headers):
            if idx == 0:
                th_parts.append(
                    f'<th class="cve-col-category">{_format_editorial_text(col_name)}</th>'
                )
            elif focal_cell_idx is not None and (idx - 1) == focal_cell_idx:
                th_parts.append(
                    f'<th class="cve-col-focal">{_format_editorial_text(col_name)}</th>'
                )
            else:
                th_parts.append(f"<th>{_format_editorial_text(col_name)}</th>")

        trs: list[str] = []
        for row_idx, row in enumerate(block.rows, start=1):
            tds = [self._render_category_cell(row.category, row_idx)]
            for c_idx, cell in enumerate(row.cells):
                fn = (
                    f' <sup class="cve-fn-id">[{sanitize_text(cell.footnote_ref)}]</sup>'
                    if cell.footnote_ref
                    else ""
                )
                pill = f'<span class="cve-pill cve-pill-{cell.status.value}">{cell.status.value}</span>'
                td_cls = ' class="cve-td-focal"' if c_idx == focal_cell_idx else ""
                tds.append(
                    f"<td{td_cls}>"
                    f'<div class="cve-cell-inner">'
                    f'<span class="cve-cell-text">{_format_editorial_text(cell.text)}{fn}</span>'
                    f"{pill}"
                    f"</div>"
                    f"</td>"
                )
            trs.append(f"<tr>{''.join(tds)}</tr>")
        panel_bar = (
            '<div class="cve-panel-bar">'
            "<span>SECTION 01 // ARCHITECTURAL CAPABILITY MATRIX</span>"
            f'<span class="cve-panel-bar-right">{len(block.rows)} EVALUATED DIMENSIONS</span>'
            "</div>"
            if viewport_slug == "onepager-pdf"
            else ""
        )
        return (
            f'<div class="cve-panel-scorecard">'
            f"{panel_bar}"
            f'<table class="cve-scorecard-table">'
            f"<thead><tr>{''.join(th_parts)}</tr></thead>"
            f"<tbody>{''.join(trs)}</tbody>"
            f"</table>"
            f"</div>"
        )

    def _render_stat(self, block: StatCalloutBlock, total_blocks: int = 1) -> str:
        fn = (
            f' <sup class="cve-fn-id">[{sanitize_text(block.footnote_ref)}]</sup>'
            if block.footnote_ref
            else ""
        )
        delta_html = (
            f'<div class="cve-stat-delta-col"><div class="cve-stat-delta">{_format_editorial_text(block.delta)}</div></div>'
            if block.delta
            else ""
        )
        single_cls = " cve-stat-card-single" if total_blocks == 1 else ""
        return (
            f'<div class="cve-stat-card{single_cls}">\n'
            f'  <div class="cve-stat-value-col"><div class="cve-stat-value">{_format_editorial_text(block.value)}</div></div>\n'
            f'  <div class="cve-stat-center">\n'
            f'    <div class="cve-stat-kicker">VERIFIED ARCHITECTURE METRIC</div>\n'
            f'    <div class="cve-stat-label">{_format_editorial_text(block.label)}{fn}</div>\n'
            f"  </div>\n"
            f"  {delta_html}\n"
            f"</div>"
        )

    def _render_contrast(self, block: ContrastPairBlock) -> str:
        left_lis = "".join(
            f'<li class="cve-contrast-item">'
            f'<span class="cve-marker-left">×</span>'
            f"<span>{_format_editorial_text(p)}</span>"
            f"</li>"
            for p in block.left_points
        )
        right_lis = "".join(
            f'<li class="cve-contrast-item">'
            f'<span class="cve-marker-right">✓</span>'
            f"<span>{_format_editorial_text(p)}</span>"
            f"</li>"
            for p in block.right_points
        )
        return (
            f'<div class="cve-contrast-pair">\n'
            f'  <div class="cve-contrast-card cve-contrast-left">\n'
            f'    <div class="cve-contrast-header">\n'
            f'      <h3 class="cve-contrast-title">{_format_editorial_text(block.left_title)}</h3>\n'
            f'      <span class="cve-contrast-tag">BASELINE / RISK</span>\n'
            f"    </div>\n"
            f'    <ul class="cve-contrast-list">{left_lis}</ul>\n'
            f"  </div>\n"
            f'  <div class="cve-contrast-card cve-contrast-right">\n'
            f'    <div class="cve-contrast-header">\n'
            f'      <h3 class="cve-contrast-title">{_format_editorial_text(block.right_title)}</h3>\n'
            f'      <span class="cve-contrast-tag">TARGET ARCHITECTURE</span>\n'
            f"    </div>\n"
            f'    <ul class="cve-contrast-list">{right_lis}</ul>\n'
            f"  </div>\n"
            f"</div>"
        )
