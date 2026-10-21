from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class StatusPill(StrEnum):
    SHIPPED = "shipped"
    SCOPED = "scoped"
    PARTIAL = "partial"
    YOURS = "yours"


class Footnote(BaseModel):
    id: str = Field(..., pattern=r"^fn-[a-zA-Z0-9_-]+$")
    text: str = Field(..., max_length=200)


class ScorecardCell(BaseModel):
    """A cell within a ScorecardRow.

    max_length is intentionally relaxed (200) relative to viewport character budgets (40-100)
    so that layout overflow produces structured compiler diagnostics (DoesNotFitError)
    with exact metrics rather than uninformative Pydantic validation errors.
    """

    text: str = Field(..., max_length=200)
    status: StatusPill
    footnote_ref: str | None = None


class ScorecardRow(BaseModel):
    category: str = Field(..., max_length=60)
    cells: list[ScorecardCell]


class ScorecardGridBlock(BaseModel):
    type: Literal["ScorecardGrid"] = "ScorecardGrid"
    id: str
    columns: list[str]
    rows: list[ScorecardRow]


class StatCalloutBlock(BaseModel):
    type: Literal["StatCallout"] = "StatCallout"
    id: str
    value: str = Field(..., max_length=20)
    label: str = Field(..., max_length=120)
    delta: str | None = Field(None, max_length=30)
    footnote_ref: str | None = None


class ContrastPairBlock(BaseModel):
    type: Literal["ContrastPair"] = "ContrastPair"
    id: str
    left_title: str = Field(..., max_length=60)
    left_points: list[str] = Field(..., max_length=5)
    right_title: str = Field(..., max_length=60)
    right_points: list[str] = Field(..., max_length=5)


BlockUnion = Annotated[
    ScorecardGridBlock | StatCalloutBlock | ContrastPairBlock,
    Field(discriminator="type"),
]


class CVESpec(BaseModel):
    title: str = Field(..., max_length=100)
    subtitle: str | None = Field(None, max_length=150)
    profile: str
    viewport: Literal["social-4x5", "onepager-pdf", "deck-16x9", "interactive-hud", "email-card"]
    density: Literal["micro", "standard", "deep"] = "standard"
    theme: Literal["light", "dark"] = "light"
    blocks: list[BlockUnion]
    footnotes: list[Footnote] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_slide_footnote_cap(self) -> "CVESpec":
        if self.viewport in ("social-4x5", "deck-16x9") and len(self.footnotes) > 3:
            raise ValueError(
                f"Maximum 3 footnotes allowed for viewport '{self.viewport}', found {len(self.footnotes)}"
            )
        return self
