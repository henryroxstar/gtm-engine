"""Amendment A: a source is ``agentic`` (its own criterion shows agents) or ``pool`` (a workflow
or market only). An agentic source must say why; a pool source carries no claim of membership."""

from __future__ import annotations

import pytest

from gtm_core.signal_obs import registry as reg

from .test_signal_sources_registry import _file, _load, _src, _write


def _put(root, *sources):
    _write(root, "realshape", "knowledge/signal-sources.toml", _file(*sources))


def test_a_source_with_no_attestation_is_a_pool_source(one_product_profiles):
    _put(one_product_profiles, _src())
    got = _load(one_product_profiles).sources[0]
    assert got.attestation == "pool" and got.agentic_basis == ""


def test_an_agentic_source_with_a_basis_loads(one_product_profiles):
    _put(
        one_product_profiles,
        _src(attestation="agentic", agentic_basis="The programme admits only agent pilots."),
    )
    got = _load(one_product_profiles).sources[0]
    assert got.attestation == "agentic"
    assert got.agentic_basis == "The programme admits only agent pilots."


@pytest.mark.parametrize("basis", [None, "", "   "])
def test_an_agentic_source_without_a_basis_is_refused(one_product_profiles, basis):
    _put(one_product_profiles, _src(attestation="agentic", agentic_basis=basis))
    with pytest.raises(reg.RegistryError) as exc:
        _load(one_product_profiles)
    assert "agentic_basis" in str(exc.value)
    assert exc.value.what and exc.value.why and exc.value.fix


@pytest.mark.parametrize("value", ["yes", "Agentic", "", 1, True])
def test_an_attestation_outside_the_two_words_is_refused(one_product_profiles, value):
    _put(one_product_profiles, _src(attestation=value, agentic_basis="x"))
    with pytest.raises(reg.RegistryError, match="attestation"):
        _load(one_product_profiles)


def test_a_pool_source_may_not_carry_an_agentic_basis(one_product_profiles):
    _put(one_product_profiles, _src(attestation="pool", agentic_basis="Looks agentic."))
    with pytest.raises(reg.RegistryError, match="agentic_basis"):
        _load(one_product_profiles)
