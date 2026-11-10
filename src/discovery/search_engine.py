from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

from src.discovery.state_manager import IGNORE_CHANNEL_HANDLES, approved_public_url, normalize_channel_handle
from src.network import NetworkError, fetch_bytes

DEFAULT_USER_AGENT = "NavaRoute-Discovery/1.0"
SOURCE_MAX_BYTES = 32 * 1024 * 1024
URI_REGEX = re.compile(r"((?:vless|vmess|trojan|ss|hy2|hysteria2|tuic|wg|wireguard)://[^\s<>'\"`]+)", re.I)
URL_REGEX = re.compile(r"https?://[^\s<>\"'`]+", re.I)
MENTION_REGEX = re.compile(r"(?<![\w:/])@([A-Za-z][A-Za-z0-9_]{2,31})(?![\w?/:=@%+#-])")
TG_PHOTO_PATTERN = re.compile(r'''(?:background-image:\s*url\(|src=)['"]?(https?://[^\s'"<>()]+(?:\.jpg|\.png|\.webp)[^\s'"<>)]*)''', re.I)
DATA_POST_PATTERN = re.compile(r'data-post="[^/]+/([0-9]{1,20})"', re.I)


def public_links(text: str) -> list[str]:
    links = []
    for raw in URL_REGEX.findall(text):
        try:
            url = approved_public_url(raw.rstrip(".,;!"))
        except ValueError:
            continue
        if url not in links:
            links.append(url)
    return links


@dataclass
class ScrapedChannelResult:
    proxy_uris: list[str] = field(default_factory=list)
    sub_urls: list[str] = field(default_factory=list)
    discovered_channels: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    oldest_post_id: Optional[int] = None


