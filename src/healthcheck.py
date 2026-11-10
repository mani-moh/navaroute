"""Public socket observations and explicitly supplied, owned-core HTTPS tests."""
from __future__ import annotations

import concurrent.futures
import http.client
import json
import math
import os
import re
import socket
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.models import ProxyNode
from src.network import _Connection, _authority, _end, _hostname, _remaining, _resolve, connect_public

HEALTHCHECK_TEST_URLS = ["https://cp.cloudflare.com/generate_204", "https://www.gstatic.com/generate_204"]


class CoreInfrastructureError(RuntimeError):
    """No reliable completed test; callers must preserve previous evidence."""


@dataclass
class BatchResult:
    passed: dict[int, float] = field(default_factory=dict)
    attempted: set[int] = field(default_factory=set)
    unsupported: set[int] = field(default_factory=set)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def core_environment() -> dict[str, str]:
    # No PATH, proxy configuration, GitHub/NVIDIA tokens, profile or client settings.
    return {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}}


def validate_limits(max_nodes: int, concurrency: int, timeout: float, batch_size: int, rounds: int) -> None:
    for value, limit in ((max_nodes, 5000), (concurrency, 64), (batch_size, 128), (rounds, 10)):
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError("Invalid test limits")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("Invalid test timeout")


def probe_node_socket(node: ProxyNode, timeout: float = 3.5) -> tuple[bool, float | None]:
    """TCP reachability only, not credential/TLS/end-to-end verification."""
    start = time.perf_counter()
    try:
        with connect_public((node.address.strip("[]"), node.port), timeout=timeout):
            return True, round((time.perf_counter() - start) * 1000, 1)
    except (OSError, ValueError):
        return False, None


def build_xray_outbound(node: ProxyNode) -> dict | None:
    """Convert the complete supported connection; anything unaccounted for is unsupported."""
    p = node.params
    scheme = node.scheme.lower()
    if scheme not in {"vless", "vmess", "trojan", "ss"} or type(node.port) is not int or not 1 <= node.port <= 65535 or not isinstance(node.credential, str) or not node.credential:
        return None
    try:
        address = _hostname(node.address.strip("[]"))
    except (ValueError, AttributeError):
        return None
    if any(not isinstance(v, str) for k, v in p.items() if k != "alpn"):
        return None
    net = p.get("type", "tcp")
    security = p.get("security", "reality" if p.get("pbk") else "tls" if scheme == "trojan" else "none") or "none"
    if net not in {"tcp", "ws", "grpc", "httpupgrade"} or security not in {"none", "tls", "reality"}:
        return None
    allowed = {"type", "security", "headerType"}
    if p.get("headerType", "none") not in {"", "none"}:
        return None
    if scheme == "vless":
        allowed |= {"encryption", "flow"}
        if p.get("encryption", "none") != "none" or p.get("flow", "") not in {"", "xtls-rprx-vision"}:
            return None
        if p.get("flow") and (net != "tcp" or security not in {"tls", "reality"}):
            return None
    elif scheme == "vmess":
        allowed |= {"v", "aid", "scy"}
        if p.get("v", "2") != "2" or not re.fullmatch(r"[0-9]{1,5}", p.get("aid", "0")) or int(p.get("aid", "0")) > 65535:
            return None
        if p.get("scy", "auto") not in {"auto", "aes-128-gcm", "chacha20-poly1305", "none", "zero"}:
            return None
    if scheme == "ss" and (net != "tcp" or security != "none"):
        return None
    stream = {"network": net, "security": security}
    if security in {"tls", "reality"}:
        allowed |= {"sni", "fp"}
        sni = p.get("sni") or address
        try:
            _hostname(sni)
        except ValueError:
            return None
        fp = p.get("fp", "chrome" if security == "reality" else "")
        if fp not in {"", "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random", "randomized"}:
            return None
        settings = {"serverName": sni}
        if fp:
            settings["fingerprint"] = fp
        if security == "tls":
            allowed |= {"alpn", "allowInsecure"}
            if "alpn" in p:
                alpn = p["alpn"].split(",") if isinstance(p["alpn"], str) else p["alpn"]
                if not isinstance(alpn, list) or not alpn or any(not isinstance(a, str) or not a or len(a) > 255 or re.search(r"[\s\x00-\x1f\x7f]", a) for a in alpn):
                    return None
                settings["alpn"] = alpn
            if "allowInsecure" in p:
                if p["allowInsecure"] not in {"true", "false", "1", "0"}:
                    return None
                settings["allowInsecure"] = p["allowInsecure"] in {"true", "1"}
            stream["tlsSettings"] = settings
        else:
            if scheme not in {"vless", "trojan"} or not p.get("pbk"):
                return None
            allowed |= {"pbk", "sid", "spx"}
            settings.update(publicKey=p["pbk"], shortId=p.get("sid", ""), spiderX=p.get("spx", ""))
            stream["realitySettings"] = settings
    if net in {"ws", "httpupgrade"}:
        allowed |= {"host", "path"}
        host = p.get("host", "")
        if host and re.search(r"[\x00-\x20\x7f]", host):
            return None
        path = p.get("path", "/")
        if not path.startswith("/") or re.search(r"[\x00-\x1f\x7f]", path):
            return None
        if net == "ws":
            stream["wsSettings"] = {"path": path, "headers": {"Host": host} if host else {}}
        else:
            stream["httpupgradeSettings"] = {"path": path, "host": host}
    elif net == "grpc":
        allowed |= {"serviceName", "mode"}
        service = p.get("serviceName", "")
        if scheme == "vmess":
            allowed |= {"path", "host"}
            if p.get("host") or ("serviceName" in p and "path" in p and p["path"] != service):
                return None
            service = service or p.get("path", "")
        if re.search(r"[\x00-\x1f\x7f]", service) or p.get("mode", "gun") not in {"gun", "multi"}:
            return None
        stream["grpcSettings"] = {"serviceName": service}
        if "mode" in p:
            stream["grpcSettings"]["multiMode"] = p["mode"] == "multi"
    elif scheme == "vmess":
        # Empty legacy VMess fields explicitly mean no transport option.
        allowed |= {"host", "path"}
        if p.get("host") or p.get("path"):
            return None
    if set(p) - allowed:
        return None
    if scheme in {"vless", "vmess"}:
        user = {"id": node.credential}
        if scheme == "vless":
            user.update(encryption=p.get("encryption", "none"), flow=p.get("flow", ""))
        else:
            user.update(alterId=int(p.get("aid", "0")), security=p.get("scy", "auto"))
        settings = {"vnext": [{"address": address, "port": node.port, "users": [user]}]}
        protocol = scheme
    else:
        server = {"address": address, "port": node.port, "password": node.credential}
        protocol = "trojan"
        if scheme == "ss":
            method, separator, password = node.credential.partition(":")
            if not separator or not password or method not in {"aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm", "2022-blake3-chacha20-poly1305"}:
                return None
            server.update(method=method, password=password)
            protocol = "shadowsocks"
        settings = {"servers": [server]}
    return {"tag": "proxy", "protocol": protocol, "settings": settings, "streamSettings": stream}


