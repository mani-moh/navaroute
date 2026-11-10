from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from src.discovery.nemotron_client import NemotronClient
from src.discovery.search_engine import (
    TelegramChannelScraper,
    WebSearchScraper,
    resilient_urlopen,
)
from src.discovery.state_manager import (
    DEFAULT_DISCOVERY_SOURCES_FILE,
    DEFAULT_LEADERBOARD_FILE,
    DEFAULT_STATE_FILE,
    IGNORE_CHANNEL_HANDLES,
    PROXY_HANDLE_KEYWORDS,
    DiscoveryStateManager,
    normalize_channel_handle,
)
from src.parsers import extract_nodes_from_content, parse_proxy_uri

logger = logging.getLogger(__name__)

CURATED_SEEDS = [
    "ConfigsHUB",
    "ConfigsHUB2",
    "ConfigsHubPlus",
    "ProxysHUB",
    "V2rayNG_VPNN",
    "FreeV2rays",
    "ProxyMTProto",
    "v2rayngvpn",
    "vmessiran",
    "customv2ray",
    "v2ray_outlineir",
    "v2rayNG_Config",
    "ShadowsocksM",
    "vlessconfig",
    "v2ray_collector",
    "V2rayNGn",
    "OutlineVpnOfficial",
]

FALLBACK_DORKS = [
    'site:t.me "vless://" reality',
    'site:t.me "vmess://" OR "trojan://" iran',
    'site:t.me "sub" "v2ray" "hysteria2"',
    'site:telegra.ph "vless://" OR "vmess://"',
]

FALLBACK_GITHUB_QUERIES = [
    "v2ray-configs sub",
    "vless-configs subscription",
    "iran proxy configs sub.txt",
    "hysteria2 vless reality sub",
]


