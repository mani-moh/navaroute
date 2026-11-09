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
    include_private: bool = False,
    private_sources_path: str = "sources.private.json",
) -> list[SourceConfig]:
    """Loads enabled sources from sources.json, and optionally private sources

    from private_sources_path and/or the PRIVATE_SOURCES environment variable.
    """
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

    # 2. Optionally load private sources
    if include_private:
        # Load from private_sources_path if it exists
        priv_p = Path(private_sources_path)
        if priv_p.exists():
            try:
                priv_data = json.loads(priv_p.read_text(encoding="utf-8"))
                for item in priv_data.get("sources", []):
                    cfg = SourceConfig.from_dict(item)
                    cfg.is_private = True
                    if "private" not in cfg.tags:
                        cfg.tags.append("private")
                    if cfg.enabled and cfg.id not in seen_ids:
                        sources.append(cfg)
                        seen_ids.add(cfg.id)
            except Exception:
                pass

        # Load from PRIVATE_SOURCES environment variable
        env_priv = os.environ.get("PRIVATE_SOURCES", "").strip()
        if env_priv:
            # Try parsing as JSON first
            parsed_json = None
            try:
                parsed_json = json.loads(env_priv)
            except Exception:
                pass

            if parsed_json is not None:
                items: list[Any] = []
                if isinstance(parsed_json, dict) and "sources" in parsed_json:
                    items = parsed_json["sources"]
                elif isinstance(parsed_json, list):
                    items = parsed_json

                for idx, entry in enumerate(items, start=1):
                    if isinstance(entry, dict):
                        cfg = SourceConfig.from_dict(entry)
                        cfg.is_private = True
                        if "private" not in cfg.tags:
                            cfg.tags.append("private")
                        if cfg.enabled and cfg.id not in seen_ids:
                            sources.append(cfg)
                            seen_ids.add(cfg.id)
                    elif isinstance(entry, str) and entry.strip().startswith("http"):
                        url = entry.strip()
                        cfg_id = f"private-env-{idx}"
                        if cfg_id not in seen_ids:
                            sources.append(
                                SourceConfig(
                                    id=cfg_id,
                                    name=f"Private Env {idx}",
                                    url=url,
                                    verified_upstream=True,
                                    format="auto",
                                    tags=["private", "bpb"],
                                    enabled=True,
                                    is_private=True,
                                )
                            )
                            seen_ids.add(cfg_id)
            else:
                # Comma or newline separated URLs
                lines = [line.strip() for line in re.split(r"[\r\n,]+", env_priv) if line.strip()]
                for idx, line in enumerate(lines, start=1):
                    if line.startswith("http"):
                        cfg_id = f"private-env-{idx}"
                        if cfg_id not in seen_ids:
                            sources.append(
                                SourceConfig(
                                    id=cfg_id,
                                    name=f"Private Env {idx}",
                                    url=line,
                                    verified_upstream=True,
                                    format="auto",
                                    tags=["private", "bpb"],
                                    enabled=True,
                                    is_private=True,
                                )
                            )
                            seen_ids.add(cfg_id)

    return sources


def _fetch_url(url: str, timeout: float = 20.0) -> tuple[int, str]:
    """Fetch URL with realistic headers. Uses httpx if available, fallback to urllib."""
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

        # Tag nodes and json configs with private status if source is private
        is_priv = source.is_private or "private" in source.tags
        if is_priv:
            for n in nodes:
                n.is_private = True
                n.tags.add("private")
            for j in json_configs:
                j["_is_private"] = True

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
            is_private=is_priv,
        )
        return nodes, json_configs, stats

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        is_priv = source.is_private or "private" in source.tags
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
            is_private=is_priv,
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
                        is_private=src.is_private or "private" in src.tags,
                    )
                )

    # Sort stats by source_id for predictable reporting
    all_stats.sort(key=lambda s: s.source_id)
    return all_nodes, all_json_configs, all_stats
