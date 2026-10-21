"""The craft rules are agnostic by design (PRD 2026-10-19 §1.1; test plan 3.A "Agnosticism").

1. Two synthetic profiles, built in a temp directory from ``profiles/_template`` with different
   palettes, product names, product types and design-system archetypes, give the SAME rule ids on
   the SAME cuts for every fixture. Only design-system findings may differ, and only when one kit
   denies a font the fixture names.
2. No tenant vocabulary in ``gtm_core/video_craft``. ``tests/lint/debrand_check.sh`` takes no path
   argument (only ``--release``), so this scans the package itself for every tenant slug under
   ``profiles/`` and every product folder name under ``profiles/*/products/``, derived at run time
   so no tenant name is written here. A negative control plants one in a temp copy.
3. Lane independence: presenter-only and filmed-only lists never get the static-share warning.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

pytest.importorskip("gtm_core.video_craft")

from gtm_core.brandkit import load_brand_kit  # noqa: E402
from gtm_core.shots_lint.result import lint_shotlist_detailed  # noqa: E402
from gtm_core.video_craft import hook  # noqa: E402
from gtm_core.video_craft.words import ONSCREEN_WORDS_WARN  # noqa: E402
from tests.unit import video_craft_fixtures as fx  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "profiles" / "_template"
PACKAGE = REPO / "gtm_core" / "video_craft"
CEILING = ONSCREEN_WORDS_WARN["16:9"]

#: Two fictional tenants (names from ``python -m gtm_core.fictionalize company``).
TENANTS = {
    "fernway": {
        "canvas": "#0B1D2A",
        "accent": "#3FA7D6",
        "display": "Fernway Grotesk",
        "archetype": "mission-control",
        "product": "fernway-ledger",
        "product_type": "a bookkeeping app for small studios",
    },
    "summitline": {
        "canvas": "#FAF7F0",
        "accent": "#C2410C",
        "display": "Summitline Serif",
        "archetype": "editorial-light",
        "product": "summitline-dispatch",
        "product_type": "a route planner for regional couriers",
    },
}


def _replace_line(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^({re.escape(key)}\s*=\s*)\"[^\"]*\"", re.MULTILINE)
    assert pattern.search(text), f"template BRAND.toml has no {key!r} line to vary"
    return pattern.sub(lambda m: f'{m.group(1)}"{value}"', text, count=1)


def _build_profile(root: Path, name: str, spec: dict) -> Path:
    dest = root / name
    shutil.copytree(TEMPLATE, dest)
    brand = dest / "knowledge" / "BRAND.toml"
    text = brand.read_text(encoding="utf-8")
    for key in ("canvas", "accent", "display", "archetype"):
        text = _replace_line(text, key, spec[key])
    brand.write_text(text, encoding="utf-8")
    sample = dest / "products" / "_sample"
    product = dest / "products" / spec["product"]
    sample.rename(product)
    (product / "PRODUCT.md").write_text(
        f"# Product: {spec['product']}\n\nname: {spec['product']}\n"
        f"description: {spec['product_type']}\n",
        encoding="utf-8",
    )
    return dest


@pytest.fixture(scope="module")
def kits(tmp_path_factory) -> dict[str, dict]:
    root = tmp_path_factory.mktemp("profiles")
    out = {}
    for name, spec in TENANTS.items():
        _build_profile(root, name, spec)
        out[name] = load_brand_kit(root, name)
    return out


def test_the_two_kits_really_differ(kits):
    """The instrument check: two identical kits would prove nothing."""
    a, b = kits["fernway"], kits["summitline"]
    assert a["palette"]["canvas"] != b["palette"]["canvas"]
    assert a["typography"]["display"] != b["typography"]["display"]
    assert a["design_system"]["archetype"] != b["design_system"]["archetype"]
    for kit in (a, b):
        assert isinstance(kit["design_system"], dict) and kit["design_system"]


def _fixtures() -> dict[str, dict]:
    good = fx.good(CEILING)
    no_class = fx.good(CEILING)
    del no_class["shots"][3]["asset"]
    unknown = fx.good(CEILING)
    unknown["shots"][4]["asset"]["class"] = "photo"
    over = fx.good(CEILING)
    over["shots"][1]["production"]["overlay"] = [{"text": fx.words(CEILING + 1)}]
    tall = fx.bad_all(CEILING)
    tall["deliverable_ratios"] = ["9:16"]
    return {
        "good": good,
        "bad_all": fx.bad_all(CEILING),
        "mut_no_class": no_class,
        "mut_unknown_class": unknown,
        "mut_words": over,
        "bad_all_9_16": tall,
    }


def _fired(document: dict) -> tuple[set[tuple[str, int | None]], list[str]]:
    result = lint_shotlist_detailed(document)
    return {(f.rule_id, f.cut) for f in result.findings}, result.not_checked


@pytest.mark.parametrize("name", sorted(_fixtures()))
def test_same_rule_ids_on_the_same_cuts_under_both_profiles(monkeypatch, kits, name):
    results = {}
    for tenant, kit in kits.items():
        fx.isolate(monkeypatch, kit=kit)
        results[tenant] = _fired(_fixtures()[name])
    assert results["fernway"] == results["summitline"]


def test_the_comparison_can_see_a_difference(monkeypatch, kits):
    """Positive control: the same fixture under two kits CAN differ when a kit rules something
    out, so the equalities above are a real comparison."""
    denied = dict(kits["summitline"])
    denied["design_system"] = {
        **denied["design_system"],
        "denied_fonts": [TENANTS["fernway"]["display"]],
    }
    document = fx.good(CEILING)
    document["shots"][1]["visual"] = f"labels set in {TENANTS['fernway']['display']}"

    fx.isolate(monkeypatch, kit=kits["fernway"])
    plain, _ = _fired(document)
    fx.isolate(monkeypatch, kit=denied)
    strict, _ = _fired(document)

    assert plain != strict
    assert strict - plain == {(hook.DESIGN_SYSTEM, 2)}
    # Only the design-system rule may differ.
    assert {r for r, _c in plain ^ strict} == {hook.DESIGN_SYSTEM}


# ── no tenant vocabulary in the package ───────────────────────────────────────────────────────


def _engine_module_names() -> set[str]:
    return {p.stem for p in (REPO / "gtm_core").iterdir() if not p.name.startswith(("_", "."))}


def tenant_tokens(profiles_root: Path) -> set[str]:
    """Every tenant slug and product folder name, minus product names that are also an engine
    module's name (a product named after a generic noun the engine already owns)."""
    tokens: set[str] = set()
    for tenant in profiles_root.iterdir():
        if not tenant.is_dir() or tenant.name.startswith(("_", ".")):
            continue
        tokens.add(tenant.name)
        products = tenant / "products"
        if products.is_dir():
            for product in products.iterdir():
                if product.is_dir() and not product.name.startswith(("_", ".")):
                    tokens.add(product.name)
                    tokens.add(product.name.replace("-", " "))
    return {t for t in tokens if t not in _engine_module_names() and len(t) >= 3}


