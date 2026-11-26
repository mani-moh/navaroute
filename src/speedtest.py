"""Throughput (download speed) testing and top-100 feed generation, stdlib only.

Measures actual fixed-size HTTPS download speed through each proxy via
the owned Xray batch infrastructure in src/healthcheck.py, persists
pruned state in data/speed_state.json, and publishes subs/speed-100.txt
(+ _base64.txt twin).

Throughput is measured from GitHub Actions runner locations, not end-user
or Iran networks.
"""
from __future__ import annotations

import base64
import concurrent.futures
from dataclasses import dataclass, field
import http.client
import json
import math
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from src.healthcheck import (CoreInfrastructureError, _ProbeConnection,
                             _build_xray_batch_config, _reserve_ports,
                             _stop_owned, _wait_ports_listening,
                             core_environment, prepare_public_outbound)
from src.models import ProxyNode, SourceConfig
from src.network import _end, _remaining
from src.parsers import parse_proxy_uri
from src.project import PROJECT_NAME
from src.storage import atomic_write_text

SPEED_FEED_NAME = "speed-100"
SPEED_FEED_LIMIT = 100
SPEED_INTERVAL_HOURS = 4
SPEED_STATE_VERSION = 1
SPEED_RESULTS_VERSION = 1
SPEED_STATE_MAX_BYTES = 16 * 1024 * 1024
SPEED_STATE_RETENTION_DAYS = 30
SPEED_BACKOFF_HOURS = {0: 0, 1: 8, 2: 16, 3: 32}
SPEED_BACKOFF_CAP_HOURS = 48

DEFAULT_CANDIDATE_CAP = 400
DEFAULT_PER_RUN_CAP = 200
DEFAULT_SPEED_TIMEOUT = 10.0
DEFAULT_SPEED_WORKERS = 16
DEFAULT_SPEED_BATCH_SIZE = 32
DEFAULT_MAX_RUNTIME = 900.0
DEFAULT_DOWNLOAD_BYTES = 2_000_000
MIN_VALID_DOWNLOAD_BYTES = 65_536

SPEED_TEST_URLS = (
    "https://speed.cloudflare.com/__down?bytes=2000000",
    "https://speed.cloudflare.com/__down?bytes=1000000",
)
DEFAULT_SPEED_TEST_URL = SPEED_TEST_URLS[0]
CANDIDATE_SOURCE_FILES = ("tested.txt", "fastest-100.txt", "online.txt")
SPEED_SOURCE = SourceConfig("speedtest", "Speedtest", "https://github.com/mani-moh/navaroute")


@dataclass
class SpeedBatchResult:
    passed: dict[int, tuple[float, float]] = field(default_factory=dict)
    attempted: set[int] = field(default_factory=set)


def speed_backoff_hours(streak: int) -> int:
    if streak <= 0:
        return 0
    return SPEED_BACKOFF_HOURS.get(streak, SPEED_BACKOFF_CAP_HOURS)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must be UTC")
    return parsed


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def read_feed_uris(path: Path) -> list[str]:
    """Read non-comment proxy URI lines from a feed file."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if "://" in line and not line.startswith("#")]


def collect_candidates(subs_dir: Path, max_candidates: int = DEFAULT_CANDIDATE_CAP) -> list[dict[str, object]]:
    """Collect deduplicated candidate configs from tested/fastest/online pools."""
    if max_candidates < 1:
        raise ValueError("max_candidates must be >= 1")
    candidates: list[dict[str, object]] = []
    seen_fps: set[str] = set()
    for filename in CANDIDATE_SOURCE_FILES:
        for uri in read_feed_uris(subs_dir / filename):
            node = parse_proxy_uri(uri, SPEED_SOURCE)
            if node is None:
                continue
            fp = node.semantic_fingerprint()
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            candidates.append({"uri": uri, "fingerprint": fp, "node": node})
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def prune_speed_state(state: dict[str, dict], live_fingerprints: set[str],
                      now: datetime | None = None,
                      max_age_days: int = SPEED_STATE_RETENTION_DAYS) -> dict[str, dict]:
    """Keep only live fingerprints whose last test is within max_age_days."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    kept: dict[str, dict] = {}
    for fp, entry in state.items():
        if fp not in live_fingerprints:
            continue
        try:
            last_test = _parse_time(entry["last_test"])
        except (ValueError, KeyError, TypeError):
            continue
        if last_test < cutoff:
            continue
        kept[fp] = entry
    return kept


