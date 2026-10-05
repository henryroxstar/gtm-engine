"""Every reader of a knowledge file is classified against the product run scope.

Each ``gtm_core`` CLI is its own process, so a run scope that lived only in a variable would be
dropped between the first call and the fifth, and the reader that lost it would silently serve the
default product's file. The defence is structural, in two halves:

1. **Classification.** Every function under ``gtm_core/``, ``agent/`` and ``backend/`` that
   resolves a knowledge file, or joins ``knowledge`` onto a path by hand, must be in exactly one
   of ``SCOPED`` (it calls ``run_scope.require``), ``NEEDS_NO_SCOPE`` (it reads only company-wide
   files, or is the resolver, with the reason written down) or ``_UNSCOPED_PENDING`` (a P2 reader
   that takes ``product`` today but does not yet refuse its omission). A reader on none of them
   fails here, so a new one cannot join the gap — the ``_UNWIRED`` discipline of
   ``test_overlay_reach.py``.
2. **Behaviour.** Each SCOPED reader is run on the ``realshape`` fixture: under the second product
   its output carries that product's sentinel term (``zqbeta``), under the default it does not, and
   with the product omitted it refuses. Static analysis proves a call exists; only a run proves
   the value reaches the output.

``_UNSCOPED_PENDING`` may only shrink. It must be empty at the end of phase P2 of the
one-product-per-run design note (§R13: a ban lives in code, not prose).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from gtm_core import run_scope

ROOT = Path(__file__).resolve().parents[2]
_TOP = ("gtm_core", "agent", "backend")

#: Functions that call ``run_scope.require`` themselves (or whose enclosing function does).
SCOPED: dict[str, str] = {
    "gtm_core/web_sweep_hits.py::get_ai_vocab_regex": "AI-vocabulary regex for hit classification",
    "gtm_core/web_sweep_queries.py::_load_query_vocab": "search terms: what a run spends on",
    "gtm_core/hook_coverage/premise.py::capability_vocab": "capability groups",
    "gtm_core/hook_coverage/audit.py::audit_campaign": "matrix + registry for coverage",
    "gtm_core/hook_coverage/backlog.py::backlog": "matrix + registry for the backlog",
    "gtm_core/hook_coverage/cli.py::main": "matrix-only branch",
    "gtm_core/lanes/context.py::load_context": "hook matrix for the router",
    "gtm_core/messaging_intake.py::export_profile_to_markdown._file": "registry export",
    "gtm_core/messaging_intake.py::stage_intake": "refuses a second product",
    "gtm_core/messaging/registry.py::load._path": "the choke point for every messaging verb",
    "gtm_core/hook_coverage/premise.py::load_premise_vocab": "premise vocabulary; no fallback",
    "gtm_core/resolve_knowledge.py::main": "the resolver's CLI: require, then product_file",
    "gtm_core/signal_obs/registry.py::locate": "the source registry: the profile file, or a second product's own",
    "gtm_core/signal_view.py::main": "the signal view CLI: require, then the product's lists and premises",
    "gtm_core/signal_first_status.py::record_lines": "status report for source lists",
}

#: Functions that hand ``product`` to a loader that calls ``run_scope.require`` itself
#: (``registry.load``, ``load_premise_vocab``). They need no ``require`` of their own, but each
#: must PASS the product: a call that drops it is a call that refuses on a two-product company.
DELEGATES: dict[str, str] = {
    "gtm_core/build_eval_sheet.py::_load_premise_vocab": "eval sheet premise vocabulary",
    "gtm_core/messaging/cli.py::_load": "every messaging verb's registry",
    "gtm_core/messaging/registry.py::_load_premises": "the registry's premise cross-check",
    "gtm_core/messaging/resolve.py::angle_for": "angle resolution reads the premise vocabulary",
    "gtm_core/signal_obs/registry_checks.py::load_premises": "the premise vocabulary gets the product; locate() requires",
}

#: Reads only company-wide files, or is the resolver itself. The reason is the contract.
NEEDS_NO_SCOPE: dict[str, str] = {
    "gtm_core/account_integrity.py::load_domain_aliases": "domain-aliases.toml is TENANT_WIDE",
    "gtm_core/competitor_index.py::competitor_entries": "competitors.toml is TENANT_WIDE",
    "gtm_core/signal_quality.py::load_signal_quality_config": "signal-quality.toml is a tenant-level scoring config; no caller passes a product",
    "gtm_core/account_relation_load.py::load_regulators": "a regulator is a regulator for every product; regulators.toml is tenant-level",
    "gtm_core/static_windows.py::load_send_windows": "send-windows.toml is a tenant-level sending schedule",
    "gtm_core/content_quality/guards.py::_resolve_ban_file": "ban lists are TENANT_WIDE",
    "gtm_core/funnel.py::load_yields": "funnel-yields.toml is TENANT_WIDE",
    "gtm_core/funnel.py::record_actuals": "funnel-yields.toml is TENANT_WIDE",
    "gtm_core/lanes/context.py::_knowledge_path": "lane-policy + strategic-accounts are TENANT_WIDE",
    "gtm_core/video_preflight.py::preflight": "voice-bans.txt is TENANT_WIDE",
    "gtm_core/experiments.py::_check_contents": "overlay admission, not a run read",
    "gtm_core/knowledge_index.py::build_profile_index": "indexes the whole corpus; no fact read",
    "gtm_core/knowledge_index.py::main": "indexes the whole corpus; no fact read",
    "gtm_core/knowledge_staging.py::live_path": "onboarding/promotion at the profile level",
    "gtm_core/knowledge_staging.py::snapshot_dir": "onboarding/promotion at the profile level",
    "gtm_core/knowledge_usage.py::coverage": "usage report over the corpus",
    "gtm_core/knowledge_usage.py::default_template_knowledge": "usage report over the corpus",
    "gtm_core/snapshots.py::snapshot_dir": "snapshots the whole knowledge folder",
    "gtm_core/voc/collect.py::collect": "the VoC registry is a company fact",
    "gtm_core/voc/registry.py::apply": "the VoC registry is a company fact",
    "gtm_core/voc/registry.py::load": "the VoC registry is a company fact",
    "gtm_core/voc/registry.py::main": "the VoC registry is a company fact",
    "gtm_core/brandkit.py::brand_kit_paths": "BRAND.toml merges product-over-company by design",
    "gtm_core/brandkit.py::identity_write_target": "identity handles, written by its own CLI",
    "gtm_core/content_quality/sources.py::load_profile_facts": "content lane; reads profile facts",
    "agent/onboard_cli.py::cmd_render_stage": "onboarding family",
    "agent/wizard.py::knowledge_status": "onboarding family",
    "agent/wizard.py::write_knowledge_files": "onboarding family",
    "backend/routers/onboard.py::_ingest": "onboarding family",
    "backend/routers/onboard.py::_re_extract": "onboarding family",
    "gtm_core/paths.py::resolve_knowledge_file": "the resolver itself",
    "gtm_core/run_scope.py::_check_second_product": "the manifest check itself",
    "gtm_core/run_scope.py::product_file": "the scope's own resolver; refuses a fallback",
}

#: Product-aware today (takes ``product``) but does not yet refuse its omission, or a
#: SHARED_BY_DEFAULT reader that has not been threaded. P2. Must be empty at the P2 exit.
_UNSCOPED_PENDING: dict[str, str] = {
    "agent/mcp/judge/scoring.py::load_case_studies": "P2: case-studies.md (shared by default)",
    "agent/readiness.py::check_readiness": "P2: readiness reads the default product's files",
    "gtm_core/build_eval_sheet.py::_load_matrix_if_present": "P2: eval sheet",
    "gtm_core/content_quality/sources.py::load_hook_matrix": "P2: content lane matrix",
    "gtm_core/hook_cell.py::derive_hook_cell": "P2: hook cell derivation",
    "gtm_core/hook_coverage/premise.py::_knowledge_path": "P2: helper; capability_vocab requires",
    "gtm_core/hooks.py::hooks_matrix_path": "P2: hook library",
    "gtm_core/hooks.py::hooks_toml_path": "P2: hook library",
    "gtm_core/icp_check/checks.py::_load_matrix": "P2: icp check",
    "gtm_core/material_intake.py::_candidate_cells": "P2: material intake",
    "gtm_core/material_intake.py::classify_text": "P2: material intake",
    "gtm_core/messaging/angle_status.py::_move": "P2: angle writer; loads the registry first",
    "gtm_core/messaging/matrix_view.py::matrix_path": "P2: derived from a loaded registry",
    "gtm_core/prospects_backlog.py::rubric_path": "P2: backlog select",
    "gtm_core/role_vocabulary/__init__.py::vocabulary_path": "P2: seat vocabulary (shared)",
    "gtm_core/scorecard/loader.py::scorecard_path": "P2: scorecard (shared)",
    "gtm_core/send_cards.py::generate_cards_page": "P2: voice panel; file carries another "
    "session's edits and a known wrong-argument bug tracked in PENDING.md",
}


def _is_loader_call(call: ast.Call) -> bool:
    """``load_premise_vocab(...)`` or ``registry.load(...)`` — the loaders that call ``require``."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id == "load_premise_vocab"
    if isinstance(f, ast.Attribute):
        if f.attr == "load_premise_vocab":
            return True
        return (
            f.attr == "load"
            and isinstance(f.value, ast.Name)
            and f.value.id in {"registry", "_registry"}
        )
    return False


