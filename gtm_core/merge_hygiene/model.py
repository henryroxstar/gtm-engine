from __future__ import annotations

from dataclasses import dataclass

# --- findings ------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One merge-field defect. ``level`` is ``block`` (must not load) or ``warn``."""

    level: str  # "block" | "warn"
    field: str  # "first" | "company" | "email"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.field}: {self.rule} — {self.detail}"
