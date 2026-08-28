"""Deterministic local quality gates for content items.

Performs file/knowledge checks before generation spend (``pre_check``), on a written script
before storyboard/render spend (``script_check``), and before Gate 2 publish
(``post_check``). All checks are local file reads — no external I/O, no secrets.

Skills invoke this via CLI because they are markdown and cannot import Python::

    uv run python -m gtm_core.content_quality pre    --profile <p> --item <id>
    uv run python -m gtm_core.content_quality script --profile <p> --item <id>
    uv run python -m gtm_core.content_quality post   --profile <p> --item <id>

Returns JSON::

    {"proceed": true, "blocking": [], "warnings": [], "checks": {...}}
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess  # nosec B404 — only to shell out to the approved linter CLI
import sys
from pathlib import Path
from typing import Any

from .brandkit import load_brand_kit
from .ledgers import Ledgers
from .paths import (
    PathConfig,
    _safe_segment,
    resolve_content_root,
    resolve_knowledge_file,
    resolve_profiles_root,
)
from .render_manifest import FinishManifest, load_finish
from .video_lint import ERROR, SAFE_AREAS, evaluate
from .video_lint import probe as video_probe

_BLOCK = "block"
_WARN = "warn"

#: Video/reel formats that may trigger synthetic-identity disclosure requirements.
_VIDEO_FORMATS = frozenset({"reel", "clip"})

#: FinishManifest.ratio is stored filename-safe ("9x16", matching gtm_core.video_finish's
#: _ratio_slug and the finish-<ratio>.json filename convention), but SAFE_AREAS and
#: video_lint.evaluate() key on the colon form ("9:16"). Reverse-lookup built from SAFE_AREAS
#: itself rather than a blind ":"<->"x" string replace, so it only remaps ratios that are
#: actually known. Fixed 2026-08-17 — this silently no-op'd the caption-safe-area check
#: (checks["captions_inside_safe_area"] stayed None forever, read as "nothing to report" rather
#: than "never checked") and crashed the V1-V4 video-lint call outright, for every video item,
#: once the two upstream manifest-discovery bugs stopped masking it.
_RATIO_SLUG_TO_COLON = {ratio.replace(":", "x"): ratio for ratio in SAFE_AREAS}

#: LinkedIn/X/Instagram/Facebook optimization playbooks.
_PLATFORM_PLAYBOOKS = {
    "linkedin": "docs/linkedin-optimization.md",
    "x": "docs/x-optimization.md",
    "instagram": "docs/instagram-optimization.md",
    "facebook": "docs/facebook-optimization.md",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _find_item(content_root: Path, profile: str, item_id: str) -> dict | None:
    """Search ``content/<profile>/plans/`` and ``.pending/`` for a ContentItem by id."""
    plans_dir = content_root / _safe_segment(profile, "profile") / "plans"
    for search_dir in (plans_dir, plans_dir / ".pending"):
        if not search_dir.is_dir():
            continue
        for path in search_dir.glob("*.json"):
            data = _load_json(path)
            if not isinstance(data, list):
                continue
            for item in data:
                if isinstance(item, dict) and item.get("id") == item_id:
                    return item
    return None


def _parse_profile_md(text: str) -> dict[str, Any]:
    """Parse `` ``` `` fenced ``key: value`` blocks from PROFILE.md into a dict.

    Each fence is treated as a YAML-ish mapping. Lists/inline objects are parsed by ``yaml``
    when available; otherwise we fall back to a tolerant key:value line parser that handles
    the simple scalar/list cases used in this module.
    """
    try:
        import yaml  # type: ignore
    except ImportError:  # pragma: no cover
        yaml = None  # type: ignore

    out: dict[str, Any] = {}
    fence_re = re.compile(r"^```\s*$", re.MULTILINE)
    for match in fence_re.finditer(text):
        start = match.end()
        end_match = fence_re.search(text, pos=start)
        if end_match is None:
            break
        block = text[start : end_match.start()]
        if yaml is not None:
            try:
                parsed = yaml.safe_load(block)
                if isinstance(parsed, dict):
                    out.update(parsed)
                    continue
            except yaml.YAMLError:
                pass
        # Fallback: line-oriented key:value parser.
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                # Strip trailing comments.
                if " #" in value:
                    value = value[: value.index(" #")].strip()
                if value.startswith("[") and value.endswith("]"):
                    items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
                    out[key] = [i for i in items if i]
                else:
                    value = value.strip('"').strip("'")
                    try:
                        out[key] = int(value)
                    except ValueError:
                        try:
                            out[key] = float(value)
                        except ValueError:
                            out[key] = value
    return out


