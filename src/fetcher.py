from __future__ import annotations

import concurrent.futures
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from src.models import ProxyNode, SourceConfig, SourceStats
from src.parsers import extract_nodes_from_content

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

    # 1. Load public sources from sources.json
    p = Path(sources_path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for item in data.get("sources", []):
                cfg = SourceConfig.from_dict(item)
                if cfg.enabled and cfg.id not in seen_ids:
                    sources.append(cfg)
                    seen_ids.add(cfg.id)
        except Exception:
            pass

    # 2. Load auto-discovered sources from discovered_sources_path if it exists
    disc_p = Path(discovered_sources_path)
    if disc_p.exists():
        try:
            disc_data = json.loads(disc_p.read_text(encoding="utf-8"))
            for item in disc_data.get("sources", []):
                cfg = SourceConfig.from_dict(item)
                if "auto-discovered" not in cfg.tags:
                    cfg.tags.append("auto-discovered")
                if cfg.enabled and cfg.id not in seen_ids:
                    sources.append(cfg)
                    seen_ids.add(cfg.id)
        except Exception:
            pass

    return sources


def _fetch_url(url: str, timeout: float = 20.0) -> tuple[int, str]:
    """Fetch URL with realistic headers or read safe local cache file."""
    if not url.startswith(("http://", "https://")):
        local_path = Path(url)
        # Allow reading safe repo-local discovery caches
        if local_path.is_file() and (
            url.startswith("data/") or url.startswith("data\\")
        ):
            return 200, local_path.read_text(encoding="utf-8", errors="replace")
        raise ValueError(f"Unsupported URL scheme or unsafe local path: {url}")

    try:
        import httpx

        with httpx.Client(
            follow_redirects=True, timeout=timeout, headers=DEFAULT_HEADERS
        ) as client:
            resp = client.get(url)
            return resp.status_code, resp.text
    except ImportError:
        pass
    except Exception:
        # Fall back to urllib if httpx fails
        pass

    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    has_explicit_proxy = bool(
        os.environ.get("HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("ALL_PROXY")
    )

    if not has_explicit_proxy:
        # Prefer direct connection so dead local proxies (e.g. stopped local clients on 127.0.0.1:10808) don't block fetching
        opener_direct = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPHandler(),
        )
        try:
            with opener_direct.open(req, timeout=timeout) as response:
                status = response.status if hasattr(response, "status") else 200
                content = response.read().decode("utf-8", errors="replace")
                return status, content
        except Exception:
            # Fall back to default opener in case proxy was actually required
            pass

    opener_default = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPHandler(),
    )
    with opener_default.open(req, timeout=timeout) as response:
        status = response.status if hasattr(response, "status") else 200
        content = response.read().decode("utf-8", errors="replace")
        return status, content


def fetch_source(
    source: SourceConfig, timeout: int = 20
) -> tuple[list[ProxyNode], list[dict], SourceStats]:
    """Fetch content for a single source, parse its nodes, and record statistics."""
    start_t = time.perf_counter()
    nodes: list[ProxyNode] = []
    json_configs: list[dict] = []

    try:
        status_code, content = _fetch_url(source.url, timeout=float(timeout))
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
                    _, page2_content = _fetch_url(page2_url, timeout=float(timeout))
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
            url=source.url,
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
            url=source.url,
            success=False,
            status_code=None,
            elapsed_ms=round(elapsed_ms, 1),
            raw_count=0,
            unique_count=0,
            verified_count=0,
            error=str(exc),
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
            src = future_to_source[future]
            try:
                nodes, jsons, stats = future.result()
                all_nodes.extend(nodes)
                all_json_configs.extend(jsons)
                all_stats.append(stats)
            except Exception as exc:
                all_stats.append(
                    SourceStats(
                        source_id=src.id,
                        name=src.name,
                        url=src.url,
                        success=False,
                        error=str(exc),
                    )
                )

    # Sort stats by source_id for predictable reporting
    all_stats.sort(key=lambda s: s.source_id)
    return all_nodes, all_json_configs, all_stats
