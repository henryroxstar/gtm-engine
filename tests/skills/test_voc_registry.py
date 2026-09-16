"""Tests for the competitor registry (gtm_core.voc.registry).

The registry is a per-profile TOML config. Fetched content must never write it;
updates are operator-gated via ``propose`` + ``apply``.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from gtm_core.voc import registry as reg


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_missing_registry_returns_empty(tmp_path):
    registry = reg.load(profile="acme", profiles_root=tmp_path / "profiles")
    assert registry["schema"] == reg.SCHEMA_VERSION
    assert registry["competitor"] == []


def test_validate_rejects_unknown_tier(tmp_path):
    data = {"schema": 1, "competitor": [{"name": "X", "tier": "not-a-tier"}]}
    with pytest.raises(ValueError, match="unknown tier"):
        reg.validate(data)


def test_validate_rejects_duplicate_name(tmp_path):
    data = {
        "schema": 1,
        "competitor": [
            {"name": "X", "tier": "direct"},
            {"name": "X", "tier": "adjacent"},
        ],
    }
    with pytest.raises(ValueError, match="duplicate"):
        reg.validate(data)


def test_validate_rejects_non_https_watch_url(tmp_path):
    data = {
        "schema": 1,
        "competitor": [
            {
                "name": "X",
                "tier": "direct",
                "watch_urls": ["http://x.example/blog"],
                "domains": ["x.example"],
            }
        ],
    }
    with pytest.raises(ValueError, match="https"):
        reg.validate(data)


def test_propose_writes_data_not_config(tmp_path):
    content = tmp_path / "content"
    candidates = [
        {
            "name": "NewCo",
            "tier": "direct",
            "aliases": ["NewCo"],
            "watch_urls": ["https://newco.example/blog/"],
            "domains": ["newco.example"],
            "syften_filter": "",
            "note": "found in funding lane",
        }
    ]
    path = reg.propose(content_root=content, profile="acme", candidates=candidates)
    assert path.exists()
    assert path.name.startswith("registry-proposals-")
    # The registry config file must remain untouched.
    assert not (content / "acme" / "knowledge" / "competitors.toml").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["candidates"][0]["name"] == "NewCo"


def test_apply_refuses_watch_url_on_wrong_domain(tmp_path):
    profiles = tmp_path / "profiles"
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "NewCo",
            "tier": "direct",
            "aliases": ["NewCo"],
            "watch_urls": ["https://attacker.example/blog/"],
            "domains": ["newco.example"],
            "syften_filter": "",
        }
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    with pytest.raises(ValueError, match="not under allowed domains"):
        reg.apply(
            profiles_root=profiles,
            profile="acme",
            proposals_path=proposals,
            accept=["NewCo"],
        )


def test_apply_refuses_candidate_with_unknown_product(tmp_path):
    """apply() must validate a candidate's product (safe segment + dir exists) BEFORE
    writing anything — an unqualified product must not slip a row into competitors.toml."""
    profiles = tmp_path / "profiles"
    registry_path = profiles / "acme" / "knowledge" / "competitors.toml"
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "Ghost",
            "tier": "direct",
            "product": "no-such-product",
            "aliases": [],
            "watch_urls": ["https://ghost.example/blog/"],
            "domains": ["ghost.example"],
            "syften_filter": "",
        }
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown product 'no-such-product'"):
        reg.apply(
            profiles_root=profiles,
            profile="acme",
            proposals_path=proposals,
            accept=["Ghost"],
        )
    assert not registry_path.exists()


def test_apply_writes_a_valid_product(tmp_path):
    """apply() must accept a candidate whose product names a real products/<slug>/
    directory, write the `product` key (right after `tier`), and load() must return it."""
    profiles = tmp_path / "profiles"
    _make_product_dir(profiles, "acme", "widget-app")
    (profiles / "acme" / "knowledge").mkdir(parents=True)
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "Clay",
            "tier": "direct",
            "product": "widget-app",
            "aliases": [],
            "watch_urls": ["https://clay.example/blog/"],
            "domains": ["clay.example"],
            "syften_filter": "",
        }
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    appended = reg.apply(
        profiles_root=profiles,
        profile="acme",
        proposals_path=proposals,
        accept=["Clay"],
    )
    assert appended == ["Clay"]

    registry_path = profiles / "acme" / "knowledge" / "competitors.toml"
    written_keys = list(tomllib.loads(registry_path.read_text(encoding="utf-8"))["competitor"][0])
    assert written_keys.index("product") == written_keys.index("tier") + 1

    registry = reg.load(profile="acme", profiles_root=profiles)
    assert registry["competitor"][0]["product"] == "widget-app"


def test_apply_appends_and_is_idempotent(tmp_path):
    profiles = tmp_path / "profiles"
    registry_path = profiles / "acme" / "knowledge" / "competitors.toml"
    _write(
        registry_path,
        'schema = 1\nreviewed = "2026-07-01"\n[[competitor]]\nname = "OldCo"\ntier = "direct"\naliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = ""\nfirst_seen = "2026-07-01"\nlast_reviewed = "2026-07-01"\nstatus = "active"\nnote = ""\n',
    )
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "NewCo",
            "tier": "direct",
            "aliases": ["NewCo"],
            "watch_urls": ["https://newco.example/blog/"],
            "domains": ["newco.example"],
            "syften_filter": "",
            "status": "active",
        }
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    appended = reg.apply(
        profiles_root=profiles,
        profile="acme",
        proposals_path=proposals,
        accept=["NewCo"],
    )
    assert appended == ["NewCo"]

    registry = reg.load(profile="acme", profiles_root=profiles)
    assert len(registry["competitor"]) == 2
    assert registry["competitor"][1]["name"] == "NewCo"
    assert registry["competitor"][1]["first_seen"] == reg._today()

    # Second apply of the same proposal is a no-op.
    appended2 = reg.apply(
        profiles_root=profiles,
        profile="acme",
        proposals_path=proposals,
        accept=["NewCo"],
    )
    assert appended2 == []


def test_cli_apply_output_shows_each_appended_rows_product(tmp_path, capsys):
    """§R5: the operator approves by name only, but a proposal's `product` rides in
    with it — the CLI output must surface that on the approval surface."""
    profiles = tmp_path / "profiles"
    _make_product_dir(profiles, "acme", "widget-app")
    (profiles / "acme" / "knowledge").mkdir(parents=True)
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "Clay",
            "tier": "direct",
            "product": "widget-app",
            "aliases": [],
            "watch_urls": ["https://clay.example/blog/"],
            "domains": ["clay.example"],
            "syften_filter": "",
        },
        {
            "name": "Apollo",
            "tier": "direct",
            "aliases": [],
            "watch_urls": ["https://apollo.example/blog/"],
            "domains": ["apollo.example"],
            "syften_filter": "",
        },
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    exit_code = reg.main(
        [
            "--profile",
            "acme",
            "--repo-root",
            str(tmp_path),
            "apply",
            "--proposals",
            str(proposals),
            "--accept",
            "Clay",
            "Apollo",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    assert out == "Appended 2 competitor(s): Clay [widget-app], Apollo [all products]"


def _write_registry(root: Path, profile: str, rows_toml: str) -> Path:
    path = root / profile / "knowledge" / "competitors.toml"
    _write(path, f'schema = 1\nreviewed = "2026-07-01"\n{rows_toml}')
    return path


def _make_product_dir(root: Path, profile: str, slug: str) -> None:
    (root / profile / "products" / slug).mkdir(parents=True)


ROW_NO_PRODUCT = (
    '[[competitor]]\nname = "AllProducts"\ntier = "direct"\naliases = []\n'
    'watch_urls = []\ndomains = []\nsyften_filter = ""\n'
)

ROWS_TWO_PRODUCTS = (
    ROW_NO_PRODUCT + "\n"
    '[[competitor]]\nname = "GadgetRival"\ntier = "direct"\nproduct = "gadget-app"\n'
    'aliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = ""\n\n'
    '[[competitor]]\nname = "WidgetRival"\ntier = "direct"\nproduct = "widget-app"\n'
    'aliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = ""\n'
)


def test_validate_accepts_absent_product():
    data = {"schema": 1, "competitor": [{"name": "X", "tier": "direct"}]}
    reg.validate(data)  # must not raise


def test_validate_rejects_empty_product():
    data = {"schema": 1, "competitor": [{"name": "X", "tier": "direct", "product": ""}]}
    with pytest.raises(ValueError, match="product"):
        reg.validate(data)


def test_validate_rejects_unsafe_product_slug():
    for bad in ("../x", "a/b"):
        data = {"schema": 1, "competitor": [{"name": "X", "tier": "direct", "product": bad}]}
        with pytest.raises(ValueError, match="unsafe product"):
            reg.validate(data)


def test_load_absent_product_applies_to_all(tmp_path):
    root = tmp_path / "profiles"
    _write_registry(root, "acme", ROW_NO_PRODUCT)
    _make_product_dir(root, "acme", "gadget-app")

    unfiltered = reg.load(profile="acme", profiles_root=root)
    assert [c["name"] for c in unfiltered["competitor"]] == ["AllProducts"]

    filtered = reg.load(profile="acme", profiles_root=root, product="gadget-app")
    assert [c["name"] for c in filtered["competitor"]] == ["AllProducts"]


def test_load_filters_by_product(tmp_path):
    root = tmp_path / "profiles"
    _write_registry(root, "acme", ROWS_TWO_PRODUCTS)
    _make_product_dir(root, "acme", "gadget-app")
    _make_product_dir(root, "acme", "widget-app")

    gadget = reg.load(profile="acme", profiles_root=root, product="gadget-app")
    assert {c["name"] for c in gadget["competitor"]} == {"AllProducts", "GadgetRival"}

    widget_app = reg.load(profile="acme", profiles_root=root, product="widget-app")
    assert {c["name"] for c in widget_app["competitor"]} == {"AllProducts", "WidgetRival"}

    unfiltered = reg.load(profile="acme", profiles_root=root)
    assert len(unfiltered["competitor"]) == 3


def test_load_raises_on_unknown_product_slug(tmp_path):
    root = tmp_path / "profiles"
    rows = (
        '[[competitor]]\nname = "Ghost"\ntier = "direct"\nproduct = "no-such-product"\n'
        'aliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = ""\n'
    )
    _write_registry(root, "acme", rows)
    # No products/no-such-product directory created.
    with pytest.raises(ValueError, match="Ghost") as exc_info:
        reg.load(profile="acme", profiles_root=root)
    assert "no-such-product" in str(exc_info.value)


def test_load_rejects_unsafe_product_argument(tmp_path):
    root = tmp_path / "profiles"
    _write_registry(root, "acme", ROW_NO_PRODUCT)
    for bad in ("../x", "a/b"):
        with pytest.raises(ValueError, match="unsafe product"):
            reg.load(profile="acme", profiles_root=root, product=bad)


def test_load_raises_on_unknown_requested_product(tmp_path):
    """A REQUESTED --product that doesn't exist must be rejected, not silently
    return only the unscoped rows — same failure mode a typo'd row product hides."""
    root = tmp_path / "profiles"
    _write_registry(root, "acme", ROW_NO_PRODUCT)
    _make_product_dir(root, "acme", "gadget-app")
    with pytest.raises(ValueError, match="unknown product 'nope'"):
        reg.load(profile="acme", profiles_root=root, product="nope")