def load_profile_facts(profiles_root: Path, profile: str) -> dict[str, Any]:
    """Load tenant facts relevant to quality checks.

    Reads PROFILE.md, voice.md, social-tuning.md, audience-psychology.md,
    content-priority.md, icp-personas.md, company.md, product.md, the default product's
    PRODUCT.md, platform optimization playbooks, voice-bans.txt, and the merged BRAND.toml.
    No secrets, no external calls.
    """
    facts: dict[str, Any] = {"profile": profile}
    profile_dir = profiles_root / _safe_segment(profile, "profile")

    profile_md = _load_text(profile_dir / "PROFILE.md")
    if profile_md is not None:
        parsed = _parse_profile_md(profile_md)
        facts["content_pillars"] = parsed.get("content_pillars", [])
        facts["monthly_tool_budget_usd"] = parsed.get("monthly_tool_budget_usd")
        facts["products"] = parsed.get("products", [])
        facts["default_product"] = parsed.get("default_product")
        facts["brand_name"] = parsed.get("brand_name")

    for name in (
        "voice.md",
        "social-tuning.md",
        "audience-psychology.md",
        "content-priority.md",
        "icp-personas.md",
        "company.md",
        "product.md",
        "voice-bans.txt",
    ):
        path = profile_dir / "knowledge" / name
        text = _load_text(path)
        if text is not None:
            facts[name.removesuffix(".md").removesuffix(".txt")] = text

    default_product = facts.get("default_product")
    product_md_path = resolve_knowledge_file(
        profiles_root, profile, "PRODUCT.md", product=default_product
    )
    product_md = _load_text(product_md_path)
    if product_md is not None:
        facts["product"] = product_md

    playbooks: dict[str, str] = {}
    for platform, rel in _PLATFORM_PLAYBOOKS.items():
        playbook_path = profile_dir / "knowledge" / rel
        text = _load_text(playbook_path)
        if text is not None:
            playbooks[platform] = text
    facts["platform_playbooks"] = playbooks

    try:
        facts["brand_kit"] = load_brand_kit(profiles_root, profile, default_product)
    except Exception:
        facts["brand_kit"] = {}

    return facts


def _product_slugs(profiles_root: Path, profile: str) -> list[str]:
    """Return product slugs declared in PROFILE.md, or an empty list."""
    profile_dir = profiles_root / _safe_segment(profile, "profile")
    profile_md = _load_text(profile_dir / "PROFILE.md")
    if profile_md is None:
        return []
    parsed = _parse_profile_md(profile_md)
    products = parsed.get("products", [])
    slugs: list[str] = []
    for p in products:
        if isinstance(p, dict):
            slug = p.get("slug")
            if slug:
                slugs.append(str(slug))
        elif isinstance(p, str):
            slugs.append(p)
    return slugs


def load_hook_matrix(
    profiles_root: Path, profile: str, product: str | None = None
) -> list[dict[str, str]]:
    """Parse ``hook-matrix.md`` tables into rows with ``id``, ``persona``, ``signal``,
    ``angle``, and ``product``.

    Resolution is product-first, profile-fallback (same as ``resolve_knowledge_file``). The
    product column is inferred from the section heading when missing.
    """
    path = resolve_knowledge_file(profiles_root, profile, "hook-matrix.md", product)
    text = _load_text(path)
    if text is None:
        return []

    rows: list[dict[str, str]] = []
    current_product = product or ""
    table_in_progress = False
    headers: list[str] = []

    product_slugs = _product_slugs(profiles_root, profile)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            heading = line.removeprefix("## ").strip().lower()
            # Infer product slug from the heading if it matches a declared product.
            current_product = ""
            for slug in product_slugs:
                if heading.startswith(slug.lower()):
                    current_product = slug
                    break
            table_in_progress = False
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("- :") for c in cells if c):
                # Separator line — table body follows.
                table_in_progress = True
                continue
            if not table_in_progress:
                # Header line.
                headers = [c.lower().replace(" ", "_") for c in cells]
                if "id" in headers:
                    table_in_progress = True
                continue
            if table_in_progress and cells:
                row = dict(zip(headers, cells, strict=False))
                row_id = row.get("id", "").strip()
                if not row_id or row_id.lower() == "id":
                    continue
                rows.append(
                    {
                        "id": row_id,
                        "persona": row.get("persona", "").strip(),
                        "signal": row.get("signal_to_open_on", row.get("signal", "")).strip(),
                        "angle": row.get("hook_angle", row.get("angle", "")).strip(),
                        "product": current_product,
                    }
                )
        else:
            table_in_progress = False

    return rows


