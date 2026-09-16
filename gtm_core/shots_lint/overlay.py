from __future__ import annotations

import re

# ── production.overlay — a chat bubble drawn beside the phone, burned in post ──────────────────
#
# A shot may carry, under its free-form `production` namespace, ONE structured key that a reader
# actually reads: `overlay`. It describes a bubble the finish layer draws over the shot so the
# phone in frame can be held at a natural angle with its screen away from the lens — an OUTGOING
# bubble (what the character types), an INCOMING one (what a tool or a person sends back), or a
# small ACTION CHIP (the one thing they tap). The screen itself stays blank; the words live here.
#
# WHY THIS RULE EXISTS. A shot list carried exactly this data on four shots, and nothing in the
# tree consumed it: no scene drew the bubble, no finish verb composited it, no lint read the key.
# The film shipped its bubbles as a note in a JSON file. The vocabulary below is the shape both
# consumers now share — `screen_ui`'s `chat-bubble` scene draws it and `video_finish.overlays`
# composites it — and this module is the DEPENDENCY-FREE home for it, because the linter has no
# third-party imports by design and the scene needs Pillow. The scene imports the vocabulary from
# here, never the other way round.

#: The three canonical kinds. A shot list may spell them the way the overlay grammar does
#: ("outgoing bubble", "incoming bubble", "action chip"); `normalize_overlay_kind` folds those.
OVERLAY_KINDS: frozenset[str] = frozenset({"outgoing", "incoming", "chip"})

_KIND_SPELLINGS: dict[str, str] = {
    "outgoing": "outgoing",
    "outgoing bubble": "outgoing",
    "incoming": "incoming",
    "incoming bubble": "incoming",
    "chip": "chip",
    "action chip": "chip",
}

#: A bubble is read in a beat. Past this many characters it is a paragraph on a phone screen,
#: which the scene would refuse at its line ceiling anyway — refusing at plan time is free.
OVERLAY_TEXT_MAX_CHARS = 160

_SIDES: frozenset[str] = frozenset({"left", "right"})

#: Prose that asks the render for legible words ON the phone screen while an overlay already
#: carries them. Deliberately narrow — the same posture `_lint_no_in_frame_text` takes: a rule
#: that fires on "the screen stays blank" is a rule people learn to ignore.
_SCREEN_TEXT_RE = re.compile(
    r"screen\s+(?:shows|reads|displays)|text\s+on\s+(?:the\s+)?screen", re.IGNORECASE
)
_SCREEN_TEXT_SCANNED_FIELDS = ("visual", "motion_prompt")


def normalize_overlay_kind(raw: object) -> str | None:
    """The canonical kind for a shot-list spelling, or ``None`` when it is not one.

    Whitespace and case are folded ("Outgoing Bubble" is fine); anything else is not. A compound
    such as "action chip, then outgoing bubble" is refused on purpose: it is two overlays, and a
    list of two entries is how the shot list says so.
    """
    folded = " ".join(str(raw or "").lower().split())
    return _KIND_SPELLINGS.get(folded)


def overlay_entries(shot: dict) -> list[object]:
    """Every ``production.overlay`` entry on a shot, as a list — one dict, or the list as given.

    Returns ``[]`` for a shot with no overlay. Shared by the linter and the finish verb so the
    two cannot disagree about whether a shot carries an overlay at all.
    """
    production = shot.get("production")
    if not isinstance(production, dict):
        return []
    raw = production.get("overlay")
    if raw is None:
        return []
    return list(raw) if isinstance(raw, list) else [raw]


def _lint_one(entry: object, where: str, errors: list[str]) -> None:
    if not isinstance(entry, dict):
        errors.append(f"{where} is not an object")
        return
    kind = normalize_overlay_kind(entry.get("kind"))
    if kind is None:
        errors.append(
            f"{where}.kind {entry.get('kind')!r} is not one of "
            f"{sorted(_KIND_SPELLINGS)}. A compound ('chip, then bubble') is two overlays — "
            "write them as a list"
        )
    text = str(entry.get("text") or "").strip()
    if not text:
        errors.append(f"{where}.text is missing or empty — an overlay with no words draws nothing")
    elif len(text) > OVERLAY_TEXT_MAX_CHARS:
        errors.append(
            f"{where}.text is {len(text)} characters, past the {OVERLAY_TEXT_MAX_CHARS} ceiling "
            "— a bubble is read in a beat; shorten it or split the beat"
        )
    side = entry.get("side")
    if side is not None and side not in _SIDES:
        errors.append(f"{where}.side {side!r} is not one of {sorted(_SIDES)}")
    for key in ("y_frac", "w_frac"):
        value = entry.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
            errors.append(f"{where}.{key} {value!r} must be a number strictly between 0 and 1")


def _lint_overlay(shot: dict, prefix: str, errors: list[str], warnings: list[str]) -> None:
    """Check a shot's ``production.overlay`` bubble spec, and warn if the screen also shows text."""
    entries = overlay_entries(shot)
    if not entries:
        return
    listed = isinstance(shot["production"]["overlay"], list)
    for n, entry in enumerate(entries):
        where = f"{prefix}.production.overlay" + (f"[{n}]" if listed else "")
        _lint_one(entry, where, errors)

    for field_name in _SCREEN_TEXT_SCANNED_FIELDS:
        match = _SCREEN_TEXT_RE.search(str(shot.get(field_name, "") or ""))
        if match:
            warnings.append(
                f"{prefix}.{field_name} describes legible text on the phone screen "
                f"({match.group(0)!r}) while production.overlay carries the words. The overlay "
                "exists so the screen can stay blank and face away from the lens — describe it "
                "as blank and evenly lit, and let the bubble say it"
            )
            break
