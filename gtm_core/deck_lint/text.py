from __future__ import annotations

import math
import re

from .catalog import DIAGRAM_COMPONENTS, VISUAL_COMPONENTS
from .model import Slide

# Usable text lines per layout, calibrated against measured headroom on a 980×551 canvas:
# at these numbers the slides the probe reports with <24px of headroom sit at budget, and
# a slide one paragraph past them trips. Slides routinely override font sizes in scoped
# CSS, so this is an estimate with a real error bar — it warns, and the probe decides.
LAYOUT_LINE_BUDGET: dict[str, int] = {
    "cover": 21,
    "default": 21,
    "pillars": 18,
    "statement": 18,
    "two-pane": 21,
    "chapter": 8,
    "end": 10,
    "handoff": 12,
}

# Characters per rendered line. A two-pane gives ~66% of the canvas to text.
CHARS_PER_LINE = 108
TWO_PANE_CHARS_PER_LINE = 74

_TAG = re.compile(r"<[^>]+>")
_STYLE_BLOCK = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
_BANNER = re.compile(r"<Banner\b[^>]*>(.*?)</Banner>", re.DOTALL | re.IGNORECASE)
_CARD = re.compile(r"<(GlassCard|PillarCard|StatBlock)\b", re.IGNORECASE)


def _text_of(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()


def _wrapped_lines(text: str, per_line: int) -> int:
    return max(1, math.ceil(len(text) / per_line)) if text else 0


def _fit_estimate(slide: Slide) -> int:
    """Estimated rendered text lines for a slide, excluding chrome."""
    body = _STYLE_BLOCK.sub("", slide.body)
    per_line = TWO_PANE_CHARS_PER_LINE if slide.layout == "two-pane" else CHARS_PER_LINE

    lines = 0
    # Headline: <h2> wraps hard and sits at display size — 2 rendered lines per <br/>.
    for h in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", body, re.DOTALL | re.IGNORECASE):
        lines += 2 * (h.count("<br") + 1)
    body = re.sub(r"<h[12][^>]*>.*?</h[12]>", "", body, flags=re.DOTALL | re.IGNORECASE)

    # Cards laid out in a grid cost one row each, not one per card.
    cards = len(_CARD.findall(body))
    if cards:
        lines += 4 * max(1, math.ceil(cards / 3))
        body = re.sub(
            r"<(GlassCard|PillarCard|StatBlock)\b.*?</\1>",
            "",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )

    for banner in _BANNER.findall(body):
        lines += _wrapped_lines(_text_of(banner), 70) + 1
    body = _BANNER.sub("", body)

    for para in re.findall(
        r"<(?:p|li|blockquote)\b[^>]*>(.*?)</(?:p|li|blockquote)>", body, re.DOTALL | re.IGNORECASE
    ):
        lines += _wrapped_lines(_text_of(para), per_line)

    return lines


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _has_image(slide: Slide) -> bool:
    return "image:" in "\n".join(f"{k}: {v}" for k, v in slide.frontmatter.items())


def _has_visual(slide: Slide) -> bool:
    """
    A frontmatter `image:` deliberately does NOT count.

    It used to. That is how a customer deck reached a state where six slides each had a
    generated background image, satisfied this rule, satisfied the D10 word budget, and
    explained less than the version they replaced — the image restated the headline and the
    prose that carried the argument had been deleted to make room for it. A background photo
    is atmosphere. Only a component puts information on the slide.
    """
    body = _STYLE_BLOCK.sub("", slide.body)
    return any(re.search(rf"<{c}\b", body) for c in VISUAL_COMPONENTS)


def _has_diagram(slide: Slide) -> bool:
    body = _STYLE_BLOCK.sub("", slide.body)
    return any(re.search(rf"<{c}\b", body) for c in DIAGRAM_COMPONENTS)


# Text the audience reads often lives in a component *prop*, not in the markup body:
# `<AskBox question="…" />`, `<BreakTests :tests="[{claim: '…'}]" />`, `<StackDiagram :layers="…" />`.
# `_text_of` strips whole tags, so that text vanishes — which would make exactly the
# component-led slides this rule exists to police look empty. Count those strings too.
_PROP_ARRAY = re.compile(
    r":(?:tests|layers|items|nodes|lines|cards|stats|rows|steps|pillars"
    r"|checks|entities|gaps|blockers|columns|zones|anchors)=\"(\[.*?\])\"",
    re.DOTALL | re.IGNORECASE,
)
_ARRAY_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'|`([^`]*)`")

# Audience-visible text also lives in *flat* props — `left-caption="…"`, `ships-items="…"`,
# `in-sub="…"`, `heading="…"`. Counting only the array props would let a diagram component
# park forty words of real copy outside the budget, which is the same blind spot that let
# component-led slides read as empty before `_prop_text` existed at all.
_FLAT_PROP = re.compile(r'(?<![:\w-])([a-z][a-z0-9-]*)="([^"]{2,})"')
_NON_TEXT_PROPS = frozenset(
    {
        "class",
        "style",
        "src",
        "href",
        "image",
        "icon",
        "kind",
        "pad",
        "variant",
        "position",
        "page",
        "tag",
        "side",
        "state",
        "color",
        "size",
        "top",
        "left",
        "duration",
        "width",
        "height",
        "id",
        "role",
        "aria-label",
        "v-click",
        "v-if",
        "v-for",
        "v-show",
        "transition",
        "layout",
        "align",
        "name",
    }
)


def _flat_prop_text(body: str) -> list[str]:
    return [
        value
        for prop, value in _FLAT_PROP.findall(body)
        if prop not in _NON_TEXT_PROPS and " " in value
    ]


def _prop_text(body: str) -> str:
    parts: list[str] = _flat_prop_text(body)
    for array in _PROP_ARRAY.findall(body):
        for single, backtick in _ARRAY_STRING.findall(array):
            value = single or backtick
            # Skip the short machine-ish values (icon names, keys like 'WHO') — they are labels,
            # not prose. Anything with a space is content the audience reads.
            if " " in value:
                parts.append(value.replace("\\'", "'"))
    return " ".join(parts)


def _slide_words(slide: Slide) -> int:
    body = _STYLE_BLOCK.sub("", slide.body)
    return len((_text_of(body) + " " + _prop_text(body)).split())
