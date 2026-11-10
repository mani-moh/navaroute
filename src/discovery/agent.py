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
]

FALLBACK_GITHUB_QUERIES = [
    "v2ray-configs sub",
    "vless-configs subscription",
    "iran proxy configs sub.txt",
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
        """Seeds the candidate queue if queue is low or empty."""
        state = self.state_manager.get_state()
        queue = state.get("candidate_queue", [])
        if len(queue) < 3:
            self.state_manager.add_candidates(self.initial_seeds)

    def _expand_queries_via_nemotron(
        self,
        discovered_sources: dict[str, dict[str, Any]],
        max_github_probes: int = 15,
    ) -> None:
        """Uses Nemotron reasoning (with static fallback) to synthesize search dorks and expand queue."""
        state = self.state_manager.get_state()
        past_queries = state.get("queries_executed", [])[-15:]

        web_queries: list[str] = []
        gh_queries: list[str] = []

        if self.nemotron.is_available():
            prompt = (
                f"Generate creative, targeted search queries to discover new public Telegram channels "
                f"and GitHub repositories sharing free v2ray, vless, vmess, trojan, and hysteria2 subscriptions. "
                f"Focus on Iran / global censorship circumvention feeds. Avoid repeating these past queries: {past_queries}. "
                f'Respond ONLY with valid JSON: {{"web_queries": ["query1", "query2", "query3"], "github_queries": ["topic1", "topic2"]}}'
            )
            res = self.nemotron.complete_json(prompt)
            if isinstance(res, dict):
                raw_web = res.get("web_queries") or res.get("queries") or []
                raw_gh = res.get("github_queries") or []
                if isinstance(raw_web, list):
                    web_queries = [q for q in raw_web if isinstance(q, str) and q.strip()][:4]
                if isinstance(raw_gh, list):
                    gh_queries = [q for q in raw_gh if isinstance(q, str) and q.strip()][:2]

        # Fallback if Nemotron is unavailable or returned empty
        if not web_queries:
            web_queries = [q for q in FALLBACK_DORKS if q not in past_queries][:2]
        if not gh_queries:
            gh_queries = [q for q in FALLBACK_GITHUB_QUERIES if q not in past_queries][:1]

        # Execute web searches (DuckDuckGo)
        for q in web_queries:
            self.state_manager.record_query(q)
            channels, urls = self.web_scraper.search_duckduckgo(q, max_results=15)
            if channels:
                self.state_manager.add_candidates(channels)
            # Check raw GitHub/sub URLs discovered directly via DuckDuckGo
            for u in urls[:5]:
                if any(ext in u.lower() for ext in (".txt", "/sub", "raw.githubusercontent")):
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

        # Execute GitHub searches
        probed_gh = 0
        for gq in gh_queries:
            self.state_manager.record_query(f"gh:{gq}")
            candidate_raw_urls = self.web_scraper.search_github(gq, max_results=8)
            for raw_u in candidate_raw_urls:
                if probed_gh >= max_github_probes:
                    break
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

    def run_cycle(
        self,
        max_channels: int = 100,
        max_time_seconds: int = 360,
    ) -> list[dict[str, Any]]:
        """Runs a greedy discovery cycle up to max_channels or max_time_seconds budget."""
        start_time = time.time()
        self._ensure_seeds()

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
        if time.time() - start_time < (max_time_seconds - 20):
            try:
                self._expand_queries_via_nemotron(discovered_sources)
            except Exception as exc:
                logger.warning("Query expansion step failed softly: %s", exc)

        channels_checked = 0
        expansion_rounds = 0
        tg_unreachable_streak = 0
        tg_any_success = False
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

        # Step 2: Channel crawling loop
        while channels_checked < max_channels and (time.time() - start_time) < max_time_seconds:
            if not tg_any_success and tg_unreachable_streak >= 3:
                logger.info("Telegram web previews appear unreachable from this network; pausing TG crawl and preserving queue.")
                break

            batch = self.state_manager.pop_next_candidates(limit=min(5, max_channels - channels_checked))
            if not batch:
                # If queue exhausted but time remains, run another Nemotron expansion round
                if expansion_rounds < 2 and self.nemotron.is_available() and (time.time() - start_time) < (max_time_seconds - 30):
                    expansion_rounds += 1
                    try:
                        self._expand_queries_via_nemotron(discovered_sources)
                        batch = self.state_manager.pop_next_candidates(limit=min(5, max_channels - channels_checked))
                    except Exception:
                        pass
                if not batch:
                    break

            for handle in batch:
                if (time.time() - start_time) >= max_time_seconds:
                    break

                channels_checked += 1
                page_html = self.tg_scraper.fetch_channel_page(handle)
                if not page_html:
                    tg_unreachable_streak += 1
                    if not tg_any_success and tg_unreachable_streak >= 3:
                        # Put candidate back so GitHub Actions can inspect it later
                        self.state_manager.add_candidates([handle])
                        break
                    self.state_manager.record_visited(handle, nodes_found=0, is_active=False)
                    continue

                tg_any_success = True
                tg_unreachable_streak = 0

                res = self.tg_scraper.parse_channel_page(page_html, current_channel=handle)

                # Add cross-promoted sister channels to queue
                if res.discovered_channels:
                    self.state_manager.add_candidates(res.discovered_channels)

                # Count valid proxy URIs
                valid_nodes = 0
                for uri in res.proxy_uris:
                    if parse_proxy_uri(uri):
                        valid_nodes += 1

                # Deep mining: paginate backwards once if initial yield is sparse
                if valid_nodes < 5 and res.oldest_post_id and res.oldest_post_id > 1:
                    older_html = self.tg_scraper.fetch_channel_page(handle, before_id=res.oldest_post_id)
                    if older_html:
                        older_res = self.tg_scraper.parse_channel_page(older_html, current_channel=handle)
                        for uri in older_res.proxy_uris:
                            if parse_proxy_uri(uri):
                                valid_nodes += 1
                        if older_res.discovered_channels:
                            self.state_manager.add_candidates(older_res.discovered_channels)

                # Multimodal OCR via Nemotron 3.5 Super VL on image configs/QR codes
                if valid_nodes == 0 and res.image_urls and self.nemotron.is_available():
                    for img_url in res.image_urls[:2]:
                        if (time.time() - start_time) >= max_time_seconds:
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
                for sub_u in res.sub_urls[:3]:
                    if (time.time() - start_time) >= max_time_seconds:
                        break
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

                # Record visit in state
                is_active = valid_nodes > 0
                channel_sub_url = f"https://t.me/s/{handle}"
                self.state_manager.record_visited(
                    handle,
                    nodes_found=valid_nodes,
                    is_active=is_active,
                    sub_url=channel_sub_url,
                )

                # Register valid channel into discovered sources
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

        # Step 3: Save OCR configs, discovered sources, persistent state, and leaderboard
        if ocr_extracted_uris:
            # Keep latest 300 OCR-extracted nodes
            ocr_extracted_uris = ocr_extracted_uris[-300:]
            self.ocr_configs_file.parent.mkdir(parents=True, exist_ok=True)
            self.ocr_configs_file.write_text("\n".join(ocr_extracted_uris) + "\n", encoding="utf-8")
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
        help="Maximum channels to scan in this cycle (default: 100)",
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=360,
        help="Maximum execution time in seconds (default: 360)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override NVIDIA Nemotron model name",
    )
    args = parser.parse_args(argv)

    client = NemotronClient(model=args.model) if args.model else NemotronClient()
    agent = DiscoveryAgent(nemotron_client=client)

    print(f"🛰️ Starting Autonomous Discovery Agent (model={client.model}, budget={args.max_time}s, max_channels={args.max_channels})...")
    discovered = agent.run_cycle(max_channels=args.max_channels, max_time_seconds=args.max_time)
    print(f"✅ Discovery cycle finished: {len(discovered)} total active discovered sources.")
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
