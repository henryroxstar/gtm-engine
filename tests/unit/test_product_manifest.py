"""Every config file a tenant keeps must be on the product manifest, or named as not one.

The manifest (``gtm_core.run_scope``) is closed on purpose: a file on no list is refused under a
second product's folder, so a new knowledge file has to be classified before any product can carry
it. This test is what makes that a rule for the *authors of new files* too — it fails the day a
tenant gains a ``.toml``/``.txt``/``.json`` in ``knowledge/`` that nobody classified, the same
discipline ``test_overlay_reach.py`` applies to the overlayable set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import run_scope

PROFILES = Path(__file__).resolve().parents[2] / "profiles"
_CONFIG = (".toml", ".txt", ".json")


def _tenants() -> list[Path]:
    return sorted(p for p in PROFILES.iterdir() if (p / "knowledge").is_dir())


@pytest.mark.parametrize("tenant", _tenants(), ids=lambda p: p.name)
def test_every_config_file_in_a_tenant_is_classified(tenant):
    unclassified = sorted(
        f.name
        for f in (tenant / "knowledge").iterdir()
        if f.is_file() and f.name.endswith(_CONFIG) and run_scope.classify(f.name) is None
    )
    assert unclassified == [], (
        f"{tenant.name}/knowledge holds config files on no product-manifest list: {unclassified}. "
        "Classify each in gtm_core/run_scope.py (product-required, shared-by-default, "
        "tenant-wide, product-own-only, or not-product-file)."
    )


def test_a_file_is_in_exactly_one_class():
    for name in (
        "claims.toml",
        "role-vocabulary.toml",
        "competitors.toml",
        "signal-sources.toml",
        "BRAND.toml",
    ):
        assert run_scope.classify(name) is not None
    assert run_scope.classify("never-heard-of-it.toml") is None


def test_tenant_wide_files_are_not_offered_for_a_product_copy():
    """Anything that is a company fact or a safety rule is refused at the product rung."""
    for name in ("competitors.toml", "domain-aliases.toml", "voice-bans.txt", "lane-policy.toml"):
        assert name in run_scope.TENANT_WIDE
        assert name not in set(run_scope.SHARED_BY_DEFAULT) | run_scope.PRODUCT_REQUIRED
