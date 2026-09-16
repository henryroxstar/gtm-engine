"""Expression rules — the face a prompt asks for, against what a face actually does.

A rendered face fails in exactly two directions, and this repo had prose against only one of them.

**Under-direction** is documented: every ``expression`` field on one film set to a single word,
"calm", copied out of the profile's written prose-tone guidance, which rendered flat across all
five shots ("it looks like I don't want to be there"). ``video-script`` Step 1.6 check 4 exists
for that, and the storyboard's "muscles, not adjectives" field spec is its other half.

**Over-direction** is the one nothing named. It arrives in two costumes:

* an ADJECTIVE the writer never translated — the model picks maximum magnitude;
* a STACK of observations at once (e.g. moving multiple regions simultaneously).
A regex decides reaction vocabulary and region count; guidance lives in
``creator-brief/references/performance-lexicon.md``.

cannot refuse anything and a linter cannot teach a grammar; each does the half it can.

Three deliberate scoping choices, each of which was the other way round first:

* **Keyed on the field, never on ``role``.** The eight stacked values that motivated this rule all
  sit on ``role: broll`` shots — a story's actors, not its presenter — so a presenter-only rule
  would have been blind to the defect it was written for.
* **Release forms only in the stop list.** "eyes wet with nothing falling" and "refusing to cry"
  are the RESTRAINT this file argues for; only the release ("tears streaming", "sobbing") is
  refused, so the rule never fires on the writing it wants.
* **The region ceiling is a WARN and the stop list an ERROR.** Stock vocabulary has no legitimate
  reading in this field; a five-region stack is a craft judgement the operator may still make.
"""

from __future__ import annotations

import re

from .camera import _normalize

#: Anatomical regions a face direction can move, one entry per INDEPENDENT region. Matched against
#: :func:`_normalize`d text (lowercased, punctuation stripped), so no case or hyphen variants are
#: needed here — "half-smiling" arrives as "half smiling".
#:
#: Two boundary details carry weight. ``eye`` is word-bounded so that "eyebrows" counts once, as
#: brow, rather than twice; and ``head`` is word-bounded so "forehead" stays with the brow. A
#: region named twice in one field ("eyes closing … eyes wet") is one region, not two — the count
#: is of distinct families, because what over-directs a render is the number of things moving.
_REGION_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("brow", re.compile(r"\bbrows?\b|\beyebrows?\b|\bforehead\b")),
    ("eye", re.compile(r"\beyes?\b|\blids?\b|\bgaze\b|\bblink(?:s|ing|ed)?\b|\bwet\b|\bstare\b")),
    ("mouth", re.compile(r"\bmouth\b|\blips?\b|\bsmil\w*\b|\blaugh\w*\b|\bteeth\b|\bgrimace\b")),
    ("jaw", re.compile(r"\bjaws?\b")),
    ("chin", re.compile(r"\bchin\b")),
    ("cheek", re.compile(r"\bcheeks?\b|\bdimple\w*\b")),
    ("breath", re.compile(r"\bbreath\w*\b|\bexhal\w*\b|\binhal\w*\b|\bswallow\w*\b|\bsighs?\b|\bnose\b|\bnostrils?\b")),
    ("head", re.compile(r"\bhead\b|\bneck\b|\bnod(?:s|ded|ding)?\b|\btilts?\b")),
    ("shoulder", re.compile(r"\bshoulders?\b")),
    ("hand", re.compile(r"\bhands?\b|\bfingers?\b|\bpalms?\b|\bfists?\b")),
)  # fmt: skip

#: The grammar's own ceiling: two moving regions, one that holds still, and gaze. A fifth region
#: is a stack rather than a direction. Published through ``--rules`` so the lexicon can cite the
#: number without restating it.
_EXPRESSION_MAX_REGIONS = 4