def _person_hook_matrix_path() -> Path | None:
    """Return ``identity/henry/hook-matrix.md`` if it exists, else None."""
    path = _repo_root() / "identity" / "henry" / "hook-matrix.md"
    return path if path.is_file() else None


def _load_person_hook_matrix() -> list[dict[str, str]]:
    path = _person_hook_matrix_path()
    if path is None:
        return []
    text = _load_text(path)
    if text is None:
        return []

    rows: list[dict[str, str]] = []
    table_in_progress = False
    headers: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("- :") for c in cells if c):
                table_in_progress = True
                continue
            if not table_in_progress:
                headers = [c.lower().replace(" ", "_") for c in cells]
                if "id" in headers:
                    table_in_progress = True
                continue
            if table_in_progress and cells:
                row = dict(zip(headers, cells, strict=False))
                row_id = row.get("id", "").strip()
                if not row_id or row_id.lower() == "id":
                    continue
                rows.append(
                    {
                        "id": row_id,
                        "persona": row.get("persona", "").strip(),
                        "signal": row.get("signal_to_open_on", row.get("signal", "")).strip(),
                        "angle": row.get("hook_angle", row.get("angle", "")).strip(),
                        "product": "henry",
                    }
                )
        else:
            table_in_progress = False
    return rows


def hook_id_to_product(hook_id: str, profile_products: list[dict] | None = None) -> str | None:
    """Map a hook id prefix to a product slug. Unknown prefix returns ``None``."""
    prefix = hook_id.split("-", 1)[0].lower()
    if prefix == "henry":
        return "henry"
    if profile_products:
        for p in profile_products:
            slug = p.get("slug") if isinstance(p, dict) else None
            if slug and hook_id.lower().startswith(f"{slug}-"):
                return slug
    return None


def _matrix_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["id"]: row for row in rows}


def _month_budget_remaining(ledgers: Ledgers, budget: float | None) -> tuple[bool, float]:
    """Return (under_or_at_budget, spent_usd). ``budget`` ``None`` means no cap."""
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    spent = ledgers.month_cost_total(month)
    if budget is None:
        return True, spent
    return spent < budget or abs(spent - budget) < 1e-9, spent


