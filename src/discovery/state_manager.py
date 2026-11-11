from __future__ import annotations

import ipaddress
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from src.models import SourceConfig
from src.storage import atomic_write_text

DEFAULT_STATE_FILE = "data/discovery_state.json"
DEFAULT_DISCOVERY_SOURCES_FILE = "sources.discovered.json"
DEFAULT_LEADERBOARD_FILE = "DISCOVERY.md"
STATE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_CACHE_MAX_BYTES = 2 * 1024 * 1024
IMAGE_RECORD_MAX_BYTES = 65536
PENDING_CHILDREN_MAX = 5000


def bounded_image_results(results: dict[str, str], max_bytes: int = IMAGE_CACHE_MAX_BYTES) -> dict[str, str]:
    """Evict whole canonical configs/old records, never truncate a connection."""
    if not isinstance(results, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in results.items()):
        raise ValueError("Invalid image results")
    bounded = {}
    sizes = {}
    total = 0
    for key, text in results.items():
        lines = text.splitlines()
        while len("\n".join(lines).encode("utf-8")) > IMAGE_RECORD_MAX_BYTES:
            lines.pop(0)
        value = "\n".join(lines)
        size = len(json.dumps({key: value}, ensure_ascii=False, indent=2).encode("utf-8"))
        bounded[key], sizes[key] = value, size
        total += size
        while bounded and (len(bounded) > 400 or total > max_bytes):
            oldest = next(iter(bounded))
            bounded.pop(oldest)
            total -= sizes.pop(oldest)
    return bounded


def serialize_state(state: dict[str, Any]) -> str:
    """Check the exact serialized generation before any projection is replaced."""
    state["last_updated"] = int(time.time())
    base = dict(state, image_results={})
    base_bytes = len(json.dumps(base, ensure_ascii=False, indent=2).encode("utf-8"))
    if base_bytes > STATE_MAX_BYTES:
        raise ValueError("Discovery state exceeds byte limit")
    state["image_results"] = bounded_image_results(state.get("image_results", {}), min(IMAGE_CACHE_MAX_BYTES, STATE_MAX_BYTES - base_bytes))
    validate_state(state)
    content = json.dumps(state, ensure_ascii=False, indent=2)
    if len(content.encode("utf-8")) > STATE_MAX_BYTES:
        raise ValueError("Discovery state exceeds byte limit")
    return content


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
    """Accept a complete username or public Telegram channel/post URL, never a prefix."""
    if not isinstance(raw_handle, str):
        return ""
    clean = raw_handle.strip()
    if clean.startswith(("http://", "https://")):
        try:
            parsed = urlsplit(approved_public_url(clean))
        except ValueError:
            return ""
        if parsed.hostname not in ("t.me", "www.t.me") or parsed.query:
            return ""
        match = re.fullmatch(r"/(?:s/)?([A-Za-z][A-Za-z0-9_]{2,31})(?:/[0-9]+)?/?", parsed.path)
        clean = match[1] if match else ""
    else:
        clean = clean.removeprefix("@")
    return clean.lower() if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,31}", clean) else ""


def approved_public_url(url: str) -> str:
    """Offline public-input policy; transport additionally validates/pins every DNS answer.

    Query-bearing subscriptions are not approved public inputs (may carry credentials).
    Only Telegram's numeric pagination query is allowed. Never strip a secret into a
    different apparently valid URL, and never resolve merely to load a checkpoint.
    """
    try:
        SourceConfig.from_dict({"id": "discovery", "url": url})
        if url == "data/discovered_ocr_configs.txt":
            return url
        if len(url.encode("utf-8")) > 16 * 1024 or re.search(r"[\\\x00-\x20\x7f-\x9f]|%(?:0[0-9a-f]|1[0-9a-f]|7f)", url, re.I):
            raise ValueError
        parsed = urlsplit(url)
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if parsed.fragment or parsed.netloc.endswith(":") or "%" in host:
            raise ValueError
        if parsed.query and not (host == "t.me" and re.fullmatch(r"/s/[A-Za-z][A-Za-z0-9_]{2,31}", parsed.path) and re.fullmatch(r"before=[1-9][0-9]{0,19}", parsed.query)):
            raise ValueError
        path = parsed.path or "/"
        if host in ("t.me", "www.t.me"):
            channel = re.fullmatch(r"/(?:s/)?([A-Za-z][A-Za-z0-9_]{2,31})(/[0-9]+)?/?", path)
            if channel:
                host = "t.me"
                path = "/s/" + channel[1].lower() + (channel[2] or "")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or "." not in host or host.endswith((".localhost", ".local", ".internal", ".home", ".lan", ".test", ".invalid"))
                    or re.fullmatch(r"[0-9.]+|0x[0-9a-f]+", host)
                    or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split("."))):
                raise ValueError
        else:
            if (not ip.is_global or ip.is_reserved or ip.is_multicast
                    or isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo)):
                raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None and parsed.port != (443 if parsed.scheme == "https" else 80):
            authority += f":{parsed.port}"
        return urlunsplit((parsed.scheme, authority, path, parsed.query, ""))
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ValueError("Unapproved discovery URL") from None


