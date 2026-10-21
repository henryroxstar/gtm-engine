"""Helpers for ``render_svg(..., animation_ready=True)``.

That option produces markup for inline embedding in an HTML page whose elements are
addressed one by one:

* every node, edge and group is wrapped in exactly one top-level ``<g>`` carrying
  ``data-node-id``, ``data-edge="SRC->TGT"`` or ``data-group-id``; the attribute lives on the
  ``<g>`` only (it is removed from the inner rect/path);
* no literal colours: every fill/stroke is ``var(--brand-*)`` / ``var(--color-rule)`` in a
  ``style`` attribute, resolved by the host page (see ``ROLE_VARS`` / ``KIND_ROLES``);
* no ``xmlns`` attribute (inline HTML embedding supplies the namespace);
* ids are validated first (``DiagramValidationError``): node/group ids must match
  ``^[A-Za-z0-9_.:-]+$``, be at most 128 characters and be unique across nodes and groups;
  duplicate ``(source, target)`` edges and edges whose endpoint is not a node are refused
  (the default renderer silently drops the latter);
* the caller's IR is never mutated (layout runs on a deep copy).

With the default ``animation_ready=False`` the renderer's output is unchanged, byte for byte.
"""

from __future__ import annotations

import copy
import re

from .ir import DiagramIR, DiagramNode


class DiagramValidationError(ValueError):
    """Raised when an IR cannot be rendered with ``animation_ready=True``.

    The message names the offending id (truncated for very long ids).
    """


# Addressable ids: ASCII letters, digits and ``_ . : -``; 1 to 128 characters. The cap keeps
# an id usable as a lookup key and an attribute value without unbounded growth; ``-`` and
# ``>`` together never form part of a node id, so ``SRC->TGT`` in ``data-edge`` is unambiguous.
ID_MAX_LEN = 128
ID_RE = re.compile(rf"[A-Za-z0-9_.:-]{{1,{ID_MAX_LEN}}}")


def _id_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 64 else f"{text[:60]}...' ({len(str(value))} chars)"


def validate_ids(ir: DiagramIR) -> None:
    """Refuse malformed or colliding ids, duplicate edges and edges with a missing endpoint."""
    seen: set[str] = set()
    for what, items in (("node", ir.nodes), ("group", ir.groups)):
        for item in items:
            ident = item.id
            if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
                raise DiagramValidationError(
                    f"invalid {what} id {_id_repr(ident)}: must match "
                    f"^[A-Za-z0-9_.:-]+$ and be at most {ID_MAX_LEN} characters"
                )
            if ident in seen:
                raise DiagramValidationError(f"duplicate id {ident!r} ({what})")
            seen.add(ident)
    node_ids = {n.id for n in ir.nodes}
    edge_keys: set[tuple[str, str]] = set()
    for edge in ir.edges:
        for end in (edge.source, edge.target):
            if end not in node_ids:
                raise DiagramValidationError(
                    f"edge endpoint {_id_repr(end)} is not a node id "
                    f"(edge {_id_repr(edge.source)} -> {_id_repr(edge.target)})"
                )
        key = (edge.source, edge.target)
        if key in edge_keys:
            raise DiagramValidationError(f"duplicate edge '{edge.source}->{edge.target}'")
        edge_keys.add(key)


def prepare(ir: DiagramIR) -> DiagramIR:
    """Validate ``ir`` and return a deep copy, so layout never moves the caller's objects."""
    validate_ids(ir)
    return copy.deepcopy(ir)


# Colour output: every colour is a CSS custom property supplied by the host HTML page (the
# brand variables compiled by gtm_core.brandkit_css), never a literal. A paint is (variable
# name, opacity or None); translucent fills use fill-opacity instead of rgba(). Presentation
# attributes do not accept var(), so paints are emitted in a style attribute.
Paint = tuple[str, float | None]

