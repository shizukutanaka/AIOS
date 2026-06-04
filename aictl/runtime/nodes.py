"""Node management: discovery, pairing, and cluster expansion.

Design: Start local (Quadlet/systemd), grow to cluster (K3s) when a
second node is paired. The API surface stays the same.

Modes:
  local   — single node, state in SQLite, services via Quadlet/Podman
  cluster — 2+ nodes, state in K3s etcd, services via K3s + KServe
"""

from __future__ import annotations

import json
import secrets
import socket
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from aictl.core.state import StateStore


@dataclass
class PeerNode:
    node_id: str
    hostname: str
    address: str          # IP or hostname
    port: int = 7700      # aiosd port
    role: str = "worker"  # leader | worker
    paired_at: float = 0.0
    last_seen: float = 0.0
    status: str = "pending"  # pending | active | offline


@dataclass
class ClusterState:
    mode: str = "local"           # local | cluster
    leader_id: str = ""
    join_token: str = ""
    peers: list[PeerNode] = field(default_factory=list)


class NodeManager:
    """Manage node pairing and cluster formation."""

    def __init__(self, store: StateStore):
        """Initialize node manager."""
        self.store = store
        self._cluster_path = store.dir / "cluster.json"

    def load_cluster(self) -> ClusterState:
        """Load cluster."""
        if not self._cluster_path.exists():
            return ClusterState()
        data = json.loads(self._cluster_path.read_text())
        cs = ClusterState(
            mode=data.get("mode", "local"),
            leader_id=data.get("leader_id", ""),
            join_token=data.get("join_token", ""),
        )
        for p in data.get("peers", []):
            cs.peers.append(PeerNode(**{k: v for k, v in p.items() if k in PeerNode.__dataclass_fields__}))
        return cs

    def save_cluster(self, cs: ClusterState) -> None:
        """Save cluster."""
        data = {
            "mode": cs.mode,
            "leader_id": cs.leader_id,
            "join_token": cs.join_token,
            "peers": [asdict(p) for p in cs.peers],
        }
        self._cluster_path.write_text(json.dumps(data, indent=2))

    def generate_join_token(self) -> str:
        """Generate a secure join token for node pairing."""
        node = self.store.load_node()
        cs = self.load_cluster()
        token = secrets.token_urlsafe(32)
        cs.join_token = token
        cs.leader_id = node.node_id
        cs.mode = "local"  # stays local until a peer actually joins
        self.save_cluster(cs)
        return token

    def pair(self, address: str, token: str) -> tuple[bool, str]:
        """Pair with a remote node.

        This node sends its info to the remote node's /v1/node/join endpoint.
        """
        node = self.store.load_node()
        local_addr = _get_local_ip()

        # Build join request
        join_data = {
            "node_id": node.node_id,
            "hostname": node.hostname,
            "address": local_addr,
            "port": 7700,
            "token": token,
        }

        # Send to remote
        import urllib.request
        import urllib.error
        url = f"http://{address}:7700/v1/node/join"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(join_data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("accepted"):
                    # Add remote as peer
                    cs = self.load_cluster()
                    peer = PeerNode(
                        node_id=result.get("node_id", ""),
                        hostname=result.get("hostname", address),
                        address=address,
                        role="leader",
                        paired_at=time.time(),
                        last_seen=time.time(),
                        status="active",
                    )
                    cs.peers = [p for p in cs.peers if p.address != address]
                    cs.peers.append(peer)
                    cs.mode = "cluster"
                    cs.leader_id = result.get("node_id", "")
                    self.save_cluster(cs)
                    return True, f"Paired with {address}"
                else:
                    return False, result.get("reason", "Rejected")
        except Exception as e:
            return False, f"Failed to reach {address}: {e}"

    def accept_join(self, join_data: dict[str, Any]) -> dict[str, Any]:
        """Accept a join request from a remote node."""
        cs = self.load_cluster()
        node = self.store.load_node()

        # Verify token
        if join_data.get("token") != cs.join_token:
            return {"accepted": False, "reason": "Invalid token"}

        # Add peer
        peer = PeerNode(
            node_id=join_data.get("node_id", ""),
            hostname=join_data.get("hostname", ""),
            address=join_data.get("address", ""),
            port=join_data.get("port", 7700),
            role="worker",
            paired_at=time.time(),
            last_seen=time.time(),
            status="active",
        )
        cs.peers = [p for p in cs.peers if p.address != peer.address]
        cs.peers.append(peer)
        cs.mode = "cluster"
        self.save_cluster(cs)

        return {
            "accepted": True,
            "node_id": node.node_id,
            "hostname": node.hostname,
        }

    def check_peers(self) -> list[PeerNode]:
        """Health-check all peers and update status."""
        cs = self.load_cluster()
        import urllib.request
        import urllib.error

        for peer in cs.peers:
            try:
                url = f"http://{peer.address}:{peer.port}/v1/health"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    if resp.status == 200:
                        peer.status = "active"
                        peer.last_seen = time.time()
                    else:
                        peer.status = "offline"
            except Exception:
                if time.time() - peer.last_seen > 300:
                    peer.status = "offline"

        self.save_cluster(cs)
        return cs.peers

    def should_promote_to_k3s(self) -> tuple[bool, str]:
        """Determine if local→K3s promotion is warranted."""
        cs = self.load_cluster()
        active_peers = [p for p in cs.peers if p.status == "active"]

        if len(active_peers) == 0:
            return False, "No active peers — staying in local mode"

        if len(active_peers) >= 1:
            return True, f"{len(active_peers) + 1} nodes active — K3s promotion recommended"

        return False, "Conditions not met"


def _get_local_ip() -> str:
    """Get the local IP address (non-loopback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return str(ip)
    except Exception:
        return "127.0.0.1"
