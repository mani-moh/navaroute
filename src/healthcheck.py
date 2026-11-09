from __future__ import annotations

import concurrent.futures
import re
import shutil
import socket
import ssl
import subprocess
import time
from typing import Optional

from src.models import ProxyNode

IPV4_REGEX = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

HEALTHCHECK_TEST_URLS = [
    "http://cp.cloudflare.com/generate_204",
    "http://www.gstatic.com/generate_204",
    "http://captive.apple.com/hotspot-detect.html",
]


def _is_ip_address(addr: str) -> bool:
    clean = addr.strip("[]")
    if ":" in clean:
        return True
    return bool(IPV4_REGEX.match(clean))


def probe_node_socket(
    node: ProxyNode, timeout: float = 3.5
) -> tuple[bool, Optional[float]]:
    """Fast, multi-stage TCP + TLS/SNI socket handshake verifier.

    Measures real network latency and verifies server reachability.
    """
    address = node.address.strip("[]").strip()
    port = node.port

    if not address or not (1 <= port <= 65535):
        return False, None

    t0 = time.perf_counter()
    sock: Optional[socket.socket] = None

    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        sock.settimeout(timeout)

        # For VLESS-Reality:
        # Standard Python SSL cannot complete a Reality handshake because Reality
        # requires a specialized ClientHello with pbk/sid authentication.
        # However, successful TCP handshake verifies the server is responsive.
        if node.is_reality():
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            sock.close()
            return True, round(elapsed_ms, 1)

        # For standard TLS / HTTPS fronted nodes:
        needs_tls = (
            port == 443
            or node.params.get("security") == "tls"
            or (node.scheme in ("trojan", "vless", "vmess") and bool(node.params.get("sni")))
        )

        if needs_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            sni = (
                node.params.get("sni")
                or node.params.get("host")
                or (address if not _is_ip_address(address) else None)
            )
            try:
                tls_sock = context.wrap_socket(sock, server_hostname=sni if sni else None)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                tls_sock.close()
                return True, round(elapsed_ms, 1)
            except Exception:
                sock.close()
                return False, None
        else:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            sock.close()
            return True, round(elapsed_ms, 1)

    except Exception:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        return False, None


def check_nodes_health(
    nodes: list[ProxyNode],
    max_nodes: int = 1500,
    concurrency: int = 64,
    timeout: float = 3.5,
    xray_bin: Optional[str] = None,
) -> None:
    """Prioritizes and concurrently tests the reachability and latency of nodes.

    Sets passed_healthcheck and latency_ms in-place.
    """
    if not nodes:
        return

    # Prioritization:
    # 1. Verified upstream first
    # 2. Iran / Reality / ConfigsHUB nodes next
    # 3. Other nodes
    def sort_key(n: ProxyNode) -> tuple[int, int]:
        p1 = 0 if n.is_verified_source else 1
        p2 = 0 if (n.is_iran_optimized() or n.is_reality() or "confighub" in n.tags) else 1
        return (p1, p2)

    # Sort nodes by priority and select target slice
    nodes_with_priority = sorted(nodes, key=sort_key)
    target_nodes = nodes_with_priority[:max_nodes]

    # Optional Xray binary probing check
    resolved_xray = xray_bin or shutil.which("xray")

    def _worker(n: ProxyNode) -> None:
        passed, latency = probe_node_socket(n, timeout=timeout)
        n.passed_healthcheck = passed
        n.latency_ms = latency

    workers = min(concurrency, max(1, len(target_nodes)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_worker, node) for node in target_nodes]
        concurrent.futures.wait(futures)
