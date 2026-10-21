"""Printf frame-sequence pattern parsing and contiguity checking — shared, stdlib-only.

Two callers need the identical answer to "what frame numbers actually exist here, and are they
usable": :mod:`gtm_core.video_finish.mux` (``frames_to_video``/``overlay_frames``, plan change #8
— refuse a glob, refuse a gap, name the problem) and :mod:`gtm_core.screen_ui`'s build-record
driver (change #3, a separate parallel track — detect a leftover different-scene sequence or
legacy PNGs in an out-dir before a rebuild, and derive a record's ``frame_pattern`` after a
render). Neither should re-derive the parsing rules on its own, and neither needs PIL or ffmpeg
to answer the question, so this module imports neither — a track that only wants "is this
sequence contiguous" must not have to pull in an image or encoding runtime.

This file was briefly a hand-rolled stub written by the #3 track to unblock itself before #8
landed for real (its own note said as much: "reconcile this file with theirs rather than have two
definitions of the same three functions"). ``sequences``, ``pattern``, ``digit_width`` and
``is_contiguous`` below keep that stub's exact names, signatures and behaviour — including
``_FRAME_RE``'s "prefix may itself contain hyphens; anchor on the LAST ``-<digits>.png``" shape —
so #3's code needs no changes when this real module replaces the stub. Everything else
(``ParsedPattern``, ``parse_pattern``, ``find_frames``, ``validate_sequence``,
``FrameSequenceError``, ``check_contiguous``) is #8's own addition for ``mux.py``.

Two distinct shapes, because the two callers start from different things:

* :func:`parse_pattern` / :func:`find_frames` / :func:`validate_sequence` start from a known
  ffmpeg-style printf pattern (``dir/hero-reveal-%04d.png``) and ask what's on disk for it. The
  pattern is split on its own ``%0Nd`` placeholder, never on a hardcoded "text after the last
  hyphen" assumption — a literal prefix that itself contains a hyphen must not be misread as
  multiple prefixes.
* :func:`sequences` / :func:`digit_width` start from a bare directory (no pattern in hand) and
  discover what ``<prefix>-<digits>.png`` sequences already live there — the shape
  :mod:`gtm_core.screen_ui` always writes (``frames.py``'s ``f"{prefix}-{i:04d}.png"``, always
  from ``i=0``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FrameSequenceError",
    "ParsedPattern",
    "check_contiguous",
    "digit_width",
    "find_frames",
    "is_contiguous",
    "parse_pattern",
    "pattern",
    "sequences",
    "validate_sequence",
]


class FrameSequenceError(ValueError):
    """A frame pattern or an on-disk sequence is not usable: a glob wildcard, no printf
    placeholder, zero matching files, or a gap. Subclasses ``ValueError`` (not a bespoke type) so
    ``video_finish/cli.py``'s existing ``except ValueError`` handling catches it without a new
    ``except`` arm."""


# Matches ffmpeg's own printf form: %04d, %4d, %d — the digit count (if any) is the zero-pad
# width; every in-repo caller uses %0Nd, but a bare %d is still a single valid placeholder.
_PRINTF = re.compile(r"%0?(\d+)d")

#: Matches ``<prefix>-<digits>.png`` — every scene here writes frames as ``{prefix}-{i:04d}.png``
#: (``gtm_core/screen_ui/frames.py:_write_frame_stream``), and the prefix itself may contain
#: hyphens (``"hero-reveal"``, ``"shot2-booking"``), so the split is anchored on the LAST
#: ``-<digits>.png`` rather than the first hyphen.
_FRAME_RE = re.compile(r"^(?P<prefix>.+)-(?P<digits>\d+)\.png$")


@dataclass(frozen=True)
class ParsedPattern:
    """An ffmpeg printf frame pattern, split on its own ``%0Nd`` placeholder."""

    directory: Path
    prefix: str  # literal text before the placeholder — may itself contain hyphens
    width: int  # zero-pad width, e.g. 4 for %04d
    suffix: str  # literal text after the placeholder, usually ".png"

    def name_for(self, n: int) -> str:
        return f"{self.prefix}{n:0{self.width}d}{self.suffix}"


def parse_pattern(frames_glob: str) -> ParsedPattern:
    """Parse an ffmpeg-style printf frame pattern such as ``dir/hero-reveal-%04d.png``.

    Raises :class:`FrameSequenceError` for a glob wildcard (``*``/``?`` — printf patterns only;
    no in-repo caller needs glob support, see plan #8) or a filename with no ``%0Nd`` placeholder,
    naming the required printf form either way.
    """
    if "*" in frames_glob or "?" in frames_glob:
        raise FrameSequenceError(
            "frame pattern must be a printf form like 'prefix-%04d.png', not a glob "
            f"('*'/'?' are not supported): {frames_glob!r}"
        )
    path = Path(frames_glob)
    name = path.name
    matches = list(_PRINTF.finditer(name))
    if not matches:
        raise FrameSequenceError(
            "frame pattern has no printf placeholder (expected e.g. 'prefix-%04d.png'): "
            f"{frames_glob!r}"
        )
    if len(matches) > 1:
        raise FrameSequenceError(
            f"frame pattern must have exactly one printf placeholder, found {len(matches)}: "
            f"{frames_glob!r}"
        )
    m = matches[0]
    return ParsedPattern(
        directory=path.parent,
        prefix=name[: m.start()],
        width=int(m.group(1)),
        suffix=name[m.end() :],
    )


def find_frames(parsed: ParsedPattern) -> list[int]:
    """Frame numbers actually on disk for ``parsed``, sorted ascending.

    Only filenames whose digit run is exactly ``parsed.width`` long count — a ``%04d`` pattern
    must not pick up a stray 5-digit file from an unrelated sequence sharing the same directory
    and literal prefix.
    """
    if not parsed.directory.exists():
        return []
    rx = re.compile(
        rf"^{re.escape(parsed.prefix)}(\d{{{parsed.width}}}){re.escape(parsed.suffix)}$"
    )
    numbers = []
    for f in parsed.directory.iterdir():
        m = rx.match(f.name)
        if m:
            numbers.append(int(m.group(1)))
    return sorted(numbers)


def check_contiguous(numbers: Sequence[int]) -> None:
    """Raise :class:`FrameSequenceError` if ``numbers`` is empty, or has a gap relative to its
    own first (lowest) value — contiguity is judged from wherever the sequence actually starts,
    not from a hardcoded 0, so a sequence starting at 1 is not treated as having a gap at 0.

    Bare-index primitive: no filename context. :func:`validate_sequence` is the filename-aware
    version ``frames_to_video``/``overlay_frames`` call; :func:`is_contiguous` is the
    boolean-returning version for a caller that wants to ask rather than be told.
    """
    if not numbers:
        raise FrameSequenceError("no frames found")
    ordered = sorted(numbers)
    start = ordered[0]
    for offset, n in enumerate(ordered):
        expected = start + offset
        if n != expected:
            raise FrameSequenceError(
                f"gap in frame sequence: missing frame index {expected} (found {n} next)"
            )


def validate_sequence(frames_glob: str) -> ParsedPattern:
    """Parse ``frames_glob`` and check its matching files on disk exist and are contiguous from
    their start. The one call ``frames_to_video``/``overlay_frames`` make before touching ffmpeg.

    Raises :class:`FrameSequenceError` (a ``ValueError``) naming the exact missing filename on a
    gap, or "no frames found matching …" when nothing on disk matches the pattern at all.
    """
    parsed = parse_pattern(frames_glob)
    numbers = find_frames(parsed)
    if not numbers:
        raise FrameSequenceError(
            f"no frames found matching {parsed.prefix}%0{parsed.width}d{parsed.suffix} in "
            f"{parsed.directory}"
        )
    start = numbers[0]
    for offset, n in enumerate(numbers):
        expected = start + offset
        if n != expected:
            raise FrameSequenceError(
                f"gap in frame sequence: missing {parsed.name_for(expected)} (found "
                f"{parsed.name_for(n)} next) in {parsed.directory}"
            )
    return parsed


def sequences(directory: Path) -> dict[str, list[int]]:
    """Every ``<prefix>-<digits>.png`` in ``directory``, grouped by prefix, numbers sorted.

    A directory holding more than one scene's leftover frames (or none at all) is not an error
    here — the caller decides what to do with an empty or multi-prefix result. Files that do not
    match the ``<prefix>-<digits>.png`` shape (a build record, another sidecar) are ignored, not
    refused.
    """
    found: dict[str, list[int]] = {}
    if not Path(directory).is_dir():
        return found
    for path in sorted(Path(directory).glob("*.png")):
        m = _FRAME_RE.match(path.name)
        if not m:
            continue
        found.setdefault(m["prefix"], []).append(int(m["digits"]))
    for numbers in found.values():
        numbers.sort()
    return found


def digit_width(directory: Path, prefix: str) -> int:
    """The zero-padded digit width ``prefix``'s files actually use in ``directory``.

    Every writer here pads to 4 (``{i:04d}``), so that is the fallback for an empty or
    not-yet-existing match — but a legacy directory is exactly the case a build record has to
    tolerate without guessing wrong, so this reads it off the real filenames when there are any.
    """
    widths = {
        len(m["digits"])
        for path in Path(directory).glob(f"{prefix}-*.png")
        if (m := _FRAME_RE.match(path.name)) and m["prefix"] == prefix
    }
    return max(widths) if widths else 4


def pattern(prefix: str, width: int, *, suffix: str = ".png") -> str:
    """The ffmpeg-style printf pattern a prefix's files satisfy, e.g. ``"hero-reveal-%04d.png"``
    (:mod:`gtm_core.screen_ui`'s own convention: ``frames.py`` always writes
    ``f"{prefix}-{i:04d}.png"``)."""
    return f"{prefix}-%0{width}d{suffix}"


def is_contiguous(numbers: list[int], *, start: int = 0) -> bool:
    """Whether ``numbers`` (any order) is exactly ``start..start+len(numbers)-1`` with no gaps.

    Empty is never contiguous — a caller asking "is this a real sequence" about zero frames has
    already made the mistake the check exists to catch. Boolean sibling of
    :func:`check_contiguous`, which raises instead of returning, for a caller that wants to ask
    rather than be told.
    """
    if not numbers:
        return False
    return sorted(numbers) == list(range(start, start + len(numbers)))
