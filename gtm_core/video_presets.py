"""Video brief presets — tenant-configurable archetype packages for creator briefs.

Loads and validates presets from `profiles/<active>/video-presets.toml`, falling back
to `profiles/_template/video-presets.toml`.

Archetypes:
- product-walkthrough: Feature demo, procedural walkthrough, or engineering workflow
- story-anecdote: Personal builder reflection, customer breakthrough, or failure lesson
- explainer-breakdown: Deconstruct a complex system, debunk an industry myth, or unpack a framework
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import _safe_segment, resolve_profiles_root

PRESETS_FILENAME = "video-presets.toml"
TEMPLATE_PROFILE = "_template"

CORE_PRESET_KEYS = (
    "product-walkthrough",
    "story-anecdote",
    "explainer-breakdown",
)

REQUIRED_PRESET_FIELDS = (
    "name",
    "description",
    "cover",
    "outlier_structure",
    "slot_schema",
    "cheapest_medium",
    "capture_mode",
    "invariant",
    "broll_list",
    "sampling_curve",
    "visual_hook",
)


class PresetError(Exception):
    """Raised when a preset configuration is missing, malformed, or invalid."""


@dataclass(frozen=True)
class VideoPreset:
    id: str
    name: str
    description: str
    cover: str
    outlier_structure: str
    slot_schema: list[str]
    cheapest_medium: str
    capture_mode: str
    invariant: str
    broll_list: list[str]
    sampling_curve: str
    visual_hook: str
    caption_voice: str | None = None
    max_spend_credits: int = 50

    @classmethod
    def from_dict(cls, preset_id: str, data: dict[str, Any]) -> VideoPreset:
        missing = [f for f in REQUIRED_PRESET_FIELDS if f not in data]
        if missing:
            raise PresetError(
                f"Preset '{preset_id}' is missing required fields: {', '.join(missing)}"
            )

        if not isinstance(data["slot_schema"], list) or not data["slot_schema"]:
            raise PresetError(f"Preset '{preset_id}': slot_schema must be a non-empty list")
        if not isinstance(data["broll_list"], list) or not data["broll_list"]:
            raise PresetError(f"Preset '{preset_id}': broll_list must be a non-empty list")
        if data["capture_mode"] not in ("rendered", "live_action"):
            raise PresetError(
                f"Preset '{preset_id}': invalid capture_mode '{data['capture_mode']}' "
                "(must be 'rendered' or 'live_action')"
            )

        max_spend = data.get("max_spend_credits", 50)
        try:
            max_spend_int = int(max_spend)
        except (ValueError, TypeError) as exc:
            raise PresetError(
                f"Preset '{preset_id}': max_spend_credits must be an integer, got {max_spend!r}"
            ) from exc
        if max_spend_int <= 0:
            raise PresetError(
                f"Preset '{preset_id}': max_spend_credits must be > 0, got {max_spend_int}"
            )

        return cls(
            id=preset_id,
            name=str(data["name"]),
            description=str(data["description"]),
            cover=str(data["cover"]),
            outlier_structure=str(data["outlier_structure"]),
            slot_schema=list(data["slot_schema"]),
            cheapest_medium=str(data["cheapest_medium"]),
            capture_mode=str(data["capture_mode"]),
            invariant=str(data["invariant"]),
            broll_list=list(data["broll_list"]),
            sampling_curve=str(data["sampling_curve"]),
            visual_hook=str(data["visual_hook"]),
            caption_voice=str(data["caption_voice"]) if "caption_voice" in data else None,
            max_spend_credits=max_spend_int,
        )

    def to_brief_decisions(self) -> dict[str, Any]:
        """Convert this preset into decisions formatted for creator_brief.schema.json."""
        return {
            "cover": {
                "value": {
                    "subject": self.cover,
                    "headline": f"{self.name} — Overview",
                },
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "outlier_structure": {
                "value": {
                    "name": self.outlier_structure,
                    "beats": list(self.slot_schema),
                },
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "slot_schema": {
                "value": list(self.slot_schema),
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "cheapest_medium": {
                "value": {
                    "chosen": "video",
                    "reason": self.cheapest_medium,
                },
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "capture_mode": {
                "value": self.capture_mode,
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "invariant": {
                "value": {
                    "look": self.invariant,
                    "aspect_ratio": "9:16",
                },
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "broll_list": {
                "value": list(self.broll_list),
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "sampling_curve": {
                "value": {
                    "per_shot": [
                        {"shot": idx + 1, "n": int(n)}
                        for idx, n in enumerate(self.sampling_curve.split(","))
                        if n.strip().isdigit()
                    ],
                    "criterion": "visual_engagement",
                },
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            "visual_hook": {
                "value": {
                    "subject": self.visual_hook,
                    "framing": "medium_closeup",
                },
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
            **(
                {
                    "caption_voice": {
                        "value": {
                            "mode": "narrative",
                            "voice": self.caption_voice,
                        },
                        "source": "profile_default",
                        "reason": f"Preset {self.name}",
                    }
                }
                if self.caption_voice
                else {}
            ),
            "max_spend_credits": {
                "value": self.max_spend_credits,
                "source": "profile_default",
                "reason": f"Preset {self.name}",
            },
        }


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        try:
            return tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise PresetError(f"Malformed TOML in {path}: {exc}") from exc


def load_presets(
    profile: str,
    profiles_root: Path | None = None,
) -> dict[str, VideoPreset]:
    """Load video presets for ``profile``, falling back to template presets.

    Returns dict mapping preset id -> VideoPreset instance.
    """
    root = resolve_profiles_root() if profiles_root is None else profiles_root
    _safe_segment(profile, "profile")

    # Load template presets
    template_file = root / TEMPLATE_PROFILE / PRESETS_FILENAME
    template_raw = _load_toml(template_file).get("presets", {})

    # Load tenant presets if available
    tenant_file = root / profile / PRESETS_FILENAME
    tenant_raw = _load_toml(tenant_file).get("presets", {}) if tenant_file.is_file() else {}

    # Merge: tenant overrides template per preset key
    merged_raw: dict[str, Any] = dict(template_raw)
    merged_raw.update(tenant_raw)

    if not merged_raw:
        raise PresetError(f"No video presets found in {tenant_file} or {template_file}")

    presets: dict[str, VideoPreset] = {}
    for pid, data in merged_raw.items():
        if not isinstance(data, dict):
            raise PresetError(f"Preset '{pid}' must be a TOML table")
        presets[pid] = VideoPreset.from_dict(pid, data)

    return presets


def resolve_preset(
    profile: str,
    preset_id: str,
    profiles_root: Path | None = None,
) -> VideoPreset:
    """Resolve a single preset by ID, raising PresetError if not found."""
    presets = load_presets(profile, profiles_root)
    if preset_id not in presets:
        available = ", ".join(sorted(presets.keys()))
        raise PresetError(f"Unknown preset '{preset_id}'. Available presets: {available}")
    return presets[preset_id]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uv run python -m gtm_core.video_presets",
        description="Inspect available video brief presets for a profile.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default=None,
        help="action: 'list' (default) or 'resolve', or a preset ID",
    )
    parser.add_argument(
        "preset_arg",
        nargs="?",
        default=None,
        help="preset ID to inspect when using 'resolve <preset>'",
    )
    parser.add_argument("--profile", default=TEMPLATE_PROFILE, help="profile slug to inspect")
    parser.add_argument("--preset", default=None, help="specific preset ID to inspect")
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    args = parser.parse_args(argv)

    target_preset: str | None = None
    if args.preset:
        target_preset = args.preset
    elif args.action == "resolve" and args.preset_arg:
        target_preset = args.preset_arg
    elif args.action and args.action not in ("list", "resolve"):
        target_preset = args.action

    try:
        if target_preset:
            p = resolve_preset(args.profile, target_preset)
            if args.json:
                print(json.dumps(asdict(p), indent=2))
            else:
                print(f"Preset: {p.name} ({p.id})")
                print(f"Description: {p.description}")
                print(f"Capture Mode: {p.capture_mode}")
                print(f"Slot Schema: {' -> '.join(p.slot_schema)}")
                print(f"B-Roll: {', '.join(p.broll_list)}")
                print(f"Sampling: {p.sampling_curve}")
        else:
            presets = load_presets(args.profile)
            if args.json:
                print(json.dumps({k: asdict(v) for k, v in presets.items()}, indent=2))
            else:
                print(f"Available presets for profile '{args.profile}':\n")
                for pid, p in presets.items():
                    print(f"  • {p.name} [{pid}]: {p.description} ({p.capture_mode})")
        return 0
    except PresetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