class DiscoveryAgent:
    """Autonomous discovery agent orchestrator for proxy subscriptions and channels."""

    def __init__(
        self,
        state_file: str = DEFAULT_STATE_FILE,
        discovered_sources_file: str = DEFAULT_DISCOVERY_SOURCES_FILE,
        leaderboard_file: str = DEFAULT_LEADERBOARD_FILE,
        initial_seeds: Optional[list[str]] = None,
        nemotron_client: Optional[NemotronClient] = None,
        timeout: int = 15,
    ) -> None:
        self.state_manager = DiscoveryStateManager(state_file=state_file)
        self.discovered_sources_file = Path(discovered_sources_file)
        self.leaderboard_file = Path(leaderboard_file)
        self.ocr_configs_file = Path("data/discovered_ocr_configs.txt")
        self.initial_seeds = initial_seeds if initial_seeds is not None else CURATED_SEEDS
        self.nemotron = nemotron_client or NemotronClient()
        self.tg_scraper = TelegramChannelScraper(timeout=timeout)
        self.web_scraper = WebSearchScraper(timeout=timeout)

    def _ensure_seeds(self) -> None:
        """Cleans stale ignored handles from queue and seeds if queue is low or empty."""
        state = self.state_manager.get_state()
        rejected = set(state.get("rejected_channels", []))
        queue = state.get("candidate_queue", [])
        cleaned_queue = [
            h for h in queue
            if h not in IGNORE_CHANNEL_HANDLES
            and h not in rejected
            and not any(bad in h for bad in ("contest", "round2", "r2_2026"))
        ]
        state["candidate_queue"] = cleaned_queue
        if len(cleaned_queue) < 3:
            self.state_manager.add_candidates(self.initial_seeds)

    def _analyze_webpage_via_nemotron(
        self,
        url: str,
        discovered_sources: dict[str, dict[str, Any]],
        existing_public_urls: set[str],
        max_api_requests: int = 0,
        start_requests: int = 0,
    ) -> None:
        """Fetches a webpage/README snippet and asks Nemotron to extract sub URLs & TG handles."""
        if not self.nemotron.is_available() or self.state_manager.is_url_visited(url):
            return
        if max_api_requests > 0 and (self.nemotron.api_requests_count - start_requests) >= max_api_requests:
            return

        self.state_manager.record_visited_url(url)
        snippet = self.web_scraper.fetch_text_snippet(url, max_chars=4500)
        if not snippet or len(snippet) < 80:
            return

        prompt = (
            "Extract all proxy subscription URLs (such as raw.githubusercontent.com, gist, pastebin, or /sub links) "
            "and public Telegram channel handles (@username or t.me/username) that share v2ray/vless/vmess/trojan/hysteria2 configs "
            f"from the following webpage excerpt ({url}):\n\n{snippet}\n\n"
            'Respond ONLY with valid JSON: {"sub_urls": ["https://..."], "channels": ["channel_handle"]}'
        )
        res = self.nemotron.complete_json(prompt)
        if not isinstance(res, dict):
            return

        channels = res.get("channels") or []
        if isinstance(channels, list):
            clean_chs = [str(c) for c in channels if isinstance(c, str) and len(c.strip()) >= 4]
            if clean_chs:
                self.state_manager.add_candidates(clean_chs)

        sub_urls = res.get("sub_urls") or []
        if isinstance(sub_urls, list):
            for su in sub_urls[:5]:
                if not isinstance(su, str) or not su.startswith(("http://", "https://")):
                    continue
                if su in existing_public_urls:
                    continue
                nodes_cnt = self.probe_sub_url(su)
                if nodes_cnt > 0:
                    sub_id = f"sub-{hashlib.sha256(su.encode()).hexdigest()[:10]}"
                    discovered_sources[sub_id] = {
                        "id": sub_id,
                        "name": f"Web Mined ({su[:30]}...)",
                        "url": su,
                        "verified_upstream": False,
                        "format": "auto",
                        "tags": ["auto-discovered", "web-mined"],
                        "enabled": True,
                        "is_private": False,
                    }
                    self.state_manager.record_external_source(sub_id, f"web:{su[:32]}", nodes_cnt, su)

    def _filter_candidates_via_nemotron(
        self,
        handles: list[str],
        max_api_requests: int = 0,
        start_requests: int = 0,
    ) -> list[str]:
        """Filters questionable candidate handles using Nemotron so spam/contest bots are skipped."""
        if not handles or not self.nemotron.is_available():
            return handles
        if max_api_requests > 0 and (self.nemotron.api_requests_count - start_requests) >= max_api_requests:
            return handles

        obvious_proxy: list[str] = []
        ambiguous: list[str] = []
        for h in handles:
            if any(kw in h for kw in PROXY_HANDLE_KEYWORDS):
                obvious_proxy.append(h)
            else:
                ambiguous.append(h)

        if not ambiguous:
            return obvious_proxy

        prompt = (
            "Which of these Telegram usernames look like channels/bots sharing VPN, V2Ray, VLESS, VMess, Shadowsocks, "
            "or proxy configurations vs unrelated spam, stickers, contests, or personal blogs?\n"
            f"Usernames: {ambiguous[:20]}\n"
            'Respond ONLY with valid JSON containing the likely proxy/VPN handles: {"keep": ["handle1"]}'
        )
        res = self.nemotron.complete_json(prompt)
        if isinstance(res, dict) and isinstance(res.get("keep"), list):
            allowed = {normalize_channel_handle(str(x)) for x in res["keep"]}
            rejected = [h for h in ambiguous if h not in allowed]
            if rejected:
                self.state_manager.record_rejected_channels(rejected)
            filtered = obvious_proxy + [h for h in ambiguous if h in allowed]
            return filtered if filtered else obvious_proxy
        return handles

    def _expand_queries_via_nemotron(
        self,
        discovered_sources: dict[str, dict[str, Any]],
        existing_public_urls: Optional[set[str]] = None,
        max_github_probes: int = 18,
        max_api_requests: int = 0,
        start_requests: int = 0,
        extended_mode: bool = False,
    ) -> None:
        """Uses Nemotron reasoning (with static fallback) to synthesize search dorks and expand queue."""
        state = self.state_manager.get_state()
        past_queries = state.get("queries_executed", [])[-25:]
        top_sources = self.state_manager.get_top_active_handles(limit=8)
        existing_public_urls = existing_public_urls or set()

        web_queries: list[str] = []
        gh_queries: list[str] = []

        if self.nemotron.is_available():
            if max_api_requests <= 0 or (self.nemotron.api_requests_count - start_requests) < max_api_requests:
                prompt = (
                    "Generate creative, targeted search queries to discover brand-new public Telegram channels, "
                    "Telegraph pages, and GitHub repositories sharing free v2ray, vless reality, vmess, trojan, and hysteria2 subscriptions. "
                    f"Build upon top-yielding sources found so far ({top_sources}) to discover sister communities and aggregators. "
                    "Vary keywords across English, Persian/Farsi transliterations, Russian, and Chinese circumvention terms. "
                    f"Avoid repeating these past queries: {past_queries}. "
                    'Respond ONLY with valid JSON: {"web_queries": ["query1", "query2", "query3", "query4"], "github_queries": ["topic1", "topic2", "topic3"]}'
                )
                res = self.nemotron.complete_json(prompt)
                if isinstance(res, dict):
                    raw_web = res.get("web_queries") or res.get("queries") or []
                    raw_gh = res.get("github_queries") or []
                    if isinstance(raw_web, list):
                        web_queries = [q for q in raw_web if isinstance(q, str) and q.strip()][:5]
                    if isinstance(raw_gh, list):
                        gh_queries = [q for q in raw_gh if isinstance(q, str) and q.strip()][:3]

        # Fallback if Nemotron is unavailable or returned empty
        if not web_queries:
            web_queries = [q for q in FALLBACK_DORKS if q not in past_queries][:2]
        if not gh_queries:
            gh_queries = [q for q in FALLBACK_GITHUB_QUERIES if q not in past_queries][:2]

        # Execute web searches (DuckDuckGo)
        for q in web_queries:
            if max_api_requests > 0 and (self.nemotron.api_requests_count - start_requests) >= max_api_requests:
                break
            self.state_manager.record_query(q)
            channels, urls = self.web_scraper.search_duckduckgo(q, max_results=15)
            if channels:
                self.state_manager.add_candidates(channels)
            for u in urls[:6]:
                if u in existing_public_urls or self.state_manager.is_url_visited(u):
                    continue
                if any(ext in u.lower() for ext in (".txt", "/sub", "raw.githubusercontent")):
                    self.state_manager.record_visited_url(u)
                    nodes_cnt = self.probe_sub_url(u)
                    if nodes_cnt > 0:
                        sub_id = f"sub-{hashlib.sha256(u.encode()).hexdigest()[:10]}"
                        disp_name = f"web:{u[:32]}"
                        discovered_sources[sub_id] = {
                            "id": sub_id,
                            "name": f"Web Discovered ({u[:30]}...)",
                            "url": u,
                            "verified_upstream": False,
                            "format": "auto",
                            "tags": ["auto-discovered", "web"],
                            "enabled": True,
                            "is_private": False,
                        }
                        self.state_manager.record_external_source(sub_id, disp_name, nodes_cnt, u)
                elif extended_mode:
                    self._analyze_webpage_via_nemotron(
                        u, discovered_sources, existing_public_urls,
                        max_api_requests=max_api_requests, start_requests=start_requests,
                    )

        # Execute GitHub searches
        probed_gh = 0
        for gq in gh_queries:
            if max_api_requests > 0 and (self.nemotron.api_requests_count - start_requests) >= max_api_requests:
                break
            self.state_manager.record_query(f"gh:{gq}")
            candidate_raw_urls = self.web_scraper.search_github(gq, max_results=8)
            for raw_u in candidate_raw_urls:
                if probed_gh >= max_github_probes:
                    break
                if raw_u in existing_public_urls or self.state_manager.is_url_visited(raw_u):
                    continue
                self.state_manager.record_visited_url(raw_u)
                probed_gh += 1
                nodes_cnt = self.probe_sub_url(raw_u)
                if nodes_cnt > 0:
                    sub_id = f"gh-{hashlib.sha256(raw_u.encode()).hexdigest()[:10]}"
                    repo_name = f"{raw_u.split('/')[3]}/{raw_u.split('/')[4]}"
                    discovered_sources[sub_id] = {
                        "id": sub_id,
                        "name": f"GitHub Auto ({repo_name})",
                        "url": raw_u,
                        "verified_upstream": False,
                        "format": "auto",
                        "tags": ["auto-discovered", "github"],
                        "enabled": True,
                        "is_private": False,
                    }
                    self.state_manager.record_external_source(sub_id, f"gh:{repo_name}", nodes_cnt, raw_u)

            if extended_mode:
                readme_urls = self.web_scraper.search_github_readmes(gq, max_results=5)
                for r_url in readme_urls:
                    self._analyze_webpage_via_nemotron(
                        r_url, discovered_sources, existing_public_urls,
                        max_api_requests=max_api_requests, start_requests=start_requests,
                    )

    def probe_sub_url(self, url: str) -> int:
        """Probes an external subscription URL and returns valid node count."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Safari/537.36"
            )
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with resilient_urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                from src.models import SourceConfig
                dummy_source = SourceConfig(id="probe", name="Probe", url=url, format="auto")
                nodes, _ = extract_nodes_from_content(content, dummy_source)
                return len(nodes)
        except Exception:
            return 0

    def _save_checkpoint(
        self,
        discovered_sources: dict[str, dict[str, Any]],
        ocr_extracted_uris: list[str],
    ) -> list[dict[str, Any]]:
        """Persists current discovery outputs, state, OCR feed, and leaderboard to disk."""
        if ocr_extracted_uris:
            trimmed = ocr_extracted_uris[-400:]
            self.ocr_configs_file.parent.mkdir(parents=True, exist_ok=True)
            self.ocr_configs_file.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
            discovered_sources["nemotron-vision-ocr"] = {
                "id": "nemotron-vision-ocr",
                "name": "Nemotron 3.5 Super VL Vision OCR Feed",
                "url": "data/discovered_ocr_configs.txt",
                "verified_upstream": False,
                "format": "auto",
                "tags": ["auto-discovered", "vision-ocr", "nemotron"],
                "enabled": True,
                "is_private": False,
            }

        active_list = list(discovered_sources.values())
        self.discovered_sources_file.parent.mkdir(parents=True, exist_ok=True)
        self.discovered_sources_file.write_text(
            json.dumps({"sources": active_list}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.state_manager.save()

        leaderboard_content = self.state_manager.generate_leaderboard_markdown()
        self.leaderboard_file.parent.mkdir(parents=True, exist_ok=True)
        self.leaderboard_file.write_text(leaderboard_content, encoding="utf-8")
        return active_list

    def run_cycle(
        self,
        max_channels: int = 100,
        max_time_seconds: int = 360,
        max_api_requests: int = 0,
    ) -> list[dict[str, Any]]:
        """Runs a greedy discovery cycle bounded by max_channels, max_time_seconds, and optional max_api_requests."""
        start_time = time.time()
        start_requests = self.nemotron.api_requests_count
        extended_mode = max_api_requests > 0 and self.nemotron.is_available()

        if extended_mode and self.nemotron.min_request_interval <= 0:
            # Polite pacing across extended 500-request runs
            self.nemotron.min_request_interval = 1.1

        self._ensure_seeds()

        # Load existing public sources.json URLs to prevent duplicates
        existing_public_urls: set[str] = set()
        pub_p = Path("sources.json")
        if pub_p.exists():
            try:
                for item in json.loads(pub_p.read_text(encoding="utf-8")).get("sources", []):
                    if isinstance(item, dict) and item.get("url"):
                        existing_public_urls.add(item["url"])
            except Exception:
                pass

        # Load existing discovered sources
        discovered_sources: dict[str, dict[str, Any]] = {}
        if self.discovered_sources_file.exists():
            try:
                data = json.loads(self.discovered_sources_file.read_text(encoding="utf-8"))
                for s in data.get("sources", []):
                    if isinstance(s, dict) and "id" in s:
                        discovered_sources[s["id"]] = s
            except Exception:
                pass

        # Step 1: Initial search expansion
        if time.time() - start_time < max(5, max_time_seconds - 10):
            try:
                self._expand_queries_via_nemotron(
                    discovered_sources,
                    existing_public_urls=existing_public_urls,
                    max_api_requests=max_api_requests,
                    start_requests=start_requests,
                    extended_mode=extended_mode,
                )
            except Exception as exc:
                logger.warning("Query expansion step failed softly: %s", exc)

        channels_checked = 0
        tg_unreachable_streak = 0
        tg_any_success = False
        refresh_offset_handles: set[str] = set()
        ocr_extracted_uris: list[str] = []
        if self.ocr_configs_file.exists():
            try:
                ocr_extracted_uris = [
                    line.strip()
                    for line in self.ocr_configs_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and "://" in line
                ]
            except Exception:
                pass

        def _budget_remaining() -> bool:
            if (time.time() - start_time) >= max_time_seconds:
                return False
            if extended_mode:
                return (self.nemotron.api_requests_count - start_requests) < max_api_requests
            return channels_checked < max_channels

        # Step 2: Continuous crawling & expansion loop
        while _budget_remaining():
            if not tg_any_success and tg_unreachable_streak >= 3:
                if extended_mode:
                    # Keep running web/GitHub discovery via Nemotron even if TG web is blocked
                    try:
                        self._expand_queries_via_nemotron(
                            discovered_sources,
                            existing_public_urls=existing_public_urls,
                            max_api_requests=max_api_requests,
                            start_requests=start_requests,
                            extended_mode=True,
                        )
                        continue
                    except Exception:
                        break
                logger.info("Telegram web previews appear unreachable from this network; pausing TG crawl.")
                break

            batch_limit = 8 if extended_mode else min(5, max(1, max_channels - channels_checked))
            batch = self.state_manager.pop_next_candidates(limit=batch_limit)

            # In extended mode, filter batch candidates via Nemotron if ambiguous
            if extended_mode and batch and self.nemotron.is_available():
                batch = self._filter_candidates_via_nemotron(
                    batch,
                    max_api_requests=max_api_requests,
                    start_requests=start_requests,
                )

            if not batch:
                if self.nemotron.is_available() and _budget_remaining():
                    try:
                        self._expand_queries_via_nemotron(
                            discovered_sources,
                            existing_public_urls=existing_public_urls,
                            max_api_requests=max_api_requests,
                            start_requests=start_requests,
                            extended_mode=extended_mode,
                        )
                        batch = self.state_manager.pop_next_candidates(limit=batch_limit)
                    except Exception:
                        pass

                # If queue still empty in extended mode, re-mine oldest active TG channels deeper
                if not batch and extended_mode and _budget_remaining():
                    oldest = [
                        h for h in self.state_manager.get_oldest_active_channels(limit=10)
                        if h not in refresh_offset_handles
                    ]
                    if oldest:
                        batch = oldest[:5]
                        refresh_offset_handles.update(batch)
                    else:
                        refresh_offset_handles.clear()
                        oldest = self.state_manager.get_oldest_active_channels(limit=5)
                        if oldest:
                            batch = oldest

                if not batch:
                    break

            for handle in batch:
                if not _budget_remaining():
                    break

                channels_checked += 1
                resume_before = (
                    self.state_manager.get_channel_oldest_post_id(handle)
                    if handle in refresh_offset_handles
                    else None
                )
                page_html = self.tg_scraper.fetch_channel_page(handle, before_id=resume_before)
                if not page_html:
                    tg_unreachable_streak += 1
                    if not tg_any_success and tg_unreachable_streak >= 3:
                        self.state_manager.add_candidates([handle])
                        break
                    self.state_manager.record_visited(handle, nodes_found=0, is_active=False)
                    continue

                tg_any_success = True
                tg_unreachable_streak = 0
                res = self.tg_scraper.parse_channel_page(page_html, current_channel=handle)

                if res.discovered_channels:
                    self.state_manager.add_candidates(res.discovered_channels)

                valid_nodes = 0
                for uri in res.proxy_uris:
                    if parse_proxy_uri(uri):
                        valid_nodes += 1

                # Deep pagination (up to 2 pages back in extended mode)
                max_pages_back = 2 if extended_mode else 1
                cur_oldest = res.oldest_post_id
                cur_images = list(res.image_urls)
                cur_subs = list(res.sub_urls)
                for _ in range(max_pages_back):
                    if not (valid_nodes < (20 if extended_mode else 5) and cur_oldest and cur_oldest > 1):
                        break
                    older_html = self.tg_scraper.fetch_channel_page(handle, before_id=cur_oldest)
                    if not older_html:
                        break
                    older_res = self.tg_scraper.parse_channel_page(older_html, current_channel=handle)
                    for uri in older_res.proxy_uris:
                        if parse_proxy_uri(uri):
                            valid_nodes += 1
                    if older_res.discovered_channels:
                        self.state_manager.add_candidates(older_res.discovered_channels)
                    cur_images.extend(u for u in older_res.image_urls if u not in cur_images)
                    cur_subs.extend(u for u in older_res.sub_urls if u not in cur_subs)
                    cur_oldest = older_res.oldest_post_id

                # Multimodal OCR via Nemotron 3.5 Super VL on base64-downloaded images
                should_ocr = bool(cur_images and self.nemotron.is_available() and (valid_nodes == 0 or extended_mode))
                if should_ocr:
                    for img_url in cur_images[: (3 if extended_mode else 2)]:
                        if not _budget_remaining():
                            break
                        try:
                            ocr_text = self.nemotron.analyze_image(
                                img_url,
                                prompt="Extract any vless://, vmess://, trojan://, ss://, or hysteria2:// config links visible."
                            )
                            for uri_match in [u.strip() for u in ocr_text.split() if "://" in u]:
                                clean_uri = uri_match.rstrip(".,;!)\"'>")
                                if parse_proxy_uri(clean_uri):
                                    valid_nodes += 1
                                    if clean_uri not in ocr_extracted_uris:
                                        ocr_extracted_uris.append(clean_uri)
                        except Exception:
                            pass

                # Probe external sub URLs posted in the channel
                for sub_u in cur_subs[:5]:
                    if not _budget_remaining():
                        break
                    if sub_u in existing_public_urls or self.state_manager.is_url_visited(sub_u):
                        continue
                    self.state_manager.record_visited_url(sub_u)
                    sub_nodes = self.probe_sub_url(sub_u)
                    if sub_nodes > 0:
                        sub_id = f"sub-{hashlib.sha256(sub_u.encode()).hexdigest()[:10]}"
                        if sub_id not in discovered_sources:
                            discovered_sources[sub_id] = {
                                "id": sub_id,
                                "name": f"Discovered Sub ({sub_u[:30]}...)",
                                "url": sub_u,
                                "verified_upstream": False,
                                "format": "auto",
                                "tags": ["auto-discovered", "subscription"],
                                "enabled": True,
                                "is_private": False,
                            }
                        valid_nodes += sub_nodes

                is_active = valid_nodes > 0
                channel_sub_url = f"https://t.me/s/{handle}"
                self.state_manager.record_visited(
                    handle,
                    nodes_found=valid_nodes,
                    is_active=is_active,
                    sub_url=channel_sub_url,
                    oldest_post_id=cur_oldest,
                )

                source_id = f"tg-{handle}"
                if is_active:
                    discovered_sources[source_id] = {
                        "id": source_id,
                        "name": f"Telegram @{handle}",
                        "url": channel_sub_url,
                        "verified_upstream": False,
                        "format": "telegram_web",
                        "tags": ["auto-discovered", "telegram", "iran"],
                        "enabled": True,
                        "is_private": False,
                    }

                # Periodic expansion & checkpoint every 15 channels in extended mode
                if extended_mode and channels_checked % 15 == 0 and _budget_remaining():
                    try:
                        self._expand_queries_via_nemotron(
                            discovered_sources,
                            existing_public_urls=existing_public_urls,
                            max_api_requests=max_api_requests,
                            start_requests=start_requests,
                            extended_mode=True,
                        )
                    except Exception:
                        pass
                    self._save_checkpoint(discovered_sources, ocr_extracted_uris)

        requests_used = self.nemotron.api_requests_count - start_requests
        self.state_manager.record_api_usage(requests_used)
        return self._save_checkpoint(discovered_sources, ocr_extracted_uris)


def load_local_dotenv(dotenv_path: str = ".env") -> None:
    """Safely loads key=value pairs from local .env without overriding existing env vars."""
    p = Path(dotenv_path)
    if not p.is_file():
        return
    try:
        for raw_line in p.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def run_cli(argv: Optional[list[str]] = None) -> int:
    load_local_dotenv()
    parser = argparse.ArgumentParser(
        prog="pattn-discovery",
        description="PattN Autonomous Discovery Agent powered by NVIDIA Nemotron 3.5 Super VL",
    )
    parser.add_argument(
        "--max-channels",
        type=int,
        default=100,
        help="Maximum channels to scan in standard cycle (default: 100)",
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=360,
        help="Maximum execution time in seconds (default: 360)",
    )
    parser.add_argument(
        "--max-api-requests",
        type=int,
        default=0,
        help="Target number of NVIDIA API requests for extended deep runs (e.g. 500)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override NVIDIA Nemotron model name",
    )
    args = parser.parse_args(argv)

    client = NemotronClient(model=args.model) if args.model else NemotronClient()
    agent = DiscoveryAgent(nemotron_client=client)

    print(
        f"🛰️ Starting Autonomous Discovery Agent "
        f"(model={client.model}, budget={args.max_time}s, max_channels={args.max_channels}, max_api_requests={args.max_api_requests})..."
    )
    discovered = agent.run_cycle(
        max_channels=args.max_channels,
        max_time_seconds=args.max_time,
        max_api_requests=args.max_api_requests,
    )
    print(
        f"✅ Discovery cycle finished: {len(discovered)} total active discovered sources "
        f"({client.api_requests_count} NVIDIA API requests used)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
