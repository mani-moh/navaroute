from __future__ import annotations

import html
import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


def resilient_urlopen(req: urllib.request.Request, timeout: int = 15):
    """Opens URL; if default fails (e.g. dead Windows system proxy), retries direct without proxy."""
    has_explicit_proxy = bool(
        os.environ.get("HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("ALL_PROXY")
    )
    if not has_explicit_proxy:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        opener_direct = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPHandler(),
        )
        try:
            return opener_direct.open(req, timeout=timeout)
        except Exception:
            pass
    return urllib.request.urlopen(req, timeout=timeout)

from src.discovery.state_manager import normalize_channel_handle

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

URI_REGEX = re.compile(
    r"((?:vless|vmess|trojan|ss|hy2|hysteria2|tuic|wg|wireguard)://[^\s<>'\"`]+)",
    re.IGNORECASE,
)

SUB_URL_PATTERN = re.compile(
    r'https?://(?:[a-zA-Z0-9\-._~%!$&\'()*+,;=:@/]+(?:\.(?:txt|json|html)|/(?:sub|subscription|mix|proxies|vless|vmess)[a-zA-Z0-9\-._~%!$&\'()*+,;=:@/]*)|(?:raw\.githubusercontent\.com|gist\.githubusercontent\.com)/[^\s<>"\'`]+)',
    re.IGNORECASE,
)

TG_CHANNEL_MENTION_PATTERN = re.compile(
    r'(?:@|https?://(?:www\.)?t\.me/(?:s/)?)([a-zA-Z0-9_]{4,})',
    re.IGNORECASE,
)

TG_PHOTO_PATTERN = re.compile(
    r'(?:background-image:\s*url\(|src=)[\'"]?(https?://[^\s\'"<>()]+(?:\.jpg|\.png|\.webp)[^\s\'"<>]*)[\'"]?',
    re.IGNORECASE,
)

DATA_POST_PATTERN = re.compile(r'data-post="[^/]+/(\d+)"', re.IGNORECASE)


@dataclass
class ScrapedChannelResult:
    proxy_uris: list[str] = field(default_factory=list)
    sub_urls: list[str] = field(default_factory=list)
    discovered_channels: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    oldest_post_id: Optional[int] = None


