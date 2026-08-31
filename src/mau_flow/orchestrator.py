"""Deterministic workflow orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .contracts import BaseHandoff
from .mau import MAU


class PipelineError(RuntimeError):
    pass


Route = Callable[[BaseHandoff], str | None]


@dataclass(frozen=True)
class Edge:
    target: str
    when: Callable[[BaseHandoff], bool] = lambda _handoff: True


class Pipeline:
    def __init__(self, *, max_node_visits: int = 100):
        if max_node_visits < 1:
            raise ValueError("max_node_visits must be at least 1")
        self.nodes: dict[str, MAU] = {}
        self.edges: dict[str, list[Edge]] = {}
        self.max_node_visits = max_node_visits

    def add_mau(self, mau: MAU) -> Pipeline:
        if mau.name in self.nodes:
            raise PipelineError(f"Duplicate MAU: {mau.name}")
        self.nodes[mau.name] = mau
        return self

    def add_edge(
        self,
        source: str,
        target: str,
        *,
        when: Callable[[BaseHandoff], bool] | None = None,
    ) -> Pipeline:
        self.edges.setdefault(source, []).append(Edge(target, when or (lambda _h: True)))
        return self

    def execute(
        self, initial_handoff: BaseHandoff, *, start: str, max_rounds_per_agent: int
    ) -> BaseHandoff:
        if max_rounds_per_agent < 1:
            raise ValueError("max_rounds_per_agent must be at least 1")
        self._check_graph(start)
        current: str | None = start
        handoff = initial_handoff
        visits = 0
        while current is not None:
            visits += 1
            if visits > self.max_node_visits:
                raise PipelineError("Pipeline exceeded max_node_visits (possible cycle)")
            handoff = self.nodes[current].run(handoff, max_rounds=max_rounds_per_agent)
            matches = [edge.target for edge in self.edges.get(current, []) if edge.when(handoff)]
            if len(matches) > 1:
                raise PipelineError(f"Ambiguous route from {current}: {matches}")
            current = matches[0] if matches else None
        return handoff

    def _check_graph(self, start: str) -> None:
        if start not in self.nodes:
            raise PipelineError(f"Unknown start node: {start}")
        missing = {
            edge.target
            for edges in self.edges.values()
            for edge in edges
            if edge.target not in self.nodes
        }
        missing.update(source for source in self.edges if source not in self.nodes)
        if missing:
            raise PipelineError(f"Edges reference unknown nodes: {sorted(missing)}")
