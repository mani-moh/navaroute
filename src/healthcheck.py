from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.request
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


def build_xray_outbound(node: ProxyNode) -> Optional[dict]:
    """Converts a ProxyNode into an Xray outbound configuration dictionary."""
    scheme = node.scheme.lower()
    address = node.address.strip("[]").strip()
    port = node.port

    if not address or not (1 <= port <= 65535):
        return None

    outbound: dict = {"tag": "proxy"}

    net = node.params.get("type", node.params.get("net", node.params.get("network", "tcp"))).lower()
    security = node.params.get("security", "").lower()
    sni = node.params.get("sni", node.params.get("host", ""))
    host = node.params.get("host", node.params.get("sni", ""))

    stream_settings: dict = {"network": net}

    if security == "tls":
        stream_settings["security"] = "tls"
        tls_settings: dict = {}
        if sni:
            tls_settings["serverName"] = sni
        if alpn := node.params.get("alpn"):
            tls_settings["alpn"] = [a.strip() for a in alpn.split(",") if a.strip()]
        if fp := node.params.get("fp"):
            tls_settings["fingerprint"] = fp
        stream_settings["tlsSettings"] = tls_settings
    elif security == "reality" or node.is_reality():
        stream_settings["security"] = "reality"
        stream_settings["realitySettings"] = {
            "serverName": sni,
            "publicKey": node.params.get("pbk", ""),
            "shortId": node.params.get("sid", ""),
            "spiderX": node.params.get("spx", ""),
            "fingerprint": node.params.get("fp", "chrome"),
        }

    if net == "ws":
        stream_settings["wsSettings"] = {
            "path": node.params.get("path", "/"),
            "headers": {"Host": host} if host else {},
        }
    elif net == "grpc":
        service = node.params.get("serviceName", node.params.get("service_name", ""))
        stream_settings["grpcSettings"] = {"serviceName": service}

    outbound["streamSettings"] = stream_settings

    if scheme == "vless":
        outbound["protocol"] = "vless"
        outbound["settings"] = {
            "vnext": [{
                "address": address,
                "port": port,
                "users": [{
                    "id": node.credential,
                    "encryption": node.params.get("encryption", "none"),
                    "flow": node.params.get("flow", ""),
                }]
            }]
        }
    elif scheme == "vmess":
        outbound["protocol"] = "vmess"
        outbound["settings"] = {
            "vnext": [{
                "address": address,
                "port": port,
                "users": [{
                    "id": node.credential,
                    "alterId": int(node.params.get("aid", 0)),
                    "security": node.params.get("scy", "auto"),
                }]
            }]
        }
    elif scheme == "trojan":
        outbound["protocol"] = "trojan"
        outbound["settings"] = {
            "servers": [{
                "address": address,
                "port": port,
                "password": node.credential,
            }]
        }
    elif scheme == "ss":
        outbound["protocol"] = "shadowsocks"
        method, _, password = node.credential.partition(":")
        if not password:
            return None
        outbound["settings"] = {
            "servers": [{
                "address": address,
                "port": port,
                "method": method,
                "password": password,
            }]
        }
    else:
        return None

    return outbound


def probe_node_xray(
    node: ProxyNode,
    xray_bin: str,
    timeout: float = 4.0,
    test_url: str = "http://cp.cloudflare.com/generate_204",
) -> tuple[bool, Optional[float]]:
    """Probes a node using an ephemeral Xray core process via SOCKS5 proxy."""
    outbound = build_xray_outbound(node)
    if not outbound:
        return probe_node_socket(node, timeout=timeout)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]

    config = {
        "log": {"loglevel": "none"},
        "inbounds": [{
            "port": local_port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"auth": "noauth"},
        }],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
    }

    tmp_config = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(config, tmp_config)
        tmp_config.close()

        try:
            proc = subprocess.Popen(
                [xray_bin, "run", "-c", tmp_config.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return probe_node_socket(node, timeout=timeout)

        try:
            time.sleep(0.15)
            t0 = time.perf_counter()
            proxy_handler = urllib.request.ProxyHandler({"http": f"socks5://127.0.0.1:{local_port}"})
            opener = urllib.request.build_opener(proxy_handler)
            req = urllib.request.Request(test_url, headers={"User-Agent": "PattN-Healthcheck/1.0"})
            with opener.open(req, timeout=timeout) as resp:
                if resp.status in (200, 204):
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    return True, round(elapsed_ms, 1)
                return False, None
        except Exception:
            # Fall back to socket probe if SOCKS request or urllib fails
            return probe_node_socket(node, timeout=timeout)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    finally:
        try:
            os.remove(tmp_config.name)
        except Exception:
            pass


def check_nodes_health(
    nodes: list[ProxyNode],
    max_nodes: int = 1500,
    concurrency: int = 64,
    timeout: float = 3.5,
    xray_bin: Optional[str] = None,
    use_xray: bool = False,
) -> None:
    """Prioritizes and concurrently tests the reachability and latency of nodes.

    Supports optional deep Xray SOCKS probing when use_xray=True and xray binary exists,
    with seamless fallback to high-speed concurrent TCP+TLS/SNI socket verification.
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
    resolved_xray = (xray_bin or shutil.which("xray")) if use_xray else None

    def _worker(n: ProxyNode) -> None:
        if resolved_xray:
            passed, latency = probe_node_xray(n, resolved_xray, timeout=timeout)
        else:
            passed, latency = probe_node_socket(n, timeout=timeout)
        n.passed_healthcheck = passed
        n.latency_ms = latency

    workers = min(concurrency, max(1, len(target_nodes)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_worker, node) for node in target_nodes]
        concurrent.futures.wait(futures)
