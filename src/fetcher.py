from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
import urllib.error
import urllib.parse
from pathlib import Path

from src.models import ProxyNode, SourceConfig, SourceStats
from src.parsers import extract_nodes_from_content
from src.network import NetworkError, approved_public_url, fetch_bytes

SOURCE_MAX_BYTES = 32 * 1024 * 1024
# shortcut: bound raw model multiplication for current runners; raise only after measured memory upgrades.
AGGREGATE_MAX_NODES = 250_000
logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
}


def load_sources(
    sources_path: str = "sources.json",
    discovered_sources_path: str = "sources.discovered.json",
) -> list[SourceConfig]:
    """Loads enabled sources from sources.json and sources.discovered.json."""
    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()

    for path, discovered in ((Path(sources_path), False), (Path(discovered_sources_path), True)):
        try:
            with path.open("rb") as stream:
                content = stream.read(SOURCE_MAX_BYTES + 1)
            if len(content) > SOURCE_MAX_BYTES:
                raise ValueError
            data = json.loads(content)
            if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
                raise ValueError
        except FileNotFoundError:
            if discovered:
                continue
            raise ValueError("Cannot read source registry") from None
        except (OSError, ValueError, UnicodeError):
            raise ValueError("Cannot read source registry") from None
        for index, item in enumerate(data["sources"]):
            try:
                cfg = SourceConfig.from_dict(item)
                if discovered and "auto-discovered" not in cfg.tags:
                    cfg.tags.append("auto-discovered")
                if cfg.enabled and cfg.id not in seen_ids:
                    sources.append(cfg)
                    seen_ids.add(cfg.id)
            except (ValueError, TypeError, KeyError, AttributeError):
                logger.warning("Skipping invalid source registry row %d", index + 1)

    return sources


def _fetch_url(url: str, timeout: float = 20.0) -> tuple[int, str]:
    """Fetch URL with realistic headers or read safe local cache file."""
    if not isinstance(url, str):
        raise ValueError("Invalid source URL")
    if not url.lower().startswith(("http://", "https://")):
        try:
            cwd = Path.cwd().resolve()
            root = (cwd / "data").resolve()
            local_path = Path(url.replace("\\", "/")).resolve()
        except (OSError, RuntimeError, ValueError):
            raise ValueError("Unsafe local cache path") from None
        if (not url.startswith(("data/", "data\\")) or root != cwd / "data"
                or not local_path.is_relative_to(root) or local_path == root):
            raise ValueError("Unsafe local cache path")
        try:
            if not local_path.is_file():
                raise NetworkError("Cannot read source cache")
            with local_path.open("rb") as stream:
                content = stream.read(SOURCE_MAX_BYTES + 1)
        except OSError:
            raise NetworkError("Cannot read source cache") from None
        if len(content) > SOURCE_MAX_BYTES:
            raise ValueError("Source cache exceeds byte limit")
        status = 200
    else:
        status, content = fetch_bytes(url, timeout=timeout, max_bytes=SOURCE_MAX_BYTES, headers=DEFAULT_HEADERS)
    try:
        return status, content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Source is not valid UTF-8") from None


def _report_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            host = parsed.hostname
            authority = f"[{host}]" if ":" in host else host
            if parsed.port is not None:
                authority += f":{parsed.port}"
            return urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
        return "local cache" if url.startswith(("data/", "data\\")) else "invalid source URL"
    except ValueError:
        return "invalid source URL"


def _report_error(exc: Exception) -> str:
    if isinstance(exc, NetworkError):
        # Cause chain carries fixed stdlib class names only, never URLs or addresses.
        cause = exc.__cause__
        if cause is not None:
            return f"{exc} ({type(cause).__name__})"
        return str(exc)
    if isinstance(exc, urllib.error.URLError) and exc.reason == "Connection refused":
        return "Connection refused"
    return f"Fetch failed ({type(exc).__name__})"


def fetch_source(
    source: SourceConfig, timeout: int = 20
) -> tuple[list[ProxyNode], list[dict], SourceStats]:
    """Fetch content for a single source, parse its nodes, and record statistics."""
    start_t = time.perf_counter()
    nodes: list[ProxyNode] = []
    json_configs: list[dict] = []
    status_code = None

    try:
        approved_public_url(source.url)
        status_code, content = _fetch_url(source.url, timeout=float(timeout))
        if not 200 <= status_code < 300:
            raise NetworkError("Source returned unsuccessful HTTP status")
        extracted_nodes, extracted_jsons = extract_nodes_from_content(content, source)
        nodes.extend(extracted_nodes)
        json_configs.extend(extracted_jsons)

        # Telegram web preview pagination: follow 1 page back if ?before= is present
        if source.format == "telegram_web" or "t.me/s/" in source.url:
            before_match = re.search(r'\?before=(\d+)', content)
            if before_match:
                before_id = before_match.group(1)
                page2_url = f"{source.url.split('?')[0]}?before={before_id}"
                try:
                    page2_status, page2_content = _fetch_url(page2_url, timeout=float(timeout))
                    if not 200 <= page2_status < 300:
                        raise NetworkError("Source returned unsuccessful HTTP status")
                    p2_nodes, p2_jsons = extract_nodes_from_content(page2_content, source)
                    nodes.extend(p2_nodes)
                    json_configs.extend(p2_jsons)
                except Exception:
                    pass

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        unique_count = len({n.semantic_fingerprint() for n in nodes})
        verified_count = len(nodes) if source.verified_upstream else 0

        stats = SourceStats(
            source_id=source.id,
            name=source.name,
            url=_report_url(source.url),
            success=True,
            status_code=status_code,
            elapsed_ms=round(elapsed_ms, 1),
            raw_count=len(nodes),
            unique_count=unique_count,
            verified_count=verified_count,
        )
        return nodes, json_configs, stats

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        stats = SourceStats(
            source_id=source.id,
            name=source.name,
            url=_report_url(source.url),
            success=False,
            status_code=status_code,
            elapsed_ms=round(elapsed_ms, 1),
            raw_count=0,
            unique_count=0,
            verified_count=0,
            error=_report_error(exc),
        )
        return [], [], stats


def fetch_all_sources(
    sources: list[SourceConfig], max_workers: int = 16, timeout: int = 20
) -> tuple[list[ProxyNode], list[dict], list[SourceStats]]:
    """Concurrently fetches all sources using a thread pool.

    Returns (all_extracted_nodes, all_json_configs, all_source_stats).
    """
    all_nodes: list[ProxyNode] = []
    all_json_configs: list[dict] = []
    all_stats: list[SourceStats] = []

    if not sources:
        return all_nodes, all_json_configs, all_stats

    workers = min(max_workers, max(1, len(sources)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_source = {
            executor.submit(fetch_source, src, timeout): src for src in sources
        }
        for future in concurrent.futures.as_completed(future_to_source):
            src = future_to_source.pop(future)
            try:
                nodes, jsons, stats = future.result()
            except Exception as exc:
                all_stats.append(
                    SourceStats(
                        source_id=src.id,
                        name=src.name,
                        url=_report_url(src.url),
                        success=False,
                        error=_report_error(exc),
                    )
                )
            else:
                if len(all_nodes) + len(nodes) > AGGREGATE_MAX_NODES:
                    for pending in future_to_source:
                        pending.cancel()
                    future_to_source.clear()
                    raise ValueError("Aggregate raw node limit exceeded")
                all_nodes.extend(nodes)
                all_json_configs.extend(jsons)
                all_stats.append(stats)

    # Sort stats by source_id for predictable reporting
    all_stats.sort(key=lambda s: s.source_id)
    return all_nodes, all_json_configs, all_stats