def _sites() -> dict[str, set[str]]:
    """``{module::qualname: {"resolve" | "join"}}`` for every knowledge read under the roots."""
    found: dict[str, set[str]] = {}

    def visit(node: ast.AST, rel: str, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, rel, [*stack, child.name])
                continue
            kind = None
            if isinstance(child, ast.Call):
                f = child.func
                name = (
                    f.id
                    if isinstance(f, ast.Name)
                    else f.attr
                    if isinstance(f, ast.Attribute)
                    else ""
                )
                if name in {"resolve_knowledge_file", "product_file"}:
                    kind = "resolve"
                elif _is_loader_call(child):
                    kind = "loader"
            elif isinstance(child, ast.BinOp) and isinstance(child.op, ast.Div):
                left = any(
                    isinstance(n, ast.Constant) and n.value == "knowledge"
                    for n in ast.walk(child.left)
                )
                right = isinstance(child.right, ast.Constant) and child.right.value == "knowledge"
                if left or right:
                    kind = "join"
            if kind:
                found.setdefault(f"{rel}::{'.'.join(stack) or '<module>'}", set()).add(kind)
            visit(child, rel, stack)

    for top in _TOP:
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            visit(ast.parse(path.read_text(encoding="utf-8")), rel, [])
    return found


def _function_calls_require(rel: str, qualname: str) -> bool:
    """True when the function, or any function enclosing it, calls ``run_scope.require``."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    parts = qualname.split(".")

    def find(node: ast.AST, remaining: list[str]) -> list[ast.AST]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
                child.name == remaining[0]
            ):
                return [child, *(find(child, remaining[1:]) if len(remaining) > 1 else [])]
        return []

    for scope in find(tree, parts):
        for n in ast.walk(scope):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "require":
                    return True
                if isinstance(f, ast.Name) and f.id == "require":
                    return True
    return False


SITES = _sites()


def test_every_knowledge_reader_is_classified_exactly_once():
    groups = {
        "SCOPED": SCOPED,
        "DELEGATES": DELEGATES,
        "NEEDS_NO_SCOPE": NEEDS_NO_SCOPE,
        "_UNSCOPED_PENDING": _UNSCOPED_PENDING,
    }
    seen: dict[str, str] = {}
    for name, group in groups.items():
        for key in group:
            assert key not in seen, f"{key} is in both {seen[key]} and {name}"
            seen[key] = name
    unclassified = sorted(set(SITES) - set(seen))
    # A site in a module the public cut withholds (video, voc) cannot be found there; it is stale
    # only if its module exists and no longer reads a knowledge file.
    stale = sorted(k for k in set(seen) - set(SITES) if (ROOT / k.partition("::")[0]).exists())
    assert not unclassified, (
        f"{unclassified} read a knowledge file and are on no list. Add each to SCOPED (call "
        "run_scope.require), DELEGATES (passes product to a loader that does), NEEDS_NO_SCOPE (with "
        "the reason) or _UNSCOPED_PENDING (P2)."
    )
    assert not stale, f"classified but no longer reading a knowledge file: {stale}"


@pytest.mark.parametrize("key", sorted(SCOPED))
def test_a_scoped_reader_calls_require(key):
    rel, _, qualname = key.partition("::")
    assert _function_calls_require(rel, qualname), (
        f"{key} is listed SCOPED but neither it nor an enclosing function calls run_scope.require"
    )


def _loader_calls(rel: str, qualname: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    out: list[ast.Call] = []
    parts = qualname.split(".")

    def find(node: ast.AST, remaining: list[str]) -> ast.AST | None:
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                and child.name == remaining[0]
            ):
                return child if len(remaining) == 1 else find(child, remaining[1:])
        return None

    fn = find(tree, parts)
    assert fn is not None, f"{rel}::{qualname} not found"
    out.extend(n for n in ast.walk(fn) if isinstance(n, ast.Call) and _is_loader_call(n))
    return out


@pytest.mark.parametrize("key", sorted(DELEGATES))
def test_a_delegate_passes_the_product_to_its_loader(key):
    rel, _, qualname = key.partition("::")
    calls = _loader_calls(rel, qualname)
    assert calls, f"{key} is listed DELEGATES but calls no loader"
    for call in calls:
        by_keyword = any(k.arg == "product" for k in call.keywords)
        # load_premise_vocab(profile, profiles_root, product, overlay): product is the third slot
        by_position = isinstance(call.func, ast.Name | ast.Attribute) and len(call.args) >= 3
        assert by_keyword or by_position, f"{key}: {ast.unparse(call)} drops the product"


def test_reasons_are_written_down():
    for group in (NEEDS_NO_SCOPE, _UNSCOPED_PENDING):
        assert all(reason.strip() for reason in group.values())
    assert all(reason.startswith("P2") for reason in _UNSCOPED_PENDING.values())


def test_the_pending_set_only_shrinks():
    """Ratchet: the set may only shrink. Lower the number when an entry is deleted, never raise it."""
    assert len(_UNSCOPED_PENDING) <= 17


# --- behaviour: the value must reach the output --------------------------------------------------


@pytest.fixture
def realshape(one_product_profiles, tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    return one_product_profiles


def _readers(profiles_root: Path):
    """``name -> callable(product) -> text`` for each P1 reader that carries a value."""
    from agent.mcp.judge.server import judge_context
    from gtm_core import web_sweep_hits, web_sweep_queries
    from gtm_core.hook_coverage.premise import capability_vocab
    from gtm_core.messaging import registry
    from gtm_core.messaging_intake import export_profile_to_markdown

    def regex(product):
        web_sweep_hits._regex_cache.clear()
        return web_sweep_hits.get_ai_vocab_regex("realshape", product).pattern

    def queries(product):
        groups, _ = web_sweep_queries._load_query_vocab(profiles_root, "realshape", product)
        return " ".join(groups.values())

    def claims(product):
        reg = registry.load("realshape", profiles_root, product=product)
        return " ".join(c.statement for c in reg.claims.values())

    def export(product):
        return export_profile_to_markdown("realshape", profiles_root, product=product)

    def caps(product):
        return " ".join(capability_vocab("realshape", profiles_root, product))

    def judge(product):
        return " ".join(
            judge_context("nonexistent-spec.md", "realshape", product).get("capability_groups", [])
        )

    def premises(product):
        from gtm_core.hook_coverage.premise import load_premise_vocab

        return " ".join(
            f"{k} {v.claim} {' '.join(v.terms)}"
            for k, v in load_premise_vocab("realshape", profiles_root, product).items()
        )

    def resolved_path(product):
        import contextlib
        import io

        from gtm_core import resolve_knowledge

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = resolve_knowledge.main(
                [
                    "premise-vocab.toml",
                    "--profile",
                    "realshape",
                    "--profiles-root",
                    str(profiles_root),
                ]
                + (["--product", product] if product else [])
            )
        if code == 2:  # main reports a refusal as an exit code; readers here raise
            raise run_scope.ProductRequired("product-required", "more than one product")
        return out.getvalue()

    def lane_matrix(product):
        from gtm_core.lanes.context import load_context

        ctx = load_context("realshape", profiles_root=profiles_root, product=product)
        return " ".join(str(k) for k in (ctx.matrix.cells if ctx.matrix else {}))

    def backlog_matrix(product):
        from gtm_core.hook_coverage.backlog import backlog

        return repr(backlog("realshape", profiles_root=profiles_root, product=product))

    return {
        "lane_matrix": lane_matrix,
        "backlog_matrix": backlog_matrix,
        "premise_vocab": premises,
        "resolve_knowledge": resolved_path,
        "ai_vocab_regex": regex,
        "registry_claims": claims,
        "intake_export": export,
        "capability_vocab": caps,
        "judge_context": judge,
        "query_vocab": queries,
    }


@pytest.mark.parametrize(
    "name", ["ai_vocab_regex", "registry_claims", "intake_export", "premise_vocab"]
)
def test_a_second_products_output_carries_its_sentinel_and_the_default_never_does(realshape, name):
    read = _readers(realshape)[name]
    assert "zqbeta" in read("beta"), f"{name}: the beta run did not read beta's file"
    assert "zqbeta" not in read("alpha"), f"{name}: the default product's run read beta's file"


@pytest.mark.parametrize(
    "name",
    [
        "ai_vocab_regex", "registry_claims", "intake_export", "capability_vocab", "judge_context",
        "premise_vocab", "resolve_knowledge",
    ],
)  # fmt: skip
def test_omitting_the_product_refuses_on_a_multi_product_profile(realshape, name):
    with pytest.raises(run_scope.ProductRequired):
        _readers(realshape)[name](None)


@pytest.mark.parametrize("name", ["lane_matrix", "backlog_matrix"])
def test_the_matrix_readers_read_the_products_own_matrix(realshape, name):
    """Each product's matrix names its own premise, so the coordinates say whose grid was read.
    Without this, a reader that calls ``require`` but then reads the default's matrix stays green
    (the fresh audit's F1: four readers had refusal tests only)."""
    read = _readers(realshape)[name]
    assert "beta-premise" in read("beta") and "alpha-premise" not in read("beta")
    assert "alpha-premise" in read("alpha") and "beta-premise" not in read("alpha")
    with pytest.raises(run_scope.ProductRequired):
        read(None)


def test_the_resolver_prints_the_second_products_own_file_and_never_the_defaults(realshape):
    read = _readers(realshape)["resolve_knowledge"]
    assert "products/beta/premise-vocab.toml" in read("beta")
    assert "products/" not in read("alpha") and "knowledge/premise-vocab.toml" in read("alpha")


#: Every SCOPED reader, and how its behaviour is proven. A reader that carries a value is run above
#: (its sentinel reaches the output under beta and never under alpha); one that only routes or
#: gates is proven by its own suite, named here so the list cannot quietly go stale.
BEHAVIOUR_PROVEN_BY: dict[str, str] = {
    "gtm_core/web_sweep_hits.py::get_ai_vocab_regex": "here: ai_vocab_regex + the cache test",
    "gtm_core/web_sweep_queries.py::_load_query_vocab": "here: refuses a dropped product; "
    "tests/unit/test_web_sweep.py holds the values",
    "gtm_core/hook_coverage/premise.py::capability_vocab": "here: capability_vocab refuses",
    "gtm_core/hook_coverage/audit.py::audit_campaign": "static: calls require and threads product; "
    "no sentinel run yet (fresh audit F1, tracked in PENDING.md)",
    "gtm_core/hook_coverage/backlog.py::backlog": "here: backlog_matrix",
    "gtm_core/hook_coverage/cli.py::main": "static: matrix-only branch; no sentinel run yet "
    "(fresh audit F1, tracked in PENDING.md)",
    "gtm_core/lanes/context.py::load_context": "here: lane_matrix",
    "gtm_core/messaging_intake.py::export_profile_to_markdown._file": "here: intake_export",
    "gtm_core/messaging_intake.py::stage_intake": "tests/unit/test_scope_reaches_the_gates.py",
    "gtm_core/messaging/registry.py::load._path": "here: registry_claims",
    "gtm_core/hook_coverage/premise.py::load_premise_vocab": "here: premise_vocab",
    "gtm_core/resolve_knowledge.py::main": "here: resolve_knowledge",
    "gtm_core/signal_obs/registry.py::locate": "tests/unit/test_signal_sources_registry.py: a "
    "second product never borrows the profile's registry, an omitted product refuses",
    "gtm_core/signal_view.py::main": "static: calls require first and passes the product on; the "
    "registry read it reaches is proven by the line above",
    "gtm_core/signal_first_status.py::record_lines": "tests/unit/test_signal_first_status.py: "
    "calls require and tests plain lines across switch states",
}


def test_every_scoped_reader_names_the_test_that_proves_its_behaviour():
    assert set(BEHAVIOUR_PROVEN_BY) == set(SCOPED)
    for path in {
        v.split(":")[0].removeprefix("tests/")
        for v in BEHAVIOUR_PROVEN_BY.values()
        if v.startswith("tests/")
    }:
        assert (ROOT / "tests" / path).is_file(), f"{path} does not exist"


def test_the_query_vocabulary_refuses_a_dropped_product(realshape):
    with pytest.raises(run_scope.ProductRequired):
        _readers(realshape)["query_vocab"](None)


def test_the_regex_cache_does_not_hand_one_product_the_others_vocabulary(realshape):
    """Same process, alpha then beta, with every mtime forced equal. A cache keyed on
    ``(profile, mtime, mtime)`` returns alpha's regex for beta (§R18: this must be able to fail)."""
    from gtm_core import web_sweep_hits

    root = realshape / "realshape"
    for path in (
        root / "knowledge" / "web-sweep.toml",
        root / "knowledge" / "role-vocabulary.toml",
        root / "products" / "beta" / "web-sweep.toml",
    ):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    web_sweep_hits._regex_cache.clear()
    alpha = web_sweep_hits.get_ai_vocab_regex("realshape", "alpha").pattern
    beta = web_sweep_hits.get_ai_vocab_regex("realshape", "beta").pattern
    assert "zqbeta" not in alpha
    assert "zqbeta" in beta


def test_a_single_product_profile_needs_no_product(one_product_profiles, tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    from gtm_core import web_sweep_hits

    web_sweep_hits._regex_cache.clear()
    assert "relay" in web_sweep_hits.get_ai_vocab_regex("oneprod").pattern
