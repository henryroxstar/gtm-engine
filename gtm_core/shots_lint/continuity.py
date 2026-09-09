"""Cross-shot reference rules — a continuity clause that points at something the render never sees.

Each shot is rendered on its own. A prompt field that says "the same jacket as shot 1" reads to a
human author like an enforced constraint and is inert: the renderer receives this shot's fields and
nothing else, so the clause buys nothing and hides the fact that nothing is pinning the jacket. The
scan behind this rule found 12 such clauses across 188 shipped shots and nobody had noticed one.

Two things make a reference RESOLVABLE, and both stay clean:

* an input **file** the render actually receives (``refs/ref-b07.jpg``);
* an ordinal into this shot's own ``reference_images`` ("the subject from the second reference
  image") — resolvable exactly when that list is present and long enough. Without the list the
  same sentence is the silent case: it reads as enforcement and points at nothing.

Keyed on SHAPE, never on vocabulary. An earlier attempt keyed on the words "same" and "still"
returned a 36% false-positive rate, because "one open hand resting still at chest height" is a
description of a person, not a pointer to another shot. What makes a clause a pointer is a
**referent** — a named shot, or "the previous frame", or "as before" — so that is what is matched.

WARN, never ERROR: all 12 hits are in films that shipped acceptably, so error-level would be
over-claiming. It is also one corpus: WARN standing is what makes a single scan acceptable
evidence, and it is what a promotion to ERROR would have to earn on our own assets first.
"""

from __future__ import annotations

import re

#: Prompt fields scanned. ``spoken`` is exempt by design: dialogue may legitimately say "same as
#: before", and it is read aloud rather than sent to an image model as a constraint.
_SCANNED_FIELDS: tuple[str, ...] = (
    "visual",
    "motion_prompt",
    "stability",
    "wardrobe",
    "expression",
    "lighting",
)

#: A pointer to another shot: a numbered shot, a positional one, or a bare "as before". This is
#: the whole rule — a comparative word ("same", "matching") is neither necessary nor sufficient.
_SHOT_REFERENT_RE = re.compile(
    r"\b(?:shot|frame|beat|scene|clip)\s*#?\s*\d+\b"
    r"|\b(?:the\s+)?(?:previous|prior|preceding|last|earlier|opening|first|second|third)\s+"
    r"(?:shot|frame|beat|scene|clip)\b"
    r"|\bas\s+before\b"
    r"|\b(?:same|unchanged|identical|matching)\s+as\s+(?:above|before|earlier)\b"
    r"|\bearlier\s+in\s+the\s+(?:video|film|piece|edit|sequence)\b",
    re.IGNORECASE,
)

#: An ordinal into this shot's own ``reference_images``. Group 1 is the word form, group 2 the
#: digit form; ``_ordinal_index`` turns either into a 1-based position.
_REFERENCE_ORDINAL_RE = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+"
    r"reference(?:\s+(?:image|still|photo))?\b"
    r"|\breference\s+(?:image|still|photo)?\s*#?\s*(\d+)\b"
    r"|\b(\d+)(?:st|nd|rd|th)\s+reference(?:\s+(?:image|still|photo))?\b",
    re.IGNORECASE,
)

_ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

#: A path-like token naming an image the render receives. Its presence makes the clause resolvable.
_FILE_REF_RE = re.compile(r"\b[\w./-]+\.(?:png|jpe?g|webp|heic|heif)\b", re.IGNORECASE)

#: Clause boundaries. A pointer is judged against the clause it sits in, so a resolvable file
#: reference in one half of a sentence does not excuse a dangling pointer in the other.
_CLAUSE_SPLIT_RE = re.compile(r"[;,.]|\s+—\s+|\s+--\s+")


def _ordinal_index(match: re.Match[str]) -> int | None:
    """The 1-based position an ordinal reference names, or ``None`` if it named none."""
    word, digits_after, digits_before = match.group(1), match.group(2), match.group(3)
    if word:
        return _ORDINAL_WORDS.get(word.lower())
    raw = digits_after or digits_before
    if raw and raw.isdigit():
        value = int(raw)
        return value if value > 0 else None
    return None


def _lint_cross_shot_reference(shot: dict, prefix: str, warnings: list[str]) -> None:
    """Warn when a prompt field points at another shot, or at a reference image nobody attached."""
    references = shot.get("reference_images")
    n_references = len(references) if isinstance(references, list) else 0

    for field_name in _SCANNED_FIELDS:
        value = shot.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        for clause in _CLAUSE_SPLIT_RE.split(value):
            if not clause.strip() or _FILE_REF_RE.search(clause):
                # A named input file is a reference the render actually receives.
                continue

            ordinal = _REFERENCE_ORDINAL_RE.search(clause)
            if ordinal is not None:
                position = _ordinal_index(ordinal)
                if position is not None and position <= n_references:
                    continue
                have = f"only {n_references} attached" if n_references else "the shot attaches none"
                warnings.append(
                    f"{prefix}.{field_name} points at {ordinal.group(0)!r} but {have} — "
                    "the clause reads as a constraint and the renderer receives nothing to "
                    "honour it. Attach the stills to `reference_images` in the order the "
                    "sentence addresses, or state the constraint positively in `stability`"
                )
                continue

            referent = _SHOT_REFERENT_RE.search(clause)
            if referent is not None:
                warnings.append(
                    f"{prefix}.{field_name} refers to another shot ({referent.group(0)!r}) — "
                    "each shot is rendered on its own, so the renderer never sees the shot "
                    "being pointed at and the continuity clause is inert. Carry the constraint "
                    "with a `reference_images` still, or restate what must be true of THIS "
                    "frame in `stability`"
                )
