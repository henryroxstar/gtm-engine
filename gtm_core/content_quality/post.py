from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..paths import _safe_segment, resolve_content_root, resolve_profiles_root
from ..render_manifest import FinishManifest, load_finish
from ..video_lint import ERROR, SAFE_AREAS, evaluate
from ..video_lint import probe as video_probe
from .guards import (
    _asset_text,
    _load_bans,
    _resolve_ban_file,
    _run_linter,
    _safe_to_share,
    _voice_bans_violated,
)
from .model import _RATIO_SLUG_TO_COLON, _VIDEO_FORMATS
from .sources import (
    _find_item,
    _load_json,
    _load_person_hook_matrix,
    _matrix_by_id,
    _repo_root,
    load_hook_matrix,
    load_profile_facts,
)


def _text_post_check(
    item: dict,
    facts: dict,
    content_root: Path,
    profile: str,
) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}

    item_id = item["id"]
    assets_dir = content_root / _safe_segment(profile, "profile") / "assets"
    asset_path = assets_dir / f"{item_id}.asset.json"
    if not asset_path.is_file():
        # Try locale variants if primary missing.
        variants = list(assets_dir.glob(f"{item_id}.*.asset.json"))
        if variants:
            asset_path = variants[0]
        else:
            blocking.append(f"asset file not found: {asset_path}")
            return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    asset = _load_json(asset_path)
    if asset is None:
        blocking.append(f"could not parse asset JSON: {asset_path}")
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    ban_file = _resolve_ban_file(facts)
    lint_ok, lint_messages = _run_linter(asset_path, ban_file=ban_file)
    checks["linter_pass"] = lint_ok
    if not lint_ok:
        blocking.append(f"content linter failed: {'; '.join(lint_messages)}")

    text = _asset_text(asset)
    safe_ok, safe_messages = _safe_to_share(text)
    checks["safe_to_share_pass"] = safe_ok
    if not safe_ok:
        blocking.append(f"safe-to-share check failed: {'; '.join(safe_messages)}")

    bans = _load_bans(ban_file)
    ban_violations = _voice_bans_violated(text, bans)
    checks["voice_bans_pass"] = not ban_violations
    if ban_violations:
        blocking.extend(ban_violations)

    # Advisory hook-quality heuristic: concrete anchor + zero-context opener.
    hook = (asset.get("hook") or "").strip()
    has_concrete_anchor = bool(re.search(r"\d", hook)) or bool(re.search(r"https?://", hook))
    has_zero_context = bool(hook) and len(hook) <= 140
    checks["hook_quality_advisory"] = has_concrete_anchor and has_zero_context
    if not checks["hook_quality_advisory"]:
        warnings.append(
            "hook lacks a concrete anchor or is not self-contained (see docs/hook-craft.md)"
        )

    proceed = not blocking
    return {
        "proceed": proceed,
        "blocking": blocking,
        "warnings": warnings,
        "checks": checks,
    }


