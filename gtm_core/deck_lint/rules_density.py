from __future__ import annotations

from .model import ERROR, WARN, Finding, Slide
from .text import _has_diagram, _slide_words

# ══════════════════════════════════════════════════════════════════════════════════════
# D10 — word budget
# ══════════════════════════════════════════════════════════════════════════════════════
#
# D1 asks "does it fit the canvas"; D7 asks "is there anything to look at". Neither asks
# "is anyone going to read this". One delivered deck passed all nine tiers at 1229 words
# across 15 slides — one slide carried 160 — because every one of them *fit*. A slide can be
# perfectly composed, fully sourced, inside its click budget, and still be a wall the buyer
# skims while you talk over it.
#
# The counted text is what the audience must read on the slide. Presenter notes are excluded
# deliberately: cutting a slide moves its argument into the spoken track, so notes getting
# LONGER as slides get shorter is the intended trade, not a regression.

WORDS_PER_SLIDE = 55
WORDS_PER_SLIDE_HARD = 90
# A diagram's labels are read at a glance, in the position that gives them meaning — a
# 90-word schematic is not a 90-word paragraph, and holding both to one budget is what
# pushed this deck toward slides that were short because they were empty.
WORDS_PER_SLIDE_DIAGRAM = 95
WORDS_PER_SLIDE_DIAGRAM_HARD = 135
# The floor. A slide can fail by saying too little just as easily as too much: a headline,
# a background image and nothing else passes every other tier while explaining nothing.
# Under this, with no diagram to carry the argument, the slide is decoration.
WORDS_PER_SLIDE_MIN = 22
# A deck-wide average is the honest measure — one dense evidence slide is fine, a deck of them
# is not. Raised from 45 when diagram labels started counting toward the total: a deck that
# draws its arguments legitimately carries more words than one that hides them in pictures,
# and the per-slide caps above catch a genuine wall long before this average moves.
DECK_WORDS_AVG = 80
# ...but a FLAT average was internally inconsistent with the per-slide budgets above, and the
# inconsistency mattered: a diagram slide is allowed 95 soft, which is ABOVE this 80, so a deck
# whose every slide passed individually could still fail the deck check — permanently, with no
# edit that clears it. A warning that cannot be cleared is worse than no warning: it trains the
# reader to skim the whole tier, which is where the real errors were the day this was found.
#
# So the target is derived per slide instead. 80 sits 71% of the way from the text soft budget
# (55) to its hard cap (90); apply that same fraction to whichever pair of budgets the slide is
# actually held to, and average those. A text-only deck still gets exactly 80 — this is a
# generalisation of the old constant, not a loosening of it.
DECK_WORDS_AVG_FRACTION = (DECK_WORDS_AVG - WORDS_PER_SLIDE) / (
    WORDS_PER_SLIDE_HARD - WORDS_PER_SLIDE
)


def _deck_words_target(slides: list[Slide]) -> float:
    """The deck-average target, weighted by how many slides legitimately carry diagram labels."""
    per_slide = []
    for slide in slides:
        diagram = _has_diagram(slide)
        soft = WORDS_PER_SLIDE_DIAGRAM if diagram else WORDS_PER_SLIDE
        hard = WORDS_PER_SLIDE_DIAGRAM_HARD if diagram else WORDS_PER_SLIDE_HARD
        per_slide.append(soft + DECK_WORDS_AVG_FRACTION * (hard - soft))
    return sum(per_slide) / len(per_slide) if per_slide else float(DECK_WORDS_AVG)


_LOW_TEXT_LAYOUTS = frozenset({"chapter", "end"})


def _d10(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    counted = [s for s in slides if s.layout not in _LOW_TEXT_LAYOUTS]
    for slide in counted:
        words = _slide_words(slide)
        diagram = _has_diagram(slide)
        hard = WORDS_PER_SLIDE_DIAGRAM_HARD if diagram else WORDS_PER_SLIDE_HARD
        soft = WORDS_PER_SLIDE_DIAGRAM if diagram else WORDS_PER_SLIDE
        if words > hard:
            out.append(
                Finding(
                    "D10",
                    "wall of words",
                    ERROR,
                    slide.index,
                    f"{words} words",
                    f"over {hard} — nobody reads this while you are talking. Move the "
                    "explanation into the presenter notes and leave the claim, or route the "
                    "content to a component that carries it",
                )
            )
        elif words > soft:
            out.append(
                Finding(
                    "D10",
                    "over word budget",
                    WARN,
                    slide.index,
                    f"{words} words against a {soft}-word budget",
                    "trim to the claim — the argument belongs in the notes, not on the slide",
                )
            )
        elif words < WORDS_PER_SLIDE_MIN and not diagram:
            out.append(
                Finding(
                    "D10",
                    "under-explained",
                    ERROR,
                    slide.index,
                    f"{words} words, no diagram",
                    f"under {WORDS_PER_SLIDE_MIN} words with nothing structural on the slide — "
                    "a headline over a background is not an explanation. Draw the argument "
                    "(FlowSequence / ScopeMap / GateFunnel / ReuseTrack / ProofContrast) or "
                    "put the substance back",
                )
            )
    if counted:
        avg = sum(_slide_words(s) for s in counted) / len(counted)
        target = _deck_words_target(counted)
        if avg > target:
            drawn = sum(1 for s in counted if _has_diagram(s))
            detail = f"{avg:.0f} words/slide average across {len(counted)} slides"
            if drawn:
                detail += f" ({drawn} carrying diagrams)"
            out.append(
                Finding(
                    "D10",
                    "deck is dense",
                    WARN,
                    0,
                    detail,
                    f"target {target:.0f} — a deck that averages more than this is a "
                    "document being read aloud",
                )
            )
    return out