def prepare_public_outbound(node: ProxyNode, timeout: float = 5.0) -> dict | None:
    outbound = build_xray_outbound(node)
    if outbound is not None:
        answers = _resolve(node.address.strip("[]"), node.port, _end(timeout))
        endpoint = outbound["settings"].get("vnext", outbound["settings"].get("servers"))[0]
        original_authority = _authority(endpoint["address"], node.port)
        stream = outbound["streamSettings"]
        if stream["network"] == "ws" and not node.params.get("host"):
            stream["wsSettings"]["headers"]["Host"] = original_authority
        elif stream["network"] == "httpupgrade" and not node.params.get("host"):
            stream["httpupgradeSettings"]["host"] = original_authority
        elif stream["network"] == "grpc":
            # grpc authority otherwise derives from the dial target; pinning must not alter it.
            security_settings = stream.get("tlsSettings", stream.get("realitySettings", {}))
            stream["grpcSettings"]["authority"] = security_settings.get("serverName", original_authority)
        endpoint["address"] = answers[0][4][0]
    return outbound


def _reserve_ports(count: int) -> list[int]:
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _build_xray_batch_config(outbounds: list[tuple[int, dict]], ports: list[int]) -> dict:
    config = {"log": {"loglevel": "none"}, "inbounds": [], "outbounds": [{"protocol": "blackhole", "tag": "block"}],
              "routing": {"domainStrategy": "AsIs", "rules": []}}
    for slot, (_, outbound) in enumerate(outbounds):
        config["inbounds"].append({"tag": f"in_{slot}", "port": ports[slot], "listen": "127.0.0.1", "protocol": "http", "settings": {}})
        config["outbounds"].append(dict(outbound, tag=f"out_{slot}"))
        config["routing"]["rules"].append({"type": "field", "inboundTag": [f"in_{slot}"], "outboundTag": f"out_{slot}"})
    return config


