"""Does every sound the shot list DESIGNED have an asset to be mixed from?

A shot list declares audio twice over — ``sfx`` (a diegetic cue: "keyboard taps", "a
notification chime") and ``audio_bed`` (what the shot sounds like under everything: "the
felt-piano figure enters here", "room tone alone"). Both are prose, and nothing ever checked
that the prose became a file. On 2026-09-07 a 30s film shipped whose nine shots declared nine
cues and eight bed directions, none of which existed; the whole soundtrack was a synthesized
room-tone floor laid so the dead-air lint would pass. ``video_lint`` V10 now catches that shape
on the finished asset (``audio_floor_only``); this CLI catches it BEFORE the mix, while it is
free to fix, and is the only layer that can name WHICH cue is missing.

    python -m gtm_core.audio_plan --shots <shots.json> --cues <cues.json> [--shots-root <dir>] [--json]

``cues.json``::

    {"bed":       {"path": "bed/felt-piano.wav", "start_at_s": 0.0, "gain_db": -18.0} | null,
     "room_tone": {"path": "bed/room-tone.m4a"} | null,
     "cues": [{"shot_id": "shot-02", "label": "keyboard taps", "path": "sfx/keyboard-taps.wav",
               "at_s": 0.4, "gain_db": -12.0, "trim_s": 0.0}, ...]}

Paths resolve against ``--shots-root`` (default: the cues file's directory). ``shot_id`` matches
``shot["id"]`` or the positional ``shot-NN`` fallback — the SAME fallback
``gtm_core.video_finish.burn_captions`` uses, so a cue keys to the same shot the burn does.

Per shot: a declared ``sfx`` (non-empty, not "silent"/"silence"/"none") needs at least one cue
for that shot whose file exists; a declared ``audio_bed`` that is not "silent" needs a ``bed`` or
a ``room_tone``; and a bed direction that names MUSIC (piano, figure, score, strings, ...) with
only ``room_tone`` laid is a WARN — a noise floor is not the bed that was designed. Spoken /
narration audio is out of scope here: that lane has its own parity checks in ``shots_lint``.

Output: one row per shot, the list of missing items, and ``mix_plan`` — per shot, the exact
``{"cues": [...]}`` payload ``python -m gtm_core.video_finish mix-sfx --cues`` accepts, so the
plan is a runnable next step and not a second thing to transcribe by hand.

Exit 0 when everything declared has an asset; 2 when anything is missing or a cue names an
unknown shot; 3 when an input cannot be read. Deterministic, read-only, no ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["AudioPlan", "ShotAudioRow", "load_shots", "plan_audio", "main"]

#: A declaration that asks for nothing. ``silence`` as well as ``silent`` because a shot list
#: writes "silence — the scroll stops and the sound stops with it" as an SFX direction.
_NOTHING_RE = re.compile(r"^\s*(silent|silence|none)\b", re.IGNORECASE)
#: A bed direction that names a MUSICAL shape. Room tone can stand in for "room tone"; it cannot
#: stand in for a piano figure, and the difference is exactly what shipped.
_MUSIC_RE = re.compile(
    r"\b(piano|note|figure|score|music|strings|synth|melody|chord)", re.IGNORECASE
)
#: The field names ``gtm_core.video_finish.sfx_cues.SfxCue`` takes — ``mix_plan`` emits exactly
#: these, in this order, so the payload round-trips into ``mix-sfx --cues`` unchanged.
_CUE_FIELDS = ("path", "at_s", "gain_db", "trim_s", "label")


@dataclass(frozen=True)
class ShotAudioRow:
    shot_id: str
    sfx: str
    audio_bed: str
    #: ``ok`` — everything declared has an asset; ``missing`` — something declared does not;
    #: ``warn`` — covered, but by a floor where a bed was designed; ``n/a`` — nothing declared.
    status: str
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class AudioPlan:
    rows: tuple[ShotAudioRow, ...]
    missing: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    mix_plan: dict[str, dict] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing and not self.errors


def load_shots(path: Path) -> list[dict]:
    """A shot list is ``{"shots": [...]}`` (the schema) or a bare list; anything else is refused."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    shots = doc.get("shots") if isinstance(doc, dict) else doc
    if not isinstance(shots, list) or not all(isinstance(s, dict) for s in shots):
        raise ValueError(f"{path} is not a shot list: expected {{'shots': [...]}} or a list")
    return shots