def load_speed_state(path: Path, live_fingerprints: set[str] | None = None,
                     now: datetime | None = None) -> dict[str, dict]:
    """Load data/speed_state.json, pruning stale entries when live_fingerprints is given."""
    if not path.exists():
        return {}
    if path.stat().st_size > SPEED_STATE_MAX_BYTES and live_fingerprints is None:
        raise ValueError("Speed state exceeds size limit")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid speed state") from exc
    if (not isinstance(data, dict) or data.get("version") != SPEED_STATE_VERSION
            or not isinstance(data.get("entries"), dict)):
        raise ValueError("Invalid speed state")
    entries: dict[str, dict] = {}
    for fp, entry in data["entries"].items():
        if not isinstance(fp, str) or not isinstance(entry, dict):
            raise ValueError("Invalid speed state")
        failures = entry.get("failures")
        last_test = entry.get("last_test")
        speed_mbps = entry.get("speed_mbps")
        latency_ms = entry.get("latency_ms")
        if type(failures) is not int or failures < 0 or not isinstance(last_test, str):
            raise ValueError("Invalid speed state")
        if speed_mbps is not None and (type(speed_mbps) not in (int, float)
                                       or not math.isfinite(speed_mbps) or speed_mbps < 0):
            raise ValueError("Invalid speed state")
        if latency_ms is not None and (type(latency_ms) not in (int, float)
                                       or not math.isfinite(latency_ms) or latency_ms < 0):
            raise ValueError("Invalid speed state")
        _parse_time(last_test)
        entries[fp] = {
            "failures": failures,
            "last_test": last_test,
            "speed_mbps": float(speed_mbps) if speed_mbps is not None else None,
            "latency_ms": float(latency_ms) if latency_ms is not None else None,
        }
    if live_fingerprints is not None:
        entries = prune_speed_state(entries, live_fingerprints, now=now)
    return entries


