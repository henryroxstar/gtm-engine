"""``screen-ui-build.json`` — proof that an out-dir's PNGs still match the inputs that made them.

A rendered out-dir on its own cannot say which scene, which image, which actions file, or which
kit produced it — two renders into the same directory look identical on disk right up until a
frame is read. That is how a stale ``phone-walkthrough`` out-dir ends up muxed into a "hero-reveal"
master, or a re-run with a changed ``actions.json`` silently reuses the OLD frames because nothing
noticed the input moved. This module writes and reads the one file that answers "was this out-dir
built from what I think it was built from": a fingerprint over every input that can change what a
render draws, plus the frame count and pattern the render actually produced.

**The whole resolved extras dict, not a hand-picked subset.** An earlier draft of this fingerprint
hashed only ``image``/``actions``/``stills`` — which misses ``crop_frac``, ``title``, ``message``,
``bubble``, ``cta_text``, ``logo_variant``, ``speak_from_s``: every one of those changes a render
without touching an image or an actions file, so a fingerprint that skipped them would call a
stale frame "current" the moment a caller changed a card's copy but not its picture.

**Digests, not paths, enter the hash.** Every ``Path``-valued key in ``extras`` is replaced by its
content digest (:func:`gtm_core.page_inputs.digest`) before hashing, so a file moved or renamed
with identical bytes fingerprints identically and an in-place edit under the same name does not.
Paths are still recorded, verbatim, in the record's own ``inputs`` list — for a human reading the
file, not for the hash.

**The kit and font enter by content, not by path either.** ``render_scene`` is handed an
already-parsed ``kit`` dict (never a path — re-reading the kit file on every call is exactly what
``render_scene`` exists to avoid; see ``cli.py``'s docstring), so this hashes the kit's own
canonical JSON rather than a file on disk. The caption font is a real file on disk regardless of
where the kit came from (``font_role`` selects a different TTF per scene, resolved through
:func:`gtm_core.captions.load_face`), so its bytes ARE digested from disk. A scene that never
draws text (hero-reveal, phone-walkthrough) may legitimately be rendered against a kit with no
font configured at all — :func:`gtm_core.captions.FontMissing` there is not this module's error to
raise, so an unresolvable font degrades to ``font_sha256: null`` rather than failing the write.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..page_inputs import digest

RECORD_NAME = "screen-ui-build.json"


def _canonical(value: object) -> object:
    """Recursively replace every ``Path`` with its content digest; everything else is left as a
    JSON-serialisable shape (dict keys sorted, tuples become lists via the caller's ``json.dumps``).
    """
    if isinstance(value, Path):
        return f"sha256:{digest(value)}" if value.is_file() else f"missing:{value}"
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False).encode("utf-8")


def _font_path(kit: dict, font_role: str, repo_root: Path | None) -> Path | None:
    """Best-effort font resolution — ``None`` (never a raise) when the kit has no ``font_role``
    configured, since not every scene this record covers draws text at all."""
    from ..captions import FontMissing, load_face

    try:
        return load_face(kit, role=font_role, repo_root=repo_root)
    except FontMissing:
        return None


def fingerprint(
    scene: str,
    ratio: str,
    fps: int,
    duration_s: float,
    extras: dict,
    kit: dict,
    font_role: str,
    repo_root: Path | None,
) -> str:
    """A sha256 over every input that can change what ``render_scene`` draws for ``scene``.

    Unchanged when a ``Path``-valued input is renamed to a byte-identical file at a different
    path (see :func:`_canonical`); changed by anything else in ``extras``, the kit's own content,
    or the resolved caption font's bytes.
    """
    font_path = _font_path(kit, font_role, repo_root)
    payload = {
        "scene": scene,
        "ratio": ratio,
        "fps": fps,
        "duration_s": duration_s,
        "font_role": font_role,
        "extras": _canonical(extras),
        "kit": _canonical(kit),
        "font": _canonical(font_path) if font_path is not None else None,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _human_inputs(extras: dict) -> list[dict]:
    """``[{"arg", "path", "sha256"}]`` for every real file an ``extras`` value names — the
    record's human-readable inventory, kept separate from the fingerprint hash above. A list of
    paths (``stills``, order-significant) gets one row per index so the order itself is visible
    in the file, not just baked into the hash."""
    rows: list[dict] = []
    for key, value in sorted(extras.items()):
        if isinstance(value, Path):
            if value.is_file():
                rows.append({"arg": key, "path": str(value), "sha256": digest(value)})
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, Path) and item.is_file():
                    rows.append({"arg": f"{key}[{i}]", "path": str(item), "sha256": digest(item)})
    return rows


def build_record(
    *,
    scene: str,
    ratio: str,
    fps: int,
    duration_s: float,
    extras: dict,
    kit: dict,
    font_role: str,
    repo_root: Path | None,
    frames: int,
    frame_pattern: str | None,
) -> dict:
    """The full record for a just-finished render: fingerprint plus the outputs it produced."""
    font_path = _font_path(kit, font_role, repo_root)
    return {
        "scene": scene,
        "ratio": ratio,
        "fps": fps,
        "duration_s": duration_s,
        "frames": frames,
        "frame_pattern": frame_pattern,
        "inputs": _human_inputs(extras),
        "kit_sha256": hashlib.sha256(_canonical_json(kit)).hexdigest(),
        "font_sha256": digest(font_path) if font_path is not None else None,
        "fingerprint": fingerprint(
            scene, ratio, fps, duration_s, extras, kit, font_role, repo_root
        ),
    }


def read(out_dir: Path) -> dict | None:
    """The record at ``out_dir/screen-ui-build.json``, or ``None`` — absent, unreadable, or
    malformed JSON all read as "no record", never a raise: a caller deciding whether to trust an
    out-dir needs an answer, not an exception from a corrupt leftover file."""
    path = Path(out_dir) / RECORD_NAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write(out_dir: Path, record: dict) -> None:
    """Atomic write: a tmp file plus ``os.replace``, so a crash mid-write never leaves a
    half-written record that later reads as valid JSON for the wrong content."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RECORD_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def delete(out_dir: Path) -> None:
    """Remove the record, if any. Called BEFORE a render starts — a crash mid-render must not
    leave an old fingerprint vouching for a directory of half-replaced frames."""
    path = Path(out_dir) / RECORD_NAME
    path.unlink(missing_ok=True)
