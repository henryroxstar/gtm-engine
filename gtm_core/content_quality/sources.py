from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..brandkit import load_brand_kit
from ..paths import _safe_segment, resolve_knowledge_file
from .model import _PLATFORM_PLAYBOOKS


def _repo_root() -> Path:
    # parents[2], not [1]: this module sits one level deeper than the pre-split
    # gtm_core/content_quality.py it came from, so the same walk-up would stop at gtm_core/
    # and every repo-relative lookup below it (tests/linter/content_linter.py) would miss.
    return Path(__file__).resolve().parents[2]


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


def load_profile_facts(profiles_root: Path, profile: str, overlay: str = "") -> dict[str, Any]:
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
        ov = (overlay or None) if name == "icp-personas.md" else None  # only overlayable name
        text = _load_text(resolve_knowledge_file(profiles_root, profile, name, overlay=ov))
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
    profiles_root: Path,
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
) -> list[dict[str, str]]:
    """Parse ``hook-matrix.md`` tables into rows with ``id``, ``persona``, ``signal``,
    ``angle``, and ``product``.

    Resolution is product-first, profile-fallback (same as ``resolve_knowledge_file``). The
    product column is inferred from the section heading when missing.
    """
    path = resolve_knowledge_file(profiles_root, profile, "hook-matrix.md", product, overlay)
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