def _wait_ports_listening(ports: list[int], proc: subprocess.Popen, timeout: float = 4.0) -> bool:
    deadline = _end(timeout)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        ready = True
        for port in ports:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=min(.1, _remaining(deadline))):
                    pass
            except OSError:
                ready = False
                break
        if ready:
            return True
        time.sleep(.025)
    return False


def _stop_owned(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=1.5)
    else:
        proc.wait(timeout=1.5)


class _ProbeConnection(_Connection):
    proxy_request_started = False

    def send(self, data):
        if self.sock is not None:
            _remaining(self.deadline)
            self.proxy_request_started = True
        return super().send(data)


def _probe_http_via_local_proxy(port: int, test_url: str = HEALTHCHECK_TEST_URLS[0], timeout: float = 5.0) -> tuple[bool, float | None]:
    if test_url not in HEALTHCHECK_TEST_URLS:
        raise ValueError("Unsupported probe policy endpoint")
    from urllib.parse import urlsplit
    parsed = urlsplit(test_url)
    start = time.perf_counter()
    deadline = _end(timeout)
    conn = None
    response = None
    try:
        conn = _ProbeConnection(parsed.hostname, 443, True, deadline, ("127.0.0.1", port, None))
        conn.connect()
        conn.sock.settimeout(_remaining(deadline))
        conn.request("GET", parsed.path, headers={"Host": parsed.hostname, "Connection": "close", "Accept-Encoding": "identity"})
        response = conn.getresponse()
        _remaining(deadline)
        # 204 is bodyless by definition; reject a contradictory framing header as well.
        if response.status != 204 or response.getheader("Content-Length", "0") != "0" or response.getheader("Transfer-Encoding") is not None:
            return False, None
        # HTTPResponse ignores an illegal 204 body; inspect one wire byte through EOF instead.
        if response.getheader("Content-Encoding", "identity") != "identity" or response.fp.read(1):
            return False, None
        _remaining(deadline)
        return True, round((time.perf_counter() - start) * 1000, 1)
    except (OSError, ValueError, http.client.HTTPException):
        if conn is None or not conn.proxy_request_started:
            raise CoreInfrastructureError("Policy probe infrastructure unavailable before CONNECT") from None
        return False, None
    finally:
        if conn is not None and conn.timer is not None:
            conn.timer.cancel()
        if response is not None:
            response.close()
        if conn is not None:
            conn.close()
            conn.watched_socket = None


def _run_xray_items(items: list[tuple[int, dict]], xray_bin: str, timeout: float, test_url: str, workers: int) -> BatchResult:
    ports = _reserve_ports(len(items))
    with tempfile.TemporaryDirectory(prefix="navaroute-core-") as tmp:
        path = Path(tmp) / "config.json"
        config = _build_xray_batch_config(items, ports)
        path.write_text(json.dumps(config, allow_nan=False), encoding="utf-8")
        try:
            checked = subprocess.run([xray_bin, "run", "-test", "-c", str(path)], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, env=core_environment(), cwd=tmp)
        except (OSError, subprocess.SubprocessError):
            raise CoreInfrastructureError("Core configuration validation unavailable") from None
        if checked.returncode != 0:
            if len(items) == 1:
                return BatchResult(unsupported={items[0][0]})
            mid = len(items) // 2
            left = _run_xray_items(items[:mid], xray_bin, timeout, test_url, workers)
            right = _run_xray_items(items[mid:], xray_bin, timeout, test_url, workers)
            return BatchResult(left.passed | right.passed, left.attempted | right.attempted, left.unsupported | right.unsupported)
        try:
            proc = subprocess.Popen([xray_bin, "run", "-c", str(path)], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=core_environment(), cwd=tmp)
        except OSError:
            raise CoreInfrastructureError("Owned core startup failed") from None
        try:
            if not _wait_ports_listening(ports, proc, timeout=min(timeout, 4)):
                raise CoreInfrastructureError("Owned core did not become ready")
            result = BatchResult()
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
                futures = {pool.submit(_probe_http_via_local_proxy, ports[slot], test_url, timeout): index for slot, (index, _) in enumerate(items)}
                try:
                    for future in concurrent.futures.as_completed(futures):
                        index = futures[future]
                        passed, latency = future.result()
                        result.attempted.add(index)
                        if passed and latency is not None:
                            result.passed[index] = latency
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise
            if proc.poll() is not None:
                raise CoreInfrastructureError("Owned core exited during probes")
            return result
        finally:
            _stop_owned(proc)


