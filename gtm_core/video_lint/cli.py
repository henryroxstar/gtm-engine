from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluate import evaluate
from .measure import (
    frame_deltas,
    measure_audio,
    measure_caption_contrast,
    measure_cuts_and_motion,
    measure_transitions,
)
from .model import ERROR, SAFE_AREAS, Finding
from .probe import ProbeFailed, ProbeUnavailable, probe
from .suppress import (
    BadSuppression,
    Suppression,
    _validate_suppressions,
    apply_suppressions,
    stale_suppressions,
)

#: The measured audio figures surfaced in every report, keyed to their unit — so an operator can
#: see the numbers V10 judged, not only the verdict. Order is the order they print in.
_AUDIO_REPORT_KEYS: tuple[tuple[str, str, str], ...] = (
    ("integrated_lufs", "I", "LUFS"),
    ("true_peak_dbfs", "TP", "dBTP"),
    ("loudness_range_lu", "LRA", "LU"),
    ("crest_db", "crest", "dB"),
    ("loudness_abruptness_lu", "abruptness", "LU"),
    ("loudness_event_fraction", "events", ""),
)


def audio_summary(audio_context: dict | None) -> dict:
    """The measured audio numbers an operator should see, or ``{}`` when nothing was measured.
    Declared (producer) keys are deliberately excluded — this is what the file contains."""
    if not audio_context:
        return {}
    return {
        k: audio_context[k] for k, _, _ in _AUDIO_REPORT_KEYS if audio_context.get(k) is not None
    }


def _audio_line(audio: dict) -> str:
    parts = []
    for key, label, unit in _AUDIO_REPORT_KEYS:
        if key not in audio:
            continue
        value = audio[key]
        text = f"{float(value):.1%}" if key == "loudness_event_fraction" else f"{value:g}"
        parts.append(f"{label}={text}{(' ' + unit) if unit else ''}")
    return "  audio: " + ", ".join(parts)


def report(
    asset: str,
    findings: list[Finding],
    suppressed: dict[str, int],
    audio: dict | None = None,
    stale: list[Suppression] | None = None,
) -> None:
    if not findings:
        print(f"✓ {asset} — clean")
    else:
        print(f"\n{asset}")
        for f in sorted(findings, key=lambda f: (f.tier, f.rule)):
            mark = "✗" if f.severity == ERROR else "!"
            print(f"  {mark} [{f.tier} {f.rule}] {f.excerpt}")
            print(f"      → {f.fix}")
    if audio:
        print(_audio_line(audio))
    total_suppressed = sum(suppressed.values())
    if total_suppressed:
        breakdown = ", ".join(f"{tier}:{n}" for tier, n in sorted(suppressed.items()))
        print(f"\n  suppressed: {total_suppressed} ({breakdown})")
    else:
        print("\n  suppressed: 0")
    for s in stale or []:
        print(
            f"  stale suppression: {s.tier} matched nothing this run — delete it before it "
            f"hides the next real {s.tier}"
        )


def sibling_manifest(asset: Path, ratio: str) -> Path | None:
    """The finish manifest ``gtm_core.video_finish`` writes beside an asset —
    ``finish-<ratio-slug>.json`` with ``:`` as ``x`` (``finish-9x16.json``) — if one exists.
    Looked up so a bare lint can say out loud that it is ignoring it: without ``--manifest``
    V3/V5/V8/V11 have no input and skip, and a skip reads exactly like a pass."""
    candidate = asset.parent / f"finish-{ratio.replace(':', 'x')}.json"
    return candidate if candidate.is_file() else None


