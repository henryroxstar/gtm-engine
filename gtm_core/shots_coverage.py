"""Does every field the shot list DECLARES have a CONSUMER at this stage?

A shot list promises things a viewer will see or hear — a chat bubble in ``production.overlay``,
a diegetic cue in ``sfx``, a bed in ``audio_bed``, a last frame in ``end_frame``, a caption.
Each is consumed by a different verb, and nothing ever asked whether the verb ran. A 30s film
shipped with four bubbles, nine cues and one end frame that existed only as prose: no code read
those fields, so no code missed them. This lint turns each declaration into a refusal at the
stage where its consumer should already exist.

    python -m gtm_core.shots_coverage --shots <shots.json> --dir <run folder> \\
        --stage plan|render|finish [--ratio 9:16] [--render-manifest P] [--finish-manifest P] \\
        [--profile P --product S --profiles-root R] [--json]

Per shot (``id``, else the positional ``shot-NN`` that ``video_finish.burn`` keys on):

- ``production.overlay`` — plan/render: ``kind`` normalises and ``text`` is non-empty;
  finish: ``<dir>/shots-overlaid/<id>-overlaid.overlays.json`` carries >= 1 overlay, or the
  finish manifest's ``overlays`` list names the shot.
- ``production.transition_in`` — plan/render: ``kind`` and ``duration_s`` are well-formed;
  finish: the finish manifest's ``transitions`` records the join INTO this shot (join k is into
  shot k+1) with the same kind and duration, which is what ``video_finish stitch`` writes beside
  the master when it honours the declaration.
- ``sfx`` (not "silent"/"none") — render/finish: ``<dir>/sfx/cues.json`` has a cue for the shot
  whose file exists. ``audio_bed`` (not silent) — a ``bed`` or ``room_tone`` with a file.
- ``end_frame`` — render/finish: the render manifest's entry with the same ``n`` carries a
  non-null ``end_image_job_id`` (or ``end_media_id``).
- a caption (``caption_text_override`` / ``spoken`` / ``caption_segments``) — finish:
  ``<dir>/shots-captioned/<id>-captioned.captions.json`` exists, or the finish manifest says
  ``captions_preburned`` with screens.
- a product slug (``--product`` or the list's own) — plan: the product kit exists under the
  profile. The slug is guarded as a bare segment: a traversal here is the tenant error.

Exit 0 when everything declared has a consumer, 2 when anything is missing, 1 when an input
cannot be read. Read-only: nothing here writes a file. The cues.json shape is re-read here rather
than imported from ``gtm_core.audio_plan`` so this lint stands even where that module does not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import PathConfig, _safe_segment
from .shots_lint.keyframe import is_keyframe_shot
from .shots_lint.overlay import normalize_overlay_kind, overlay_entries
from .shots_lint.transition import transition_errors, transition_in

__all__ = [
    "Finding",
    "CoverageReport",
    "STAGES",
    "shot_id_of",
    "check_coverage",
    "load_json",
    "sfx_declared",
    "bed_declared",
    "caption_declared",
    "cue_present",
    "bed_present",
    "overlay_burned",
    "captions_sidecar",
    "manifest_entry",
    "main",
]

STAGES = ("plan", "render", "finish")

#: A declaration that asks for nothing — the same spellings ``gtm_core.audio_plan`` accepts.
_NOTHING_RE = re.compile(r"^\s*(silent|silence|none)\b", re.IGNORECASE)
_CAPTION_KEYS = ("caption_text_override", "spoken", "caption_segments")


@dataclass(frozen=True)
class Finding:
    shot_id: str
    field: str
    consumer: str
    detail: str


@dataclass(frozen=True)
class CoverageReport:
    stage: str
    missing: tuple[Finding, ...]
    ok: tuple[Finding, ...]

    def to_json(self) -> dict:
        return {
            "stage": self.stage,
            "missing": [asdict(f) for f in self.missing],
            "ok": [asdict(f) for f in self.ok],
        }


# ── declarations ──────────────────────────────────────────────────────────────────────────────


def shot_id_of(shot: dict, i: int) -> str:
    """``shot["id"]`` or the positional fallback — the SAME one ``video_finish.burn`` uses,
    so a sidecar named by the burn is found by this lint."""
    return str(shot.get("id") or f"shot-{i:02d}")


def _shot_n(shot: dict, i: int) -> str:
    return str(shot.get("n") or i + 1)


def sfx_declared(shot: dict) -> str:
    text = str(shot.get("sfx") or "").strip()
    return "" if not text or _NOTHING_RE.match(text) else text


def bed_declared(shot: dict) -> str:
    text = str(shot.get("audio_bed") or "").strip()
    return "" if not text or _NOTHING_RE.match(text) else text


def caption_declared(shot: dict) -> bool:
    return any(shot.get(k) for k in _CAPTION_KEYS)


# ── consumers ─────────────────────────────────────────────────────────────────────────────────


def load_json(path: Path | None) -> dict | None:
    """The parsed file, or ``None`` when it is absent or not a JSON object. Absence is a finding
    for the caller to make, not an exception."""
    if path is None or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _asset_exists(root: Path, raw: object) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    p = Path(text)
    return (p if p.is_absolute() else root / p).is_file()


def cue_present(cues: dict | None, shot_id: str, sfx_dir: Path) -> bool:
    """>= 1 cue keyed to ``shot_id`` whose file exists (relative to ``<dir>/sfx/`` or absolute)."""
    if not cues:
        return False
    rows = cues.get("cues")
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(c, dict)
        and str(c.get("shot_id") or "") == shot_id
        and _asset_exists(sfx_dir, c.get("path"))
        for c in rows
    )


def bed_present(cues: dict | None, sfx_dir: Path) -> bool:
    if not cues:
        return False
    return any(
        isinstance(cues.get(k), dict) and _asset_exists(sfx_dir, cues[k].get("path"))
        for k in ("bed", "room_tone")
    )


def overlay_burned(run_dir: Path, shot_id: str, finish: dict | None) -> bool:
    sidecar = load_json(run_dir / "shots-overlaid" / f"{shot_id}-overlaid.overlays.json")
    if sidecar and isinstance(sidecar.get("overlays"), list) and sidecar["overlays"]:
        return True
    rows = (finish or {}).get("overlays")
    return isinstance(rows, list) and any(
        isinstance(r, dict) and str(r.get("shot_id") or "") == shot_id for r in rows
    )


def captions_sidecar(run_dir: Path, shot_id: str) -> Path:
    return run_dir / "shots-captioned" / f"{shot_id}-captioned.captions.json"


def _captions_preburned(finish: dict | None) -> bool:
    if not finish or not finish.get("captions_preburned"):
        return False
    pre = finish.get("preburned")
    caps = pre.get("captions") if isinstance(pre, dict) else finish.get("captions")
    return isinstance(caps, dict) and bool(caps.get("screens"))


def manifest_entry(render: dict | None, n: str) -> dict | None:
    """The render manifest's shot entry with this ``n`` — the manifest writes ``n`` as a string,
    the shot list as an integer, so both are compared as text."""
    rows = (render or {}).get("shots")
    if not isinstance(rows, list):
        return None
    for r in rows:
        if isinstance(r, dict) and str(r.get("n") or "") == n:
            return r
    return None


def _end_frame_requested(entry: dict | None) -> bool:
    return bool(entry and (entry.get("end_image_job_id") or entry.get("end_media_id")))


# ── the check ─────────────────────────────────────────────────────────────────────────────────


def _product_finding(
    doc: dict, product: str | None, profile: str | None, profiles_root: Path | None
) -> Finding | None:
    """Plan stage only: a named product must resolve to a kit under the bound profile."""
    production = doc.get("production") if isinstance(doc.get("production"), dict) else {}
    slug = str(product or doc.get("product") or production.get("product") or "").strip()
    if not slug:
        return None
    consumer = "products/<slug>/BRAND.toml"
    try:
        _safe_segment(slug, "product")
        if profile:
            _safe_segment(profile, "profile")
    except ValueError as exc:
        return Finding("*", "product", consumer, str(exc))
    if not profile or profiles_root is None:
        return Finding(
            "*", "product", consumer, f"{slug!r} named, but no --profile to resolve under"
        )
    kit = profiles_root / profile / "products" / slug / "BRAND.toml"
    if not kit.is_file():
        return Finding("*", "product", consumer, f"{kit} does not exist")
    return None


def _overlay_findings(shot: dict, sid: str, stage: str, run_dir: Path, finish: dict | None):
    entries = overlay_entries(shot)
    if not entries:
        return
    if stage in ("plan", "render"):
        consumer = "shots_lint.overlay kind+text"
        for k, entry in enumerate(entries):
            bad = (
                not isinstance(entry, dict)
                or normalize_overlay_kind(entry.get("kind")) is None
                or not str(entry.get("text") or "").strip()
            )
            detail = f"overlay[{k}] kind/text malformed" if bad else f"overlay[{k}] well-formed"
            yield bad, Finding(sid, "production.overlay", consumer, detail)
    else:
        burned = overlay_burned(run_dir, sid, finish)
        detail = (
            "burned sidecar or finish-manifest entry found"
            if burned
            else f"no shots-overlaid/{sid}-overlaid.overlays.json and no finish-manifest entry"
        )
        yield (not burned), Finding(sid, "production.overlay", "video_finish overlays", detail)


def _join_recorded(finish: dict | None, join: int, entry: dict) -> bool:
    """The finish manifest records THIS join with the kind and duration the shot declared.

    Kind and duration are both checked, not merely presence: a manifest that records a join is
    evidence that a transition happened there, and only the pair is evidence that the transition
    the shot list asked for is the one that happened."""
    rows = (finish or {}).get("transitions")
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(r, dict)
        and r.get("join") == join
        and r.get("kind") == entry.get("kind")
        and abs(float(r.get("duration_s") or 0.0) - float(entry.get("duration_s") or 0.0)) <= 0.001
        for r in rows
    )


def _transition_findings(shot: dict, i: int, sid: str, stage: str, finish: dict | None):
    entry = transition_in(shot)
    if entry is None:
        return
    field = "production.transition_in"
    if stage in ("plan", "render"):
        errors = transition_errors(entry, field)
        detail = errors[0] if errors else "kind and duration_s well-formed"
        yield bool(errors), Finding(sid, field, "shots_lint.transition kind+duration", detail)
        return
    consumer = "video_finish stitch join"
    if i == 0:
        yield True, Finding(sid, field, consumer, "the first shot has no join to be cut into")
        return
    ok = isinstance(entry, dict) and _join_recorded(finish, i - 1, entry)
    detail = (
        f"finish manifest records join {i - 1} as declared"
        if ok
        else f"finish manifest has no join {i - 1} matching {entry!r}"
    )
    yield (not ok), Finding(sid, field, consumer, detail)


def _audio_findings(shot: dict, sid: str, run_dir: Path, cues: dict | None):
    sfx_dir = run_dir / "sfx"
    if sfx_declared(shot):
        ok = cue_present(cues, sid, sfx_dir)
        detail = "cue with existing file" if ok else "no cue with an existing file in sfx/cues.json"
        yield (not ok), Finding(sid, "sfx", "sfx/cues.json cue", detail)
    if bed_declared(shot):
        ok = bed_present(cues, sfx_dir)
        detail = "bed or room_tone laid" if ok else "no bed and no room_tone in sfx/cues.json"
        yield (not ok), Finding(sid, "audio_bed", "sfx/cues.json bed", detail)


def _shot_findings(
    shot: dict, i: int, *, stage: str, run_dir: Path, render, finish, cues
) -> list[tuple[bool, Finding]]:
    sid = shot_id_of(shot, i)
    out: list[tuple[bool, Finding]] = list(_overlay_findings(shot, sid, stage, run_dir, finish))
    out.extend(_transition_findings(shot, i, sid, stage, finish))
    if stage in ("render", "finish"):
        out.extend(_audio_findings(shot, sid, run_dir, cues))
        if is_keyframe_shot(shot):
            n = _shot_n(shot, i)
            ok = _end_frame_requested(manifest_entry(render, n))
            detail = (
                "end_image_job_id recorded"
                if ok
                else f"render manifest shot n={n} has no end_image_job_id / end_media_id"
            )
            out.append(
                (not ok, Finding(sid, "end_frame", "render manifest end_image_job_id", detail))
            )
    if stage == "finish" and caption_declared(shot):
        sidecar = captions_sidecar(run_dir, sid)
        ok = sidecar.is_file() or _captions_preburned(finish)
        detail = "captions sidecar or preburned screens" if ok else f"{sidecar.name} absent"
        out.append((not ok, Finding(sid, "captions", "video_finish burn-captions", detail)))
    return out


def check_coverage(
    doc: dict,
    *,
    run_dir: Path,
    stage: str,
    render_manifest: Path | None = None,
    finish_manifest: Path | None = None,
    ratio: str = "9:16",
    profile: str | None = None,
    product: str | None = None,
    profiles_root: Path | None = None,
) -> CoverageReport:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")
    slug = ratio.replace(":", "x")
    render = load_json(render_manifest or run_dir / f"render-{slug}.json")
    finish = load_json(finish_manifest or run_dir / f"finish-{slug}.json")
    cues = load_json(run_dir / "sfx" / "cues.json")
    shots = doc.get("shots") if isinstance(doc.get("shots"), list) else []

    missing: list[Finding] = []
    ok: list[Finding] = []
    if stage == "plan":
        pf = _product_finding(doc, product, profile, profiles_root)
        if pf:
            missing.append(pf)
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        for is_missing, finding in _shot_findings(
            shot, i, stage=stage, run_dir=run_dir, render=render, finish=finish, cues=cues
        ):
            (missing if is_missing else ok).append(finding)
    return CoverageReport(stage=stage, missing=tuple(missing), ok=tuple(ok))


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────


def _print_table(report: CoverageReport) -> None:
    rows = [(f, "MISSING") for f in report.missing] + [(f, "ok") for f in report.ok]
    rows.sort(key=lambda r: (r[0].shot_id, r[0].field))
    print(f"[shots-coverage] stage={report.stage}")
    for f, status in rows:
        print(f"  {f.shot_id:<10} {f.field:<20} -> {f.consumer:<36} {status:<8} {f.detail}")
    if report.missing:
        print(f"  {len(report.missing)} declared field(s) have no consumer at stage {report.stage}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.shots_coverage")
    parser.add_argument("--shots", required=True, type=Path)
    parser.add_argument("--dir", required=True, type=Path, help="the run folder")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--render-manifest", type=Path, default=None)
    parser.add_argument("--finish-manifest", type=Path, default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--product", default=None)
    parser.add_argument("--profiles-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[shots-coverage] cannot read {args.shots}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(doc, dict) or not isinstance(doc.get("shots"), list):
        print(f"[shots-coverage] {args.shots} is not a shot list", file=sys.stderr)
        return 1

    profiles_root = args.profiles_root or PathConfig.from_env().profiles_root
    report = check_coverage(
        doc,
        run_dir=args.dir,
        stage=args.stage,
        render_manifest=args.render_manifest,
        finish_manifest=args.finish_manifest,
        ratio=args.ratio,
        profile=args.profile,
        product=args.product,
        profiles_root=profiles_root,
    )
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        _print_table(report)
    return 2 if report.missing else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