ROLE_VARS: dict[str, str] = {
    "paper": "--brand-canvas",
    "surface": "--brand-surface",
    "ink": "--brand-text",
    "primary": "--brand-primary",
    "accent": "--brand-accent",
    "muted": "--brand-text-muted",
    "soft": "--brand-text-dim",
    "rule": "--color-rule",
    "link": "--brand-primary",
    "secondary": "--brand-secondary",
    "warn": "--brand-warn",
    "good": "--brand-good",
}

# Node kind -> theme role for its stroke, chip and chip text (light and dark alike; the host
# page's variables carry the light/dark difference). Kinds not listed use the "soft" role.
KIND_ROLES: dict[str, str] = {
    "external": "secondary",
    "client": "secondary",
    "stream": "accent",
    "security": "accent",
    "datastore": "warn",
    "store": "warn",
    "target": "warn",
    "model": "good",
    "ai": "good",
    "gateway": "primary",
    "surface": "primary",
    "step": "primary",
}


def role(name: str, opacity: float | None = None) -> Paint:
    return (ROLE_VARS[name], opacity)


def paint(*props: tuple[str, str | Paint]) -> str:
    """Emit colour attributes.

    Plain strings (the default renderer's colours) become presentation attributes exactly as
    before; any paint tuple switches the whole set to one ``style`` attribute of var() refs.
    """
    if all(isinstance(v, str) for _, v in props):
        return " ".join(f'{p}="{v}"' for p, v in props)
    decls: list[str] = []
    for prop, value in props:
        if isinstance(value, str):
            decls.append(f"{prop}:{value}")
            continue
        var_name, opacity = value
        decls.append(f"{prop}:var({var_name})")
        if opacity is not None:
            decls.append(f"{prop}-opacity:{opacity:g}")
    return f'style="{";".join(decls)}"'


def node_paints(node: DiagramNode) -> tuple[Paint | str, ...]:
    """Counterpart of render._get_node_colors: roles, not literals.

    Order: fill, stroke, chip fill, chip stroke, chip text, label text.
    """
    if node.kind == "focal":
        return (
            role("primary", 0.08),
            role("primary"),
            role("primary", 0.16),
            role("accent"),
            role("accent"),
            role("ink"),
        )
    kind_role = KIND_ROLES.get(node.kind)
    if kind_role:
        return (
            role("surface"),
            role(kind_role),
            role(kind_role, 0.14),
            role(kind_role),
            role(kind_role),
            role("ink"),
        )
    return (role("surface"), role("soft"), "none", role("ink"), role("ink"), role("ink"))


def group_paints() -> tuple[Paint, Paint, Paint]:
    """Group stroke, translucent fill and label-box fill."""
    return role("primary"), role("primary", 0.03), role("surface")


def edge_paints(kind: str) -> tuple[Paint, Paint, Paint, Paint]:
    """Edge stroke, then label-box fill, label-box stroke and label text."""
    stroke = role(kind if kind in ("accent", "primary") else "muted")
    return stroke, role("surface"), role("rule"), role("ink")


def svg_open(vb_w: int, vb_h: int, title_id: str, desc_id: str) -> str:
    """Root element: no xmlns (for inline HTML embedding).

    data-gtm-diagram is a hint for tests and tooling that this markup came from this
    renderer. It is trivially forgeable and is NOT a trust boundary.
    """
    return (
        f'<svg data-gtm-diagram="1" viewBox="0 0 {vb_w} {vb_h}" '
        f'role="img" aria-labelledby="{title_id} {desc_id}">'
    )


def header_fills() -> tuple[str, str, str, str, str]:
    """Fill attributes for the muted/accent/primary/link arrowheads and the background."""
    m_muted, m_accent, m_primary, m_link, bg = (
        paint(("fill", role(r))) for r in ("muted", "accent", "primary", "link", "paper")
    )
    return m_muted, m_accent, m_primary, m_link, bg


def wrap(parts: list[str], attr: str, escaped_value: str, ready: bool) -> list[str]:
    """Wrap one element's parts in ``<g attr="value">`` when ready; ``escaped_value`` is XML-safe."""
    if not ready:
        return parts
    return [f'  <g {attr}="{escaped_value}">', *parts, "  </g>"]