def save_speed_state(path: Path, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SPEED_STATE_VERSION,
        "entries": {
            fp: {
                "failures": e["failures"],
                "last_test": e["last_test"],
                "speed_mbps": e["speed_mbps"],
                "latency_ms": e["latency_ms"],
            }
            for fp, e in sorted(entries.items())
        },
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def update_speed_state(state: dict[str, dict],
                       results: dict[str, tuple[float, float] | None],
                       now: datetime) -> dict[str, dict]:
    """Fold one run's measurements (fp -> (speed_mbps, latency_ms) or None) into state."""
    timestamp = _iso(now)
    for fp, outcome in results.items():
        if outcome is None:
            prev = state.get(fp)
            failures = (prev["failures"] + 1) if prev else 1
            state[fp] = {
                "failures": failures,
                "last_test": timestamp,
                "speed_mbps": None,
                "latency_ms": None,
            }
        else:
            speed_mbps, latency_ms = outcome
            state[fp] = {
                "failures": 0,
                "last_test": timestamp,
                "speed_mbps": round(float(speed_mbps), 2),
                "latency_ms": round(float(latency_ms), 1),
            }
    return state


def select_due_speed(candidates: list[dict[str, object]], state: dict[str, dict],
                     now: datetime, limit: int) -> list[dict[str, object]]:
    """Pick due candidates: never-tested first, then lowest streak, then oldest."""
    if limit < 1:
        return []
    due: list[tuple[int, float, int, dict[str, object]]] = []
    for idx, entry in enumerate(candidates):
        record = state.get(str(entry["fingerprint"]))
        if record is None:
            due.append((0, 0.0, idx, entry))
            continue
        interval = timedelta(hours=speed_backoff_hours(record["failures"]))
        last_test = _parse_time(record["last_test"])
        if now - last_test >= interval:
            due.append((record["failures"], last_test.timestamp(), idx, entry))
    due.sort(key=lambda item: (item[0], item[1], item[2]))
    return [entry for _, _, _, entry in due[:limit]]


def rank_top_speed(candidates: list[dict[str, object]], state: dict[str, dict],
                   limit: int = SPEED_FEED_LIMIT) -> list[str]:
    """Return up to `limit` URIs ranked deterministically:
    throughput desc, latency asc, fingerprint asc.
    """
    scored: list[tuple[float, float, str, str]] = []
    for entry in candidates:
        fp = str(entry["fingerprint"])
        record = state.get(fp)
        if not record or record["failures"] != 0:
            continue
        speed = record.get("speed_mbps")
        if speed is None or speed <= 0:
            continue
        latency = record.get("latency_ms")
        lat_key = float(latency) if latency is not None else float("inf")
        scored.append((-float(speed), lat_key, fp, str(entry["uri"])))
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [uri for _, _, _, uri in scored[:limit]]


def render_speed_feed(uris: list[str], timestamp: str,
                      interval_hours: int = SPEED_INTERVAL_HOURS) -> tuple[str, str]:
    """Render (plain, base64) for subs/speed-100.txt and subs/speed-100_base64.txt."""
    title = f"{PROJECT_NAME} Top 100 Download Speed (GitHub Runner Measured)"
    header = (
        f"#profile-title: base64:{base64.b64encode(title.encode('utf-8')).decode('ascii')}\n"
        f"#profile-update-interval: {interval_hours}\n"
        f"# snapshot-time: {timestamp}\n"
        f"# note: Download throughput (Mbps) measured from GitHub runner locations, "
        f"not a guarantee of end-user performance.\n\n"
    )
    body = "\n".join(uris)
    plain = header + body + ("\n" if uris else "")
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return plain, encoded


def build_speed_results(candidates: list[dict[str, object]], state: dict[str, dict],
                        tested_fps: set[str], now: datetime,
                        top_count: int) -> dict:
    """Build privacy-safe summary payload for data/speed_results.json."""
    live_fps = {str(c["fingerprint"]) for c in candidates}
    speeds = [
        float(state[fp]["speed_mbps"])
        for fp in live_fps
        if fp in state and state[fp]["failures"] == 0 and state[fp]["speed_mbps"]
    ]
    speeds.sort(reverse=True)
    top_mbps = round(speeds[0], 2) if speeds else None
    median_mbps = round(speeds[len(speeds) // 2], 2) if speeds else None
    return {
        "version": SPEED_RESULTS_VERSION,
        "generated": _iso(now),
        "measurement_location": "github-runner",
        "candidate_pool": len(candidates),
        "tested": len(tested_fps),
        "passed": len(speeds),
        "top_feed_count": top_count,
        "top_speed_mbps": top_mbps,
        "median_speed_mbps": median_mbps,
    }


def _validate_speed_limits(node_count: int, workers: int, timeout: float, batch_size: int) -> None:
    if type(node_count) is not int or node_count < 1:
        raise ValueError("Invalid node count")
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Invalid workers limit")
    if type(batch_size) is not int or not 1 <= batch_size <= 64:
        raise ValueError("Invalid batch_size limit")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1.0 <= timeout <= 30.0:
        raise ValueError("Invalid speedtest timeout")


def measure_download_via_local_proxy(
    port: int,
    test_url: str = DEFAULT_SPEED_TEST_URL,
    timeout: float = DEFAULT_SPEED_TIMEOUT,
    max_bytes: int = DEFAULT_DOWNLOAD_BYTES,
    min_bytes: int = MIN_VALID_DOWNLOAD_BYTES,
) -> tuple[float | None, float | None]:
    """Measure HTTPS download throughput (Mbps) and TTFB latency (ms) via local HTTP CONNECT proxy."""
    if test_url not in SPEED_TEST_URLS:
        raise ValueError("Unsupported speedtest policy endpoint")
    parsed = urlsplit(test_url)
    path_with_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    deadline = _end(timeout)
    conn = None
    response = None
    try:
        start = time.perf_counter()
        conn = _ProbeConnection(parsed.hostname, 443, True, deadline, ("127.0.0.1", port, None))
        conn.connect()
        conn.sock.settimeout(_remaining(deadline))
        conn.request("GET", path_with_query, headers={
            "Host": parsed.hostname,
            "Connection": "close",
            "Accept-Encoding": "identity",
        })
        response = conn.getresponse()
        latency_ms = round((time.perf_counter() - start) * 1000.0, 1)
        if response.status != 200:
            return None, None
        total_bytes = 0
        transfer_start = time.perf_counter()
        while total_bytes < max_bytes:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            conn.sock.settimeout(rem)
            chunk = response.read(min(65536, max_bytes - total_bytes))
            if not chunk:
                break
            total_bytes += len(chunk)
        elapsed = max(time.perf_counter() - transfer_start, 0.001)
        if total_bytes < min_bytes:
            return None, None
        mbps = round((total_bytes * 8) / (elapsed * 1_000_000.0), 2)
        return mbps, latency_ms
    except (OSError, ValueError, http.client.HTTPException):
        if conn is None or not conn.proxy_request_started:
            raise CoreInfrastructureError("Speedtest proxy infrastructure unavailable before CONNECT") from None
        return None, None
    finally:
        if conn is not None and conn.timer is not None:
            conn.timer.cancel()
        if response is not None:
            response.close()
        if conn is not None:
            conn.close()
            conn.watched_socket = None


def _run_xray_speed_items(items: list[tuple[int, dict]], xray_bin: str,
                          timeout: float, test_url: str,
                          workers: int) -> dict[int, tuple[float, float]]:
    ports = _reserve_ports(len(items))
    with tempfile.TemporaryDirectory(prefix="navaroute-speed-") as tmp:
        path = Path(tmp) / "config.json"
        config = _build_xray_batch_config(items, ports)
        path.write_text(json.dumps(config, allow_nan=False), encoding="utf-8")
        try:
            checked = subprocess.run(
                [xray_bin, "run", "-test", "-c", str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10,
                env=core_environment(), cwd=tmp,
            )
        except (OSError, subprocess.SubprocessError):
            raise CoreInfrastructureError("Core configuration validation unavailable") from None
        if checked.returncode != 0:
            if len(items) == 1:
                return {}
            mid = len(items) // 2
            merged = _run_xray_speed_items(items[:mid], xray_bin, timeout, test_url, workers)
            merged.update(_run_xray_speed_items(items[mid:], xray_bin, timeout, test_url, workers))
            return merged
        try:
            proc = subprocess.Popen(
                [xray_bin, "run", "-c", str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=core_environment(), cwd=tmp,
            )
        except OSError:
            raise CoreInfrastructureError("Owned core startup failed") from None
        try:
            if not _wait_ports_listening(ports, proc, timeout=min(timeout, 4.0)):
                raise CoreInfrastructureError("Owned core did not become ready")
            passed: dict[int, tuple[float, float]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
                futures = {
                    pool.submit(measure_download_via_local_proxy, ports[slot], test_url, timeout): index
                    for slot, (index, _) in enumerate(items)
                }
                for future in concurrent.futures.as_completed(futures):
                    index = futures[future]
                    mbps, latency = future.result()
                    if mbps is not None and latency is not None:
                        passed[index] = (mbps, latency)
            if proc.poll() is not None:
                raise CoreInfrastructureError("Owned core exited during speedtest")
            return passed
        finally:
            _stop_owned(proc)


def measure_batch_xray(
    nodes: list[ProxyNode],
    xray_bin: str,
    timeout: float = DEFAULT_SPEED_TIMEOUT,
    test_url: str = DEFAULT_SPEED_TEST_URL,
    workers: int = DEFAULT_SPEED_WORKERS,
    batch_size: int = DEFAULT_SPEED_BATCH_SIZE,
    max_runtime: float = DEFAULT_MAX_RUNTIME,
    *,
    prepared: list[dict | None] | None = None,
) -> SpeedBatchResult:
    """Measure download throughput across `nodes` in bounded Xray batches.

    Degrades gracefully when `max_runtime` expires by returning partial results
    collected from completed batches.
    """
    if not isinstance(xray_bin, str) or not xray_bin or not Path(xray_bin).is_absolute():
        raise CoreInfrastructureError("Explicit absolute standalone core path required")
    if not nodes:
        return SpeedBatchResult()
    _validate_speed_limits(len(nodes), workers, timeout, batch_size)
    if test_url not in SPEED_TEST_URLS:
        raise ValueError("Unsupported speedtest policy endpoint")
    items: list[tuple[int, dict]] = []
    unsupported: set[int] = set()
    for index, node in enumerate(nodes):
        if prepared is not None:
            outbound = prepared[index]
        else:
            try:
                outbound = prepare_public_outbound(node, min(timeout, 5.0))
            except (OSError, ValueError):
                outbound = None
        if outbound is None:
            unsupported.add(index)
        else:
            items.append((index, outbound))
    outcome = SpeedBatchResult()
    outcome.attempted |= unsupported
    deadline = time.monotonic() + max_runtime
    for start in range(0, len(items), batch_size):
        if time.monotonic() >= deadline:
            break
        batch = items[start:start + batch_size]
        batch_indices = {idx for idx, _ in batch}
        try:
            outcome.passed.update(_run_xray_speed_items(batch, xray_bin, timeout, test_url, workers))
            outcome.attempted |= batch_indices
        except CoreInfrastructureError:
            if outcome.passed:
                break
            raise
    return outcome
