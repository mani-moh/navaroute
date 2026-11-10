from __future__ import annotations

import concurrent.futures
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import statistics
import subprocess
import tempfile
import time
import urllib.parse
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
    elif net == "httpupgrade":
        stream_settings["httpupgradeSettings"] = {
            "path": node.params.get("path", "/"),
            "host": host or "",
        }
    elif net in ("xhttp", "splithttp"):
        stream_settings["xhttpSettings"] = {
            "path": node.params.get("path", "/"),
            "host": host or "",
        }

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


def _reserve_ports(count: int) -> list[int]:
    """Allocates `count` available ephemeral loopback ports."""
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            sockets.append(s)
            ports.append(s.getsockname()[1])
    finally:
        for s in sockets:
            try:
                s.close()
            except Exception:
                pass
    return ports


def _build_xray_batch_config(
    outbounds: list[tuple[int, dict]],
    ports: list[int],
) -> dict:
    """Builds one Xray config mapping each loopback HTTP inbound to its node outbound.

    Places a blackhole outbound first so unmatched routing can never fall through direct.
    """
    inbounds = []
    xray_outbounds = [{"protocol": "blackhole", "tag": "block"}]
    rules = []

    for slot, (_, ob) in enumerate(outbounds):
        in_tag = f"in_{slot}"
        out_tag = f"out_{slot}"
        inbounds.append({
            "tag": in_tag,
            "port": ports[slot],
            "listen": "127.0.0.1",
            "protocol": "http",
            "settings": {},
        })
        ob_copy = dict(ob)
        ob_copy["tag"] = out_tag
        xray_outbounds.append(ob_copy)
        rules.append({
            "type": "field",
            "inboundTag": [in_tag],
            "outboundTag": out_tag,
        })

    return {
        "log": {"loglevel": "none"},
        "inbounds": inbounds,
        "outbounds": xray_outbounds,
        "routing": {
            "domainStrategy": "AsIs",
            "rules": rules,
        },
    }


def _wait_ports_listening(
    ports: list[int],
    proc: subprocess.Popen,
    timeout: float = 4.0,
) -> bool:
    """Waits until first and last batch inbound ports accept connections or process exits."""
    if not ports:
        return True
    check_ports = [ports[0]] if len(ports) == 1 else [ports[0], ports[-1]]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        ready = True
        for p in check_ports:
            try:
                with socket.create_connection(("127.0.0.1", p), timeout=0.2):
                    pass
            except OSError:
                ready = False
                break
        if ready:
            return True
        time.sleep(0.05)
    return False


def _probe_http_via_local_proxy(
    port: int,
    test_url: str = "http://cp.cloudflare.com/generate_204",
    timeout: float = 5.0,
) -> tuple[bool, Optional[float]]:
    """Sends a real HTTP request through a local Xray HTTP proxy inbound."""
    parsed = urllib.parse.urlsplit(test_url)
    host = parsed.netloc
    conn: Optional[http.client.HTTPConnection] = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        t0 = time.perf_counter()
        conn.request(
            "GET",
            test_url,
            headers={
                "Host": host,
                "User-Agent": "Mozilla/5.0 Chrome/133.0.0.0",
                "Connection": "close",
            },
        )
        resp = conn.getresponse()
        resp.read()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if resp.status in (200, 204):
            return True, round(elapsed_ms, 1)
        return False, None
    except Exception:
        return False, None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def probe_batch_xray(
    nodes: list[ProxyNode],
    xray_bin: str,
    timeout: float = 5.0,
    test_url: str = "http://cp.cloudflare.com/generate_204",
    workers: int = 24,
) -> dict[int, float]:
    """Tests a batch of nodes end-to-end inside a single Xray process.

    Returns a mapping `{node_index: latency_ms}` strictly for nodes whose proxied
    HTTP request returned 200/204 (no fallback to TCP connect). Automatically bisects
    if a malformed node causes Xray startup to reject the batch.
    """
    valid_items: list[tuple[int, dict]] = []
    for idx, node in enumerate(nodes):
        ob = build_xray_outbound(node)
        if ob is not None:
            valid_items.append((idx, ob))

    if not valid_items:
        return {}

    return _run_xray_items(valid_items, xray_bin=xray_bin, timeout=timeout, test_url=test_url, workers=workers)


