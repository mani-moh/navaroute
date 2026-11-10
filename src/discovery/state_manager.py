from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "data/discovery_state.json"
DEFAULT_DISCOVERY_SOURCES_FILE = "sources.discovered.json"
DEFAULT_LEADERBOARD_FILE = "DISCOVERY.md"


PROXY_HANDLE_KEYWORDS = (
    "v2ray", "vless", "vmess", "trojan", "vpn", "proxy", "proxys", "sub",
    "config", "reality", "shadow", "hy2", "hysteria", "outline", "iran",
    "free", "freedom", "mtproto", "warp", "net", "bypass", "xray",
    "sing", "clash", "nekobox", "node", "server", "tunnel",
)

IGNORE_CHANNEL_HANDLES = {
    "joinchat", "addstickers", "stickers", "c", "share", "socks", "telegram",
    "contest", "durov", "tgramdom", "proxy", "channel", "group",
}


def normalize_channel_handle(raw_handle: str) -> str:
    """Extracts a clean telegram channel username from URLs or @mentions."""
    clean = raw_handle.strip()
    clean = re.sub(r"^https?://(?:www\.)?t\.me/(?:s/)?", "", clean, flags=re.IGNORECASE)
    clean = clean.lstrip("@").strip("/?#")
    # Take first path component if subpaths exist (e.g. chan/123 -> chan)
    clean = clean.split("/")[0]
    return clean.lower()


