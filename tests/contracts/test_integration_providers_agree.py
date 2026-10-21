"""Contract test: integration provider sets must stay identical across surfaces.

Enforces that:
1. gtm_core.packs.loader._VALID_INTEGRATION_PROVIDERS
2. backend.routers.integrations.ProviderType
3. agent.readiness.PROVIDER_DISPLAY_NAMES

stay in lockstep. Widening or renaming a provider in one surface without the others
will fail this contract.
"""

from __future__ import annotations

import typing

from agent.readiness import PROVIDER_DISPLAY_NAMES
from backend.routers.integrations import ProviderType
from gtm_core.packs.loader import _VALID_INTEGRATION_PROVIDERS


def test_loader_and_router_providers_agree():
    router_providers = set(typing.get_args(ProviderType))
    assert _VALID_INTEGRATION_PROVIDERS == router_providers, (
        f"Loader providers {_VALID_INTEGRATION_PROVIDERS} do not match "
        f"integrations router ProviderType {router_providers}"
    )


def test_readiness_display_names_cover_all_loader_providers():
    assert set(PROVIDER_DISPLAY_NAMES.keys()) == _VALID_INTEGRATION_PROVIDERS, (
        f"Readiness display names {set(PROVIDER_DISPLAY_NAMES.keys())} do not match "
        f"loader providers {_VALID_INTEGRATION_PROVIDERS}"
    )


def test_expected_core_providers_present():
    """Pin the four current supported providers."""
    expected = {"saleshandy", "apollo", "rocketreach", "syften"}
    assert _VALID_INTEGRATION_PROVIDERS == expected
