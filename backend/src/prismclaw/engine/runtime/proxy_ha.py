"""P4-NET-01: Proxy 3-replica HA.

Manages multiple proxy replicas with health checking and VIP failover.
When the active (VIP-holding) proxy becomes unhealthy, the cluster
automatically elects a new leader and reassigns the VIP.

VIP management is abstracted behind the ``VipManager`` protocol so the
same ProxyCluster can operate under Docker Swarm or Kubernetes.
Concrete implementations are skeleton stubs that log what they *would*
do, since actual infrastructure may not be available in test/dev.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Data classes ───────────────────────────────────────────────────


@dataclass
class ProxyNode:
    """A single proxy replica."""

    host: str
    port: int
    status: str = "healthy"  # healthy | unhealthy | unknown
    last_health_check: float = 0.0

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"


@dataclass
class FailoverEvent:
    """Record of a VIP failover occurrence."""

    timestamp: float
    from_node: ProxyNode
    to_node: ProxyNode
    reason: str


# ── VIP Manager protocol + skeleton implementations ───────────────


@runtime_checkable
class VipManager(Protocol):
    """Protocol for VIP (Virtual IP) management.

    Implementations handle the infrastructure-specific mechanics of
    assigning / releasing a virtual IP address to a given proxy node.
    """

    async def assign_vip(self, node: ProxyNode) -> None: ...
    async def release_vip(self, node: ProxyNode) -> None: ...


class DockerVipManager:
    """Skeleton VIP manager for Docker Swarm.

    In a real Swarm deployment this would invoke ``docker service update``
    or manipulate Swarm's VIP routing mesh.  Here it only logs.
    """

    async def assign_vip(self, node: ProxyNode) -> None:
        logger.info(
            "DockerVipManager: would assign VIP to %s (Swarm service update)",
            node.address,
        )

    async def release_vip(self, node: ProxyNode) -> None:
        logger.info(
            "DockerVipManager: would release VIP from %s (Swarm service update)",
            node.address,
        )


class K8sServiceVipManager:
    """Skeleton VIP manager for Kubernetes Service load balancer.

    In a real K8s deployment this would patch a Service resource or
    update Endpoints to direct traffic to the new node.  Here it only logs.
    """

    async def assign_vip(self, node: ProxyNode) -> None:
        logger.info(
            "K8sServiceVipManager: would assign VIP to %s (patch Service/Endpoints)",
            node.address,
        )

    async def release_vip(self, node: ProxyNode) -> None:
        logger.info(
            "K8sServiceVipManager: would release VIP from %s (patch Service/Endpoints)",
            node.address,
        )


# ── ProxyCluster ──────────────────────────────────────────────────


class ProxyCluster:
    """Manages multiple proxy replicas with health checking and VIP failover.

    Exactly one replica holds the VIP at any time (the *leader*).  A
    background health monitor periodically probes every replica; if the
    leader becomes unhealthy a failover is triggered: a new leader is
    elected from the remaining healthy replicas, the VIP is migrated,
    and a ``FailoverEvent`` is recorded for audit.
    """

    def __init__(
        self,
        *,
        replicas: list[ProxyNode],
        health_check_interval: float = 5.0,
        failover_timeout: float = 10.0,
        vip_manager: VipManager | None = None,
    ) -> None:
        if not replicas:
            raise ValueError("At least one replica is required")
        self._replicas = list(replicas)
        self._health_check_interval = health_check_interval
        self._failover_timeout = failover_timeout
        self._vip_manager: VipManager = vip_manager or DockerVipManager()

        # Leader is the first replica by default
        self._leader: ProxyNode = self._replicas[0]

        # Audit trail
        self._failover_history: list[FailoverEvent] = []

        # Health monitor task handle
        self._monitor_task: asyncio.Task | None = None
        self._running = False

        # Round-robin election cursor
        self._election_cursor = 0

    # ── Public API ────────────────────────────────────────────────

    @property
    def replicas(self) -> list[ProxyNode]:
        return list(self._replicas)

    @property
    def leader(self) -> ProxyNode:
        return self._leader

    @property
    def failover_history(self) -> list[FailoverEvent]:
        return list(self._failover_history)

    def get_active_proxy(self) -> ProxyNode:
        """Return the current VIP-holding (leader) node."""
        return self._leader

    def start_health_monitor(self) -> asyncio.Task:
        """Start the background health-check loop as an asyncio Task."""
        if self._monitor_task is not None and not self._monitor_task.done():
            return self._monitor_task
        self._running = True
        self._monitor_task = asyncio.create_task(self._health_monitor_loop())
        return self._monitor_task

    async def stop_health_monitor(self) -> None:
        """Stop the background health-check loop."""
        self._running = False
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def check_node_health(self, node: ProxyNode) -> bool:
        """HTTP GET /health to *node*.

        Returns ``True`` if the node responds with a 2xx status,
        ``False`` otherwise.  On any connection error the node is
        considered unhealthy.
        """
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    node.health_url,
                    timeout=aiohttp.ClientTimeout(total=self._failover_timeout),
                ) as resp:
                    node.last_health_check = time.time()
                    healthy = 200 <= resp.status < 300
                    node.status = "healthy" if healthy else "unhealthy"
                    return healthy
        except Exception:
            node.last_health_check = time.time()
            node.status = "unhealthy"
            logger.warning("Health check failed for %s", node.address)
            return False

    async def trigger_failover(self, dead_node: ProxyNode) -> FailoverEvent:
        """Elect a new leader and migrate the VIP away from *dead_node*.

        Returns the recorded ``FailoverEvent``.
        """
        new_leader = self.elect_leader()
        if new_leader is None:
            raise RuntimeError(
                f"Cannot failover: no healthy replicas available "
                f"(dead_node={dead_node.address})"
            )

        event = FailoverEvent(
            timestamp=time.time(),
            from_node=dead_node,
            to_node=new_leader,
            reason=f"node {dead_node.address} unhealthy",
        )

        # Migrate VIP
        await self._vip_manager.release_vip(dead_node)
        await self._vip_manager.assign_vip(new_leader)

        self._leader = new_leader
        self.record_failover(event)

        logger.warning(
            "Failover: VIP moved from %s to %s (reason: %s)",
            dead_node.address,
            new_leader.address,
            event.reason,
        )
        return event

    def elect_leader(self) -> ProxyNode | None:
        """Simple round-robin election among healthy nodes.

        Returns ``None`` when no healthy replica exists.
        """
        healthy = [r for r in self._replicas if r.status == "healthy"]
        if not healthy:
            return None

        # Round-robin: advance cursor and pick
        self._election_cursor = self._election_cursor % len(healthy)
        chosen = healthy[self._election_cursor]
        self._election_cursor = (self._election_cursor + 1) % len(healthy)
        return chosen

    def record_failover(self, event: FailoverEvent) -> None:
        """Append a failover event to the audit history."""
        self._failover_history.append(event)
        logger.info(
            "Failover recorded: %s → %s at %.3f",
            event.from_node.address,
            event.to_node.address,
            event.timestamp,
        )

    # ── Internal ──────────────────────────────────────────────────

    async def _health_monitor_loop(self) -> None:
        """Periodically health-check all replicas and trigger failover
        when the leader dies.
        """
        while self._running:
            try:
                for node in self._replicas:
                    await self.check_node_health(node)

                # If leader is unhealthy, failover
                if self._leader.status != "healthy":
                    try:
                        await self.trigger_failover(self._leader)
                    except RuntimeError:
                        logger.error(
                            "Health monitor: leader %s is dead and no healthy "
                            "replica available for failover",
                            self._leader.address,
                        )
            except Exception:
                logger.exception("Health monitor iteration error")

            await asyncio.sleep(self._health_check_interval)


# ── Stress Test Fixture ────────────────────────────────────────────


@dataclass
class StressTestMetrics:
    """Metrics collected by ``StressTestFixture``."""

    total_workspaces: int = 0
    successful: int = 0
    failed: int = 0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    throughput_per_sec: float = 0.0
    total_duration_sec: float = 0.0


class StressTestFixture:
    """Simulate concurrent workspace creation and report throughput / latency.

    Designed for the 1000-workspace concurrent fixture: ramp up workspace
    creation over *ramp_up_seconds* and collect metrics.

    NOTE: This uses a stub ``WorkspaceContainerManager`` (or the real one
    if available) to simulate workspace creation.  In CI / unit-test
    environments the backend is a no-op so metrics reflect orchestration
    overhead only.
    """

    def __init__(
        self,
        manager: object | None = None,
    ) -> None:
        # Import lazily so the module is importable even if the full
        # container stack is not available.
        if manager is not None:
            self._manager = manager
        else:
            try:
                from prismclaw.engine.runtime.container_manager import WorkspaceContainerManager
                self._manager = WorkspaceContainerManager()
            except Exception:
                self._manager = None

    async def run_concurrent_workspaces(
        self,
        count: int = 1000,
        ramp_up_seconds: float = 30.0,
    ) -> StressTestMetrics:
        """Simulate *count* concurrent workspace creations.

        Workspaces are launched in batches to ramp up over
        *ramp_up_seconds* rather than all at once.
        """
        import random

        start = time.time()
        latencies: list[float] = []
        successful = 0
        failed = 0

        # Compute batch schedule
        batches = max(1, int(ramp_up_seconds))
        per_batch = max(1, count // batches)
        remaining = count

        async def _create_one(ws_id: str) -> None:
            nonlocal successful, failed
            t0 = time.time()
            try:
                if self._manager is not None and hasattr(self._manager, "create_workspace"):
                    from prismclaw.engine.security.gate.policies import WorkspaceTag, DataResidency
                    tag = WorkspaceTag(workspace_id=ws_id, owner_id="system", data_residency=DataResidency.CN)
                    self._manager.create_workspace(ws_id, tag)
                else:
                    # No manager available — simulate with a tiny sleep
                    await asyncio.sleep(random.uniform(0.001, 0.01))
                successful += 1
            except Exception:
                failed += 1
            elapsed_ms = (time.time() - t0) * 1000.0
            latencies.append(elapsed_ms)

        tasks: list[asyncio.Task] = []
        for batch_idx in range(batches):
            batch_size = min(per_batch, remaining)
            for i in range(batch_size):
                ws_id = f"stress-ws-{batch_idx}-{i}"
                tasks.append(asyncio.create_task(_create_one(ws_id)))
            remaining -= batch_size
            if remaining <= 0:
                break
            # Ramp-up delay between batches
            await asyncio.sleep(ramp_up_seconds / batches)

        # Wait for all tasks to finish
        await asyncio.gather(*tasks, return_exceptions=True)

        total_duration = time.time() - start

        if not latencies:
            return StressTestMetrics(
                total_workspaces=count,
                total_duration_sec=total_duration,
            )

        latencies.sort()
        metrics = StressTestMetrics(
            total_workspaces=count,
            successful=successful,
            failed=failed,
            min_latency_ms=latencies[0],
            max_latency_ms=latencies[-1],
            avg_latency_ms=sum(latencies) / len(latencies),
            p50_latency_ms=latencies[int(len(latencies) * 0.50)],
            p99_latency_ms=latencies[min(int(len(latencies) * 0.99), len(latencies) - 1)],
            throughput_per_sec=successful / total_duration if total_duration > 0 else 0.0,
            total_duration_sec=total_duration,
        )
        return metrics
