"""R1.3: an operator-only pack graph (`internal`) and a graph-level `egress_scope`.

`internal = true` marks a graph that only an operator-side runner may run; `egress_scope` names
the closed-set scope whose gate the runner must build for every node of the graph. Both are
declarative data on the graph, read by the loader, never a pack name in engine code.

The shipped `source-capture` graph is the one user of both today; it is asserted here by shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.packs import loader
from gtm_core.packs.loader import PackValidationError, load_pack_graph

REPO = Path(__file__).resolve().parents[2]
SOURCE_CAPTURE = REPO / "packs" / "prospecting" / "graphs" / "source-capture.toml"

_NODE = '\n[[nodes]]\nid = "a"\nprompt = "do it"\n'


def _graph(tmp_path: Path, header: str = "") -> Path:
    path = tmp_path / "g.toml"
    path.write_text(f'pack = "p"\nvariant = "v"\n{header}{_NODE}')
    return path


# --- loader: `internal` -----------------------------------------------------------------


def test_internal_defaults_to_false_and_true_is_read(tmp_path):
    assert load_pack_graph(_graph(tmp_path)).internal is False
    assert load_pack_graph(_graph(tmp_path, "internal = true\n")).internal is True
    assert load_pack_graph(_graph(tmp_path, "internal = false\n")).internal is False


@pytest.mark.parametrize("bad", ['"true"', '"yes"', "1", "0", "[true]"])
def test_a_non_bool_internal_is_refused(tmp_path, bad):
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(_graph(tmp_path, f"internal = {bad}\n"))
    assert exc.value.rule == "invalid_internal"


# --- loader: `egress_scope` -------------------------------------------------------------


def test_the_closed_scope_set_has_exactly_one_member():
    assert loader._ALLOWED_EGRESS_SCOPES == frozenset({"capture_manifest"})


def test_a_known_scope_on_an_internal_graph_is_read(tmp_path):
    g = load_pack_graph(_graph(tmp_path, 'internal = true\negress_scope = "capture_manifest"\n'))
    assert g.egress_scope == "capture_manifest"
    assert load_pack_graph(_graph(tmp_path)).egress_scope is None


@pytest.mark.parametrize("bad", ['"anything_else"', '""', "true", "3", '["capture_manifest"]'])
def test_an_unknown_or_non_string_scope_is_refused(tmp_path, bad):
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(_graph(tmp_path, f"internal = true\negress_scope = {bad}\n"))
    assert exc.value.rule == "unknown_egress_scope"


def test_a_scoped_graph_must_be_internal(tmp_path):
    """The backend builds no scope gate, so a scoped graph it could run would run ungated."""
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(_graph(tmp_path, 'egress_scope = "capture_manifest"\n'))
    assert exc.value.rule == "egress_scope_requires_internal"
    with pytest.raises(PackValidationError):
        load_pack_graph(_graph(tmp_path, 'internal = false\negress_scope = "capture_manifest"\n'))


# --- the tenant merge cannot shed either flag -------------------------------------------


def test_a_tenant_override_merge_keeps_internal_and_scope():
    from gtm_core.packs.tenant import PackOverride, merge_pack_override

    base = load_pack_graph(SOURCE_CAPTURE)
    merged = merge_pack_override(
        base, PackOverride(pack=base.pack, variant=base.variant, add_nodes=(), strengthen=())
    )
    assert merged.internal is True
    assert merged.egress_scope == "capture_manifest"


# --- reachability: an internal graph adds nothing to a tenant's skill set ----------------


def test_an_internal_graph_never_counts_toward_reachable_skills(tmp_path):
    from gtm_core.packs.reachability import active_skills_for_profile

    profiles = tmp_path / "profiles"
    (profiles / "acme").mkdir(parents=True)
    (profiles / "acme" / "packs.toml").write_text('active = ["p"]\n')
    graphs = tmp_path / "packs" / "p" / "graphs"
    graphs.mkdir(parents=True)
    (graphs / "public.toml").write_text(
        'pack = "p"\nvariant = "public"\n[[nodes]]\nid = "a"\nskill = "prospect"\n'
    )
    (graphs / "hidden.toml").write_text(
        'pack = "p"\nvariant = "hidden"\ninternal = true\n'
        '[[nodes]]\nid = "a"\nskill = "account-dossier"\n'
    )
    assert active_skills_for_profile(profiles, "acme", tmp_path / "packs") == {"prospect"}


# --- the shipped graph ------------------------------------------------------------------


def test_source_capture_is_one_ungated_internal_scoped_node():
    g = load_pack_graph(SOURCE_CAPTURE)
    assert (g.pack, g.variant) == ("prospecting", "source-capture")
    assert g.ids == ("capture",) and len(g.nodes) == 1
    node = g.node("capture")
    assert node.model_role == "brain_plan"  # a scraped page is untrusted; the brain stays Claude
    assert node.gate is False and node.external_effect is None
    assert node.depends_on == () and node.skill is None
    assert g.internal is True and g.egress_scope == "capture_manifest"


def test_source_capture_prompt_pins_the_tool_the_urls_and_the_untrusted_rule():
    prompt = load_pack_graph(SOURCE_CAPTURE).node("capture").prompt
    for needle in ("manifest", "firecrawl_scrape", "formats", "onlyMainContent", "maxAge"):
        assert needle in prompt, needle
    assert "untrusted" in prompt.lower()


# --- the closed set is spelled once in the loader and once in the engine ------------------


def test_the_engine_builds_a_gate_for_exactly_the_scopes_the_loader_admits():
    """Adding a scope to the loader without its gate builder would validate a graph that the
    runner then cannot scope; adding a builder the loader refuses would be dead code."""
    from agent import egress_scope

    assert frozenset(egress_scope.BUILDERS) == loader._ALLOWED_EGRESS_SCOPES


def test_the_external_effect_set_is_not_widened():
    assert loader._ALLOWED_EXTERNAL_EFFECTS == frozenset({"publish", "email_enroll", "dnc_add"})
