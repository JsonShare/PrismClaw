"""Tests for P4-NET-01: Proxy 3-replica HA."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from prismclaw.engine.runtime.proxy_ha import (
    DockerVipManager,
    FailoverEvent,
    K8sServiceVipManager,
    ProxyCluster,
    ProxyNode,
    VipManager,
)


# ── Helpers ────────────────────────────────────────────────────────


def _make_nodes(n: int = 3, *, status: str = "healthy") -> list[ProxyNode]:
    return [
        ProxyNode(host="10.0.0", port=8080 + i, status=status)
        for i in range(n)
    ]


def _cluster(nodes: list[ProxyNode] | None = None, **kw) -> ProxyCluster:
    if nodes is None:
        nodes = _make_nodes()
    return ProxyCluster(replicas=nodes, **kw)


# ── test_failover_on_node_death ────────────────────────────────────


@pytest.mark.asyncio
async def test_failover_on_node_death():
    """Kill one node (the leader), verify failover triggers and a new
    leader is elected."""
    nodes = _make_nodes()
    cluster = _cluster(nodes)

    # Leader is nodes[0] by default
    assert cluster.get_active_proxy() is nodes[0]

    # Simulate leader death
    nodes[0].status = "unhealthy"

    # Mock VIP manager so we don't need real infrastructure
    cluster._vip_manager = AsyncMock(spec=VipManager)

    event = await cluster.trigger_failover(nodes[0])

    # Failover event recorded
    assert isinstance(event, FailoverEvent)
    assert event.from_node is nodes[0]
    assert event.to_node.status == "healthy"
    assert event.to_node is not nodes[0]

    # VIP migrated
    cluster._vip_manager.release_vip.assert_awaited_once_with(nodes[0])
    cluster._vip_manager.assign_vip.assert_awaited_once()

    # Leader changed
    assert cluster.get_active_proxy() is not nodes[0]
    assert cluster.get_active_proxy().status == "healthy"

    # Audit trail
    assert len(cluster.failover_history) == 1
    assert cluster.failover_history[0] is event


# ── test_election_priority ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_election_priority():
    """Leader election picks a healthy node and skips unhealthy ones."""
    nodes = _make_nodes()
    nodes[0].status = "unhealthy"
    nodes[1].status = "healthy"
    nodes[2].status = "healthy"

    cluster = _cluster(nodes)

    leader = cluster.elect_leader()
    assert leader is not None
    assert leader.status == "healthy"
    assert leader is not nodes[0]

    # Round-robin: second call should return a different healthy node
    leader2 = cluster.elect_leader()
    assert leader2 is not None
    assert leader2.status == "healthy"


# ── test_no_healthy_nodes ──────────────────────────────────────────


def test_no_healthy_nodes():
    """When all nodes are down, elect_leader returns None."""
    nodes = _make_nodes(status="unhealthy")
    cluster = _cluster(nodes)

    assert cluster.elect_leader() is None


@pytest.mark.asyncio
async def test_failover_raises_when_no_healthy():
    """trigger_failover raises RuntimeError when no healthy replica exists."""
    nodes = _make_nodes(status="unhealthy")
    cluster = _cluster(nodes)

    with pytest.raises(RuntimeError, match="no healthy replicas"):
        await cluster.trigger_failover(nodes[0])


# ── VipManager protocol compliance ─────────────────────────────────


@pytest.mark.asyncio
async def test_docker_vip_manager_is_vip_manager():
    mgr = DockerVipManager()
    assert isinstance(mgr, VipManager)
    # Should not raise
    node = ProxyNode(host="10.0.0.1", port=8080)
    await mgr.assign_vip(node)
    await mgr.release_vip(node)


@pytest.mark.asyncio
async def test_k8s_vip_manager_is_vip_manager():
    mgr = K8sServiceVipManager()
    assert isinstance(mgr, VipManager)
    node = ProxyNode(host="10.0.0.1", port=8080)
    await mgr.assign_vip(node)
    await mgr.release_vip(node)
