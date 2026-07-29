"""Graph-shaped stage model for the pipeline runner (E-1).

The linear ``STAGES`` tuple in :mod:`agent.pipeline` is a degenerate 1-path DAG. This
module generalizes that to a real graph — nodes with ``depends_on`` edges — while
keeping the linear pipeline's behavior identical via :data:`DEFAULT_GRAPH`.

Deliberately minimal for E-1: a node is just an id + its dependency ids. Per-node
``model_role``/``gate`` metadata stays in ``agent.pipeline_executor`` until E-2 (pack
graphs), where a node gains real per-pack metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    """One unit of work in a pipeline graph."""

    id: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Graph:
    """An immutable, validated DAG of :class:`Node`.

    Validates at construction (fail fast, not at first use):
      - every ``depends_on`` id must reference a node id declared in this graph
      - no cycles (DFS-based)

    Iteration order is declaration order — the order ``nodes`` was given in — so
    resume/frontier computation is deterministic across runs.
    """

    nodes: tuple[Node, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            dupes = {i for i in ids if ids.count(i) > 1}
            raise ValueError(f"Graph has duplicate node ids: {sorted(dupes)}")

        known = set(ids)
        for n in self.nodes:
            unknown = [d for d in n.depends_on if d not in known]
            if unknown:
                raise ValueError(f"Node {n.id!r} depends_on unknown node(s): {unknown}")

        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        by_id = {n.id: n for n in self.nodes}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(by_id, WHITE)

        def visit(node_id: str, path: list[str]) -> None:
            color[node_id] = GRAY
            for dep in by_id[node_id].depends_on:
                if color[dep] == GRAY:
                    cycle = [*path, node_id, dep]
                    raise ValueError(f"Graph has a cycle: {' -> '.join(cycle)}")
                if color[dep] == WHITE:
                    visit(dep, [*path, node_id])
            color[node_id] = BLACK

        for node_id in by_id:
            if color[node_id] == WHITE:
                visit(node_id, [])

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(n.id for n in self.nodes)

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


def linear_graph(stage_names) -> Graph:
    """Build a straight-line chain graph from an ordered sequence of stage names."""
    names = list(stage_names)
    nodes = tuple(
        Node(id=name, depends_on=(names[i - 1],) if i > 0 else ()) for i, name in enumerate(names)
    )
    return Graph(nodes=nodes)
