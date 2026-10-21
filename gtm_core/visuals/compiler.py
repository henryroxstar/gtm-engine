"""Layout compiler and budget enforcement for Composable Visual Engine (CVE)."""

from __future__ import annotations

import hashlib
from typing import Any

from gtm_core.visuals.schema import (
    ContrastPairBlock,
    CVESpec,
    ScorecardGridBlock,
    StatCalloutBlock,
)


class DoesNotFitError(RuntimeError):
    """Raised when visual content exceeds the layout budget for the target viewport."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


VIEWPORT_SPECS: dict[str, dict[str, Any]] = {
    "social-4x5": {
        "width": 1080,
        "height": 1350,
        "max_chars_cell": 60,
        "max_chars_stat": 70,
        "max_chars_contrast": 80,
        "max_rows": 6,
    },
    "onepager-pdf": {
        "width": 1240,
        "height": 1754,
        "max_chars_cell": 100,
        "max_chars_stat": 120,
        "max_chars_contrast": 120,
        "max_rows": 12,
    },
    "deck-16x9": {
        "width": 1920,
        "height": 1080,
        "max_chars_cell": 80,
        "max_chars_stat": 90,
        "max_chars_contrast": 90,
        "max_rows": 5,
    },
    "interactive-hud": {
        "width": 1440,
        "height": 900,
        "max_chars_cell": 90,
        "max_chars_stat": 100,
        "max_chars_contrast": 100,
        "max_rows": 10,
    },
    "email-card": {
        "width": 600,
        "height": 800,
        "max_chars_cell": 40,
        "max_chars_stat": 50,
        "max_chars_contrast": 60,
        "max_rows": 4,
    },
}


DENSITY_MULTIPLIERS: dict[str, float] = {
    "micro": 0.85,
    "standard": 1.0,
    "deep": 1.15,
}


def compute_cache_key(spec: CVESpec, brand_tokens_digest: str) -> str:
    """Compute content-addressable SHA256 cache key from CVESpec and brand tokens digest."""
    raw = f"{spec.model_dump_json()}:{brand_tokens_digest}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LayoutCompiler:
    """Evaluates viewport targets, density projections, and layout budgets."""

    def __init__(self, spec: CVESpec):
        self.spec = spec
        self.viewport_cfg = VIEWPORT_SPECS.get(spec.viewport, VIEWPORT_SPECS["social-4x5"])
        self.density_multiplier = DENSITY_MULTIPLIERS.get(spec.density, 1.0)

    def compute_cache_key(self, brand_tokens_digest: str) -> str:
        """Compute content-addressable cache key for this compiler's spec."""
        return compute_cache_key(self.spec, brand_tokens_digest)

    def _check_scorecard_grid(self, block: ScorecardGridBlock) -> float:
        max_rows = self.viewport_cfg["max_rows"]
        if len(block.rows) > max_rows:
            reduction_pct = int(((len(block.rows) - max_rows) / len(block.rows)) * 100)
            raise DoesNotFitError(
                f"Too many rows ({len(block.rows)}) for viewport {self.spec.viewport}",
                {
                    "block_id": block.id,
                    "field": "rows",
                    "current_count": len(block.rows),
                    "max_allowed": max_rows,
                    "overflow_chars": len(block.rows) - max_rows,
                    "target_viewport": self.spec.viewport,
                    "suggested_reduction_pct": reduction_pct,
                },
            )

        max_cell = int(self.viewport_cfg["max_chars_cell"] * self.density_multiplier)
        max_ratio = 1.0
        for row in block.rows:
            for cell in row.cells:
                ratio = len(cell.text) / max_cell
                if ratio > max_ratio:
                    max_ratio = ratio
                if ratio > 1.30:
                    reduction_pct = int(((len(cell.text) - max_cell) / len(cell.text)) * 100)
                    raise DoesNotFitError(
                        f"Text overflow in block '{block.id}' cell: {len(cell.text)} chars > max {max_cell}",
                        {
                            "block_id": block.id,
                            "field": "text",
                            "current_count": len(cell.text),
                            "max_allowed": max_cell,
                            "overflow_chars": len(cell.text) - max_cell,
                            "target_viewport": self.spec.viewport,
                            "suggested_reduction_pct": reduction_pct,
                        },
                    )
        return max_ratio

    def _check_stat_callout(self, block: StatCalloutBlock) -> float:
        max_stat = int(self.viewport_cfg["max_chars_stat"] * self.density_multiplier)
        ratio = len(block.label) / max_stat
        if ratio > 1.30:
            reduction_pct = int(((len(block.label) - max_stat) / len(block.label)) * 100)
            raise DoesNotFitError(
                f"Text overflow in StatCalloutBlock '{block.id}' label: {len(block.label)} chars > max {max_stat}",
                {
                    "block_id": block.id,
                    "field": "label",
                    "current_count": len(block.label),
                    "max_allowed": max_stat,
                    "overflow_chars": len(block.label) - max_stat,
                    "target_viewport": self.spec.viewport,
                    "suggested_reduction_pct": reduction_pct,
                },
            )
        return max(1.0, ratio)

    def _check_contrast_pair(self, block: ContrastPairBlock) -> float:
        max_contrast = int(self.viewport_cfg["max_chars_contrast"] * self.density_multiplier)
        max_ratio = 1.0
        for p in block.left_points + block.right_points:
            ratio = len(p) / max_contrast
            if ratio > max_ratio:
                max_ratio = ratio
            if ratio > 1.30:
                reduction_pct = int(((len(p) - max_contrast) / len(p)) * 100)
                raise DoesNotFitError(
                    f"Text overflow in ContrastPairBlock '{block.id}' point: {len(p)} chars > max {max_contrast}",
                    {
                        "block_id": block.id,
                        "field": "point",
                        "current_count": len(p),
                        "max_allowed": max_contrast,
                        "overflow_chars": len(p) - max_contrast,
                        "target_viewport": self.spec.viewport,
                        "suggested_reduction_pct": reduction_pct,
                    },
                )
        return max_ratio

    def compile(self) -> dict[str, Any]:
        """Compile layout and enforce physical viewport budgets."""
        max_overflow_ratio = 1.0

        for block in self.spec.blocks:
            if isinstance(block, ScorecardGridBlock):
                ratio = self._check_scorecard_grid(block)
            elif isinstance(block, StatCalloutBlock):
                ratio = self._check_stat_callout(block)
            elif isinstance(block, ContrastPairBlock):
                ratio = self._check_contrast_pair(block)
            else:
                ratio = 1.0

            if ratio > max_overflow_ratio:
                max_overflow_ratio = ratio

        # Tier 1 auto-scaling down to 0.85
        scale_factor = 1.0
        if max_overflow_ratio > 1.0:
            scale_factor = max(0.85, 1.0 / max_overflow_ratio)

        return {
            "spec": self.spec,
            "viewport": self.viewport_cfg,
            "density": self.spec.density,
            "scale_factor": round(scale_factor, 2),
        }
