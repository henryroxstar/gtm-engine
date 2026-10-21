"""Pre-render validator for Mission Control video scripts.

Enforces brief gate, per-block payload schemas, duration checks, 6-word density guidelines,
and plain-language jargon suggestions.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from ...brandkit import design_system_denied, scan_denied
from ...paths import _safe_segment

ROOT = Path(__file__).resolve().parents[3]

BRIEF_FIELDS = {
    "placement": "Where it plays (booth TV, venue reel, LinkedIn, website…)",
    "formats": "Formats and size (e.g. 16:9 1080p)",
    "maxDuration": "Hard length cap in seconds",
    "sound": "Sound on or off",
    "audience": "Who it is for",
    "message": "The one thing they should remember",
    "cta": "Call to action (URL or booth)",
    "claimsOwner": "Who signs off the claims",
}

DEFAULT_DURATIONS = {
    "hook": 3.6,
    "intro": 3.0,
    "claim": 4.6,
    "outcome": 4.5,
    "endcard": 5.1,
}

REQUIRED_BLOCK_FIELDS = {
    "hook-constellation": ["line1", "line2"],
    "hook-traffic": ["line1", "line2"],
    "intro": ["logo", "line"],
    "idcard": ["title", "fields"],
    "gate": ["title", "rules", "requests"],
    "log": ["title", "agents"],
    "traffic-gate": ["title"],
    "reviewers": ["title"],
    "budgets": ["title", "teams"],
    "outcome": ["line1", "line2"],
    "endcard": ["logo", "cta", "url"],
}

BUILTIN_JARGON_REPLACEMENTS = {
    "zero-trust": "verify every step",
    "oauth": "login handoff",
    "jwt": "access pass",
    "blast radius": "damage reach",
    "mcp": "tool protocol",
    "prompt injection": "instruction hijacking",
    "fail-closed": "default-deny",
    "synergy": "teamwork",
    "paradigm": "approach",
    "game-changer": "major shift",
    "leverage": "use",
    "utilize": "use",
    "frictionless": "seamless",
    "mission-critical": "vital",
    "single pane of glass": "one console",
    "next-gen": "modern",
    "best-of-breed": "leading",
    "robust": "reliable",
    "scalable": "fast",
    "actionable": "practical",
    "holistic": "complete",
}


def load_jargon_replacements(
    profile: str = "_template", repo_root: Path | None = None
) -> dict[str, str]:
    """Load plain-language jargon replacements from profile knowledge or fallback to builtins."""
    safe_profile = _safe_segment(profile, "profile")
    root = repo_root or ROOT
    path = root / f"profiles/{safe_profile}/knowledge/brand/video-jargon-replacements.toml"
    merged = dict(BUILTIN_JARGON_REPLACEMENTS)
    if path.is_file():
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
                merged.update(data.get("replacements", {}))
        except (tomllib.TOMLDecodeError, OSError):  # nosec B110
            pass
    return merged


def validate_script(  # noqa: C901, PLR0912, PLR0915  # PRD 2026-10-16-html-motion-scenes
    script: dict[str, Any],
    *,
    brand_kit: dict[str, Any] | None = None,
    profile: str = "_template",
    strict_density: bool = False,
    repo_root: Path | None = None,
) -> tuple[bool, list[str], list[str], float, list[dict[str, Any]]]:
    """Validate a video script dictionary.

    Returns:
        (is_valid, errors, warnings, total_duration, jargon_suggestions)
    """
    errors: list[str] = []
    warnings: list[str] = []
    jargon_suggestions: list[dict[str, Any]] = []

    root = repo_root or ROOT
    resolved_kit: dict[str, Any] = brand_kit or {}
    if brand_kit is None and profile:
        safe_prof = _safe_segment(profile, "profile")
        kit_path = root / "profiles" / safe_prof / "knowledge" / "BRAND.toml"
        if not kit_path.is_file() and safe_prof != "_template":
            errors.append(f"Profile '{profile}' is missing BRAND.toml ({kit_path}).")
        elif kit_path.is_file():
            try:
                with open(kit_path, "rb") as kf:
                    resolved_kit = tomllib.load(kf)
            except (tomllib.TOMLDecodeError, OSError):  # nosec B110
                resolved_kit = {}

    denied_fonts, denied_hexes = design_system_denied(resolved_kit)

    # 1. Brief validation (accepts both "brief" and "meta.brief")
    brief = script.get("brief") or (
        script.get("meta", {}).get("brief") if isinstance(script.get("meta"), dict) else None
    )
    if not brief or not isinstance(brief, dict):
        errors.append(
            'No "brief" in the script. Run the brief gate and record the answers before rendering.'
        )
    else:
        for k, label in BRIEF_FIELDS.items():
            val = brief.get(k)
            if val is None or val == "":
                errors.append(f'Brief is missing "{k}" — {label}.')
        if brief.get("confirmed") is not True:
            errors.append(
                'Brief is not confirmed by the requester ("confirmed": true). Read it back to them and get a yes first.'
            )
        formats = str(brief.get("formats", ""))
        if re.search(r"9:16|portrait|vertical|reel", formats, re.IGNORECASE):
            warnings.append(
                "Portrait (9:16) was requested: the runtime renders 16:9 only. Tell the requester before going further."
            )

    # 2. Product HUD field
    if not script.get("product"):
        errors.append('Missing "product" (shown in the HUD).')

    # 3. Scenes presence and minimum count
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append('Missing "scenes".')
        return False, errors, warnings, 0.0, jargon_suggestions

    is_single_beat = (brief.get("placement") == "single-beat") if brief else False
    if not is_single_beat and len(scenes) < 3:
        errors.append(f"A video film requires at least 3 scenes (got {len(scenes)}).")

    # 4. First and last scene structural conventions
    if scenes and not str(scenes[0].get("type", "")).startswith("hook"):
        warnings.append("The first scene is not a hook. Open on the question, not the logo.")
    if scenes and scenes[-1].get("type") != "endcard":
        warnings.append("The last scene is not an end card.")

    jargon_dict = load_jargon_replacements(profile, repo_root=repo_root)

    total_duration = 0.0
    for idx, sc in enumerate(scenes):
        where = f"scene {idx + 1} ({sc.get('type')})"
        sc_type = sc.get("type")
        if sc_type not in REQUIRED_BLOCK_FIELDS:
            errors.append(f"{where}: unknown block type.")
            continue

        for req_field in REQUIRED_BLOCK_FIELDS[sc_type]:
            val = sc.get(req_field)
            if val is None or val == "":
                errors.append(f'{where}: missing "{req_field}".')

        # Scan scene payload (including any inline SVG / custom styling) against denied_fonts and denied_hexes
        if denied_fonts or denied_hexes:
            for kind, token in scan_denied(json.dumps(sc), denied_fonts, denied_hexes):
                errors.append(
                    f"{where}: [DS-DRIFT-03] contains denied {kind} '{token}' "
                    f"(forbidden by [design_system].denied_{kind}s)."
                )

        # Logo and brandLogo file existence check when specified as an asset path
        for logo_key in ("logo", "brandLogo"):
            logo_val = sc.get(logo_key)
            if (
                isinstance(logo_val, str)
                and ("/" in logo_val or logo_val.endswith((".svg", ".png")))
                and not (profile == "_template" and logo_val == "logos/brand/logo.svg")
            ):
                safe_prof = _safe_segment(profile, "profile") if profile else "_template"
                candidate = root / "profiles" / safe_prof / "knowledge" / "brand" / logo_val
                if not candidate.is_file() and safe_prof == "_template":
                    found_any = any(
                        (p / "knowledge" / "brand" / logo_val).is_file()
                        for p in (root / "profiles").iterdir()
                        if p.is_dir() and not p.name.startswith(".")
                    )
                    if not found_any:
                        errors.append(f"{where}: {logo_key} file not found at brand/{logo_val}.")
                elif not candidate.is_file():
                    errors.append(f"{where}: {logo_key} file not found at brand/{logo_val}.")
                elif candidate.suffix.lower() == ".svg" and (denied_fonts or denied_hexes):
                    try:
                        svg_text = candidate.read_text(encoding="utf-8")
                        for kind, token in scan_denied(svg_text, denied_fonts, denied_hexes):
                            errors.append(
                                f"{where}: [DS-DRIFT-03] {logo_key} SVG contains denied {kind} '{token}'."
                            )
                    except OSError:
                        pass

        # Type-specific payload sanity
        if sc_type == "idcard":
            fields = sc.get("fields")
            if not isinstance(fields, list) or not fields:
                errors.append(f'{where}: "fields" must be a non-empty list.')
            else:
                for f_idx, field in enumerate(fields):
                    if not isinstance(field, (list, tuple)) or len(field) < 2:
                        errors.append(f"{where}: field {f_idx + 1} must be [label, value].")

        if sc_type == "gate":
            rules = sc.get("rules", [])
            requests = sc.get("requests", [])
            if not isinstance(rules, list):
                errors.append(f'{where}: "rules" must be a list.')
            if not isinstance(requests, list):
                errors.append(f'{where}: "requests" must be a list.')
            else:
                for req_idx, req in enumerate(requests):
                    passes = req.get("pass") if isinstance(req, dict) else None
                    if not isinstance(passes, list) or len(passes) != len(rules):
                        errors.append(
                            f"{where}: request {req_idx + 1} needs one pass/fail per rule."
                        )

        if sc_type == "budgets":
            teams = sc.get("teams")
            if not isinstance(teams, list) or not teams:
                errors.append(f'{where}: "teams" must be a non-empty list.')
            else:
                for t_idx, team in enumerate(teams):
                    if not isinstance(team, (list, tuple)) or len(team) < 3:
                        errors.append(f"{where}: team {t_idx + 1} must be [name, cap, pts].")
                    elif (
                        not isinstance(team[0], str)
                        or not isinstance(team[1], (int, float))
                        or not isinstance(team[2], list)
                    ):
                        errors.append(
                            f"{where}: team {t_idx + 1} fields invalid (expected [str, number, list])."
                        )

        if sc_type == "log":
            agents = sc.get("agents")
            if not isinstance(agents, list) or not agents:
                errors.append(f'{where}: "agents" must be a non-empty list.')
            else:
                for a_idx, agent in enumerate(agents):
                    if not isinstance(agent, (list, tuple)) or len(agent) < 2:
                        errors.append(f"{where}: agent {a_idx + 1} must be [name, action].")

        # Calculate scene duration
        default_dur = (
            DEFAULT_DURATIONS["hook"]
            if sc_type.startswith("hook")
            else DEFAULT_DURATIONS["intro"]
            if sc_type == "intro"
            else DEFAULT_DURATIONS["outcome"]
            if sc_type == "outcome"
            else DEFAULT_DURATIONS["endcard"]
            if sc_type == "endcard"
            else DEFAULT_DURATIONS["claim"]
        )
        dur = sc.get("dur")
        total_duration += float(dur if dur is not None else default_dur)

        # Text line density & jargon checks
        text_lines = [
            sc.get("title"),
            sc.get("line1"),
            sc.get("line2"),
            sc.get("line"),
            sc.get("sub"),
        ]
        for line_text in text_lines:
            if not line_text or not isinstance(line_text, str):
                continue
            words = len(re.sub(r"[\[\]]", "", line_text).strip().split())
            if words > 6:
                density_msg = (
                    f'{where}: title has {words} words ("{line_text}"). Aim for 6 or fewer.'
                )
                if strict_density:
                    errors.append(density_msg)
                else:
                    warnings.append(density_msg)

            if line_text.count("[") > 1:
                errors.append(
                    f'{where}: more than one accent span in "{line_text}" (one per heading, DR-015).'
                )

            low_line = f" {line_text.lower()} "
            for term, replacement in jargon_dict.items():
                pattern = rf"(?<!\w){re.escape(term.lower())}(?!\w)"
                if re.search(pattern, low_line):
                    msg = f'{where}: jargon "{term}" in "{line_text}". Use plain language (suggested: "{replacement}").'
                    warnings.append(msg)
                    jargon_suggestions.append(
                        {
                            "scene": idx + 1,
                            "term": term,
                            "suggestion": replacement,
                            "line": line_text,
                        }
                    )

    # Declared duration match check
    declared_dur = script.get("duration")
    if declared_dur is not None:
        try:
            target_d = float(declared_dur)
            if not math.isclose(total_duration, target_d, abs_tol=0.5):
                errors.append(
                    f"Scenes add up to {total_duration:.1f}s, but declared duration is {target_d:.1f}s (must match within ±0.5s)."
                )
        except (ValueError, TypeError):
            errors.append(f'Invalid "duration" field: {declared_dur!r}.')

    # Duration cap check
    max_dur = brief.get("maxDuration") if brief else 30
    if max_dur is not None:
        try:
            cap = float(max_dur)
            if total_duration > cap + 0.5:
                errors.append(f"Scenes add up to {total_duration:.1f}s, over the {cap:.1f}s cap.")
        except (ValueError, TypeError):
            pass

    is_valid = len(errors) == 0
    return is_valid, errors, warnings, total_duration, jargon_suggestions


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Mission Control video script.")
    parser.add_argument("script", help="Path to script JSON file.")
    parser.add_argument(
        "--strict-density", action="store_true", help="Fail if text line exceeds 6 words."
    )
    parser.add_argument(
        "--profile", default="_template", help="Profile to load jargon replacements from."
    )
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    args = parser.parse_args()

    script_path = Path(args.script)
    if not script_path.is_file():
        if args.json:
            print(json.dumps({"valid": False, "errors": [f"Script file not found: {script_path}"]}))
        else:
            print(f"✖ Script file not found: {script_path}", file=sys.stderr)
        sys.exit(2)

    try:
        with open(script_path, encoding="utf-8") as f:
            script_data = json.load(f)
    except Exception as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [f"Malformed JSON: {exc}"]}))
        else:
            print(f"✖ Malformed JSON: {exc}", file=sys.stderr)
        sys.exit(2)

    is_valid, errors, warnings, total_duration, suggestions = validate_script(
        script_data,
        profile=args.profile,
        strict_density=args.strict_density,
    )

    if args.json:
        res = {
            "valid": is_valid,
            "errors": errors,
            "warnings": warnings,
            "total_duration": total_duration,
            "jargon_suggestions": suggestions,
        }
        print(json.dumps(res, indent=2))
    else:
        for w in warnings:
            print(f"⚠ {w}", file=sys.stderr)
        for e in errors:
            print(f"✖ {e}", file=sys.stderr)
        if is_valid:
            print(f"✓ script OK — {total_duration:.1f}s")
        else:
            print(f"✖ {len(errors)} error(s)", file=sys.stderr)

    sys.exit(0 if is_valid else 2)


if __name__ == "__main__":
    main()
