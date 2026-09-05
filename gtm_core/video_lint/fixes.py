"""The human-readable ``fix`` strings a Finding carries.

Kept apart from ``evaluate`` because they are prose composition, not rule logic: the
remedy has to be honestly scoped to the ratio (a placement that cannot exist at 9:16 is an
instruction the caller cannot follow), and a report nobody reads is the same as no gate.
"""

from __future__ import annotations

from .model import SafeArea


def _screens_phrase(idxs: list) -> str:
    """ "screen 7" / "screens 7-9" / "51 screens (7-63)" — a count, never a wall of identical
    lines. A report nobody reads is the same as no gate at all."""
    if len(idxs) == 1:
        return f"screen {idxs[0]}"
    span = f"{idxs[0]}-{idxs[-1]}"
    if len(idxs) <= 3:
        return f"screens {', '.join(str(i) for i in idxs)}"
    return f"{len(idxs)} screens ({span})"


def _face_band_fix(area: SafeArea, band_top: float) -> str:
    """The remedy, honestly scoped to this ratio.

    On a full-bleed 9:16 or 16:9 presenter the face band starts ABOVE the safe box, so ``upper``
    has negative room and offering it is an instruction the caller cannot follow. Say so instead
    of naming a placement that cannot exist here."""
    upper_room = area.height * band_top - area.height * area.top
    if upper_room < 1.0:
        return (
            "use placement='lower' (gtm_core.captions.render) — at this ratio the face band "
            f"starts {abs(upper_room):.0f}px ABOVE the safe box, so there is no upper placement "
            "clear of a presenter's head; the lower third is the only one"
        )
    return (
        "render captions with placement='upper' or 'lower' (gtm_core.captions.render) — "
        "centring inside the safe box puts text on the mouth"
    )