def _read_manifest(args) -> tuple[dict, dict | None, list[Suppression]] | int:
    """``(raw manifest, captions payload, suppressions)`` — or the exit code to return.

    With no ``--manifest`` the result is empty, and if a finish manifest sits beside the asset
    that is said out loud: the skipped tiers read exactly like passed ones otherwise."""
    if not args.manifest:
        sibling = sibling_manifest(args.asset, args.ratio)
        if sibling is not None:
            print(
                f"[video_lint] WARN: {sibling.name} sits beside this asset and was not passed — "
                "bare lint silently skips V3/V5/V8/V11 — pass --manifest",
                file=sys.stderr,
            )
        return {}, None, []
    if not args.manifest.exists():
        print(f"[video_lint] no such manifest: {args.manifest}", file=sys.stderr)
        return 3
    try:
        raw = json.loads(args.manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[video_lint] malformed manifest JSON: {exc}", file=sys.stderr)
        return 2
    suppressions: list[Suppression] = []
    if not args.no_suppress:
        try:
            suppressions = _validate_suppressions(raw.get("lint_suppressions", []))
        except BadSuppression as exc:
            print(f"[video_lint] {exc}", file=sys.stderr)
            return 2
    return raw, raw.get("captions"), suppressions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.video_lint")
    parser.add_argument("asset", type=Path)
    parser.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    parser.add_argument("--purpose", choices=("predictor",), default=None)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="finish-<ratio>.json — carries captions.json's frame/screens AND lint_suppressions",
    )
    parser.add_argument(
        "--no-suppress", action="store_true", help="ignore the manifest's suppressions"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip the measurement ffmpeg passes (V6, V9, V10's signal checks and V11 will "
        "not run; V10 still catches a missing audio stream from the probe alone)",
    )
    args = parser.parse_args(argv)

    try:
        p = probe(args.asset)
    except ProbeUnavailable as exc:
        print(f"[video_lint] {exc}", file=sys.stderr)
        return 3
    except ProbeFailed as exc:
        print(f"[video_lint] {exc}", file=sys.stderr)
        return 3

    loaded = _read_manifest(args)
    if isinstance(loaded, int):
        return loaded
    raw_manifest, manifest_payload, suppressions = loaded

    # Context the CLI previously never assembled, so V5/V6 could not fire from the command line
    # at all and V7-V9 would have been dead on arrival. --fast skips the two ffmpeg passes.
    cuts = motion = transitions = None
    if not args.fast:
        # ONE tblend decode feeds both V9 (motion) and V12 (transitions). `None` from either
        # measure means that tier is SKIPPED, not that the asset is clean — a verdict nothing
        # measured is the one answer this tool must never give.
        deltas = frame_deltas(args.asset)
        cuts, motion = measure_cuts_and_motion(args.asset, deltas=deltas)
        transitions = measure_transitions(args.asset, deltas=deltas)

    # V5 needs facts about the MIX that only the producer knows (is there a bed, was it ducked);
    # V10 needs facts about the SIGNAL that only measurement knows. One dict, two sources, the
    # measured half last so a stale declaration can never mask what the file actually contains.
    contrast = None
    audio_context = dict(raw_manifest.get("audio_context") or {})
    if not args.fast:
        measured = measure_audio(args.asset, duration_s=p.duration_s)
        if measured:
            audio_context.update(measured)
        if manifest_payload:
            contrast = measure_caption_contrast(args.asset, manifest_payload)

    findings = evaluate(
        p,
        ratio=args.ratio,
        manifest=manifest_payload,
        purpose=args.purpose,
        audio_context=audio_context or None,
        scene_changes=cuts,
        transitions=transitions,
        spoken_text=(manifest_payload or {}).get("spoken_text") or raw_manifest.get("spoken_text"),
        motion_stats=motion,
        identity_used=raw_manifest.get("identity_used"),
        caption_contrast=contrast,
        caption_route=raw_manifest.get("caption_route"),
        captions_preburned=raw_manifest.get("captions_preburned"),
    )
    asset_name = args.asset.name
    findings = [Finding(f.tier, f.rule, f.severity, asset_name, f.excerpt, f.fix) for f in findings]
    kept, suppressed_counts = apply_suppressions(findings, suppressions, asset=asset_name)
    audio = audio_summary(audio_context)

    # Which tiers this pass actually RAN — the line stale_suppressions needs to tell "matched
    # nothing" from "never checked". V6/V9/V10/V12/V13 need the tblend/audio measurement passes
    # --fast skips; V11's contrast check additionally needs a captions payload to sample.
    ran_tiers = {"V1", "V2", "V3", "V4", "V5", "V7", "V8"}
    if not args.fast:
        ran_tiers |= {"V6", "V9", "V10", "V12", "V13"}
        if manifest_payload:
            ran_tiers.add("V11")
    raw_counts: dict[str, int] = {}
    for f in findings:
        raw_counts[f.tier] = raw_counts.get(f.tier, 0) + 1
    stale = stale_suppressions(
        suppressions, {t: raw_counts.get(t, 0) for t in ran_tiers}, asset=asset_name
    )

    if args.as_json:
        print(
            json.dumps(
                {
                    "asset": asset_name,
                    "findings": [f.__dict__ for f in kept],
                    "suppressed": suppressed_counts,
                    "audio": audio,
                    "stale_suppressions": [
                        {"tier": s.tier, "asset": s.asset, "reason": s.reason} for s in stale
                    ],
                },
                indent=2,
            )
        )
    else:
        report(asset_name, kept, suppressed_counts, audio=audio, stale=stale)

    return 1 if any(f.severity == ERROR for f in kept) else 0