def test_cli_show_filters_by_product(tmp_path, capsys):
    root = tmp_path / "profiles"
    _write_registry(root, "acme", ROWS_TWO_PRODUCTS)
    _make_product_dir(root, "acme", "gadget-app")
    _make_product_dir(root, "acme", "widget-app")

    exit_code = reg.main(
        ["--profile", "acme", "--repo-root", str(tmp_path), "show", "--product", "gadget-app"]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert {c["name"] for c in payload["competitor"]} == {"AllProducts", "GadgetRival"}


def test_cli_show_raises_on_unknown_requested_product(tmp_path):
    root = tmp_path / "profiles"
    _write_registry(root, "acme", ROW_NO_PRODUCT)
    _make_product_dir(root, "acme", "gadget-app")

    with pytest.raises(ValueError, match="unknown product 'nope'"):
        reg.main(["--profile", "acme", "--repo-root", str(tmp_path), "show", "--product", "nope"])


def test_cli_budget_counts_only_that_products_rows(tmp_path, capsys):
    root = tmp_path / "profiles"
    rows = (
        '[[competitor]]\nname = "GadgetRival"\ntier = "direct"\nproduct = "gadget-app"\n'
        'aliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = "gadget-tag"\n\n'
        '[[competitor]]\nname = "WidgetRival"\ntier = "direct"\nproduct = "widget-app"\n'
        'aliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = "fl-tag"\n'
    )
    _write_registry(root, "acme", rows)
    _make_product_dir(root, "acme", "gadget-app")
    _make_product_dir(root, "acme", "widget-app")

    exit_code = reg.main(
        ["--profile", "acme", "--repo-root", str(tmp_path), "budget", "--product", "gadget-app"]
    )
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 1
    assert report["uncovered_direct"][0]["name"] == "GadgetRival"


def test_filter_budget_report_matches_tags():
    registry = {
        "competitor": [
            {"name": "CoveredDirect", "tier": "direct", "syften_filter": "competitor-direct"},
            {"name": "Uncovered", "tier": "direct", "syften_filter": ""},
        ]
    }
    syften = {
        "filters": {
            "something $tag:competitor-direct lang:en": {"category": "competitor"},
        }
    }
    report = reg.filter_budget_report(registry=registry, syften_filters=syften)
    assert report["total"] == 2
    assert len(report["covered"]) == 1
    assert len(report["uncovered_direct"]) == 1
    assert report["uncovered_direct"][0]["name"] == "Uncovered"