def shot_ids(shots: list[dict]) -> list[str]:
    """``shot["id"]`` or ``shot-NN`` — byte-identical to the fallback in
    ``gtm_core.video_finish.burn_captions``, so one shot has one name across the pipeline."""
    return [str(s.get("id") or f"shot-{i:02d}") for i, s in enumerate(shots)]


def _declared(value: object) -> str:
    text = str(value or "").strip()
    return "" if not text or _NOTHING_RE.match(text) else text


def _resolve(root: Path, raw: object) -> Path:
    p = Path(str(raw))
    return p if p.is_absolute() else root / p


def _short(text: str, n: int = 48) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _parse_cues(payload: dict, *, root: Path, known: set[str]) -> tuple[dict, list[str]]:
    """Group the cue entries by shot, resolving paths and refusing a cue that names a shot the
    list does not have — a cue that matches nothing looks exactly like a shot with no cue."""
    by_shot: dict[str, list[dict]] = {}
    errors: list[str] = []
    for n, raw in enumerate(payload.get("cues") or []):
        if not isinstance(raw, dict):
            errors.append(f"cues[{n}] is not an object")
            continue
        sid = str(raw.get("shot_id") or "").strip()
        label = str(raw.get("label") or "")
        if not sid:
            errors.append(f"cues[{n}] ({label or 'unlabelled'}) has no shot_id")
            continue
        if sid not in known:
            errors.append(
                f"cues[{n}] ({label or 'unlabelled'}) names shot_id {sid!r}, which is not in "
                f"the shot list (it has {sorted(known)})"
            )
            continue
        try:
            path = _resolve(root, raw["path"])
            cue = {
                "path": str(path),
                "at_s": float(raw["at_s"]),
                "gain_db": float(raw.get("gain_db", 0.0)),
                "trim_s": float(raw.get("trim_s", 0.0)),
                "label": label,
            }
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"cues[{n}] ({label or 'unlabelled'}) is malformed: {exc}")
            continue
        by_shot.setdefault(sid, []).append({**cue, "_exists": path.is_file()})
    return by_shot, errors


def _bed_asset(payload: dict, key: str, *, root: Path, missing: list[str]) -> bool:
    """Is a ``bed`` / ``room_tone`` entry present AND on disk? A declared-but-absent file is
    reported once, here, not once per shot that leaned on it."""
    entry = payload.get(key)
    if not entry:
        return False
    if not isinstance(entry, dict) or not entry.get("path"):
        missing.append(f"{key} is declared but has no path")
        return False
    path = _resolve(root, entry["path"])
    if not path.is_file():
        missing.append(f"{key} → {path} (not found)")
        return False
    return True