class TelegramChannelScraper:
    """Scrapes public Telegram channel previews, extracting nodes, sub links, and sister channels."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def fetch_channel_page(
        self,
        channel_handle: str,
        before_id: Optional[int] = None,
    ) -> Optional[str]:
        """Fetches the Telegram web preview HTML for a channel."""
        clean = normalize_channel_handle(channel_handle)
        if not clean:
            return None

        url = f"https://t.me/s/{clean}"
        if before_id is not None:
            url += f"?before={before_id}"

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
        }

        req = urllib.request.Request(url, headers=headers)
        try:
            with resilient_urlopen(req, timeout=self.timeout) as resp:
                raw_bytes = resp.read()
                return raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("Failed to fetch Telegram channel preview for %s: %s", clean, exc)
            return None

    def parse_channel_page(
        self,
        html_content: str,
        current_channel: str = "",
    ) -> ScrapedChannelResult:
        """Parses a Telegram channel preview page."""
        result = ScrapedChannelResult()
        current_clean = normalize_channel_handle(current_channel)

        # 1. Unescape HTML text
        text = html.unescape(html_content)

        # 2. Extract proxy URIs
        found_uris = URI_REGEX.findall(text)
        # Clean trailing html artifacts or punctuation
        cleaned_uris = []
        for u in found_uris:
            cu = u.rstrip(".,;!)\"'>")
            if cu and cu not in cleaned_uris:
                cleaned_uris.append(cu)
        result.proxy_uris = cleaned_uris

        # 3. Extract external subscription URLs
        raw_sub_matches = SUB_URL_PATTERN.findall(text)
        cleaned_subs = []
        for su in raw_sub_matches:
            # Filter out telegram itself, cdn assets, and common false positives
            if "t.me" in su.lower() or "cdn.telegram.org" in su.lower():
                continue
            csu = su.rstrip(".,;!)\"'>")
            if csu and csu not in cleaned_subs:
                cleaned_subs.append(csu)
        result.sub_urls = cleaned_subs

        # 4. Extract cross-promoted sister channels
        channel_matches = TG_CHANNEL_MENTION_PATTERN.findall(text)
        seen_channels: set[str] = set()
        system_ignores = {"joinchat", "addstickers", "share", "socks", "proxy", "channel", "group"}
        for ch in channel_matches:
            clean_ch = ch.lower()
            if (
                clean_ch != current_clean
                and clean_ch not in system_ignores
                and clean_ch not in seen_channels
                and len(clean_ch) >= 4
            ):
                seen_channels.add(clean_ch)
                result.discovered_channels.append(clean_ch)

        # 5. Extract image URLs (potential QR codes or config cards)
        img_matches = TG_PHOTO_PATTERN.findall(html_content)
        for img_url in img_matches:
            if img_url not in result.image_urls:
                result.image_urls.append(img_url)

        # 6. Extract oldest post ID for pagination
        post_ids = [int(pid) for pid in DATA_POST_PATTERN.findall(html_content)]
        if post_ids:
            result.oldest_post_id = min(post_ids)

        return result


class WebSearchScraper:
    """Scrapes web and GitHub search results for subscription feeds and telegram channels."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def search_duckduckgo(
        self,
        query: str,
        max_results: int = 15,
    ) -> tuple[list[str], list[str]]:
        """Queries DuckDuckGo HTML and extracts discovered telegram channels and subscription URLs."""
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        req = urllib.request.Request(url, headers=headers)
        try:
            with resilient_urlopen(req, timeout=self.timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                return self.parse_duckduckgo_html(content, max_results=max_results)
        except Exception as exc:
            logger.debug("DuckDuckGo search error: %s", exc)
            return [], []

    def parse_duckduckgo_html(
        self,
        html_content: str,
        max_results: int = 15,
    ) -> tuple[list[str], list[str]]:
        """Extracts telegram channels and candidate URLs from DuckDuckGo HTML."""
        channels: list[str] = []
        urls: list[str] = []

        # Find URLs inside result__url or href
        url_matches = re.findall(r'href=[\'"]([^\'"]+)[\'"]', html_content)
        for u in url_matches:
            # DuckDuckGo redirect uddg parameter
            if "uddg=" in u:
                m = re.search(r'uddg=([^&]+)', u)
                if m:
                    u = urllib.parse.unquote(m.group(1))

            if "t.me/" in u:
                handle = normalize_channel_handle(u)
                if handle and handle not in channels:
                    channels.append(handle)
            elif u.startswith("http") and not any(skip in u.lower() for skip in ("duckduckgo", "yandex", "bing")):
                if u not in urls:
                    urls.append(u)

        return channels[:max_results], urls[:max_results]

    def search_github(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[str]:
        """Queries GitHub search API for repositories containing v2ray subscriptions."""
        url = f"https://api.github.com/search/repositories?q={urllib.parse.quote_plus(query)}&sort=updated"
        headers = {
            "User-Agent": "PattN-Discovery-Agent",
            "Accept": "application/vnd.github.v3+json",
        }
        gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"

        req = urllib.request.Request(url, headers=headers)
        discovered_subs = []
        try:
            with resilient_urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", [])
                for item in items[:max_results]:
                    default_branch = item.get("default_branch", "main")
                    full_name = item.get("full_name")
                    if full_name:
                        # Construct common raw file paths
                        for candidate_file in ("sub.txt", "configs.txt", "All_Configs_Sub.txt", "sub"):
                            discovered_subs.append(
                                f"https://raw.githubusercontent.com/{full_name}/{default_branch}/{candidate_file}"
                            )
        except Exception as exc:
            logger.debug("GitHub search error: %s", exc)

        return discovered_subs

    def fetch_text_snippet(self, url: str, max_chars: int = 4500) -> str:
        """Fetches readable text from a webpage or README for LLM inspection."""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,text/plain,text/markdown;q=0.9,*/*;q=0.8",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with resilient_urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(250_000).decode("utf-8", errors="replace")
                text = html.unescape(raw)
                text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:max_chars]
        except Exception:
            return ""

    def search_github_readmes(self, query: str, max_results: int = 8) -> list[str]:
        """Returns raw README.md URLs for GitHub repos matching query."""
        url = f"https://api.github.com/search/repositories?q={urllib.parse.quote_plus(query)}&sort=updated"
        headers = {
            "User-Agent": "PattN-Discovery-Agent",
            "Accept": "application/vnd.github.v3+json",
        }
        gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"
        req = urllib.request.Request(url, headers=headers)
        readme_urls: list[str] = []
        try:
            with resilient_urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("items", [])[:max_results]:
                    full_name = item.get("full_name")
                    branch = item.get("default_branch", "main")
                    if full_name:
                        readme_urls.append(
                            f"https://raw.githubusercontent.com/{full_name}/{branch}/README.md"
                        )
        except Exception:
            pass
        return readme_urls