def _run_xray_items(
    items: list[tuple[int, dict]],
    xray_bin: str,
    timeout: float,
    test_url: str,
    workers: int,
) -> dict[int, float]:
    ports = _reserve_ports(len(items))
    config = _build_xray_batch_config(items, ports)

    tmp_config = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(config, tmp_config)
        tmp_config.close()

        try:
            proc = subprocess.Popen(
                [xray_bin, "run", "-c", tmp_config.name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return {}

        started = False
        try:
            started = _wait_ports_listening(ports, proc, timeout=4.0)
            if not started:
                # Terminate failed batch and bisect so 1 malformed config never drops valid peers
                if len(items) > 1:
                    proc.kill()
                    proc.wait()
                    mid = len(items) // 2
                    left = _run_xray_items(items[:mid], xray_bin, timeout, test_url, workers)
                    right = _run_xray_items(items[mid:], xray_bin, timeout, test_url, workers)
                    left.update(right)
                    return left
                return {}

            results: dict[int, float] = {}
            max_w = min(workers, len(items))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_w) as pool:
                future_map = {
                    pool.submit(_probe_http_via_local_proxy, ports[slot], test_url, timeout): orig_idx
                    for slot, (orig_idx, _) in enumerate(items)
                }
                for fut in concurrent.futures.as_completed(future_map):
                    orig_idx = future_map[fut]
                    try:
                        ok, latency = fut.result()
                        if ok and latency is not None:
                            results[orig_idx] = latency
                    except Exception:
                        pass
            return results
        finally:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
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
    batch_size: int = 48,
    rounds: int = 1,
    test_urls: Optional[list[str]] = None,
) -> None:
    """Prioritizes and concurrently tests the reachability and latency of nodes.

    When use_xray=True and an Xray binary is available, runs batched end-to-end
    HTTP-proxied probes across `rounds` rounds (intersection of all rounds survives).
    Otherwise runs fast concurrent TCP+TLS/SNI socket verification.
    Sets passed_healthcheck and latency_ms in-place.
    """
    if not nodes:
        return

    # Reset status before probing
    def sort_key(n: ProxyNode) -> tuple[int, int]:
        p1 = 0 if n.is_verified_source else 1
        p2 = 0 if (n.is_iran_related() or n.is_reality() or "confighub" in n.tags) else 1
        return (p1, p2)

    nodes_with_priority = sorted(nodes, key=sort_key)
    target_nodes = nodes_with_priority[:max_nodes]

    resolved_xray = (xray_bin or shutil.which("xray")) if use_xray else None

    if resolved_xray:
        endpoints = test_urls or HEALTHCHECK_TEST_URLS
        latencies: dict[int, list[float]] = {i: [] for i in range(len(target_nodes))}
        survivor_indices = set(range(len(target_nodes)))

        for r_idx in range(max(1, rounds)):
            if not survivor_indices:
                break
            cur_url = endpoints[r_idx % len(endpoints)]
            active_list = sorted(survivor_indices)
            round_passed: set[int] = set()

            for start in range(0, len(active_list), max(1, batch_size)):
                chunk_indices = active_list[start : start + batch_size]
                chunk_nodes = [target_nodes[i] for i in chunk_indices]
                batch_res = probe_batch_xray(
                    chunk_nodes,
                    xray_bin=resolved_xray,
                    timeout=max(timeout, 4.5),
                    test_url=cur_url,
                    workers=min(concurrency, len(chunk_nodes), 32),
                )
                for local_idx, lat in batch_res.items():
                    orig_i = chunk_indices[local_idx]
                    round_passed.add(orig_i)
                    latencies[orig_i].append(lat)

            survivor_indices &= round_passed

        for idx, n in enumerate(target_nodes):
            if idx in survivor_indices and latencies[idx]:
                n.passed_healthcheck = True
                n.latency_ms = round(statistics.median(latencies[idx]), 1)
            else:
                n.passed_healthcheck = False
                n.latency_ms = None
        return

    def _worker(n: ProxyNode) -> None:
        passed, latency = probe_node_socket(n, timeout=timeout)
        n.passed_healthcheck = passed
        n.latency_ms = latency

    workers = min(concurrency, max(1, len(target_nodes)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_worker, node) for node in target_nodes]
        concurrent.futures.wait(futures)