def read_public_text(path: Path, max_bytes: int = 8 * 1024 * 1024) -> str:
    """Bound checkpoint reads, including successful but oversized local files."""
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("Discovery checkpoint exceeds byte limit")
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid discovery checkpoint encoding") from None


def _counter(value: Any) -> bool:
    return type(value) is int and value >= 0


def validate_state(data: Any) -> None:
    if not isinstance(data, dict) or data.get("version", 1) != 1 or type(data.get("version", 1)) is not int:
        raise ValueError("Invalid discovery state")
    allowed = {"version", "last_updated", "queries_executed", "candidate_queue", "candidate_scores",
               "visited_channels", "rejected_channels", "visited_urls", "sources_metadata",
               "last_run_api_requests", "total_api_requests", "image_results", "external_attempt_order", "pending_queries", "pending_pages", "pending_children"}
    if set(data) - allowed:
        raise ValueError("Unknown discovery state fields")
    for key in ("candidate_queue", "visited_channels", "rejected_channels"):
        values = data.get(key, [])
        if not isinstance(values, list) or len(values) > 10000 or any(not normalize_channel_handle(v) or v != normalize_channel_handle(v) for v in values):
            raise ValueError("Invalid discovery handles")
    for key in ("queries_executed", "pending_queries", "visited_urls", "pending_pages"):
        values = data.get(key, [])
        if not isinstance(values, list) or len(values) > 5000 or any(not isinstance(v, str) or len(v) > 16384 for v in values):
            raise ValueError("Invalid discovery history")
        for value in values:
            if key in ("visited_urls", "pending_pages"):
                approved_public_url(value)
                if key == "pending_pages" and not value.startswith(("http://", "https://")):
                    raise ValueError("Invalid pending discovery page")
            elif not value or len(value) > 500 or re.search(r"[\x00-\x1f\x7f]|https?://", value):
                raise ValueError("Invalid discovery query")
    children = data.get("pending_children", {})
    if not isinstance(children, dict) or len(children) > PENDING_CHILDREN_MAX:
        raise ValueError("Invalid pending discovery children")
    total = 0
    for parent, work in children.items():
        if (parent not in data.get("pending_pages", []) + data.get("pending_queries", [])
                or parent in data.get("visited_urls", []) + data.get("queries_executed", [])
                or not isinstance(work, list) or len(work) > PENDING_CHILDREN_MAX):
            raise ValueError("Invalid pending discovery parent")
        if parent in data.get("pending_pages", []):
            if parent != approved_public_url(parent):
                raise ValueError("Invalid pending discovery page identity")
        elif parent.strip() != parent or "http" in parent or parent == "gh:":
            raise ValueError("Invalid pending discovery query identity")
        total += len(work)
        seen = set()
        for child in work:
            if (not isinstance(child, dict) or set(child) != {"kind", "url"}
                    or child["kind"] not in ("subscription", "page")
                    or parent in data.get("pending_pages", []) and child["kind"] != "subscription"):
                raise ValueError("Invalid pending discovery child role")
            url = approved_public_url(child["url"])
            if not url.startswith(("http://", "https://")) or url != child["url"] or url == parent or url in seen:
                raise ValueError("Invalid pending discovery child URL")
            seen.add(url)
    if total > PENDING_CHILDREN_MAX:
        raise ValueError("Pending discovery children exceed limit")
    scores = data.get("candidate_scores", {})
    if not isinstance(scores, dict) or len(scores) > 10000 or any(not normalize_channel_handle(h) or h != normalize_channel_handle(h) or not _counter(n) for h, n in scores.items()):
        raise ValueError("Invalid discovery scores")
    attempts = data.get("external_attempt_order", {})
    if (not isinstance(attempts, dict) or len(attempts) > 10000
            or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) or not _counter(order) for key, order in attempts.items())):
        raise ValueError("Invalid external discovery attempt order")
    for key in ("last_updated", "last_run_api_requests", "total_api_requests"):
        if not _counter(data.get(key, 0)):
            raise ValueError("Invalid discovery counter")
    metadata = data.get("sources_metadata", {})
    if not isinstance(metadata, dict) or len(metadata) > 10000:
        raise ValueError("Invalid discovery metadata")
    for key, entry in metadata.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) or not isinstance(entry, dict):
            raise ValueError("Invalid discovery source")
        required = {"handle", "last_checked", "last_nodes_yield", "total_nodes_yield", "checks_count", "is_active", "sub_url"}
        if not required.issubset(entry) or set(entry) - required - {"oldest_post_id", "is_telegram", "retained_fingerprints"}:
            raise ValueError("Invalid discovery source schema")
        for field in ("last_checked", "last_nodes_yield", "total_nodes_yield", "checks_count", "oldest_post_id"):
            if field in entry and not _counter(entry[field]):
                raise ValueError("Invalid discovery metrics")
        for field in ("is_active", "is_telegram"):
            if field in entry and type(entry[field]) is not bool:
                raise ValueError("Invalid discovery status")
        if "sub_url" in entry:
            approved_public_url(entry["sub_url"])
        if "handle" in entry and (not isinstance(entry["handle"], str) or not re.fullmatch(r"[A-Za-z0-9_:/.-]{1,100}", entry["handle"])):
            raise ValueError("Invalid discovery label")
        if entry.get("is_telegram", True) and (key != entry["handle"] or normalize_channel_handle(entry["handle"]) != entry["handle"]
                                               or normalize_channel_handle(entry["sub_url"]) != entry["handle"]):
            raise ValueError("Invalid Telegram discovery metadata")
        refs = entry.get("retained_fingerprints", [])
        if not isinstance(refs, list) or len(refs) > 400 or any(not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref) for ref in refs):
            raise ValueError("Invalid retained references")
    images = data.get("image_results", {})
    if not isinstance(images, dict) or len(images) > 400 or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) or not isinstance(text, str) or len(text.encode("utf-8")) > 65536 for h, text in images.items()):
        raise ValueError("Invalid image results")
    from src.parsers import parse_proxy_uri
    if any(parse_proxy_uri(uri) is None for text in images.values() for uri in text.splitlines()):
        raise ValueError("Invalid cached image config")


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
                data = json.loads(read_public_text(self.state_file))
                validate_state(data)
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError("Cannot load discovery state") from None
            self.state.update(data)

    def save(self) -> None:
        """Persists state to disk atomically."""
        content = serialize_state(self.state)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.state_file, content)

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
        url = approved_public_url(url)
        if self.state.get("pending_children", {}).get(url):
            raise ValueError("Discovery page has unfinished children")
        self.state.get("pending_children", {}).pop(url, None)
        if url in self.state.get("pending_pages", []):
            self.state["pending_pages"].remove(url)
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

    def restore_candidates(self, handles: list[str]) -> None:
        """Restore unacknowledged work first, including active-channel refreshes."""
        clean = [normalize_channel_handle(h) for h in handles]
        queue = self.state["candidate_queue"]
        self.state["candidate_queue"] = list(dict.fromkeys([h for h in clean if h] + queue))

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
        deepest_id = oldest_post_id

        entry: dict[str, Any] = {
            "handle": handle,
            "last_checked": int(time.time()),
            "last_nodes_yield": nodes_found,
            "total_nodes_yield": existing.get("total_nodes_yield", 0) + nodes_found,
            "checks_count": times_seen,
            "is_active": is_active,
            "sub_url": approved_public_url(sub_url or f"https://t.me/s/{handle}"),
            "is_telegram": True,
        }
        if deepest_id is not None:
            entry["oldest_post_id"] = deepest_id
        if "retained_fingerprints" in existing:
            entry["retained_fingerprints"] = existing["retained_fingerprints"]
        meta[handle] = entry

    def record_query(self, query: str) -> None:
        """Records an executed search query."""
        queries = self.state.setdefault("queries_executed", [])
        q_clean = query.strip()
        if self.state.get("pending_children", {}).get(q_clean):
            raise ValueError("Discovery query has unfinished children")
        self.state.get("pending_children", {}).pop(q_clean, None)
        if q_clean and len(q_clean) <= 500 and not re.search(r"[\x00-\x1f\x7f]|https?://", q_clean) and q_clean not in queries:
            queries.append(q_clean)
            self.state["queries_executed"] = queries[-5000:]
        if q_clean in self.state.get("pending_queries", []):
            self.state["pending_queries"].remove(q_clean)

    def record_external_source(
        self,
        source_id: str,
        display_name: str,
        nodes_found: int,
        sub_url: str,
    ) -> None:
        """Records metrics for non-Telegram sources (GitHub/Web) without polluting Telegram channel queue."""
        sub_url = approved_public_url(sub_url)
        if not re.fullmatch(r"[A-Za-z0-9_:/.-]{1,100}", display_name):
            display_name = source_id
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

    def generate_leaderboard_markdown(self, state: Optional[dict[str, Any]] = None) -> str:
        """Generates an aesthetic markdown report of discovered sources."""
        state = self.state if state is None else state
        meta = state.get("sources_metadata", {})
        visited_count = len(state.get("visited_channels", []))
        queued_count = len(state.get("candidate_queue", []))

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
            f"- Unique query patterns synthesized: **{len(state.get('queries_executed', []))}**",
            f"- NVIDIA Nemotron API calls (last run / cumulative): **{state.get('last_run_api_requests', 0)}** / **{state.get('total_api_requests', 0)}**",
            "",
            "---",
            "*Automated intelligence powered by PattN Pipeline*",
            ""
        ])

        return "\n".join(lines)