def plan_audio(shots: list[dict], cues_payload: dict, *, root: Path) -> AudioPlan:
    """Pure apart from ``Path.is_file`` on the declared assets."""
    ids = shot_ids(shots)
    missing: list[str] = []
    by_shot, errors = _parse_cues(cues_payload, root=root, known=set(ids))
    has_bed = _bed_asset(cues_payload, "bed", root=root, missing=missing)
    has_tone = _bed_asset(cues_payload, "room_tone", root=root, missing=missing)

    rows: list[ShotAudioRow] = []
    warnings: list[str] = []
    mix_plan: dict[str, dict] = {}
    for sid, shot in zip(ids, shots, strict=True):
        sfx, bed = _declared(shot.get("sfx")), _declared(shot.get("audio_bed"))
        problems: list[str] = []
        cues = by_shot.get(sid, [])
        if cues:
            mix_plan[sid] = {"cues": [{k: c[k] for k in _CUE_FIELDS} for c in cues]}
        for c in cues:
            if not c["_exists"]:
                item = f"{sid} cue {c['label']!r} → {c['path']} (not found)"
                missing.append(item)
                problems.append(item)
        if sfx and not any(c["_exists"] for c in cues):
            item = f"{sid} sfx {_short(sfx)!r} — no cue with an existing file in cues.json"
            missing.append(item)
            problems.append(item)
        if bed and not (has_bed or has_tone):
            item = f"{sid} audio_bed {_short(bed)!r} — no bed and no room_tone in cues.json"
            missing.append(item)
            problems.append(item)
        elif bed and not has_bed and has_tone and _MUSIC_RE.search(bed):
            item = (
                f"{sid} audio_bed names music ({_short(bed)!r}) but only room_tone is laid — "
                "a noise floor is not the bed that was designed"
            )
            warnings.append(item)
            problems.append(item)
        if not sfx and not bed:
            status = "n/a"
        elif any(p in missing for p in problems):
            status = "missing"
        elif problems:
            status = "warn"
        else:
            status = "ok"
        rows.append(ShotAudioRow(sid, sfx, bed, status, tuple(problems)))
    return AudioPlan(tuple(rows), tuple(missing), tuple(warnings), tuple(errors), mix_plan)


def _print_text(plan: AudioPlan) -> None:
    n_sfx = sum(1 for r in plan.rows if r.sfx)
    n_bed = sum(1 for r in plan.rows if r.audio_bed)
    print(f"audio plan — {len(plan.rows)} shots, {n_sfx} declare sfx, {n_bed} declare a bed")
    for r in plan.rows:
        sfx = _short(r.sfx, 32) if r.sfx else "-"
        bed = _short(r.audio_bed, 32) if r.audio_bed else "-"
        print(f"  {r.shot_id:<10} sfx: {sfx:<33} bed: {bed:<33} {r.status.upper()}")
    for title, items in (
        ("errors", plan.errors),
        ("missing", plan.missing),
        ("warnings", plan.warnings),
    ):
        if items:
            print(f"\n{title} ({len(items)}):")
            for item in items:
                print(f"  - {item}")
    if plan.complete:
        print(f"\n✓ every declared cue and bed has an asset ({len(plan.mix_plan)} shots to mix)")
    else:
        print(
            f"\n✗ {len(plan.missing)} declared item(s) have no asset, {len(plan.errors)} "
            "error(s) — nothing to mix yet"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.audio_plan", description=__doc__)
    parser.add_argument("--shots", type=Path, required=True, help="the shot list JSON")
    parser.add_argument("--cues", type=Path, required=True, help="the cues JSON (see docstring)")
    parser.add_argument(
        "--shots-root",
        type=Path,
        default=None,
        help="directory cue/bed paths resolve against (default: the cues file's directory)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        shots = load_shots(args.shots)
        cues_payload = json.loads(args.cues.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[audio_plan] cannot read inputs: {exc}", file=sys.stderr)
        return 3
    if not isinstance(cues_payload, dict):
        print(f"[audio_plan] {args.cues} is not a JSON object", file=sys.stderr)
        return 3
    root = args.shots_root or args.cues.parent

    plan = plan_audio(shots, cues_payload, root=root)
    if args.as_json:
        print(
            json.dumps(
                {
                    "shots": [asdict(r) for r in plan.rows],
                    "missing": list(plan.missing),
                    "warnings": list(plan.warnings),
                    "errors": list(plan.errors),
                    "mix_plan": plan.mix_plan,
                    "complete": plan.complete,
                },
                indent=2,
            )
        )
    else:
        _print_text(plan)
    return 0 if plan.complete else 2


if __name__ == "__main__":
    sys.exit(main())
