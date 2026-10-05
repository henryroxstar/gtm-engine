"""The run scope: which product a run is for, and what it refuses.

Every case runs on the fictional fixture tenants (``tests/fixtures/one_product``). ``realshape`` is
the live shape: the default product's files sit at profile level, so ``products/alpha/`` holds only
a brand file and a product doc, while the second product ``beta`` holds the full required set. The
rev-1 rule ("a product counts only if its own folder holds files") would have called ``realshape``
a profile with **one** product and made it beta — case ``test_default_product_is_never_replaced``
is the discriminating check (§R18).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gtm_core import run_scope
from gtm_core.run_scope import Ask, Refusal, RunScope


def _resolve(root, profile, **kw):
    return run_scope.resolve(profile, profiles_root=root, **kw)


# --- the real-shape truth table -----------------------------------------------------------------


def test_no_product_interactive_asks_with_the_default_first(one_product_profiles):
    got = _resolve(one_product_profiles, "realshape", interactive=True)
    assert isinstance(got, Ask)
    assert [o.slug for o in got.options] == ["alpha", "beta"]
    assert got.default == "alpha"
    assert [o.name for o in got.options] == ["Alpha Relay", "Beta Ledger"]


def test_no_product_unattended_refuses(one_product_profiles):
    got = _resolve(one_product_profiles, "realshape", interactive=False)
    assert isinstance(got, Refusal)
    assert got.code == "product-required"
    assert "Alpha Relay" in got.message
    assert "Beta Ledger" in got.message


@pytest.mark.parametrize("given", ["beta", "Beta", "beta-ledger", "Beta Ledger", "  beta_ledger "])
def test_second_product_resolves_from_slug_or_display_name(one_product_profiles, given):
    got = _resolve(one_product_profiles, "realshape", product=given)
    assert isinstance(got, RunScope)
    assert (got.product, got.is_second_product, got.multi) == ("beta", True, True)


def test_default_product_is_never_replaced(one_product_profiles):
    """The rev-1 inversion: with no product named the default must not become the second one."""
    got = _resolve(one_product_profiles, "realshape", product="alpha")
    assert isinstance(got, RunScope)
    assert (got.product, got.is_second_product) == ("alpha", False)
    # The rev-1 rule counted a product only if its own folder held files. Alpha's folder holds
    # none, so that rule would have made beta the only product; assert the premise holds.
    alpha = one_product_profiles / "realshape" / "products" / "alpha"
    assert not any((alpha / f).exists() for f in run_scope.PRODUCT_REQUIRED)


def test_single_product_and_brand_only_folders_are_untouched(one_product_profiles):
    """A tenant with one product, or with only brand/doc product folders, sees no change."""
    for profile in ("oneprod", "branddirs"):
        got = _resolve(one_product_profiles, profile, interactive=True)
        assert isinstance(got, RunScope)
        assert got.product is None
        assert got.multi is False
        assert run_scope.header_lines(got) == []
        assert run_scope.record_lines(got) == []
    named = _resolve(one_product_profiles, "branddirs", product="north")
    assert isinstance(named, RunScope)
    assert named.product == "north"


def test_cli_is_silent_for_a_profile_with_no_second_product(
    one_product_profiles, capsys, monkeypatch
):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    assert run_scope._cli(["resolve", "--profile", "oneprod"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_exit_codes(one_product_profiles, capsys, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    assert run_scope._cli(["resolve", "--profile", "realshape"]) == 3
    assert "ask: alpha|Alpha Relay (Recommended)" in capsys.readouterr().out
    assert run_scope._cli(["resolve", "--profile", "realshape", "--unattended"]) == 2
    assert "product-required" in capsys.readouterr().err
    assert run_scope._cli(["resolve", "--profile", "realshape", "--product", "beta"]) == 0
    assert capsys.readouterr().out.startswith("product=beta\nProspecting for Beta Ledger.")


# --- naming a product ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "\x00", "gamma"])
def test_bad_names_refuse_and_never_fall_back(one_product_profiles, bad):
    got = _resolve(one_product_profiles, "realshape", product=bad)
    assert isinstance(got, Refusal)
    assert got.code in {"product-unknown", "product-unsafe"}


def test_two_products_that_differ_only_in_case_refuse(one_product_profiles):
    profile = one_product_profiles / "realshape" / "PROFILE.md"
    text = profile.read_text().replace(
        "  - { slug: beta, name: Beta Ledger, capabilities: [beta] }",
        "  - { slug: beta, name: Beta Ledger, capabilities: [beta] }\n"
        "  - { slug: Beta-2, name: BETA LEDGER, capabilities: [beta] }",
    )
    profile.write_text(text)
    got = _resolve(one_product_profiles, "realshape", product="beta ledger")
    assert isinstance(got, Refusal)
    assert got.code == "product-ambiguous"


def test_a_declared_product_with_no_files_is_not_selectable(one_product_profiles):
    profile = one_product_profiles / "realshape" / "PROFILE.md"
    body = profile.read_text().rstrip("\n")
    assert body.endswith("```")
    profile.write_text(
        body[:-3] + "  - { slug: gamma, name: Fictional Gamma Relay, capabilities: [pay] }\n```\n"
    )
    got = _resolve(one_product_profiles, "realshape", product="gamma")
    assert isinstance(got, Refusal)
    assert got.code == "product-not-set-up"


def test_a_brand_only_folder_missing_from_products_is_ignored_and_reported(one_product_profiles):
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, RunScope) and got.ignored_dirs == ()
    orphan = one_product_profiles / "realshape" / "products" / "orphan"
    orphan.mkdir()
    (orphan / "PRODUCT.md").write_text("# orphan\n")
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, RunScope) and got.ignored_dirs == ("orphan",)
    assert "orphan" in " ".join(run_scope.record_lines(got))
    asked = _resolve(one_product_profiles, "realshape", interactive=True)
    assert isinstance(asked, Ask) and "orphan" not in [o.slug for o in asked.options]


@pytest.mark.parametrize("profile", ["realshape", "oneprod"])
def test_an_unlisted_folder_holding_product_files_refuses_every_run(one_product_profiles, profile):
    """An undeclared folder with prospecting files is a product nobody listed. Passing it through
    (today's single-product meaning of `--product X`) would read its files and write its fit as if
    it were the default product. The red-team case: a single-product profile, `--product orphan`."""
    orphan = one_product_profiles / profile / "products" / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "claims.toml").write_text("x = 1\n")
    for kw in ({}, {"product": "orphan"}, {"interactive": True}):
        got = _resolve(one_product_profiles, profile, **kw)
        assert isinstance(got, Refusal), kw
        assert got.code == "unlisted-product-folder"
    with pytest.raises(run_scope.ScopeError):
        run_scope.require(profile, None, profiles_root=one_product_profiles)


def test_refusal_copy_takes_names_from_profile_md(one_product_profiles):
    profile = one_product_profiles / "realshape" / "PROFILE.md"
    profile.write_text(profile.read_text().replace("Alpha Relay", "Fictional Zenith Relay"))
    got = _resolve(one_product_profiles, "realshape")
    assert isinstance(got, Refusal) and "Fictional Zenith Relay" in got.message


# --- require(): the cross-process guard ---------------------------------------------------------


def test_require_refuses_a_dropped_product_on_a_multi_product_profile(one_product_profiles):
    with pytest.raises(run_scope.ProductRequired):
        run_scope.require("realshape", None, profiles_root=one_product_profiles)
    ok = run_scope.require("realshape", "beta", profiles_root=one_product_profiles)
    assert ok.product == "beta"


def test_require_defaults_only_while_nothing_is_ambiguous(one_product_profiles):
    for profile in ("oneprod", "branddirs"):
        scope = run_scope.require(profile, None, profiles_root=one_product_profiles)
        assert scope.product is None


# --- the manifest refusals (badbeta) ------------------------------------------------------------


def _beta(root: Path) -> Path:
    return root / "realshape" / "products" / "beta"


#: Written out, not read from `run_scope`: a test that parametrises over the constant it checks
#: survives the constant being wrong (the audit moved proof.toml to "shared" and every test passed).
REQUIRED = ("angles.toml", "claims.toml", "premise-vocab.toml", "proof.toml", "web-sweep.toml")
DERIVED = ("hook-matrix.md",)


def test_the_required_set_is_exactly_the_products_own_arguments():
    assert set(REQUIRED) | set(DERIVED) == run_scope.PRODUCT_REQUIRED
    assert set(DERIVED) == run_scope.PRODUCT_DERIVED


@pytest.mark.parametrize("missing", REQUIRED)
def test_a_product_missing_a_required_file_is_not_ready_and_never_falls_back(
    one_product_profiles, missing
):
    control = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(control, RunScope)  # negative control: the same fixture minus the defect
    (_beta(one_product_profiles) / missing).unlink()
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, Refusal)
    assert got.code == "product-not-ready"
    assert missing in got.message
    # Not offered while it is being set up: the company behaves as single-product again.
    unnamed = _resolve(one_product_profiles, "realshape", interactive=True)
    assert isinstance(unnamed, RunScope) and unnamed.product is None and not unnamed.multi
    with pytest.raises(run_scope.ScopeError):
        run_scope.require("realshape", "Beta Ledger", profiles_root=one_product_profiles)


def test_a_missing_derived_matrix_does_not_block_the_scope(one_product_profiles):
    (_beta(one_product_profiles) / "hook-matrix.md").unlink()
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, RunScope) and got.is_second_product


@pytest.mark.parametrize(
    "block",
    [
        "  - { slug: beta, name: Beta Ledger: Pro, capabilities: [beta] }",  # YAML error
        "  - just-a-string",  # not a mapping
    ],
)
def test_an_unreadable_products_block_refuses_instead_of_reading_as_no_products(
    one_product_profiles, block
):
    """A tolerant parse would read a broken block as "no products" and switch every guard off."""
    profile = one_product_profiles / "realshape" / "PROFILE.md"
    profile.write_text(
        profile.read_text().replace(
            "  - { slug: beta, name: Beta Ledger, capabilities: [beta] }", block
        )
    )
    for kw in ({}, {"product": "beta"}, {"interactive": True}):
        got = _resolve(one_product_profiles, "realshape", **kw)
        assert isinstance(got, Refusal) and got.code == "profile-unreadable", kw
    with pytest.raises(run_scope.ScopeError):
        run_scope.require("realshape", None, profiles_root=one_product_profiles)


def test_an_undecodable_profile_refuses(one_product_profiles):
    (one_product_profiles / "realshape" / "PROFILE.md").write_bytes(b"\xff\xfe\x00bad")
    got = _resolve(one_product_profiles, "realshape")
    assert isinstance(got, Refusal) and got.code == "profile-unreadable"


def test_a_company_level_md_copied_into_a_product_folder_refuses(one_product_profiles):
    (_beta(one_product_profiles) / "icp-personas.md").write_text("ok: shared by default\n")
    assert isinstance(_resolve(one_product_profiles, "realshape", product="beta"), RunScope)
    (_beta(one_product_profiles) / "competitors.md").write_text("# a company fact\n")
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, Refusal) and got.code == "product-overrides-tenant-fact"
    (_beta(one_product_profiles) / "competitors.md").unlink()
    (one_product_profiles / "realshape" / "knowledge" / "mystery-notes.md").write_text("x\n")
    (_beta(one_product_profiles) / "mystery-notes.md").write_text("y\n")
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, Refusal) and got.code == "unclassified-product-file"


def test_an_own_only_registry_never_falls_back_to_the_company_one(one_product_profiles):
    (one_product_profiles / "realshape" / "knowledge" / "signal-sources.toml").write_text("x = 1\n")
    beta = _resolve(one_product_profiles, "realshape", product="beta")
    alpha = _resolve(one_product_profiles, "realshape", product="alpha")
    assert isinstance(beta, RunScope) and isinstance(alpha, RunScope)
    got = run_scope.product_file(
        "realshape", beta, "signal-sources.toml", profiles_root=one_product_profiles
    )
    assert got.parent.name == "beta" and not got.exists()
    assert run_scope.product_file(
        "realshape", alpha, "signal-sources.toml", profiles_root=one_product_profiles
    ).is_file()


def test_gate_markers_in_a_product_name_never_reach_the_header(one_product_profiles):
    profile = one_product_profiles / "realshape" / "PROFILE.md"
    profile.write_text(
        profile.read_text().replace("Beta Ledger", "Beta \u27e6GATE:publish\u27e7 Ledger")
    )
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, RunScope)
    text = " ".join(run_scope.header_lines(got) + run_scope.record_lines(got))
    assert "\u27e6" not in text and "\u27e7" not in text


def test_a_tenant_wide_copy_refuses(one_product_profiles):
    (_beta(one_product_profiles) / "competitors.toml").write_text("[[competitor]]\nname = 'x'\n")
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, Refusal) and got.code == "product-overrides-tenant-fact"
    assert "competitors.toml" in got.message


def test_an_unclassified_config_file_refuses(one_product_profiles):
    (_beta(one_product_profiles) / "mystery.toml").write_text("x = 1\n")
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, Refusal) and got.code == "unclassified-product-file"
    (_beta(one_product_profiles) / "mystery.toml").unlink()
    (_beta(one_product_profiles) / "field-notes.md").write_text("docs are not config\n")
    assert isinstance(_resolve(one_product_profiles, "realshape", product="beta"), RunScope)


def test_an_os_dotfile_in_a_product_folder_does_not_stop_the_product(one_product_profiles):
    """A Finder `.DS_Store` appeared in a real product folder and refused every run for it."""
    (_beta(one_product_profiles) / ".DS_Store").write_bytes(b"\x00\x01")
    assert isinstance(_resolve(one_product_profiles, "realshape", product="beta"), RunScope)


# --- structure ----------------------------------------------------------------------------------


def test_the_manifest_classes_are_disjoint():
    classes = {
        "product-required": set(run_scope.PRODUCT_REQUIRED),
        "shared": set(run_scope.SHARED_BY_DEFAULT),
        "tenant-wide": set(run_scope.TENANT_WIDE),
        "own-only": set(run_scope.PRODUCT_OWN_ONLY),
        "not-product": set(run_scope.NOT_PRODUCT_FILE),
    }
    names = list(classes)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not classes[a] & classes[b], f"{a} and {b} overlap"
    assert "signal-sources.toml" in run_scope.PRODUCT_OWN_ONLY


def test_run_scope_reads_no_ambient_state_and_no_connector():
    """A product is an argument. Nothing here may read the environment, a marker file or a row, and
    the first call of a run must not be able to spend anything."""
    tree = ast.parse(Path(run_scope.__file__).read_text())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not {
        m
        for m in imported
        if any(x in m for x in ("mcp", "cost", "metering", "connector", "agent"))
    }
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attrs & {"environ", "getenv"}
    assert "os" not in imported


def test_a_missing_derived_file_resolves_but_its_readers_refuse(one_product_profiles):
    """``hook-matrix.md`` is generated from the product's angles, so its absence must not block
    the run scope (or it could never be generated); a reader that would fall back must refuse."""
    (_beta(one_product_profiles) / "hook-matrix.md").unlink()
    scope = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(scope, RunScope)
    with pytest.raises(run_scope.ScopeError) as err:
        run_scope.product_file(
            "realshape", scope, "hook-matrix.md", profiles_root=one_product_profiles
        )
    assert "messaging matrix" in str(err.value) and "--product beta" in str(err.value)
    # the default product's matrix is the profile's, and is never refused
    alpha = _resolve(one_product_profiles, "realshape", product="alpha")
    assert isinstance(alpha, RunScope)
    assert (
        run_scope.product_file(
            "realshape", alpha, "hook-matrix.md", profiles_root=one_product_profiles
        ).parent.name
        == "knowledge"
    )


def test_product_file_never_falls_back_for_a_required_file(one_product_profiles):
    scope = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(scope, RunScope)
    got = run_scope.product_file(
        "realshape", scope, "claims.toml", profiles_root=one_product_profiles
    )
    assert got.parent.name == "beta"
    shared = run_scope.product_file(
        "realshape", scope, "role-vocabulary.toml", profiles_root=one_product_profiles
    )
    assert shared.parent.name == "knowledge"  # shared by default: the fallback is allowed


# --- the mutation pass (2026-09-29): branches no test could tell from their absence ------------------

_BETA_FILES = ("angles.toml", "claims.toml", "proof.toml", "premise-vocab.toml", "web-sweep.toml")


def _profile_md(root: Path, profile: str = "realshape") -> Path:
    return root / profile / "PROFILE.md"


def test_a_declared_default_that_is_not_a_product_leaves_no_default(one_product_profiles):
    """A typo in ``default_product`` must not become a default the company never chose."""
    path = _profile_md(one_product_profiles)
    path.write_text(path.read_text().replace("default_product: alpha", "default_product: ghost"))
    got = _resolve(one_product_profiles, "realshape", interactive=False)
    assert isinstance(got, Refusal) and got.code == "no-default-product"
    assert isinstance(_resolve(one_product_profiles, "realshape", product="beta"), RunScope)


def test_a_company_with_one_product_and_no_declared_default_uses_that_product(
    one_product_profiles,
):
    path = _profile_md(one_product_profiles, "oneprod")
    path.write_text(path.read_text().replace("default_product: solo\n", ""))
    got = _resolve(one_product_profiles, "oneprod", interactive=False)
    assert isinstance(got, RunScope)
    assert got.product_display == "Solo Relay" and not got.is_second_product and not got.multi


def test_the_default_product_is_never_offered_as_a_second_one_even_with_a_full_folder(
    one_product_profiles,
):
    root = one_product_profiles / "realshape"
    for name in _BETA_FILES:
        (root / "products" / "alpha" / name).write_text(
            (root / "products" / "beta" / name).read_text()
        )
    asked = _resolve(one_product_profiles, "realshape", interactive=True)
    assert isinstance(asked, Ask) and [o.slug for o in asked.options] == ["alpha", "beta"]
    named = _resolve(one_product_profiles, "realshape", product="alpha")
    assert isinstance(named, RunScope) and not named.is_second_product


def test_a_third_product_being_set_up_is_not_ready_even_beside_a_ready_second(
    one_product_profiles,
):
    root = one_product_profiles / "realshape"
    path = _profile_md(one_product_profiles)
    path.write_text(
        path.read_text().replace(
            "  - { slug: beta, name: Beta Ledger, capabilities: [beta] }\n",
            "  - { slug: beta, name: Beta Ledger, capabilities: [beta] }\n"
            "  - { slug: gamma, name: Gamma Desk, capabilities: [gamma] }\n",
        )
    )
    (root / "products" / "gamma").mkdir()
    (root / "products" / "gamma" / "claims.toml").write_text(
        '[[claim]]\nid = "g"\n'
    )  # one file: begun, not finished
    got = _resolve(one_product_profiles, "realshape", product="gamma")
    assert isinstance(got, Refusal) and got.code == "product-not-ready"
    assert "gamma desk" in got.message.lower()


def test_an_unsafe_overlay_slug_refuses_before_anything_resolves(one_product_profiles):
    got = _resolve(one_product_profiles, "realshape", product="beta", overlay="../elsewhere")
    assert isinstance(got, Refusal) and got.code == "product-unsafe"


def test_a_fenced_block_that_is_not_the_products_block_is_skipped_not_parsed(
    one_product_profiles,
):
    """A profile may carry other code fences (a shell snippet, a config sample) ahead of the block
    that names products. Parsing every fence as YAML would refuse a healthy profile."""
    path = _profile_md(one_product_profiles)
    path.write_text("# Notes\n\n```bash\necho hi: [\n```\n\n" + path.read_text())
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, RunScope) and got.is_second_product


def test_an_unclosed_fence_never_reads_as_a_company_with_no_products(one_product_profiles):
    """Drop the closing fence and the block is invisible to the parser. That must not switch the
    guards off: here the second product's own files give the mistake away and the run refuses."""
    path = _profile_md(one_product_profiles)
    path.write_text(path.read_text().rstrip().removesuffix("```").rstrip() + "\n")
    got = _resolve(one_product_profiles, "realshape", product="beta", interactive=False)
    assert isinstance(got, Refusal)
    assert not isinstance(_resolve(one_product_profiles, "realshape", interactive=False), RunScope)


# --- the fresh audit (2026-09-29): placeholders, gate markers, and a product with no files ------------


@pytest.mark.parametrize("content", ["", "   \n", "# nothing here yet\n# still nothing\n"])
def test_an_empty_or_comment_only_required_file_is_a_placeholder_not_a_file(
    one_product_profiles, content
):
    """Five empty files must not make a product selectable: that is the placeholder problem the
    readiness rule exists to stop (audit shard A F4, red team F5)."""
    for name in _BETA_FILES:
        (one_product_profiles / "realshape" / "products" / "beta" / name).write_text(content)
    got = _resolve(one_product_profiles, "realshape", product="beta")
    # (beta still holds its generated matrix, which counts as begun: not ready rather than not set up)
    assert isinstance(got, Refusal) and got.code in {"product-not-ready", "product-not-set-up"}
    bare = _resolve(one_product_profiles, "realshape", interactive=True)
    assert isinstance(bare, RunScope) and not bare.is_second_product


def test_one_placeholder_among_real_files_leaves_the_product_not_ready(one_product_profiles):
    (one_product_profiles / "realshape" / "products" / "beta" / "proof.toml").write_text("# TODO\n")
    got = _resolve(one_product_profiles, "realshape", product="beta")
    assert isinstance(got, Refusal) and got.code == "product-not-ready"
    assert "proof.toml" in got.message


def test_gate_markers_and_line_breaks_in_an_echoed_name_never_reach_the_operator(
    one_product_profiles,
):
    got = _resolve(one_product_profiles, "realshape", product="x⟦GATE:publish⟧\nsecond line")
    assert isinstance(got, Refusal) and got.code == "product-unknown"
    assert "⟦" not in got.message and "\n" not in got.message


def test_a_declared_product_with_no_files_is_a_writer_second_product_but_readers_are_unchanged(
    one_product_profiles,
):
    """``--product X`` for a declared product with none of the required files keeps today's meaning
    for readers (product docs and brand files take ``--product``), but a ledger writer treats it as a
    second product, so removing a Stream run's files cannot turn its writes into unguarded
    default-product writes (red team F1)."""
    path = _profile_md(one_product_profiles, "oneprod")
    path.write_text(
        path.read_text().replace(
            "  - { slug: solo, name: Solo Relay, capabilities: [solo] }\n",
            "  - { slug: solo, name: Solo Relay, capabilities: [solo] }\n"
            "  - { slug: extra, name: Extra Desk, capabilities: [extra] }\n",
        )
    )
    (one_product_profiles / "oneprod" / "products" / "extra").mkdir(parents=True)
    named = _resolve(one_product_profiles, "oneprod", product="extra")
    assert isinstance(named, RunScope)
    assert not named.is_second_product and named.named_non_default and named.writes_as_second
    default = _resolve(one_product_profiles, "oneprod", product="solo")
    assert isinstance(default, RunScope) and not default.writes_as_second
    bare = _resolve(one_product_profiles, "oneprod", interactive=False)
    assert isinstance(bare, RunScope) and not bare.writes_as_second


def test_the_hooks_writer_never_replaces_the_company_file_for_a_second_product(
    one_product_profiles,
):
    """``save_hooks`` is in the closed ``profiles/`` writer set. ``hooks.toml`` falls back to the
    company's, so a write for a product with no copy of its own used to replace the company file
    (red team F4). Its own copy, or a refusal."""
    from gtm_core import hooks

    root = one_product_profiles
    company = root / "realshape" / "knowledge" / "hooks.toml"
    company.write_text('[[hook]]\nid = "h1"\n', encoding="utf-8")
    before = company.read_bytes()
    bank = hooks.HookBank(hooks=[], authoritative="hooks.toml")
    with pytest.raises(run_scope.ScopeError, match="no hooks.toml of its own"):
        hooks.save_hooks(root, "realshape", bank, product="beta")
    assert company.read_bytes() == before
    with pytest.raises(run_scope.ProductRequired):
        hooks.save_hooks(root, "realshape", bank)
    own = root / "realshape" / "products" / "beta" / "hooks.toml"
    own.write_text("", encoding="utf-8")
    assert hooks.save_hooks(root, "realshape", bank, product="beta") == own
    assert company.read_bytes() == before
    assert hooks.save_hooks(root, "realshape", bank, product="alpha") == company  # the default


def test_a_product_name_the_onboarding_writes_always_survives_the_strict_reader():
    """Onboarding renders each product into a YAML flow mapping. A name with a colon, comma or brace
    made the run scope refuse the whole profile (red team F2); plain names stay byte-identical."""
    from agent.onboard.render_profile import _render_profile_md

    names = ["Plain Name", "Gateway: Enterprise, Cloud", "Braces {a}", 'Quote " here', "Hash #1"]
    products = [
        {"slug": f"p{i}", "name": n, "capabilities": ["x"], "flagship": i == 0}
        for i, n in enumerate(names)
    ]
    text = _render_profile_md(
        {"slug": "acme", "brand_name": "Acme", "name": "Acme Co"}, {}, {}, [], products, {}
    )
    assert "name: Plain Name," in text  # unchanged for a plain name
    parsed = run_scope._products_block(text)
    assert [p["name"] for p in parsed["products"]] == names


def test_the_resolver_cli_refuses_a_missing_derived_file_and_prints_nothing(
    one_product_profiles, capsys
):
    """A second product with no matrix of its own must not be handed the company's through the
    command the skills use to fetch it (audit F5: this exit code was never asserted)."""
    from gtm_core import resolve_knowledge

    (one_product_profiles / "realshape" / "products" / "beta" / "hook-matrix.md").unlink()
    argv = [
        "hook-matrix.md",
        "--profile",
        "realshape",
        "--profiles-root",
        str(one_product_profiles),
    ]
    assert resolve_knowledge.main([*argv, "--product", "beta"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "hook-matrix.md" in out.err
    assert resolve_knowledge.main([*argv, "--product", "alpha"]) == 0  # the default's own is fine


def test_a_corrupt_product_file_refuses_in_its_loader(one_product_profiles):
    """A malformed ``angles.toml`` for a second product stops the run rather than reading as an
    empty registry (audit F11; the plan's T12 listed this and no test held it)."""
    from gtm_core.messaging import registry

    (one_product_profiles / "realshape" / "products" / "beta" / "angles.toml").write_text(
        "[[angle]\nid = ", encoding="utf-8"
    )
    with pytest.raises(registry.RegistryError):
        registry.load("realshape", one_product_profiles, product="beta")