def probe_batch_xray(nodes: list[ProxyNode], xray_bin: str, timeout: float = 5.0,
                     test_url: str = HEALTHCHECK_TEST_URLS[0], workers: int = 24, *, prepared: list[dict] | None = None) -> BatchResult:
    if not isinstance(xray_bin, str) or not xray_bin or not Path(xray_bin).is_absolute():
        raise CoreInfrastructureError("Explicit absolute standalone core path required")
    validate_limits(max(1, len(nodes)), workers, timeout, 48, 1)
    if test_url not in HEALTHCHECK_TEST_URLS:
        raise ValueError("Unsupported probe policy endpoint")
    items = []
    unsupported = set()
    for index, node in enumerate(nodes):
        outbound = prepared[index] if prepared is not None else prepare_public_outbound(node, timeout)
        if outbound is None:
            unsupported.add(index)
        else:
            items.append((index, outbound))
    result = _run_xray_items(items, xray_bin, timeout, test_url, workers) if items else BatchResult()
    result.unsupported |= unsupported
    return result


def probe_node_xray(node: ProxyNode, xray_bin: str, timeout: float = 4.0,
                    test_url: str = HEALTHCHECK_TEST_URLS[0]) -> tuple[bool, float | None]:
    result = probe_batch_xray([node], xray_bin, timeout, test_url, workers=1)
    return 0 in result.passed, result.passed.get(0)


def check_nodes_health(nodes: list[ProxyNode], max_nodes: int = 1500, concurrency: int = 64, timeout: float = 3.5,
                       xray_bin: str | None = None, use_xray: bool = False, batch_size: int = 48,
                       rounds: int = 1, test_urls: list[str] | None = None) -> dict:
    validate_limits(max_nodes, concurrency, timeout, batch_size, rounds)
    for node in nodes:
        node.passed_healthcheck = False
        node.latency_ms = None
        node.healthcheck_kind = "untested"
        node.healthchecked_at = None
    ordered = sorted(nodes, key=lambda n: (not n.is_verified_source, not (n.is_iran_related() or n.is_reality() or "confighub" in n.tags)))
    unsupported = 0
    unsupported_fingerprints = set()
    if use_xray:
        if not isinstance(xray_bin, str) or not xray_bin or not Path(xray_bin).is_absolute():
            raise CoreInfrastructureError("Explicit absolute standalone core path required")
        endpoints = test_urls or HEALTHCHECK_TEST_URLS
        if any(url not in HEALTHCHECK_TEST_URLS for url in endpoints):
            raise ValueError("Unsupported probe policy endpoint")
        supported = [n for n in ordered if build_xray_outbound(n) is not None]
        unsupported = len(nodes) - len(supported)
        unsupported_fingerprints = {n.semantic_fingerprint() for n in nodes if build_xray_outbound(n) is None}
        target = supported[:max_nodes]
        # Validate every selected public DNS answer before any core is started; never re-resolve for dial.
        prepared = [prepare_public_outbound(n, timeout) for n in target]
        latencies = {i: [] for i in range(len(target))}
        active = set(range(len(target)))
        for round_index in range(rounds):
            round_passed = set()
            indices = sorted(active)
            for start in range(0, len(indices), batch_size):
                chunk = indices[start:start + batch_size]
                result = probe_batch_xray([target[i] for i in chunk], xray_bin, timeout,
                                          endpoints[round_index % len(endpoints)], concurrency,
                                          prepared=[prepared[i] for i in chunk])
                if round_index > 0 and result.unsupported:
                    raise CoreInfrastructureError("Previously supported core configuration failed")
                unsupported += len(result.unsupported)
                unsupported_fingerprints.update(target[chunk[i]].semantic_fingerprint() for i in result.unsupported)
                for local in result.attempted:
                    node = target[chunk[local]]
                    node.healthcheck_kind = "xray"
                    node.healthchecked_at = utc_now()
                for local, latency in result.passed.items():
                    index = chunk[local]
                    latencies[index].append(latency)
                    round_passed.add(index)
            active &= round_passed
        for index in active:
            if len(latencies[index]) == rounds:
                target[index].passed_healthcheck = True
                target[index].latency_ms = round(statistics.median(latencies[index]), 1)
    else:
        def worker(node):
            passed, latency = probe_node_socket(node, timeout)
            node.passed_healthcheck = passed
            node.latency_ms = latency
            node.healthcheck_kind = "socket"
            node.healthchecked_at = utc_now()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(worker, ordered[:max_nodes]))
    tested = sum(n.healthcheck_kind != "untested" for n in nodes)
    passed = sum(n.passed_healthcheck for n in nodes)
    return {"tested": tested, "passed": passed, "failed": tested - passed, "unsupported": unsupported,
            "untested": len(nodes) - tested - unsupported, "unsupported_fingerprints": sorted(unsupported_fingerprints)}