def vocabulary_hits(package: Path, tokens: set[str]) -> list[str]:
    hits: list[str] = []
    files = [
        p
        for p in sorted(package.rglob("*"))
        if p.suffix in {".py", ".toml", ".html", ".css", ".js"} and "__pycache__" not in p.parts
    ]
    for token in sorted(tokens):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", re.IGNORECASE)
        for path in files:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(package)}:{lineno}: {token!r}")
    return hits


def _tokens() -> set[str]:
    tokens = tenant_tokens(REPO / "profiles")
    if not tokens:
        pytest.skip("no tenant profiles in this tree (public cut): nothing to look for")
    return tokens


def test_the_package_carries_no_tenant_vocabulary():
    assert vocabulary_hits(PACKAGE, _tokens()) == []


def test_negative_control_a_planted_tenant_slug_is_caught(tmp_path):
    tokens = _tokens()
    planted = sorted(t for t in tokens if " " not in t)[0]
    copy = tmp_path / "video_craft"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "words.py"
    target.write_text(
        target.read_text(encoding="utf-8") + f"\n# made for {planted.upper()}\n", encoding="utf-8"
    )
    hits = vocabulary_hits(copy, tokens)
    assert hits and all(h.startswith("words.py:") for h in hits)


# ── lane independence ─────────────────────────────────────────────────────────────────────────


def _presenter_only() -> dict:
    return fx.doc([fx.shot(n, "presenter", 6.0, role="presenter") for n in range(1, 7)])


def _live_action_only() -> dict:
    document = fx.doc([fx.shot(n, "live_action", 6.0) for n in range(1, 7)])
    document["capture_mode"] = "live_action"
    return document


@pytest.mark.parametrize("build", [_presenter_only, _live_action_only])
def test_presenter_and_filmed_lanes_never_get_the_static_share_warning(monkeypatch, kits, build):
    for kit in kits.values():
        fx.isolate(monkeypatch, kit=kit)
        fired, _ = _fired(build())
        assert hook.STATIC_SHARE not in {r for r, _c in fired}
        _f, classes, _w, result = hook.motion_findings(build()["shots"])
        assert set(classes) == {"moving"}
        assert result.share == 0.0
