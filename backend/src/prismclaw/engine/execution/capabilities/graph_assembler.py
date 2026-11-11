from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass

from prismclaw.schema.agent.capability import (
    CapabilityAssemblerResult,
    CapabilityGraph,
    CapabilityKind,
    CapabilityNode,
    CapabilityStatus,
    ConflictDecision,
    RuntimeCapabilities,
)


@dataclass(slots=True)
class CapabilityAssembler:
    """Builds immutable run-scoped capability snapshots.

    P0 only freezes the graph boundary and deterministic runtime projections.
    Source resolution and policy enrichment can continue to evolve behind this
    interface as long as the resulting graph shape remains stable.
    """

    def build(
        self,
        *,
        run_id: str,
        source_fingerprint: str,
        nodes: list[CapabilityNode],
        conflict_decisions: list[ConflictDecision] | None = None,
        policy_fingerprint: str = "default",
    ) -> CapabilityAssemblerResult:
        frozen_nodes = self.freeze_graph(nodes)
        frozen_conflicts = tuple(conflict_decisions or ())
        graph = CapabilityGraph(
            graph_id=self._new_graph_id(run_id),
            run_id=run_id,
            source_fingerprint=source_fingerprint,
            nodes=frozen_nodes,
            conflict_decisions=frozen_conflicts,
            built_at_epoch_ms=int(time.time() * 1000),
            policy_fingerprint=policy_fingerprint,
        )
        return CapabilityAssemblerResult(graph=graph, runtime=self.derive_runtime_views(graph))

    def freeze_graph(self, nodes: list[CapabilityNode]) -> tuple[CapabilityNode, ...]:
        # Sorting by kind + canonical id makes runtime projections deterministic.
        return tuple(sorted(nodes, key=lambda item: (item.kind.value, item.canonical_id)))

    def derive_runtime_views(self, graph: CapabilityGraph) -> RuntimeCapabilities:
        enabled = graph.enabled_nodes()

        def _ids(kind: CapabilityKind) -> tuple[str, ...]:
            return tuple(node.canonical_id for node in enabled if node.kind == kind)

        return RuntimeCapabilities(
            graph_id=graph.graph_id,
            commands=_ids(CapabilityKind.COMMAND),
            tools=_ids(CapabilityKind.TOOL),
            agents=_ids(CapabilityKind.AGENT),
            hooks=_ids(CapabilityKind.HOOK),
            mcp_servers=_ids(CapabilityKind.MCP_SERVER),
            mcp_tools=_ids(CapabilityKind.MCP_TOOL),
            output_styles=_ids(CapabilityKind.OUTPUT_STYLE),
            skills=_ids(CapabilityKind.SKILL),
        )

    @staticmethod
    def make_enabled_node(
        *,
        canonical_id: str,
        kind: CapabilityKind,
        display_name: str,
        source_ref,
        version: str | None = None,
        priority: int = 0,
        metadata: dict[str, object] | None = None,
        projection_targets: tuple[str, ...] = (),
        policy_tags: tuple[str, ...] = (),
    ) -> CapabilityNode:
        def _sanitize_json(value: object):
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            if isinstance(value, list):
                return [_sanitize_json(item) for item in value]
            if isinstance(value, dict):
                return {str(k): _sanitize_json(v) for k, v in value.items()}
            raise TypeError(f"CapabilityNode.metadata must be JSON-compatible (got {type(value)!r})")

        safe_metadata = {str(key): _sanitize_json(value) for key, value in (metadata or {}).items()}
        return CapabilityNode(
            canonical_id=canonical_id,
            kind=kind,
            display_name=display_name,
            source_ref=source_ref,
            status=CapabilityStatus.ENABLED,
            version=version,
            priority=priority,
            policy_tags=policy_tags,
            projection_targets=projection_targets,
            metadata=safe_metadata,
        )

    @staticmethod
    def fingerprint(parts: list[str]) -> str:
        digest = hashlib.sha256()
        for part in sorted(parts):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _new_graph_id(run_id: str) -> str:
        return f"{run_id}:cg:{uuid.uuid4().hex[:12]}"
