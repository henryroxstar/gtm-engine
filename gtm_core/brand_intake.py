"""Deterministic Design System Intake parser, exporter, stager, differ, and atomic promoter.

Synchronizes ``knowledge/BRAND.toml``, ``products/*/BRAND.toml``, ``knowledge/brand/tokens.css``,
``knowledge/brand/brand-generation-source.md``, and ``knowledge/brand-notes.md`` through
``gtm_core.knowledge_staging`` (no new ``profiles/`` writer), gated by ``check_brand_consistency``.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from . import knowledge_staging as ks
from .brandkit import check_brand_consistency
from .brandkit_css import render_tokens_css
from .paths import _safe_segment, resolve_content_root, resolve_profiles_root

_TOML_FENCE_RE = re.compile(r"```toml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_COMPANY_SECTION_ORDER: tuple[str, ...] = (
    "meta",
    "design_system",
    "palette",
    "typography",
    "assets",
    "layout",
    "imagery",
    "motion",
    "voice",
    "identity",
)


def parse_intake_spec(text: str) -> dict[str, Any]:
    """Parse a Design System Intake spec from raw TOML or Markdown with a ```toml block."""
    raw = text.strip()
    if not raw:
        raise ValueError("Design system intake spec is empty.")
    fence_match = _TOML_FENCE_RE.search(raw)
    toml_str = fence_match.group(1) if fence_match else raw
    try:
        parsed = tomllib.loads(toml_str)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in design system intake spec: {exc}") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("Design system intake spec parsed to an empty table.")
    return parsed


def _toml_format_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int | float):
        return str(val)
    if isinstance(val, str):
        escaped = (
            val.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(val, list):
        return "[" + ", ".join(_toml_format_value(item) for item in val) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(val)!r}")


def _emit_toml_table(prefix: str, tbl: dict[str, Any], lines: list[str]) -> None:
    """Recursively emit a TOML table and any nested sub-tables."""
    scalar_items = [(k, v) for k, v in tbl.items() if v is not None and not isinstance(v, dict)]
    sub_tables = [(k, v) for k, v in tbl.items() if isinstance(v, dict)]
    if scalar_items or not sub_tables:
        if lines:
            lines.append("")
        lines.append(f"[{prefix}]")
        for k, v in scalar_items:
            lines.append(f"{k} = {_toml_format_value(v)}")
    for sub_k, sub_v in sub_tables:
        _emit_toml_table(f"{prefix}.{sub_k}", sub_v, lines)


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    merged = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def serialize_toml(
    data: dict[str, Any], *, section_order: tuple[str, ...] = _COMPANY_SECTION_ORDER
) -> str:
    """Deterministically serialize nested dicts (plus nested product tables) to valid TOML."""
    lines: list[str] = []
    ordered_keys = [k for k in section_order if k in data] + [
        k for k in data if k not in section_order and k != "products"
    ]
    for sec in ordered_keys:
        sec_val = data.get(sec)
        if isinstance(sec_val, dict):
            _emit_toml_table(sec, sec_val, lines)

    products = data.get("products")
    if isinstance(products, dict):
        for prod_slug in sorted(products):
            prod_data = products[prod_slug]
            if isinstance(prod_data, dict):
                for sub_sec, sub_val in prod_data.items():
                    if isinstance(sub_val, dict):
                        _emit_toml_table(f"products.{prod_slug}.{sub_sec}", sub_val, lines)
    return "\n".join(lines).strip() + "\n"


def export_profile_spec(profiles_root: Path, profile: str) -> str:
    """Export the live profile's design system + product overrides as a canonical TOML intake spec."""
    _safe_segment(profile, "profile")
    brand_path = profiles_root / profile / "knowledge" / "BRAND.toml"
    if not brand_path.is_file():
        raise FileNotFoundError(f"Missing live BRAND.toml at {brand_path}")
    company_kit = tomllib.loads(brand_path.read_text(encoding="utf-8"))

    export_dict: dict[str, Any] = {}
    ordered_keys = [k for k in _COMPANY_SECTION_ORDER if k in company_kit and k != "identity"] + [
        k
        for k in company_kit
        if k not in _COMPANY_SECTION_ORDER and k not in ("identity", "products")
    ]
    for sec in ordered_keys:
        if isinstance(company_kit.get(sec), dict):
            export_dict[sec] = copy.deepcopy(company_kit[sec])

    prod_dir = profiles_root / profile / "products"
    products_map: dict[str, Any] = {}
    if prod_dir.is_dir():
        for child in sorted(prod_dir.iterdir()):
            p_toml = child / "BRAND.toml"
            if child.is_dir() and not child.name.startswith(".") and p_toml.is_file():
                products_map[child.name] = tomllib.loads(p_toml.read_text(encoding="utf-8"))
    if products_map:
        export_dict["products"] = products_map
    return serialize_toml(export_dict)


def _render_brand_generation_source_md(company_kit: dict[str, Any], *, today_iso: str) -> str:
    ds = company_kit.get("design_system", {})
    pal = company_kit.get("palette", {})
    typo = company_kit.get("typography", {})
    layout = company_kit.get("layout", {})
    motion = company_kit.get("motion", {})
    grad = ", ".join(f"`{c}`" for c in pal.get("gradient", []))
    return (
        f"---\nsource: brand-intake\nrefreshed: {today_iso}\nreview: 180d\n---\n"
        f"# {ds.get('name', 'Design System')} (v{ds.get('version', '1.0')}) — Canonical Generation Source\n\n"
        f"## 1. Design System Policy (`[design_system]`)\n\n"
        f"- **Archetype**: `{ds.get('archetype', 'custom')}`\n"
        f"- **Surface Ratio Rule**: `{ds.get('surface_ratio_rule', '90/10')}`\n"
        f"- **Panel Style**: `{ds.get('panel_style', 'glass-hairline')}`\n"
        f"- **Button Fill Policy**: `{ds.get('button_fill_policy', 'solid_only')}`\n"
        f"- **Illustration Dimension Policy**: `{ds.get('illustration_dimension_policy', '2d_only')}`\n"
        f"- **Background Plates in UI**: `{str(bool(ds.get('background_plates_in_ui', False))).lower()}`\n"
        f"- **Product Override Policy**: `{ds.get('product_override_policy', 'suite_gradient_and_logo_only')}`\n\n"
        f"## 2. Canonical Palette (`[palette]`)\n\n"
        f"| Token | Value |\n|---|---|\n"
        f"| `canvas` | `{pal.get('canvas', '')}` |\n"
        f"| `surface` | `{pal.get('surface', '')}` |\n"
        f"| `elevated` | `{pal.get('elevated', '')}` |\n"
        f"| `primary` | `{pal.get('primary', '')}` |\n"
        f"| `accent` | `{pal.get('accent', '')}` |\n"
        f"| `ink` | `{pal.get('ink', '')}` |\n"
        f"| `muted` | `{pal.get('muted', '')}` |\n"
        f"| `subtle` | `{pal.get('subtle', '')}` |\n"
        f"| `rule` | `{pal.get('rule', '')}` |\n"
        f"| `positive` | `{pal.get('positive', '')}` |\n"
        f"| `warning` | `{pal.get('warning', '')}` |\n"
        f"| `danger` | `{pal.get('danger', '')}` |\n"
        f"| `gradient` | {grad} |\n\n"
        f"## 3. Canonical Typography (`[typography]`)\n\n"
        f"- **Display**: `{typo.get('display', '')}`\n"
        f"- **Body**: `{typo.get('body', '')}`\n"
        f"- **Mono**: `{typo.get('mono', '')}`\n\n"
        f"## 4. Layout & Motion (`[layout]`, `[motion]`)\n\n"
        f"- **Grid**: `{layout.get('grid', '')}`\n"
        f"- **Safe Zone**: `{layout.get('safe_zone', '')}`\n"
        f"- **Pacing**: `{motion.get('pacing', '')}`\n"
        f"- **Glow on Focal**: `{str(bool(motion.get('glow_on_focal', False))).lower()}`\n"
    )


def _render_or_update_brand_notes_md(
    existing_text: str, company_kit: dict[str, Any], *, today_iso: str
) -> str:
    ds = company_kit.get("design_system", {})
    pal = company_kit.get("palette", {})
    typo = company_kit.get("typography", {})
    denied_fonts = [str(f).strip() for f in ds.get("denied_fonts", []) if str(f).strip()]
    denied_hexes = [str(h).strip() for h in ds.get("denied_hexes", []) if str(h).strip()]
    summary_block = (
        f"## Design System Summary ({ds.get('name', 'Design System')} v{ds.get('version', '1.0')})\n\n"
        f"- **Canvas / Surface / Elevated**: `{pal.get('canvas', '')}` / `{pal.get('surface', '')}` / `{pal.get('elevated', '')}`\n"
        f"- **Primary / Accent**: `{pal.get('primary', '')}` / `{pal.get('accent', '')}`\n"
        f"- **Ink / Muted / Rule**: `{pal.get('ink', '')}` / `{pal.get('muted', '')}` / `{pal.get('rule', '')}`\n"
        f"- **Typography (Display / Body / Mono)**: `{typo.get('display', '')}` / `{typo.get('body', '')}` / `{typo.get('mono', '')}`\n"
    )
    if not existing_text.strip():
        return (
            f"---\nsource: brand-intake\nrefreshed: {today_iso}\nreview: 180d\n---\n"
            f"# Brand Notes\n\n{summary_block}"
        )
    cleaned = existing_text
    for font in denied_fonts:
        cleaned = re.sub(
            rf"(?<![A-Za-z0-9_-]){re.escape(font)}(?![A-Za-z0-9_-])",
            str(typo.get("body") or typo.get("display") or "sans-serif"),
            cleaned,
            flags=re.IGNORECASE,
        )
    for hex_val in denied_hexes:
        cleaned = re.sub(
            rf"{re.escape(hex_val)}(?![0-9A-Fa-f])",
            str(pal.get("surface") or pal.get("canvas") or "#000000"),
            cleaned,
            flags=re.IGNORECASE,
        )
    marker = "## Design System Summary"
    if marker in cleaned:
        prefix, _, _ = cleaned.partition(marker)
        return prefix.rstrip() + "\n\n" + summary_block
    return cleaned.rstrip() + "\n\n" + summary_block


def stage_intake(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    spec_text: str,
    *,
    today: date | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Stage all design-system artifacts into ``content/<profile>/knowledge-staging/`` and validate."""
    _safe_segment(profile, "profile")
    today_d = today or datetime.now(UTC).date()
    today_iso = today_d.isoformat()
    spec = parse_intake_spec(spec_text)

    live_brand_path = profiles_root / profile / "knowledge" / "BRAND.toml"
    base_kit = (
        tomllib.loads(live_brand_path.read_text(encoding="utf-8"))
        if live_brand_path.is_file()
        else {}
    )
    merged_company_kit = copy.deepcopy(base_kit)
    for sec, sec_val in spec.items():
        if sec != "products" and isinstance(sec_val, dict):
            existing_sec = merged_company_kit.get(sec)
            merged_company_kit[sec] = (
                _deep_merge_dict(existing_sec, sec_val)
                if isinstance(existing_sec, dict)
                else copy.deepcopy(sec_val)
            )

    meta = merged_company_kit.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["updated"] = today_iso

    staged_topics: list[str] = ["BRAND.toml"]
    ks.stage(content_root, profile, "BRAND.toml", serialize_toml(merged_company_kit))

    spec_products = spec.get("products") if isinstance(spec.get("products"), dict) else {}
    for prod_slug, prod_spec in sorted(spec_products.items()):
        _safe_segment(prod_slug, "product")
        if not isinstance(prod_spec, dict):
            continue
        live_prod_path = profiles_root / profile / "products" / prod_slug / "BRAND.toml"
        prod_base = (
            tomllib.loads(live_prod_path.read_text(encoding="utf-8"))
            if live_prod_path.is_file()
            else {}
        )
        prod_merged: dict[str, Any] = {}
        if isinstance(prod_base.get("meta"), dict):
            prod_merged["meta"] = copy.deepcopy(prod_base["meta"])
        for psec, pval in prod_spec.items():
            if isinstance(pval, dict):
                prod_merged[psec] = copy.deepcopy(pval)
        prod_meta = prod_merged.setdefault("meta", {})
        if isinstance(prod_meta, dict):
            prod_meta.setdefault("profile", profile)
            prod_meta.setdefault("product", prod_slug)
            prod_meta["updated"] = today_iso
        prod_topic = f"products/{prod_slug}/BRAND.toml"
        ks.stage(content_root, profile, prod_topic, serialize_toml(prod_merged))
        staged_topics.append(prod_topic)

    ks.stage(content_root, profile, "brand/tokens.css", render_tokens_css(merged_company_kit))
    staged_topics.append("brand/tokens.css")

    gen_src_md = _render_brand_generation_source_md(merged_company_kit, today_iso=today_iso)
    ks.stage(content_root, profile, "brand/brand-generation-source", gen_src_md)
    staged_topics.append("brand/brand-generation-source")

    live_bn_path = profiles_root / profile / "knowledge" / "brand-notes.md"
    existing_bn = live_bn_path.read_text(encoding="utf-8") if live_bn_path.is_file() else ""
    bn_md = _render_or_update_brand_notes_md(existing_bn, merged_company_kit, today_iso=today_iso)
    ks.stage(content_root, profile, "brand-notes", bn_md)
    staged_topics.append("brand-notes")

    ok, errors = check_brand_consistency(
        profiles_root, profile, staged=True, content_root=content_root
    )
    return ok, staged_topics, errors


def diff_staged_intake(profiles_root: Path, content_root: Path, profile: str) -> str:
    """Return unified diffs across all staged topics for ``profile``."""
    _safe_segment(profile, "profile")
    chunks = [
        d
        for topic in ks.list_staged(content_root, profile)
        if (d := ks.diff(profiles_root, content_root, profile, topic))
    ]
    return "\n".join(chunks)


def _topic_order_key(t: str) -> tuple[int, str]:
    if t == "BRAND.toml":
        return (0, t)
    if t.startswith("products/"):
        return (1, t)
    if t == "brand/tokens.css":
        return (2, t)
    return (3, t)


def promote_staged_intake(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    *,
    today: date | None = None,
    source: str = "brand-intake",
) -> tuple[bool, list[Path], list[str]]:
    """Atomically promote all staged design-system topics with snapshot rollback on failure."""
    _safe_segment(profile, "profile")
    today_d = today or datetime.now(UTC).date()
    topics = ks.list_staged(content_root, profile)
    if not topics:
        return False, [], [f"No staged design-system candidates found for profile {profile!r}."]

    ok, pre_errors = check_brand_consistency(
        profiles_root, profile, staged=True, content_root=content_root
    )
    if not ok:
        return False, [], pre_errors

    ordered_topics = sorted(topics, key=_topic_order_key)
    pre_snapshots = {
        t: ks.snapshot_topic(profiles_root, content_root, profile, t) for t in ordered_topics
    }
    promoted_paths: list[Path] = []
    try:
        for topic in ordered_topics:
            dest = ks.promote(
                profiles_root,
                content_root,
                profile,
                topic,
                today=today_d,
                source=source,
                remove_staged=False,
            )
            promoted_paths.append(dest)
        post_ok, post_errors = check_brand_consistency(
            profiles_root, profile, staged=False, content_root=content_root
        )
        if not post_ok:
            raise RuntimeError("Post-promotion consistency check failed: " + "; ".join(post_errors))
    except Exception as exc:
        for topic in ordered_topics:
            snap = pre_snapshots.get(topic)
            if snap is not None and snap.is_file():
                ks.restore(profiles_root, content_root, profile, topic, snapshot_file=snap)
        return False, [], [str(exc)]

    for topic in ordered_topics:
        st_file = ks.staged_path(content_root, profile, topic)
        if st_file.is_file():
            st_file.unlink(missing_ok=True)
    return True, promoted_paths, []


def _cmd_stage(profiles_root: Path, content_root: Path, profile: str, spec_arg: str) -> int:
    spec_path = Path(spec_arg)
    if not spec_path.is_file():
        print(f"error: spec file not found: {spec_path}", file=sys.stderr)
        return 2
    try:
        ok, topics, errors = stage_intake(
            profiles_root, content_root, profile, spec_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"staged {len(topics)} topics for {profile}: {', '.join(topics)}")
    if not ok:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print(f"brandkit check (--staged): OK ({profile})")
    return 0


def _cmd_promote(profiles_root: Path, content_root: Path, profile: str, source: str) -> int:
    ok, promoted, errors = promote_staged_intake(
        profiles_root, content_root, profile, source=source
    )
    if not ok:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    for p in promoted:
        print(f"promoted: {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.brand_intake",
        description="Holistic Design System Intake exporter, stager, differ, and atomic promoter.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_export = sub.add_parser("export", help="Export live profile design system to TOML spec")
    p_export.add_argument("--profile", required=True, help="Profile slug")
    p_export.add_argument("--out", default=None, help="Output file path (stdout if omitted)")

    p_stage = sub.add_parser(
        "stage", help="Stage a design system intake spec and check consistency"
    )
    p_stage.add_argument("--profile", required=True, help="Profile slug")
    p_stage.add_argument("--spec", required=True, help="Path to intake .toml or .md spec file")

    p_diff = sub.add_parser("diff", help="Show unified diff of staged design system files")
    p_diff.add_argument("--profile", required=True, help="Profile slug")

    p_promote = sub.add_parser("promote", help="Atomically promote staged design system files")
    p_promote.add_argument("--profile", required=True, help="Profile slug")
    p_promote.add_argument("--source", default="brand-intake", help="Provenance source label")

    args = parser.parse_args(argv)
    profiles_root, content_root = resolve_profiles_root(), resolve_content_root()

    if args.cmd == "export":
        text = export_profile_spec(profiles_root, args.profile)
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            print(str(out_path))
        else:
            sys.stdout.write(text)
        return 0
    if args.cmd == "stage":
        return _cmd_stage(profiles_root, content_root, args.profile, args.spec)
    if args.cmd == "diff":
        d = diff_staged_intake(profiles_root, content_root, args.profile)
        sys.stdout.write(d) if d else print("(no differences)")
        return 0
    if args.cmd == "promote":
        return _cmd_promote(profiles_root, content_root, args.profile, args.source)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
