"""``messaging matrix --product`` writes the second product's matrix and nothing else.

``write_matrix`` is one member of the closed set of ``profiles/`` writers. Its destination is the
folder its angles file resolves from, so for a second product that is ``products/<slug>/``. The run
scope is what keeps a hostile or mistyped slug from becoming a path: the writer never sees it.
"""

from __future__ import annotations

import pytest

from gtm_core.messaging import cli


def _run(root, *extra):
    return cli.main(["matrix", "--profile", "realshape", "--profiles-root", str(root), *extra])


def test_a_second_products_matrix_lands_in_its_own_folder_only(one_product_profiles):
    base = one_product_profiles / "realshape"
    profile_matrix = base / "knowledge" / "hook-matrix.md"
    beta_matrix = base / "products" / "beta" / "hook-matrix.md"
    beta_matrix.unlink()
    before = profile_matrix.read_bytes()
    assert _run(one_product_profiles, "--product", "beta") == 0
    assert beta_matrix.is_file() and "zqbeta" in beta_matrix.read_text()
    assert profile_matrix.read_bytes() == before


@pytest.mark.parametrize("slug", ["../x", "a/b", "a\\b", "gamma"])
def test_a_traversal_or_unknown_slug_writes_nothing(one_product_profiles, slug, capsys):
    before = sorted(str(p) for p in one_product_profiles.rglob("*"))
    assert _run(one_product_profiles, "--product", slug) == 2
    assert sorted(str(p) for p in one_product_profiles.rglob("*")) == before
    assert capsys.readouterr().err


def test_omitting_the_product_refuses_on_a_multi_product_profile(one_product_profiles, capsys):
    assert _run(one_product_profiles) == 2
    assert "more than one product" in capsys.readouterr().err


@pytest.mark.parametrize("given", ["Beta Ledger", "beta ledger", " BETA "])
def test_a_display_name_writes_the_products_matrix_never_the_companys(one_product_profiles, given):
    """The audit's red team overwrote the company matrix with ``--product "Beta Ledger"``: the
    registry mapped the name to a slug, the path lookup did not. Normalised once, in ``main``."""
    base = one_product_profiles / "realshape"
    company = base / "knowledge" / "hook-matrix.md"
    beta = base / "products" / "beta" / "hook-matrix.md"
    beta.unlink()
    before = company.read_bytes()
    assert _run(one_product_profiles, "--product", given) == 0
    assert beta.is_file() and "zqbeta" in beta.read_text()
    assert company.read_bytes() == before


def test_the_second_lock_refuses_a_target_outside_the_products_folder(one_product_profiles):
    """The resolved-path check must fail on its own, whatever the callers above it did right."""
    from gtm_core.messaging import registry

    base = one_product_profiles / "realshape"
    with pytest.raises(registry.RegistryError, match="outside the product folder"):
        cli._confine_matrix_target(
            base / "knowledge" / "hook-matrix.md", one_product_profiles, "realshape", "beta"
        )
    with pytest.raises(registry.RegistryError):  # and outside the profile altogether
        cli._confine_matrix_target(
            one_product_profiles / "elsewhere.md", one_product_profiles, "realshape", "alpha"
        )
    cli._confine_matrix_target(  # the product's own matrix passes
        base / "products" / "beta" / "hook-matrix.md", one_product_profiles, "realshape", "beta"
    )