#: Vocabulary that is a reaction shot whatever the beat. Each entry is (pattern, why) in the shape
#: :data:`gtm_core.shots_lint.camera._GESTURE_AS_DIAGRAM` already uses.
#:
#: Every one of these is the RELEASE of an emotion, never its containment, which is what makes
#: ERROR safe here: there is no beat in a piece of business communication whose face is correctly
#: described as sobbing. Restraint reaches the same feeling and survives the cut — "eyes wet with
#: nothing falling" says the thing "tears streaming" only shouts. Measured before this rule
#: shipped: zero of the twelve expression values written across the tenant's own shot lists match
#: any pattern below, so this list is a fence around a place nobody has walked, not a correction.
_STOCK_REACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbeaming\b"), "a beaming face is the stock render of 'happy'"),
    (re.compile(r"\bgrin(?:s|ning|ned)?\b"), "a grin shows teeth and reads as performed"),
    (re.compile(r"\bear to ear\b"), "the widest smile there is, and never a felt one"),
    (re.compile(r"\bwide eyed\b|\beyes wide\b"), "wide eyes are the shock signature, not attention"),
    (re.compile(r"\beyes bulging\b|\bbulging eyes\b"), "the caricature of fear"),
    (re.compile(r"\bjaws? drops?\b|\bjaw dropped\b|\bjaw dropping\b"), "the cartoon of surprise"),
    (re.compile(r"\bagape\b"), "an open mouth held is the stock render of astonishment"),
    (re.compile(r"\bgasps?\b|\bgasping\b"), "an audible reaction the face cannot hold subtly"),
    (re.compile(r"\bshocked\b"), "an adjective at maximum arousal, and a face nobody makes"),
    (re.compile(r"\bstunned\b"), "an adjective at maximum arousal, and a face nobody makes"),
    (re.compile(r"\btears (?:streaming|falling|rolling|running|pouring)\b|\bstreaming tears\b"),
     "the release; wet eyes with nothing falling plays stronger and is what adults do"),
    (re.compile(r"\bsobbing\b|\bsobs\b"), "the release of grief, not its containment"),
    (re.compile(r"\bweeping\b|\bweeps\b"), "the release of grief, not its containment"),
    (re.compile(r"\bcrying\b|\bcries\b|\bin tears\b"),
     "casting tears into an ordinary setting reads as false and takes the honesty of the rest "
     "of the piece with it"),
    (re.compile(r"\becstatic\b|\boverjoyed\b|\belated\b"), "high-arousal joy; the low-arousal "
     "sibling (relief, quiet pride, warmth) is almost always the true one"),
    (re.compile(r"\bthrilled\b"), "high-arousal joy; the low-arousal sibling is almost always the "
     "true one"),
    (re.compile(r"\bfurious\b|\benraged\b"), "high-arousal anger renders as a caricature; a set "
     "jaw and stillness read as anger and survive a close-up"),
    (re.compile(r"\bscreams?\b|\bscreaming\b"), "a scream is an action for `motion_prompt` at "
     "best, and a face at maximum arousal at worst"),
    (re.compile(r"\bhysterical(?:ly)?\b"), "an adjective at maximum arousal, and a face nobody "
     "makes"),
    (re.compile(r"\bthrows? (?:his|her|their) head back\b|\bhead thrown back\b"),
     "a body action in a face field, and a stock one"),
    (re.compile(r"\bpunch(?:es|ing)? the air\b|\bfist pumps?\b"),
     "a body action in a face field, and a stock one"),
)  # fmt: skip

#: Where the lexicon lives, named once so every message points at the same file.
_LEXICON = "creator-brief/references/performance-lexicon.md"


def _regions_named(text: str) -> list[str]:
    """The distinct facial/body regions a direction moves, in the order this module lists them."""
    return [family for family, pattern in _REGION_FAMILIES if pattern.search(text)]


def _lint_expression(shot: dict, prefix: str, errors: list[str], warnings: list[str]) -> None:
    """A face direction names muscles, at a stated size, on as few regions as the beat needs."""
    raw = shot.get("expression")
    if not isinstance(raw, str) or not raw.strip():
        # An absent expression is a SCRIPT gap, and it already has two homes that can see the
        # whole list at once: `video-script` Step 1.6 check 4, and `video-storyboard`, which
        # infers one and flags it. Firing here would repeat them per shot with less to say.
        return

    text = _normalize(raw)

    for pattern, why in _STOCK_REACTIONS:
        hit = pattern.search(text)
        if hit is None:
            continue
        errors.append(
            f"{prefix}.expression names a stock reaction ({hit.group(0)!r}): {why}. A model "
            f"renders the label at full magnitude, so this arrives as a reaction shot rather "
            f"than a person. Write the muscle and its size instead — one region, a small action, "
            f"what holds still, and where the eyes go ({_LEXICON})"
        )
        return

    regions = _regions_named(text)
    if not regions:
        warnings.append(
            f"{prefix}.expression names no facial region ({raw!r}) — it is a tone word, and tone "
            f"is a property of prose, not of a face. An unnamed magnitude renders at the "
            f"maximum, which is the exaggeration this rule exists to catch. Say which region "
            f"moves, how far, and what stays still ({_LEXICON})"
        )
        return

    if len(regions) > _EXPRESSION_MAX_REGIONS:
        warnings.append(
            f"{prefix}.expression names {len(regions)} facial regions "
            f"({', '.join(regions)}) — past the {_EXPRESSION_MAX_REGIONS} the grammar allows "
            f"(two that move, one that holds, the eyes). Every clause can be a true marker and "
            f"the stack still render as anguish rather than restraint, because a face does not "
            f"do all of them at once. Keep the one that carries the beat, move the body cues to "
            f"`motion_prompt`, and let the rest hold still ({_LEXICON})"
        )
