from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ...vo_timings import PhraseCue
from ..base import SceneError

# ── checkpoint-flow — the payoff diagram: one place every reach goes through ───────────────────


#: WHICH NARRATION PHRASE DRIVES WHICH ANIMATION WINDOW on the checkpoint card.
#:
#: The phrases live here, beside the drawing they animate, because the binding is a fact about
#: the CARD: `DROP` exists because this function drops a gateway node onto the diagram, and
#: nothing outside the function knows that window exists. The TIMINGS are a fact about the audio
#: and live with the audio, in `<id>.words.json` — they arrive as `timing`.
#:
#: `NODES`, `LINK` and `DIRECT` are deliberately absent: they are the card establishing itself
#: before the first cue, not something the voice names. Cueing "the diagram exists" to a phrase
#: would be inventing a cue the VO does not contain, so they are derived from `DROP` instead.
#: A CUE PHRASE MAY NOT CONTAIN THE COMPANY NAME. Two independent reasons, and the second was
#: only discovered on 2026-09-03:
#:
#:   1. Engine code cannot name a real tenant (de-brand rule), so the name here has to be a
#:      placeholder — and `match_phrase` is a strict substring match on a normalised character
#:      stream, so a placeholder NEVER matches. Until today `DROP` and `STREAM` each read
#:      "<ordinal> is Nexus's <product name>"; against the real VO both
#:      raised PhraseNotFound, which is why this card has ALWAYS silently fallen back to the
#:      literals below and why the 2026-08-31 hardening pass's ~0.96s gateway lag was never
#:      actually closed.
#:   2. Even a correctly-substituted company name would not match, because `spoken` carries a
#:      RESPELLING, not the name: the kit's [pronunciation] table exists precisely so the VO says
#:      something the TTS engine pronounces correctly, and on 2026-09-03 this film's h15 VO was
#:      re-cut with the name respelled. A cue anchored on the company is anchored on a token that
#:      is deliberately not in the audio.
#:
#: So the cues anchor on the ENUMERATING CLAUSE instead — "First is" / "Second is" — which is what
#: actually locates the moment in the narration, is company-free by construction, and survives any
#: respelling. Anchoring on the product name was the other candidate and was
#: rejected on measurement: it resolves 0.049 of the card LATER than the shipped literal, and this
#: card's known defect was already that the gateway arrived late. The clause opener lands 0.018
#: EARLIER, which is the direction the hardening pass's own measurement said was correct.
_CHECKPOINT_CUES: tuple[PhraseCue, ...] = (
    PhraseCue(key="DROP", phrase="First is"),
    PhraseCue(key="REROUTE", phrase="Every call you just saw passes through it"),
    PhraseCue(key="NOTE", phrase="observability"),
    PhraseCue(key="STREAM", phrase="Second is"),
    PhraseCue(key="SUB1", phrase="input and output filters"),
    PhraseCue(key="SUB2", phrase="subjective controls"),
)

#: Measured from audio/vo/h15.wav (26.59s) — the VO this card was cut against — by dividing each
#: cue phrase's word timestamps by the wav's length. Kept as the documented FALLBACK, not as the
#: truth: pass `timing` (CLI `--words-json`) whenever the VO exists, and the scene's own JSON
#: output records which of the two was used, so the deliberate case and the accidental one never
#: look the same on disk.
_CHECKPOINT_DEFAULT_TIMING: Mapping[str, tuple[float, float]] = MappingProxyType(
    {
        "DROP": (0.145, 0.215),
        "REROUTE": (0.225, 0.32),
        "NOTE": (0.40, 0.52),
        "STREAM": (0.645, 0.745),
        # The two Stream sub-lines arrive separately, and that is what keeps the last quarter of a
        # 26.6s card alive. With every element up by 0.745 the card froze for its final 6.8s — a
        # still frame under the sentence that explains the product, and the exact shape V9
        # static_shots exists to catch.
        "SUB1": (0.79, 0.87),
        "SUB2": (0.90, 0.98),
    }
)
_CHECKPOINT_DEFAULT_TIMING_SOURCE = "default-literals (audio/vo/h15.wav, 26.59s)"

#: Every scene whose animation windows are cued off narration, so `gtm_core.vo_timings` can build
#: a timing map for one by name without owning a second copy of the phrases.
SCENE_CUES: dict[str, tuple[PhraseCue, ...]] = {"checkpoint-flow": _CHECKPOINT_CUES}


def _resolve_timing(
    scene: str,
    default: Mapping[str, tuple[float, float]],
    override: Mapping[str, tuple[float, float]] | None,
) -> Mapping[str, tuple[float, float]]:
    """The scene's windows: the measured map when there is one, the documented literals otherwise.

    A PARTIAL override is refused rather than merged. Half-measured, half-literal timing is the
    silently-wrong state: if the VO was re-cut then every window is stale, so a map supplying four
    of six keys is a bug, and filling the other two from the old literals would hide it behind a
    card that mostly works.
    """
    if override is None:
        return default
    unknown = sorted(set(override) - set(default))
    if unknown:
        raise SceneError(
            f"{scene}: timing map names {unknown}, which this scene has no window for — "
            f"known keys are {sorted(default)}"
        )
    missing = sorted(set(default) - set(override))
    if missing:
        raise SceneError(
            f"{scene}: timing map is missing {missing}. A partial map is refused, not merged with "
            "the defaults: if the VO was re-cut then every window is stale, and filling the gaps "
            "from the old literals would hide that behind a card that mostly works."
        )
    return override
