"""render_svg(animation_ready=...) contract.

Default output (animation_ready=False) is pinned byte-for-byte to goldens captured from the
renderer before the keyword existed. animation_ready=True output wraps every node, edge and
group in one addressable <g>, carries no literal colour, omits xmlns, refuses malformed or
colliding ids, and never mutates the caller's IR.

Regenerate goldens (only when a default-output change is intended):
    uv run python -c "import tests.diagrams.test_render_animation_ready as t; t.write_goldens()"
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

from gtm_core.diagrams import render as R
from gtm_core.diagrams.ir import DiagramEdge, DiagramGroup, DiagramIR, DiagramNode

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "animation_ready"

DARK_KIT: dict[str, object] = {
    "design_system": {"archetype": "mission-control"},
    "palette": {
        "canvas": "#06101C",
        "surface": "#0A182A",
        "ink": "#F4F6FB",
        "primary": "#3464FD",
        "accent": "#72F5FA",
        "rule": "#1B2A3E",
        "ink_muted": "#9AA7BD",
    },
    "typography": {"display": "Example Sans", "mono": "Example Mono"},
}

LIGHT_KIT: dict[str, object] = {
    "palette": {
        "canvas_light": "#FAFAF7",
        "surface_light": "#FFFFFF",
        "ink_light": "#1A1C24",
        "primary": "#1F4FD1",
        "accent": "#E0592A",
        "secondary": "#3B4256",
        "rule_light": "#DDE1E8",
    },
}

ALL_KINDS = [
    "external",
    "client",
    "stream",
    "security",
    "datastore",
    "store",
    "target",
    "model",
    "ai",
    "gateway",
    "surface",
    "step",
    "focal",
    "backend",
    "",
]


def _ir_all_kinds() -> DiagramIR:
    nodes = [
        DiagramNode(
            id=f"n{i}",
            label=f"Node {i} <&>",
            sublabel=("svc:80" if i % 2 else ""),
            kind=k,
        )
        for i, k in enumerate(ALL_KINDS)
    ]
    edges = [
        DiagramEdge(source="n0", target="n1", label="calls"),
        DiagramEdge(source="n1", target="n2", kind="accent", label="emits\nevents"),
        DiagramEdge(source="n2", target="n3", kind="primary"),
        DiagramEdge(source="n3", target="n4", kind="link", label="ref"),
        DiagramEdge(source="n12", target="n0", kind="primary", label="back"),
    ]
    groups = [
        DiagramGroup(id="g_core", label="Core zone", x=20, y=30, width=900, height=300),
        DiagramGroup(id="g_edge", label="", x=2, y=4, width=50, height=20),
    ]
    return DiagramIR(
        title="All [kinds]",
        description="Every node kind",
        slug="all kinds!",
        nodes=nodes,
        edges=edges,
        groups=groups,
    )


def _ir_positioned() -> DiagramIR:
    nodes = [
        DiagramNode(id="api", label="API", sublabel="edge", kind="gateway", x=41, y=62, width=150),
        DiagramNode(id="db", label="DB", kind="store", x=402, y=301, width=118, height=50),
        DiagramNode(id="q", label="Queue", kind="stream", x=403, y=58, width=140, height=90),
        DiagramNode(id="ui", label="UI", kind="focal", x=40, y=300),
    ]
    edges = [
        DiagramEdge(source="api", target="db", attach_source="bottom", attach_target="top"),
        DiagramEdge(source="api", target="q", label="enqueue", kind="accent"),
        DiagramEdge(source="ui", target="api", attach_source="top", attach_target="bottom"),
        DiagramEdge(source="q", target="db", attach_source="left", attach_target="right"),
        DiagramEdge(source="api", target="ghost", label="dropped"),
    ]
    groups = [DiagramGroup(id="g1", label="Backend", x=380, y=40, width=200, height=340)]
    return DiagramIR(
        title="Positioned",
        description="Fixed layout",
        slug="positioned",
        direction="TD",
        nodes=nodes,
        edges=edges,
        groups=groups,
    )


def _ir_td_auto() -> DiagramIR:
    return DiagramIR(
        title="Top down",
        description="Auto layout",
        slug="td",
        direction="TD",
        nodes=[DiagramNode(id=f"s{i}", label=f"Step {i}", kind="step") for i in range(4)],
        edges=[DiagramEdge(source=f"s{i}", target=f"s{i + 1}") for i in range(3)],
    )


# (golden name, IR factory, profiles_root argument)
CASES = [
    ("all_kinds_default", _ir_all_kinds, None),
    ("all_kinds_dark", _ir_all_kinds, DARK_KIT),
    ("all_kinds_light_kit", _ir_all_kinds, LIGHT_KIT),
    ("positioned_default", _ir_positioned, None),
    ("positioned_dark", _ir_positioned, DARK_KIT),
    ("td_auto_default", _ir_td_auto, None),
]


def write_goldens() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, factory, kit in CASES:
        (GOLDEN_DIR / f"{name}.svg").write_text(R.render_svg(factory(), kit), encoding="utf-8")


# --------------------------------------------------------------------------- default output


@pytest.mark.parametrize(("name", "factory", "kit"), CASES, ids=[c[0] for c in CASES])
def test_default_output_is_byte_identical_to_golden(name, factory, kit):
    golden = (GOLDEN_DIR / f"{name}.svg").read_text(encoding="utf-8")
    assert R.render_svg(factory(), kit) == golden
    assert R.render_svg(factory(), kit, animation_ready=False) == golden


def test_default_still_drops_missing_endpoint_edge():
    svg = R.render_svg(_ir_positioned())
    assert "ghost" not in svg


# --------------------------------------------------------------------------- animation_ready

KITS = [("default", None), ("dark", DARK_KIT), ("light_kit", LIGHT_KIT)]
HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
RGBA_RE = re.compile(r"rgba?\(")


def _valid_ir(factory):
    ir = factory()
    # animation_ready refuses a missing endpoint; strip it from the corpus.
    ids = {n.id for n in ir.nodes}
    ir.edges = [e for e in ir.edges if e.source in ids and e.target in ids]
    return ir


def _ready(ir, kit=None) -> str:
    return R.render_svg(ir, kit, animation_ready=True)


@pytest.mark.parametrize(("kname", "kit"), KITS, ids=[k[0] for k in KITS])
@pytest.mark.parametrize("factory", [_ir_all_kinds, _ir_positioned, _ir_td_auto])
def test_no_literal_colours(kname, kit, factory):
    svg = _ready(_valid_ir(factory), kit)
    assert HEX_RE.findall(svg) == []
    assert RGBA_RE.findall(svg) == []
    assert "var(--" in svg


@pytest.mark.parametrize("factory", [_ir_all_kinds, _ir_positioned, _ir_td_auto])
def test_no_xmlns_and_root_hint(factory):
    svg = _ready(_valid_ir(factory))
    assert "xmlns" not in svg
    root = ET.fromstring(svg)
    assert root.tag == "svg"
    assert root.get("data-gtm-diagram") == "1"
    assert "data-gtm-diagram" not in R.render_svg(_valid_ir(factory))


@pytest.mark.parametrize(("kname", "kit"), KITS, ids=[k[0] for k in KITS])
@pytest.mark.parametrize("factory", [_ir_all_kinds, _ir_positioned, _ir_td_auto])
def test_every_element_wrapped_exactly_once(kname, kit, factory):
    ir = _valid_ir(factory)
    root = ET.fromstring(_ready(ir, kit))

    node_gs = [g for g in root.iter("g") if g.get("data-node-id") is not None]
    edge_gs = [g for g in root.iter("g") if g.get("data-edge") is not None]
    group_gs = [g for g in root.iter("g") if g.get("data-group-id") is not None]

    assert sorted(g.get("data-node-id") for g in node_gs) == sorted(n.id for n in ir.nodes)
    assert sorted(g.get("data-edge") for g in edge_gs) == sorted(
        f"{e.source}->{e.target}" for e in ir.edges
    )
    assert sorted(g.get("data-group-id") for g in group_gs) == sorted(g.id for g in ir.groups)

    # The id lives on the <g> only, never repeated on an inner element.
    for attr in ("data-node-id", "data-edge", "data-group-id"):
        holders = [el for el in root.iter() if el.get(attr) is not None]
        assert all(el.tag == "g" for el in holders)

    # Wrappers are top-level children of <svg> and never nested in each other.
    top = set(map(id, list(root)))
    for g in node_gs + edge_gs + group_gs:
        assert id(g) in top

    by_id = {g.get("data-node-id"): g for g in node_gs}
    for n in ir.nodes:
        g = by_id[n.id]
        tags = [c.tag for c in g]
        assert tags[0] == "rect"
        texts = [c for c in g if c.tag == "text"]
        assert any((t.text or "") == n.label for t in texts)
        if n.sublabel:
            assert any((t.text or "") == n.sublabel for t in texts)

    for g in edge_gs:
        assert [c.tag for c in g][0] == "path"
        e = next(e for e in ir.edges if f"{e.source}->{e.target}" == g.get("data-edge"))
        if e.label:
            assert [c.tag for c in g] == ["path", "rect", "text"]
        else:
            assert [c.tag for c in g] == ["path"]

    grp_by_id = {g.get("data-group-id"): g for g in group_gs}
    for grp in ir.groups:
        tags = [c.tag for c in grp_by_id[grp.id]]
        assert tags == (["rect", "rect", "text"] if grp.label else ["rect"])


def test_same_geometry_as_default():
    """Wrapping changes structure and colour only, never coordinates or text."""
    default = R.render_svg(_valid_ir(_ir_positioned))
    ready = _ready(_valid_ir(_ir_positioned))
    num = re.compile(r'\b(?:x|y|width|height|d|viewBox)="([^"]*)"')
    assert num.findall(default) == num.findall(ready)
    txt = re.compile(r">([^<]+)</text>")
    assert txt.findall(default) == txt.findall(ready)


@pytest.mark.parametrize("factory", [_ir_all_kinds, _ir_positioned, _ir_td_auto])
def test_caller_ir_not_mutated(factory):
    ir = _valid_ir(factory)
    before = ir.to_dict()
    _ready(ir, DARK_KIT)
    assert ir.to_dict() == before


# --------------------------------------------------------------------------- id validation

BAD_IDS = [
    "a b",
    "a'b",
    'a"b',
    "</script>",
    "a->b",
    "`",
    "${x}",
    "a\nb",
    "a\x00b",
    "x" * 10000,
    "x" * 129,
    "",
]


def _two_nodes(a="a", b="b") -> DiagramIR:
    return DiagramIR(
        nodes=[DiagramNode(id=a, label="A"), DiagramNode(id=b, label="B")],
        edges=[DiagramEdge(source=a, target=b)],
    )


@pytest.mark.parametrize("bad", BAD_IDS, ids=lambda s: repr(s)[:24])
def test_malformed_node_id_refused(bad):
    with pytest.raises(R.DiagramValidationError):
        _ready(_two_nodes(a=bad))


@pytest.mark.parametrize("bad", BAD_IDS, ids=lambda s: repr(s)[:24])
def test_malformed_group_id_refused(bad):
    ir = _two_nodes()
    ir.groups = [DiagramGroup(id=bad, label="G", width=200, height=100)]
    with pytest.raises(R.DiagramValidationError):
        _ready(ir)


def test_error_names_offending_id_and_is_value_error():
    with pytest.raises(ValueError, match="bad id"):
        _ready(_two_nodes(a="bad id"))
    assert issubclass(R.DiagramValidationError, ValueError)


def test_max_length_id_accepted():
    _ready(_two_nodes(a="x" * 128))


def test_duplicate_node_ids_refused():
    ir = _two_nodes()
    ir.nodes.append(DiagramNode(id="a", label="again"))
    with pytest.raises(R.DiagramValidationError, match="'a'"):
        _ready(ir)


def test_node_and_group_sharing_id_refused():
    ir = _two_nodes()
    ir.groups = [DiagramGroup(id="b", label="G", width=200, height=100)]
    with pytest.raises(R.DiagramValidationError, match="'b'"):
        _ready(ir)


def test_duplicate_edge_refused():
    ir = _two_nodes()
    ir.edges.append(DiagramEdge(source="a", target="b", label="again"))
    with pytest.raises(R.DiagramValidationError, match="a->b"):
        _ready(ir)


def test_missing_endpoint_refused():
    ir = _two_nodes()
    ir.edges.append(DiagramEdge(source="a", target="ghost"))
    with pytest.raises(R.DiagramValidationError, match="ghost"):
        _ready(ir)


def test_reverse_edge_is_not_a_duplicate():
    ir = _two_nodes()
    ir.edges.append(DiagramEdge(source="b", target="a"))
    root = ET.fromstring(_ready(ir))
    assert sorted(g.get("data-edge") for g in root.iter("g") if g.get("data-edge")) == [
        "a->b",
        "b->a",
    ]


def test_ids_differing_only_by_case_accepted():
    root = ET.fromstring(_ready(_two_nodes(a="Node", b="node")))
    assert sorted(g.get("data-node-id") for g in root.iter("g") if g.get("data-node-id")) == [
        "Node",
        "node",
    ]


def test_allowed_punctuation_accepted():
    root = ET.fromstring(_ready(_two_nodes(a="svc.v1:edge-a_0", b="B")))
    assert root.find("g[@data-node-id='svc.v1:edge-a_0']") is not None


def test_validation_does_not_run_by_default():
    # Default output keeps today's permissive behaviour.
    R.render_svg(_two_nodes(a="a b"))


def test_exported_from_package():
    from gtm_core.diagrams import DiagramValidationError

    assert DiagramValidationError is R.DiagramValidationError


def test_group_label_with_xml_special_characters_parses():
    ir = _two_nodes()
    ir.groups = [
        DiagramGroup(id="g_rnd", label="R&D <Core> 'Zone'", x=10, y=10, width=200, height=200)
    ]
    root = ET.fromstring(_ready(ir))
    text_el = root.find(".//text[@letter-spacing='0.08em']")
    assert text_el is not None
    assert text_el.text == "R&D <CORE> 'ZONE'"