def _run_linter(asset_path: Path, ban_file: Path | None = None) -> tuple[bool, list[str]]:
    """Run ``tests/linter/content_linter.py`` on an asset. Return (pass, messages)."""
    linter = _repo_root() / "tests" / "linter" / "content_linter.py"
    cmd = [sys.executable, str(linter), str(asset_path)]
    if ban_file is not None:
        cmd.extend(["--ban-file", str(ban_file)])
    try:
        result = subprocess.run(  # nosec B603 — fixed path + item path only
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, [f"linter could not run: {exc}"]
    ok = result.returncode == 0
    messages = [result.stdout.strip(), result.stderr.strip()]
    return ok, [m for m in messages if m]


def _safe_to_share(text: str) -> tuple[bool, list[str]]:
    linter = _repo_root() / "tests" / "linter" / "content_linter.py"
    try:
        result = subprocess.run(  # nosec B603 — fixed path + text only
            [sys.executable, str(linter), "--safe-to-share", text],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, [f"safe-to-share check could not run: {exc}"]
    ok = result.returncode == 0
    messages = [result.stdout.strip(), result.stderr.strip()]
    return ok, [m for m in messages if m]


def _asset_text(asset: dict) -> str:
    """Assemble a searchable text blob from an asset dict (mirrors content_linter._asset_text)."""
    parts: list[str] = []
    for key in ("hook", "body", "caption"):
        value = asset.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("tweets", "slides", "key_points"):
        value = asset.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value if isinstance(v, str))
    return "\n".join(parts)


def _voice_bans_violated(text: str, bans: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for ban in bans:
        pattern = re.compile(rf"\b{re.escape(ban)}\b", re.IGNORECASE)
        if pattern.search(text):
            violations.append(f"voice ban violated: {ban!r}")
    return violations


def _load_bans(path: Path | None) -> tuple[str, ...]:
    if path is None or not path.is_file():
        return ()
    out: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    except OSError:
        pass
    return tuple(out)


def _resolve_ban_file(facts: dict) -> Path | None:
    bans = facts.get("voice-bans")
    if not bans:
        return None
    profile_dir = (
        resolve_profiles_root() / _safe_segment(facts.get("profile", ""), "profile") / "knowledge"
    )
    path = profile_dir / "voice-bans.txt"
    return path if path.is_file() else None


def pre_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Pre-generation quality check for a planned ContentItem.

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
    matrix = load_hook_matrix(profiles_root, profile)
    matrix_by_id = _matrix_by_id(matrix)

    hook_id = item.get("hook_id")
    if hook_id:
        if hook_id in matrix_by_id:
            checks["hook_id_in_matrix"] = True
        elif hook_id.startswith("henry-"):
            person_rows = _load_person_hook_matrix()
            checks["hook_id_in_matrix"] = hook_id in _matrix_by_id(person_rows)
            if not checks["hook_id_in_matrix"]:
                warnings.append(f"hook_id {hook_id!r} not found in person-layer hook matrix")
        else:
            checks["hook_id_in_matrix"] = False
            warnings.append(f"hook_id {hook_id!r} not found in hook-matrix.md")
    else:
        checks["hook_id_in_matrix"] = None
        warnings.append("no hook_id assigned — attribution to hook-matrix will be missing")

    pillar = item.get("pillar")
    content_pillars = facts.get("content_pillars") or []
    checks["pillar_valid"] = isinstance(pillar, str) and pillar in content_pillars
    if not checks["pillar_valid"]:
        blocking.append(f"pillar {pillar!r} not in profile content_pillars: {content_pillars!r}")

    platform = item.get("platform")
    playbooks = facts.get("platform_playbooks") or {}
    checks["platform_playbook_present"] = platform in playbooks
    if not checks["platform_playbook_present"]:
        warnings.append(f"no platform playbook found for {platform!r}")

    audience = ((item.get("brief") or {}).get("audience") or "").strip()
    personas_text = facts.get("icp-personas") or ""
    checks["audience_persona_named"] = bool(audience) and bool(
        personas_text and re.search(rf"\b{re.escape(audience)}\b", personas_text, re.IGNORECASE)
    )
    if not checks["audience_persona_named"]:
        warnings.append("brief.audience is not a named persona from icp-personas.md")

    ban_file = _resolve_ban_file(facts)
    checks["voice_bans_present"] = ban_file is not None and ban_file.is_file()
    if not checks["voice_bans_present"]:
        warnings.append("voice-bans.txt not present")

    fmt = item.get("format")
    brand_kit = facts.get("brand_kit") or {}
    disclosure_line = (
        brand_kit.get("disclosure", {}).get("line") if isinstance(brand_kit, dict) else None
    )
    is_video = fmt in _VIDEO_FORMATS
    checks["video_disclosure_configured"] = disclosure_line is not None if is_video else None
    if is_video and not disclosure_line:
        blocking.append(
            "video/reel format requires a configured disclosure.line in merged BRAND.toml"
        )

    budget = facts.get("monthly_tool_budget_usd")
    cfg = PathConfig(
        content_root=content_root,
        profiles_root=profiles_root,
        default_profile=profile,
    )
    ledgers = Ledgers(cfg, profile)
    under_budget, spent = _month_budget_remaining(ledgers, budget)
    checks["budget_ok"] = under_budget
    if not under_budget:
        blocking.append(f"monthly tool budget exceeded: ${spent:.2f} spent vs ${budget} cap")

    proceed = not blocking
    return {
        "proceed": proceed,
        "blocking": blocking,
        "warnings": warnings,
        "checks": checks,
    }


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


#: Front-block keys a video script must carry before it may reach a paid stage. Each was added
#: after a specific defect shipped; ``claims_verified`` is the newest (2026-08-19) and exists
#: because a script asserted an incident mechanism it had only ever read in an internal harvest
#: file's paraphrase, never in the primary source. Four factual defects reached the draft.
_SCRIPT_REQUIRED_FRONT_KEYS = ("source_item", "claims_verified")

#: ``claims_verified: <verified>/<total> · log: <pointer|none: reason>``. The separator is a
#: middot in authored files; tolerate a plain ASCII pipe or semicolon too rather than failing a
#: script on punctuation.
_CLAIMS_RE = re.compile(
    r"^\s*(?P<verified>\d+)\s*/\s*(?P<total>\d+)\s*(?:[·|;]\s*log\s*:\s*(?P<log>.+?))?\s*$"
)


def _parse_front_block(text: str) -> dict[str, str]:
    """Parse the leading ``key: value`` lines of a script file.

    The block ends at the first blank line or the first markdown heading, whichever comes
    first — scripts written by ``video-script`` put the join-key block above the ``# Title``
    line with no fence around it, so there is no delimiter to key on.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key and " " not in key:
            out[key] = value.strip()
    return out


def _find_script(content_root: Path, profile: str, item_id: str) -> Path | None:
    """Locate the script whose front block names ``item_id`` as its ``source_item``.

    Matches on the ``source_item`` field rather than a filename convention: the filename is
    ``<date>-<slug>.md`` and carries no item id, so globbing for the id finds nothing. Same
    class of bug as the ``plan_id``/``source_item`` mismatch fixed in
    :func:`_find_video_manifests` on 2026-08-17.
    """
    scripts_dir = content_root / _safe_segment(profile, "profile") / "scripts"
    if not scripts_dir.is_dir():
        return None
    for path in sorted(scripts_dir.glob("*.md")):
        text = _load_text(path)
        if text is None:
            continue
        if _parse_front_block(text).get("source_item") == item_id:
            return path
    return None


#: Words a person actually starts a spoken sentence with. Written prose drops these because a
#: reader can see the paragraph break; a listener cannot, so a script with none of them reads as a
#: string of disconnected aphorisms rather than someone talking.
_CONNECTIVES = frozenset(
    {
        "and",
        "so",
        "but",
        "because",
        "look",
        "now",
        "then",
        "which",
        "or",
        "anyway",
        "meanwhile",
        "except",
        "plus",
        "still",
        "yet",
        "though",
    }
)

#: Sentence openers whose repetition is ordinary speech rather than a rhetorical mirror. A person
#: really does say "It found a way. It cancelled someone else's spot." — that is not the tell.
_NATURAL_REPEAT_OPENERS = frozenset(
    {"it", "i", "you", "we", "they", "he", "she", "there", "and", "so", "but", "then"}
)

#: Minimum share of spoken sentences (after the cold open) that should open with a connective.
_MIN_CONNECTIVE_SHARE = 0.25

#: Sentence-length standard deviation, in words, below which the script reads as metronomic —
#: every line the same size, which is how a list of slogans sounds.
_MIN_SENTENCE_LEN_STDEV = 2.5

#: Below this many spoken sentences the statistical tells are noise, not signal.
_MIN_SENTENCES_FOR_REGISTER = 5

_SPOKEN_RE = re.compile(r"\*\*\[SPOKEN\]\*\*\s*[\"“](.+?)[\"”]", re.DOTALL)


def extract_spoken(text: str) -> list[str]:
    """Every ``[SPOKEN]`` line in a script, in order, quotes stripped."""
    return [re.sub(r"\s+", " ", m.group(1)).strip() for m in _SPOKEN_RE.finditer(text)]


def _sentences(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        for part in re.split(r"(?<=[.!?])\s+", line):
            part = part.strip()
            if part:
                out.append(part)
    return out


def register_findings(spoken_lines: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Countable tells that a spoken script was written rather than spoken.

    Mechanises three of the four tells in ``video-script``'s Register section. Advisory by design
    — register is a judgement and a linter that blocked on it would be wrong more often than the
    writer. It exists so the failure is *visible* rather than silent, because the 2026-08-18
    script cleared every existing gate (Part A 11/14, claims 8/8) and still landed as
    "too polished, doesn't feel authentic".

    The fourth tell — slogan endings — is deliberately not mechanised: no counter distinguishes a
    tagline from a plain strong closing line, and a bad proxy would push writers toward weak
    endings to satisfy it.
    """
    sentences = _sentences(spoken_lines)
    stats: dict[str, Any] = {"sentences": len(sentences)}
    if len(sentences) < _MIN_SENTENCES_FOR_REGISTER:
        stats["skipped"] = "too few sentences for the statistical tells to mean anything"
        return [], stats

    findings: list[str] = []

    # 1. Parallel construction — consecutive sentences opening on the same word.
    #
    # Matched on the FIRST word, not the first two: the canonical tell is
    # "That's not a gym problem. / That's every system your AI touches." — same opener, mirrored
    # shape, second word deliberately different. A two-word rule misses exactly the case this
    # check exists for.
    #
    # Personal pronouns are excluded because repeating them is ordinary speech, not a cadence
    # ("It found a way. It cancelled someone else's spot." is fine). Demonstratives are what turn
    # a repetition into an advertisement.
    mirrors: list[str] = []
    for a, b in zip(sentences, sentences[1:], strict=False):
        wa = re.findall(r"[a-z']+", a.lower())[:1]
        wb = re.findall(r"[a-z']+", b.lower())[:1]
        if wa and wa == wb and wa[0] not in _NATURAL_REPEAT_OPENERS:
            mirrors.append(f"{a!r} / {b!r}")
    stats["parallel_openings"] = len(mirrors)
    if mirrors:
        findings.append(
            f"{len(mirrors)} parallel construction(s) — consecutive sentences opening on the same "
            f"word, which is a copywriter's cadence a listener hears as an ad: {mirrors[0]}. "
            "Break the mirror and use the connective a person would actually say."
        )

    # 2. Connective tissue — the cold open is exempt, it earns its abruptness.
    body = sentences[1:]
    with_conn = sum(
        1 for s in body if (re.findall(r"[a-z']+", s.lower()) or [""])[0] in _CONNECTIVES
    )
    share = with_conn / len(body) if body else 0.0
    stats["connective_share"] = round(share, 3)
    if share < _MIN_CONNECTIVE_SHARE:
        findings.append(
            f"only {with_conn}/{len(body)} sentences after the cold open ({share:.0%}) open with a "
            f"connective (and/so/but/look/…), under the {_MIN_CONNECTIVE_SHARE:.0%} floor — "
            "written prose drops these because a reader sees the paragraph break; a listener "
            "cannot, so this reads as disconnected aphorisms"
        )

    # 3. Metronomic length.
    lengths = [len(s.split()) for s in sentences]
    stdev = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    stats["sentence_len_mean"] = round(statistics.fmean(lengths), 2)
    stats["sentence_len_stdev"] = round(stdev, 2)
    if stdev < _MIN_SENTENCE_LEN_STDEV:
        findings.append(
            f"sentence length is metronomic (mean {statistics.fmean(lengths):.1f} words, stdev "
            f"{stdev:.2f} < {_MIN_SENTENCE_LEN_STDEV}) — real speech varies hard; a run of "
            "same-length lines sounds recited"
        )

    return findings, stats


def register_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Advisory register report for a script's spoken lines. Never blocks — see
    :func:`register_findings`. ``proceed`` is always True unless the script cannot be found."""
    content_root = content_root or resolve_content_root()
    script_path = _find_script(content_root, profile, item_id)
    if script_path is None:
        return {
            "proceed": False,
            "blocking": [f"no script in content/{profile}/scripts/ carries source_item: {item_id}"],
            "warnings": [],
            "checks": {},
        }

    spoken = extract_spoken(_load_text(script_path) or "")
    if not spoken:
        return {
            "proceed": True,
            "blocking": [],
            "warnings": [f"{script_path.name}: no [SPOKEN] lines found — nothing to read aloud"],
            "checks": {"script": script_path.name, "spoken_lines": 0},
        }

    findings, stats = register_findings(spoken)
    return {
        "proceed": True,
        "blocking": [],
        "warnings": findings,
        "checks": {"script": script_path.name, "spoken_lines": len(spoken), **stats},
    }


def script_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Gate a written script before it reaches storyboard/render spend.

    Enforces the front-block contract ``video-script`` Step 2 writes, in particular
    ``claims_verified`` — the field that records whether every external claim reaching a viewer
    as VO or caption was checked against a source. The count being *present* is what this can
    verify mechanically; whether the log is honest is a human/model judgement. That is the same
    division of labour as ``visual_template``: making the field mandatory is the lever, because
    the failure being prevented is silence, not a wrong number.

    Returns ``{"proceed": bool, "blocking": [...], "warnings": [...], "checks": {...}}``.
    """
    content_root = content_root or resolve_content_root()
    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}

    item = _find_item(content_root, profile, item_id)
    if item is None:
        blocking.append(f"ContentItem {item_id!r} not found in content/{profile}/plans/")
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    script_path = _find_script(content_root, profile, item_id)
    checks["script_found"] = script_path is not None
    if script_path is None:
        blocking.append(
            f"no script in content/{profile}/scripts/ carries source_item: {item_id} "
            "(video-script Step 2 writes this front-block field)"
        )
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    text = _load_text(script_path) or ""
    front = _parse_front_block(text)

    missing = [k for k in _SCRIPT_REQUIRED_FRONT_KEYS if k not in front]
    checks["front_block_complete"] = not missing
    for key in missing:
        blocking.append(f"{script_path.name}: front block missing required key {key!r}")

    raw_claims = front.get("claims_verified")
    if raw_claims is None:
        checks["claims_verified_present"] = False
        checks["claims_verified_parsed"] = None
        checks["verification_log_present"] = None
        return {
            "proceed": False,
            "blocking": blocking,
            "warnings": warnings,
            "checks": checks,
        }

    checks["claims_verified_present"] = True
    match = _CLAIMS_RE.match(raw_claims)
    if match is None:
        checks["claims_verified_parsed"] = False
        checks["verification_log_present"] = None
        blocking.append(
            f"{script_path.name}: claims_verified {raw_claims!r} is not "
            "'<verified>/<total> · log: <pointer|none: reason>'"
        )
        return {
            "proceed": False,
            "blocking": blocking,
            "warnings": warnings,
            "checks": checks,
        }

    checks["claims_verified_parsed"] = True
    verified = int(match.group("verified"))
    total = int(match.group("total"))
    log_note = (match.group("log") or "").strip()

    if verified > total:
        blocking.append(
            f"{script_path.name}: claims_verified {verified}/{total} verifies more claims "
            "than the script makes"
        )

    has_log_section = re.search(r"^##+\s+Verification log\s*$", text, re.MULTILINE) is not None
    checks["verification_log_present"] = has_log_section

    if total > 0:
        if not has_log_section:
            blocking.append(
                f"{script_path.name}: claims_verified says {total} external claim(s) but the "
                "script has no '## Verification log' section recording source and quote per claim"
            )
        if verified < total:
            # Not a hard block by design: shipping a marked-unverified claim is a legitimate,
            # audited choice. Silence about it is not.
            warnings.append(
                f"{script_path.name}: {total - verified} of {total} claim(s) unverified — each "
                "must be cut or explicitly marked unverified in the script, and named in the "
                "handoff report"
            )
    elif not log_note.lower().startswith("none"):
        # 0/0 is legitimate but rare; it must say why rather than leaving the reader to guess
        # whether verification was skipped or genuinely not applicable.
        warnings.append(
            f"{script_path.name}: claims_verified 0/0 should record why "
            "(e.g. 'log: none: no external claims')"
        )

    proceed = not blocking
    return {"proceed": proceed, "blocking": blocking, "warnings": warnings, "checks": checks}


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.content_quality",
        description="Deterministic local quality gates for content items.",
    )
    parser.add_argument("--content-root", default=None, help="override content root")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--profile", required=True, help="active profile slug")
    parent.add_argument("--item", required=True, help="ContentItem id")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pre", parents=[parent], help="pre-generation checks")
    sub.add_parser("post", parents=[parent], help="post-generation checks")
    sub.add_parser(
        "script",
        parents=[parent],
        help="script front-block checks (claims_verified) before storyboard/render spend",
    )
    sub.add_parser(
        "register",
        parents=[parent],
        help="advisory: countable tells that a spoken script was written, not spoken",
    )
    args = parser.parse_args(argv)

    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )

    if args.cmd == "pre":
        result = pre_check(args.profile, args.item, content_root=content_root)
    elif args.cmd == "script":
        result = script_check(args.profile, args.item, content_root=content_root)
    elif args.cmd == "register":
        result = register_check(args.profile, args.item, content_root=content_root)
    else:
        result = post_check(args.profile, args.item, content_root=content_root)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["proceed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
