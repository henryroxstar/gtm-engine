from __future__ import annotations

import re
from dataclasses import dataclass

ERROR = "error"
WARN = "warn"

#: Tier names are matched by equality, never substring — this pattern is a second, independent
#: guard on anything that re-reads a tier id out of a report/suppression rather than comparing it
#: directly (the equality check alone already makes the classic "D\d single-digit capture reads
#: D10 as D1" collision impossible here; deck_lint.py:109-111 documents that trap for the inline-
#: comment form, which had no equality check to fall back on).
_TIER_RE = re.compile(r"\AV[1-9]\d*\Z")


@dataclass(frozen=True)
class Tier:
    id: str
    rule: str
    severity: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity == ERROR and len(self.evidence) < 2:
            raise ValueError(
                f"{self.id} is ERROR but names {len(self.evidence)} evidence entr"
                f"{'y' if len(self.evidence) == 1 else 'ies'} — a tier may not ship (or be "
                "promoted from WARN) as ERROR until it has caught a real defect on two separate "
                "assets, named here."
            )
        if not _TIER_RE.match(self.id):
            raise ValueError(
                f"{self.id!r} is not a valid tier id (expected V<digits>, no leading 0)"
            )


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    asset: str
    excerpt: str
    fix: str


@dataclass(frozen=True)
class SafeArea:
    ratio: str
    width: int
    height: int
    left: float  # reserved fraction of width, each edge
    right: float
    top: float  # reserved fraction of height
    bottom: float


#: Target frame dims + reserved caption margins per platform ratio. ``left``/``right`` at 0.06
#: matches the observed defect (a caption box touching both edges); ``bottom`` is wider than
#: ``top`` on every ratio — that is where a caption block actually sits.
SAFE_AREAS: dict[str, SafeArea] = {
    "9:16": SafeArea("9:16", 1080, 1920, 0.06, 0.06, 0.10, 0.14),
    "4:5": SafeArea("4:5", 1080, 1350, 0.06, 0.06, 0.08, 0.12),
    "1:1": SafeArea("1:1", 1080, 1080, 0.06, 0.06, 0.08, 0.12),
    "16:9": SafeArea("16:9", 1920, 1080, 0.05, 0.05, 0.08, 0.12),
}

#: Vertical band of the frame, as fractions of height, where a presenter's face sits. A caption
#: block overlapping this band lands ON the speaker. Per-ratio, for the same reason SAFE_AREAS is:
#: where a head sits in frame is a function of the frame's shape and the framing that shape forces.
#:
#: 4:5 / 1:1 calibrated against the original defect (2026-08-18): captions.py centred the block
#: inside the safe box, which for 4:5 put it at y=595 with h=106 in a 1350px frame — 0.44-0.52 of
#: frame height, dead centre over the face. The operator's second note was "text covering face".
#: The safe area alone cannot catch this: it only reserves EDGES, so a centred block is maximally
#: far from every margin and maximally wrong.
#:
#: 9:16 / 16:9 recalibrated 2026-08-28 against three-questions-p1. A 9:16 full-bleed presenter is
#: framed head-and-shoulders, not medium-close-up, so the head sits MUCH higher: crown ≈0.09,
#: eyes ≈0.22. Captions rendered at 0.100-0.147 (upper placement, inside the safe area) landed
#: squarely on the forehead and this tier passed clean, because the 4:5-derived 0.22 floor began
#: exactly where the eyes do. The band must start above the crown, not above the brow.
FACE_BANDS: dict[str, tuple[float, float]] = {
    "9:16": (0.06, 0.62),
    "16:9": (0.06, 0.72),
    "4:5": (0.22, 0.62),
    "1:1": (0.22, 0.62),
}

#: Fallback for a ratio not in the table. The 4:5 numbers, i.e. the historical global constants.
_DEFAULT_FACE_BAND = (0.22, 0.62)


def face_band(ratio: str) -> tuple[float, float]:
    """The (top, bottom) face band for a ratio, as fractions of frame height.

    An accessor rather than a bare dict lookup because ``gtm_core.captions`` shares these numbers
    — the renderer must place a caption using the same arithmetic the linter will judge it by,
    and a KeyError on an unknown ratio there would be a worse failure than falling back."""
    return FACE_BANDS.get(ratio, _DEFAULT_FACE_BAND)