class TelegramChannelScraper:
    """Public previews: None plus last_outcome distinguishes retryable from confirmed dead."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self.deadline: float | None = None
        self.last_outcome = "retryable"

    def fetch_channel_page(self, channel_handle: str, before_id: Optional[int] = None) -> Optional[str]:
        self.last_outcome = "retryable"
        clean = normalize_channel_handle(channel_handle)
        if not clean or before_id is not None and (type(before_id) is not int or before_id <= 0):
            raise ValueError("Invalid Telegram channel request")
        url = f"https://t.me/s/{clean}"
        if before_id is not None:
            url += f"?before={before_id}"
        try:
            status, raw = fetch_bytes(url, timeout=self.timeout, max_bytes=SOURCE_MAX_BYTES,
                                     deadline=self.deadline, headers={"User-Agent": DEFAULT_USER_AGENT})
            if status in (404, 410):
                self.last_outcome = "dead"
                return None
            if not 200 <= status < 300:
                return None
            text = raw.decode("utf-8")
            if not text:
                return None
            self.last_outcome = "success"
            return text
        except (NetworkError, UnicodeError):
            return None

    def parse_channel_page(self, html_content: str, current_channel: str = "") -> ScrapedChannelResult:
        result = ScrapedChannelResult()
        text = html.unescape(html_content)
        result.proxy_uris = list(dict.fromkeys(u.rstrip(".,;!)\"'>") for u in URI_REGEX.findall(text)))
        links = public_links(text)
        for url in links:
            parsed = urlsplit(url)
            if parsed.hostname in ("t.me", "www.t.me"):
                handle = normalize_channel_handle(url)
                if handle:
                    result.discovered_channels.append(handle)
            elif parsed.hostname not in ("cdn.telegram.org",) and (parsed.hostname in ("raw.githubusercontent.com", "gist.githubusercontent.com") or re.search(r"\.(?:txt|json|html)$|/(?:sub|subscription|mix|proxies|vless|vmess)(?:[/._-]|$)", parsed.path, re.I)):
                result.sub_urls.append(url)
        # Rejected URL credentials are still URL data, never free-standing mentions.
        mention_text = html.unescape(URL_REGEX.sub(" ", html_content))
        mention_text = re.sub(r"<[^>]+>", " ", mention_text)
        mention_text = URL_REGEX.sub(" ", mention_text)
        result.discovered_channels.extend(normalize_channel_handle(h) for h in MENTION_REGEX.findall(mention_text))
        result.discovered_channels = list(dict.fromkeys(h for h in result.discovered_channels if h and h != normalize_channel_handle(current_channel) and h not in IGNORE_CHANNEL_HANDLES))
        for raw in TG_PHOTO_PATTERN.findall(html_content):
            try:
                url = approved_public_url(html.unescape(raw))
            except ValueError:
                continue
            if url not in result.image_urls:
                result.image_urls.append(url)
        post_ids = [int(pid) for pid in DATA_POST_PATTERN.findall(html_content)]
        if post_ids:
            result.oldest_post_id = min(post_ids)
        return result


class WebSearchScraper:
    """Bounded anonymous searches; ambient GitHub credentials are not public-source input."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self.deadline: float | None = None
        self.last_outcome = "retryable"
        self.last_page_content = ""

    def _read(self, url: str, max_bytes: int = SOURCE_MAX_BYTES) -> str:
        self.last_outcome = "retryable"
        try:
            status, raw = fetch_bytes(url, timeout=self.timeout, max_bytes=max_bytes,
                                     deadline=self.deadline, headers={"User-Agent": DEFAULT_USER_AGENT})
            if status in (404, 410):
                self.last_outcome = "dead"
                return ""
            if not 200 <= status < 300:
                return ""
            text = raw.decode("utf-8")
            self.last_outcome = "success"
            return text
        except (NetworkError, UnicodeError):
            return ""

    def search_duckduckgo(self, query: str, max_results: int = 15) -> tuple[list[str], list[str]]:
        content = self._read(f"https://html.duckduckgo.com/html/?q={quote_plus(query[:500])}")
        return self.parse_duckduckgo_html(content, max_results)

    def parse_duckduckgo_html(self, html_content: str, max_results: int = 15) -> tuple[list[str], list[str]]:
        channels, urls = [], []
        for raw in re.findall(r'''href=['"]([^'"]+)['"]''', html.unescape(html_content)):
            if "uddg=" in raw:
                values = parse_qs(urlsplit(raw).query).get("uddg", [])
                if values:
                    raw = values[0]
            try:
                url = approved_public_url(raw)
            except ValueError:
                continue
            parsed = urlsplit(url)
            if parsed.hostname in ("t.me", "www.t.me"):
                handle = normalize_channel_handle(url)
                if handle and handle not in channels:
                    channels.append(handle)
            elif parsed.hostname not in ("duckduckgo.com", "html.duckduckgo.com", "yandex.com", "bing.com") and url not in urls:
                urls.append(url)
        return channels[:max_results], urls[:max_results]

    def _github_repositories(self, query: str, max_results: int) -> list[tuple[str, str]]:
        raw = self._read(f"https://api.github.com/search/repositories?q={quote_plus(query[:500])}&sort=updated", 2 * 1024 * 1024)
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return []
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return []
        repos = []
        for item in data["items"][:max_results]:
            if not isinstance(item, dict):
                continue
            name, branch = item.get("full_name"), item.get("default_branch", "main")
            if (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)
                    and isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", branch) and ".." not in branch):
                repo = (name, quote(branch, safe=""))
                if repo not in repos:
                    repos.append(repo)
        return repos

    def search_github(self, query: str, max_results: int = 10) -> list[str]:
        return [f"https://raw.githubusercontent.com/{name}/{branch}/{file}" for name, branch in self._github_repositories(query, max_results)
                for file in ("sub.txt", "configs.txt", "All_Configs_Sub.txt", "sub")]

    def fetch_text_snippet(self, url: str, max_chars: int = 4500) -> str:
        url = approved_public_url(url)
        if not url.startswith(("http://", "https://")):
            raise ValueError("Invalid public webpage")
        self.last_page_content = self._read(url, 250000)
        text = html.unescape(self.last_page_content)
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:max_chars]

    def search_github_readmes(self, query: str, max_results: int = 8) -> list[str]:
        return [f"https://raw.githubusercontent.com/{name}/{branch}/README.md" for name, branch in self._github_repositories(query, max_results)]