def _find_video_manifests(content_root: Path, profile: str, item_id: str) -> dict[str, Path | None]:
    """Locate render/finish/score manifests for a video item.

    Matches on ``render-<ratio>.json``'s ``source_item`` field, not the finish manifest's
    ``plan_id`` — ``plan_id`` is a content hash (``sha256(canonical_json(plan))[:16]`` from
    :func:`gtm_core.video_finish.plan`), never a ``ContentItem.id``, so the two can never be
    equal. Fixed 2026-08-17: this previously made ``content_quality post`` fail with "finish
    manifest not found" for every video item, always — reproduced on an already-shipped asset
    (``ci-2026W33-01``), so this predated any one render and was never scoped to a single bug.
    """
    video_dir = content_root / _safe_segment(profile, "profile") / "video"
    out: dict[str, Path | None] = {"render": None, "finish": None, "score": None, "asset": None}
    if not video_dir.is_dir():
        return out
    for subdir in video_dir.iterdir():
        if not subdir.is_dir():
            continue
        for render_path in subdir.glob("render-*.json"):
            try:
                data = json.loads(render_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("source_item") != item_id:
                continue
            out["render"] = render_path
            ratio = render_path.stem.removeprefix("render-")
            finish_path = subdir / f"finish-{ratio}.json"
            score_path = subdir / "score.json"
            if finish_path.is_file():
                out["finish"] = finish_path
                try:
                    finish_data = json.loads(finish_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    finish_data = {}
                if finish_data.get("asset_path"):
                    asset = (
                        Path(finish_data["asset_path"])
                        if Path(finish_data["asset_path"]).is_absolute()
                        else _repo_root() / finish_data["asset_path"]
                    )
                    if asset.is_file():
                        out["asset"] = asset
            if score_path.is_file():
                out["score"] = score_path
            return out
    return out


def _video_post_check(
    item: dict,
    facts: dict,
    content_root: Path,
    profile: str,
) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}

    manifests = _find_video_manifests(content_root, profile, item["id"])
    finish_path = manifests.get("finish")
    render_path = manifests.get("render")
    score_path = manifests.get("score")
    asset_path = manifests.get("asset")

    if finish_path is None:
        blocking.append("finish manifest not found for video item")
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    try:
        finish: FinishManifest = load_finish(finish_path)
        checks["finish_manifest_valid"] = True
    except Exception as exc:
        blocking.append(f"finish manifest invalid: {exc}")
        checks["finish_manifest_valid"] = False
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    # Read identity_used directly from the raw JSON rather than through load_render()/
    # RenderManifest — that dataclass is the STRICT shape a future write_render_manifest()
    # caller would produce, but no render-<ratio>.json shipped in this repo has ever gone
    # through that writer (they are hand-authored by the video-render skill's prose
    # instructions, per that module's own docstring) — confirmed 2026-08-17 to be missing
    # required fields (slug, asset_path) and using different key names (local_path instead
    # of asset_path) even after tolerating extra keys. A strict parse here would make this
    # check unreachable for every real asset, exactly as the id-matching bug in
    # _find_video_manifests did. identity_used is the one field this function actually
    # needs, so read it directly and tolerate everything else about the file's shape.
    if render_path is not None:
        try:
            render_data = json.loads(render_path.read_text(encoding="utf-8"))
            raw_identity = render_data.get("identity_used")
            if isinstance(raw_identity, list):
                identity_used = {str(v) for v in raw_identity}
                checks["render_manifest_valid"] = True
            else:
                identity_used = set()
                checks["render_manifest_valid"] = False
                blocking.append(
                    "render manifest invalid: identity_used missing or not a list "
                    f"(got {type(raw_identity).__name__ if raw_identity is not None else 'absent'})"
                )
        except (OSError, json.JSONDecodeError) as exc:
            identity_used = set()
            checks["render_manifest_valid"] = False
            blocking.append(f"render manifest invalid: {exc}")
        render_exists = True
    else:
        identity_used = set()
        checks["render_manifest_valid"] = None
        render_exists = False

    # Synthetic if any identity token is present (soul/element/voice/generated).
    synthetic = bool(identity_used)
    checks["identity_recorded"] = synthetic or not render_exists
    if render_exists and checks["render_manifest_valid"] and not identity_used:
        blocking.append("synthetic video missing identity_used handles in render manifest")

    # Disclosure line check for synthetic video.
    brand_kit = facts.get("brand_kit") or {}
    disclosure_line = (
        brand_kit.get("disclosure", {}).get("line") if isinstance(brand_kit, dict) else None
    )
    checks["disclosure_configured"] = bool(disclosure_line)
    if synthetic and not disclosure_line:
        blocking.append("synthetic video requires disclosure.line in merged BRAND.toml")

    # Caption / safe area and loudness from finish manifest.
    captions = finish.captions or {}
    screens = captions.get("screens", []) if isinstance(captions, dict) else []
    ratio = _RATIO_SLUG_TO_COLON.get(finish.ratio, finish.ratio)
    checks["captions_inside_safe_area"] = None
    checks["loudness_target_met"] = None
    if ratio in SAFE_AREAS and isinstance(captions, dict):
        area = SAFE_AREAS[ratio]
        frame = captions.get("frame")
        if isinstance(frame, (list, tuple)) and len(frame) == 2:
            frame_w, frame_h = int(frame[0]), int(frame[1])
            x0 = round(area.left * frame_w)
            x1 = frame_w - round(area.right * frame_w)
            y0 = round(area.top * frame_h)
            y1 = frame_h - round(area.bottom * frame_h)
            violations = 0
            for screen in screens:
                box = screen.get("box", {})
                if not isinstance(box, dict):
                    continue
                sx, sy = int(box.get("x", 0)), int(box.get("y", 0))
                sw, sh = int(box.get("w", 0)), int(box.get("h", 0))
                if sx < x0 or sx + sw > x1 or sy < y0 or sy + sh > y1:
                    violations += 1
            checks["captions_inside_safe_area"] = violations == 0
            if violations:
                blocking.append(f"{violations} caption screen(s) outside safe area")

    census = dict(finish.census) if finish.census else {}
    loudness_ok = bool(census.get("loudnorm", False)) or bool(census.get("loudness", False))
    checks["loudness_target_met"] = loudness_ok
    if not loudness_ok:
        blocking.append("loudness target not recorded in finish census")

    # Video lint V1–V4.
    checks["video_lint_v1_v4_pass"] = None
    if asset_path is not None:
        try:
            p = video_probe(asset_path)
            findings = evaluate(p, ratio=ratio, manifest=captions)
            errors = [f for f in findings if f.severity == ERROR]
            checks["video_lint_v1_v4_pass"] = not errors
            if errors:
                for finding in errors:
                    blocking.append(f"video lint {finding.tier}/{finding.rule}: {finding.excerpt}")
        except Exception as exc:
            blocking.append(f"video lint could not run: {exc}")
    else:
        warnings.append("video asset file not found — skipping V1-V4 lint")

    # Predictor band recorded.
    checks["predictor_band_recorded"] = score_path is not None
    if not score_path:
        warnings.append("score.json not found — predictor band will not be tagged in outcomes")

    proceed = not blocking
    return {
        "proceed": proceed,
        "blocking": blocking,
        "warnings": warnings,
        "checks": checks,
    }


def post_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Post-generation quality check before review/publish.

    Returns ``{"proceed": bool, "blocking": [...], "warnings": [...], "checks": {...}}``.
    """
    content_root = content_root or resolve_content_root()
    profiles_root = resolve_profiles_root()
    item = _find_item(content_root, profile, item_id)

    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}

    if item is None:
        blocking.append(f"ContentItem {item_id!r} not found in content/{profile}/plans/")
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    facts = load_profile_facts(profiles_root, profile)
    fmt = item.get("format")

    if fmt in _VIDEO_FORMATS:
        result = _video_post_check(item, facts, content_root, profile)
    else:
        result = _text_post_check(item, facts, content_root, profile)

    # Merge hook_id advisory.
    hook_id = item.get("hook_id")
    if hook_id:
        matrix = load_hook_matrix(profiles_root, profile)
        in_matrix = hook_id in _matrix_by_id(matrix)
        if not in_matrix and hook_id.startswith("henry-"):
            in_matrix = hook_id in _matrix_by_id(_load_person_hook_matrix())
        if not in_matrix:
            result["warnings"].append(f"hook_id {hook_id!r} not found in any hook matrix")

    return result
