from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluate import evaluate
from .measure import measure_audio, measure_caption_contrast, measure_cuts_and_motion
from .model import ERROR, SAFE_AREAS, Finding
from .probe import ProbeFailed, ProbeUnavailable, probe
from .suppress import BadSuppression, Suppression, _validate_suppressions, apply_suppressions


def report(asset: str, findings: list[Finding], suppressed: dict[str, int]) -> None:
    if not findings:
        print(f"✓ {asset} — clean")
    else:
        print(f"\n{asset}")
        for f in sorted(findings, key=lambda f: (f.tier, f.rule)):
            mark = "✗" if f.severity == ERROR else "!"
            print(f"  {mark} [{f.tier} {f.rule}] {f.excerpt}")
            print(f"      → {f.fix}")
    total_suppressed = sum(suppressed.values())
    if total_suppressed:
        breakdown = ", ".join(f"{tier}:{n}" for tier, n in sorted(suppressed.items()))
        print(f"\n  suppressed: {total_suppressed} ({breakdown})")
    else:
        print("\n  suppressed: 0")


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

    manifest_payload: dict | None = None
    suppressions: list[Suppression] = []
    if args.manifest:
        if not args.manifest.exists():
            print(f"[video_lint] no such manifest: {args.manifest}", file=sys.stderr)
            return 3
        try:
            raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[video_lint] malformed manifest JSON: {exc}", file=sys.stderr)
            return 2
        manifest_payload = raw.get("captions")
        if not args.no_suppress:
            try:
                suppressions = _validate_suppressions(raw.get("lint_suppressions", []))
            except BadSuppression as exc:
                print(f"[video_lint] {exc}", file=sys.stderr)
                return 2

    # Context the CLI previously never assembled, so V5/V6 could not fire from the command line
    # at all and V7-V9 would have been dead on arrival. --fast skips the two ffmpeg passes.
    cuts = motion = None
    if not args.fast:
        cuts, motion = measure_cuts_and_motion(args.asset)

    raw_manifest: dict = {}
    if args.manifest and args.manifest.exists():
        try:
            raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw_manifest = {}

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
        spoken_text=(manifest_payload or {}).get("spoken_text") or raw_manifest.get("spoken_text"),
        motion_stats=motion,
        identity_used=raw_manifest.get("identity_used"),
        caption_contrast=contrast,
    )
    asset_name = args.asset.name
    findings = [Finding(f.tier, f.rule, f.severity, asset_name, f.excerpt, f.fix) for f in findings]
    kept, suppressed_counts = apply_suppressions(findings, suppressions, asset=asset_name)

    if args.as_json:
        print(
            json.dumps(
                {
                    "asset": asset_name,
                    "findings": [f.__dict__ for f in kept],
                    "suppressed": suppressed_counts,
                },
                indent=2,
            )
        )
    else:
        report(asset_name, kept, suppressed_counts)

    return 1 if any(f.severity == ERROR for f in kept) else 0
