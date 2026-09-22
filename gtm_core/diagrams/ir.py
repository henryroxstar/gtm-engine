"""Intermediate Representation (IR) data models for diagrams."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiagramNode:
    """Represents a discrete component or node in a diagram."""

    id: str
    label: str
    sublabel: str = ""
    kind: str = "backend"  # focal, backend, store, external, input, optional, boundary
    shape: str = "box"  # box, cylinder, diamond, circle
    x: float = 0.0
    y: float = 0.0
    width: float = 120.0
    height: float = 80.0
    group: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "sublabel": self.sublabel,
            "kind": self.kind,
            "shape": self.shape,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "group": self.group,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagramNode:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", data.get("id", ""))),
            sublabel=str(data.get("sublabel", "")),
            kind=str(data.get("kind", "backend")),
            shape=str(data.get("shape", "box")),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            width=float(data.get("width", 120.0)),
            height=float(data.get("height", 80.0)),
            group=data.get("group"),
            tags=list(data.get("tags", [])),
        )


@dataclass
class DiagramEdge:
    """Represents a directed or annotated connection between two nodes."""

    source: str
    target: str
    label: str = ""
    kind: str = "default"  # default, accent, link, dashed
    style: str = "solid"  # solid, dashed, dotted
    attach_source: str = "right"  # top, right, bottom, left
    attach_target: str = "left"  # top, right, bottom, left

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "kind": self.kind,
            "style": self.style,
            "attach_source": self.attach_source,
            "attach_target": self.attach_target,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagramEdge:
        return cls(
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            label=str(data.get("label", "")),
            kind=str(data.get("kind", "default")),
            style=str(data.get("style", "solid")),
            attach_source=str(data.get("attach_source", "right")),
            attach_target=str(data.get("attach_target", "left")),
        )


@dataclass
class DiagramGroup:
    """Represents a container boundary, cluster, or swimlane."""

    id: str
    label: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagramGroup:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            width=float(data.get("width", 0.0)),
            height=float(data.get("height", 0.0)),
        )


@dataclass
class DiagramIR:
    """Top-level normalized Intermediate Representation of a schematic."""

    title: str = "Architecture Diagram"
    description: str = "System architecture diagram"
    slug: str = "architecture-diagram"
    visual_type: str = "architecture"
    direction: str = "LR"  # LR, TD / TB, RL, BT
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)
    groups: list[DiagramGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "slug": self.slug,
            "visual_type": self.visual_type,
            "direction": self.direction,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "groups": [g.to_dict() for g in self.groups],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagramIR:
        nodes = [DiagramNode.from_dict(n) for n in data.get("nodes", [])]
        edges = [DiagramEdge.from_dict(e) for e in data.get("edges", [])]
        groups = [DiagramGroup.from_dict(g) for g in data.get("groups", [])]
        return cls(
            title=str(data.get("title", "Architecture Diagram")),
            description=str(data.get("description", "System architecture diagram")),
            slug=str(data.get("slug", "architecture-diagram")),
            visual_type=str(data.get("visual_type", "architecture")),
            direction=str(data.get("direction", "LR")),
            nodes=nodes,
            edges=edges,
            groups=groups,
        )
