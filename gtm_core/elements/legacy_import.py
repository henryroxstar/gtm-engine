"""Import a hand-made pose folder into the library, as a DRAFT.

The shape this reads is the one that already exists on the operator's disk: a folder of
``poses/NN-<name>.png`` stills plus a README a human wrote. Two things it will not do.

It will not invent a ``use``. The pose descriptions are what make a library usable, and a
generated one ("the 03 pose") is worse than an obvious blank because it looks answered. So every
imported pose gets ``use = "TODO"`` and the element is marked ``draft = true``, which
:meth:`Element.validate_usable` refuses — the import is finished by a human writing sentences,
and until then nothing can render against it.

It will not guess a ratio. The ratio is read out of the PNG header (the IHDR chunk, with
``struct``, so this module needs no image library and stays outside the Pillow boundary
``tests/media/test_captions_module_boundary.py`` enforces). ``animatable`` is PROPOSED from it —
9:16 stills are the ones a vertical render can move — and is a proposal precisely because the
operator may disagree.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from ..confine import confined_source_file
from .model import _RATIOS, Element, ElementError, Pose

__all__ = ["read_png_size", "ratio_of", "plan_import"]

#: `01-standing-still.png` → order 1, name "standing-still".
_POSE_FILENAME_RE = re.compile(r"^(\d+)[-_]?(.*?)\.(?:png|jpe?g|webp)$", re.IGNORECASE)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _aspect(ratio: str) -> float:
    w, h = (int(x) for x in ratio.split(":"))
    return w / h


#: The ratios a pose may declare, as aspects — DERIVED from `model._RATIOS`, never a second list.
#: Two tables of the same seven strings had to be kept in sync by hand, and the failure was
#: silent in one direction: a ratio added here but not there was named by the importer and then
#: refused by `Pose.validate` on the very next line.
_KNOWN_RATIOS = {r: _aspect(r) for r in sorted(_RATIOS)}

#: How far off a named ratio a real file may sit and still be called that ratio.
#:
#: NOT cosmetic. Providers do not emit exact ratios: the sixteen-pose set this importer was first
#: run against came back at 1536x2752 — aspect 0.5581 against 9:16's 0.5625, 0.8% off — and an
#: exact-match lookup named NONE of them, so `animatable` was proposed false for every vertical
#: still in a set whose own README says six of them are 9:16. An importer that recognises only
#: exact ratios recognises nothing a provider actually produces.
#:
#: 2% is safe because the named ratios are not close together: the tightest neighbouring pair is
#: 3:4 (0.750) and 4:5 (0.800), 6.7% apart, so no file can sit within tolerance of two names.
#: Past the tolerance `ratio_of` still returns "" rather than the nearest name — "unnamed" and
#: "a ratio it is not" are very different answers to hand a render path.
RATIO_TOLERANCE = 0.02


def read_png_size(path: Path) -> tuple[int, int]:
    """``(width, height)`` from a PNG's IHDR chunk. Raises on anything that is not a PNG."""
    with path.open("rb") as fh:
        header = fh.read(24)
    if len(header) < 24 or header[:8] != _PNG_MAGIC or header[12:16] != b"IHDR":
        raise ElementError(f"{path.name} is not a PNG this importer can measure")
    return struct.unpack(">II", header[16:24])


def ratio_of(width: int, height: int) -> str:
    """The named aspect ratio this file is closest to within :data:`RATIO_TOLERANCE`, or ``""``.

    Tolerant on purpose — see the constant. Still refuses to name anything outside the tolerance.
    """
    if width <= 0 or height <= 0:
        return ""
    aspect = width / height
    best, best_err = "", RATIO_TOLERANCE
    for name, target in _KNOWN_RATIOS.items():
        err = abs(aspect - target) / target
        if err <= best_err:
            best, best_err = name, err
    return best


def plan_import(
    src_dir: Path,
    *,
    slug: str,
    kind: str,
    name: str,
    depicts: str = "",
    content_root: Path | None = None,
) -> tuple[Element, list[Path]]:
    """Read ``src_dir/poses/`` and return the draft element plus the files to copy, in order.

    Ordering is by the numeric prefix, not by ``sorted()``: `10-...` sorts before `2-...` as text,
    and a pose set whose order silently changed is a pose set whose "first reference" moved.
    """
    poses_src = src_dir / "poses"
    if not poses_src.is_dir():
        raise ElementError(f"no poses/ directory under {src_dir} — nothing to import")

    found: list[tuple[int, str, Path]] = []
    for path in poses_src.iterdir():
        if not path.is_file():
            continue
        m = _POSE_FILENAME_RE.match(path.name)
        if not m:
            continue
        order, tail = int(m.group(1)), (m.group(2) or "").strip("-_")
        found.append((order, tail or f"pose-{order:02d}", path))

    if not found:
        raise ElementError(
            f"{poses_src} holds no files named `NN-<name>.<ext>` — this importer reads the "
            "numeric prefix as the pose ORDER, and an unnumbered folder has no order to read."
        )

    found.sort(key=lambda row: row[0])
    poses: list[Pose] = []
    files: list[Path] = []
    for _order, tail, path in found:
        confined = confined_source_file(path, content_root=content_root) if content_root else path
        ratio = ratio_of(*read_png_size(path)) if path.suffix.lower() == ".png" else ""
        poses.append(
            Pose(
                name=f"beat:{tail}",
                file=path.name,
                ratio=ratio,
                use="TODO",
                animatable=ratio == "9:16",
            )
        )
        files.append(Path(confined))

    element = Element(slug=slug, kind=kind, name=name, depicts=depicts, draft=True, poses=poses)
    element.validate()
    return element, files
