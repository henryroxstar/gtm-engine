"""Each provider adapter and the capability registry must not drift apart.

`plugin/skills/email-sequence/references/providers/<tool>.md` is prose a skill reads;
`gtm_core/sequencers.toml` is the cited, dated record of what that vendor's product does.
Two files describing one vendor is exactly the shape that drifts — and the §R18 failure
this repo keeps hitting is a declared contract nobody runs.

The agreement asserted here is deliberately NOT "the adapter repeats the registry's
values". CLAUDE.md's one-home-per-fact rule forbids that: a second copy of `supported =
true` is a second thing to update and a second thing to be wrong. What is asserted is:

  - every capability the registry records for a provider is NAMED in that adapter, so a
    reader of the adapter learns the capability exists at all; and
  - the adapter does not restate a capability's `supported` value, because that value has
    one home and this is not it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "gtm_core" / "sequencers.toml"
ADAPTERS = REPO / "plugin" / "skills" / "email-sequence" / "references" / "providers"


def _registry() -> dict:
    with REGISTRY.open("rb") as fh:
        return tomllib.load(fh)


def _providers_with_rows() -> list[str]:
    return [
        name
        for name, table in _registry().get("providers", {}).items()
        if table.get("capabilities")
    ]


def test_at_least_one_provider_has_rows():
    """Instrument check: with no verified provider every test below passes vacuously."""
    assert _providers_with_rows()


@pytest.mark.parametrize("provider", _providers_with_rows())
def test_every_registry_capability_is_named_in_the_adapter(provider: str):
    adapter = ADAPTERS / f"{provider}.md"
    assert adapter.is_file(), f"{provider} has registry rows but no adapter doc"
    text = adapter.read_text(encoding="utf-8")
    capabilities = _registry()["providers"][provider]["capabilities"]
    missing = [name for name in capabilities if name not in text]
    assert not missing, (
        f"{adapter.name} never mentions {sorted(missing)} — the registry records the "
        "capability and the adapter a skill actually reads does not, so a reader of the "
        "adapter cannot know it exists"
    )


@pytest.mark.parametrize("provider", _providers_with_rows())
def test_the_adapter_does_not_restate_a_supported_value(provider: str):
    """One home per fact. A `supported = true` in prose is a second copy to drift."""
    text = (ADAPTERS / f"{provider}.md").read_text(encoding="utf-8")
    restated = re.findall(r"supported\s*=\s*(?:true|false|\"unknown\")", text)
    assert not restated, (
        f"{provider}.md restates a registry `supported` value {restated} — cite the "
        "registry instead; it is the one home for that fact"
    )


def test_an_unverified_provider_has_no_rows_rather_than_unknown_rows():
    """Rule 2 of the registry: a row exists if and only if a vendor source was read. An
    unverified provider carries a provider table and NO capability rows — an absent row
    refuses exactly as `unknown` does, and a claim of ignorance needs no citation."""
    providers = _registry().get("providers", {})
    for name in ("apollo", "gmass"):
        assert name in providers, f"{name} lost its provider table"
        assert not providers[name].get("capabilities"), (
            f"{name} gained capability rows — it is marked 'documented, not yet verified' "
            "in its adapter, so any row here is uncited by construction"
        )