class DiscoveryStateManager:
    """Manages persistent discovery memory, candidate queue, and source reputation."""

    def __init__(self, state_file: str = DEFAULT_STATE_FILE) -> None:
        self.state_file = Path(state_file)
        self.state: dict[str, Any] = {
            "version": 1,
            "last_updated": 0,
            "queries_executed": [],
            "candidate_queue": [],      # List of channel names
            "candidate_scores": {},     # handle -> mention frequency
            "visited_channels": [],     # List of visited channel handles
            "rejected_channels": [],    # Handles explicitly rejected as off-topic spam
            "visited_urls": [],         # External web/GitHub URLs already probed or mined
            "sources_metadata": {},     # handle/id -> performance record
        }
        self.load()

    def load(self) -> None:
        """Loads state from disk if it exists."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.state.update(data)
            except Exception as exc:
                logger.warning("Failed to load discovery state from %s: %s", self.state_file, exc)

    def save(self) -> None:
        """Persists state to disk atomically."""
        self.state["last_updated"] = int(time.time())
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            temp_file.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_file.replace(self.state_file)
        except Exception as exc:
            logger.error("Failed to save discovery state: %s", exc)

    def get_state(self) -> dict[str, Any]:
        return self.state

    def add_candidates(self, candidates: list[str]) -> None:
        """Adds new candidate channels, sanitizing handles and boosting proxy-relevant scores."""
        visited = set(self.state.get("visited_channels", []))
        rejected = set(self.state.get("rejected_channels", []))
        queue = self.state.setdefault("candidate_queue", [])
        scores = self.state.setdefault("candidate_scores", {})

        for raw in candidates:
            handle = normalize_channel_handle(raw)
            # Skip invalid, contest/sticker spam, rejected, or already visited handles
            if not handle or len(handle) < 3 or handle in visited or handle in rejected:
                continue
            if handle in IGNORE_CHANNEL_HANDLES or any(bad in handle for bad in ("contest", "round2", "r2_2026")):
                continue

            is_new = handle not in scores
            bonus = 5 if (is_new and any(kw in handle for kw in PROXY_HANDLE_KEYWORDS)) else 0
            scores[handle] = scores.get(handle, 0) + 1 + bonus
            if handle not in queue:
                queue.append(handle)

        # Sort queue by priority score (descending)
        queue.sort(key=lambda h: scores.get(h, 1), reverse=True)

    def record_rejected_channels(self, handles: list[str]) -> None:
        """Permanently ignores handles identified as spam/off-topic across future runs."""
        rejected = self.state.setdefault("rejected_channels", [])
        queue = self.state.setdefault("candidate_queue", [])
        for raw in handles:
            h = normalize_channel_handle(raw)
            if h and h not in rejected:
                rejected.append(h)
            if h in queue:
                queue.remove(h)

    def is_url_visited(self, url: str) -> bool:
        """Checks whether an external URL has already been probed or mined."""
        return url in set(self.state.get("visited_urls", []))

    def record_visited_url(self, url: str) -> None:
        """Records an external URL so future runs build incrementally without repeating checks."""
        urls = self.state.setdefault("visited_urls", [])
        if url not in urls:
            urls.append(url)
            # Keep bounded to recent 5000 URLs
            if len(urls) > 5000:
                self.state["visited_urls"] = urls[-5000:]

    def get_channel_oldest_post_id(self, raw_handle: str) -> Optional[int]:
        """Returns the deepest crawled post ID for a channel so re-mining resumes deeper."""
        handle = normalize_channel_handle(raw_handle)
        meta = self.state.get("sources_metadata", {}).get(handle, {})
        val = meta.get("oldest_post_id")
        return int(val) if isinstance(val, int) and val > 1 else None

    def get_top_active_handles(self, limit: int = 8) -> list[str]:
        """Returns highest-yielding active source names/handles for LLM context."""
        meta = self.state.get("sources_metadata", {})
        active = [
            info for info in meta.values()
            if info.get("is_active") and info.get("last_nodes_yield", 0) > 0
        ]
        active.sort(key=lambda s: s.get("total_nodes_yield", 0), reverse=True)
        return [str(s["handle"]) for s in active[:limit] if "handle" in s]

    def get_oldest_active_channels(self, limit: int = 5) -> list[str]:
        """Returns previously active Telegram channels sorted by oldest last_checked."""
        meta = self.state.get("sources_metadata", {})
        active_tg = [
            info for info in meta.values()
            if info.get("is_active") and info.get("is_telegram", True) and info.get("sub_url", "").startswith("https://t.me/")
        ]
        active_tg.sort(key=lambda s: s.get("last_checked", 0))
        return [s["handle"] for s in active_tg[:limit] if "handle" in s]

    def pop_next_candidates(self, limit: int = 5) -> list[str]:
        """Pops up to `limit` candidates with highest mention score."""
        queue = self.state.setdefault("candidate_queue", [])
        if not queue:
            return []

        popped = queue[:limit]
        self.state["candidate_queue"] = queue[limit:]
        return popped

    def record_visited(
        self,
        raw_handle: str,
        nodes_found: int,
        is_active: bool,
        sub_url: Optional[str] = None,
        oldest_post_id: Optional[int] = None,
    ) -> None:
        """Records channel visit metrics, reputation, and deepest crawled post ID."""
        handle = normalize_channel_handle(raw_handle)
        if not handle:
            return

        visited = self.state.setdefault("visited_channels", [])
        if handle not in visited:
            visited.append(handle)

        # Remove from queue if still present
        queue = self.state.setdefault("candidate_queue", [])
        if handle in queue:
            queue.remove(handle)

        meta = self.state.setdefault("sources_metadata", {})
        existing = meta.get(handle, {})
        times_seen = existing.get("checks_count", 0) + 1
        prev_oldest = existing.get("oldest_post_id")
        if oldest_post_id and prev_oldest:
            deepest_id = min(int(oldest_post_id), int(prev_oldest))
        else:
            deepest_id = oldest_post_id or prev_oldest

        entry: dict[str, Any] = {
            "handle": handle,
            "last_checked": int(time.time()),
            "last_nodes_yield": nodes_found,
            "total_nodes_yield": existing.get("total_nodes_yield", 0) + nodes_found,
            "checks_count": times_seen,
            "is_active": is_active,
            "sub_url": sub_url or f"https://t.me/s/{handle}",
        }
        if deepest_id is not None:
            entry["oldest_post_id"] = deepest_id
        meta[handle] = entry

    def record_query(self, query: str) -> None:
        """Records an executed search query."""
        queries = self.state.setdefault("queries_executed", [])
        q_clean = query.strip()
        if q_clean and q_clean not in queries:
            queries.append(q_clean)

    def record_external_source(
        self,
        source_id: str,
        display_name: str,
        nodes_found: int,
        sub_url: str,
    ) -> None:
        """Records metrics for non-Telegram sources (GitHub/Web) without polluting Telegram channel queue."""
        meta = self.state.setdefault("sources_metadata", {})
        existing = meta.get(source_id, {})
        times_seen = existing.get("checks_count", 0) + 1
        meta[source_id] = {
            "handle": display_name,
            "last_checked": int(time.time()),
            "last_nodes_yield": nodes_found,
            "total_nodes_yield": existing.get("total_nodes_yield", 0) + nodes_found,
            "checks_count": times_seen,
            "is_active": nodes_found > 0,
            "sub_url": sub_url,
            "is_telegram": False,
        }

    def record_api_usage(self, requests_made: int) -> None:
        """Tracks NVIDIA API requests used in the run and cumulatively."""
        self.state["last_run_api_requests"] = requests_made
        self.state["total_api_requests"] = self.state.get("total_api_requests", 0) + requests_made

    def generate_leaderboard_markdown(self) -> str:
        """Generates an aesthetic markdown report of discovered sources."""
        meta = self.state.get("sources_metadata", {})
        visited_count = len(self.state.get("visited_channels", []))
        queued_count = len(self.state.get("candidate_queue", []))

        # Filter active channels with nodes
        active_sources = [
            info for info in meta.values()
            if info.get("is_active") and info.get("last_nodes_yield", 0) > 0
        ]
        # Sort by latest nodes yield descending
        active_sources.sort(key=lambda s: s.get("last_nodes_yield", 0), reverse=True)

        total_nodes = sum(s.get("last_nodes_yield", 0) for s in active_sources)
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        lines = [
            "# 🛰️ Autonomous Subscription Discovery Leaderboard",
            "",
            "> Real-time ranking of auto-discovered proxy feeds, Telegram channels, and aggregators",
            "> *Updated automatically by PattN Autonomous Agent (powered by NVIDIA NIM Frontier AI: Kimi K2.6 / Mistral Large 4 / Nemotron)*",
            "",
            f"**Last Sync:** `{timestamp_str}`  ",
            f"**Total Channels Scanned:** `{visited_count}` | **Pending Queue:** `{queued_count}` | **Active Discovered Feeds:** `{len(active_sources)}` | **Current Node Yield:** `{total_nodes}`",
            "",
            "## 🏆 Top Discovered Channels & Subscription Feeds",
            "",
            "| Rank | Source / Channel | Node Yield | Checks | Web Link | Status |",
            "| :---: | :--- | :---: | :---: | :--- | :---: |",
        ]

        if not active_sources:
            lines.append("| - | *Discovery cycle in progress...* | 0 | 0 | - | ⏳ Pending |")
        else:
            for idx, s in enumerate(active_sources[:30], start=1):
                handle = s.get("handle", "unknown")
                nodes = s.get("last_nodes_yield", 0)
                checks = s.get("checks_count", 1)
                url = s.get("sub_url", f"https://t.me/s/{handle}")
                is_tg = s.get("is_telegram", url.startswith("https://t.me/"))
                display_label = f"`@{handle}`" if is_tg else f"`{handle}`"
                status = "🟢 Active" if nodes > 0 else "⚪ Inactive"
                lines.append(f"| {idx} | {display_label} | **{nodes}** | {checks} | [Open Feed]({url}) | {status} |")

        lines.extend([
            "",
            "## 🧭 Discovery Queue & Exploration Horizon",
            "",
            f"- Channels waiting for deep-inspection in next cycle: **{queued_count}**",
            f"- Unique query patterns synthesized: **{len(self.state.get('queries_executed', []))}**",
            f"- NVIDIA Nemotron API calls (last run / cumulative): **{self.state.get('last_run_api_requests', 0)}** / **{self.state.get('total_api_requests', 0)}**",
            "",
            "---",
            "*Automated intelligence powered by PattN Pipeline*",
            ""
        ])

        return "\n".join(lines)
