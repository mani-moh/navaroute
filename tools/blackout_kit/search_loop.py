"""24/7 searching during blackouts: monitor-gated intensive search.

Trigger: the blackout monitor's verdict. `check_blackout.py --json`
prints {"verdict": "BLACKOUT"|"DEGRADED"|"OK"|"UNKNOWN", ...} and
exits 0/1/2/3. This module runs that script (same one the hourly
auto-start cron uses) and escalates ONLY on BLACKOUT or DEGRADED.
OK and UNKNOWN never trigger a search; an unparseable monitor result
is treated as UNKNOWN.

Search loop (runs only while escalated):
  1. Web search for new circumvention methods (pluggable search
     function; the default is a small stdlib DuckDuckGo HTML client).
  2. Telegram mining from the configured public sources + Saved
     Messages (see telegram_mine.py).
  3. Dedupe against the published registry (content hash) -- a method
     is never published twice.
  4. Backoff: each round that finds nothing new doubles the wait
     before the next round (15 min -> 2 h cap), so an extended
     blackout does not burn requests on empty searches.

Publish path: findings -> publish.build_special_feeds (encrypted,
newest-wins) -> feed-data repo -> relay/sync -> ArvanCloud mirror.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import config as kit_config
from .feeds import Finding, PublishedRegistry
from .telegram_mine import extract_findings

ESCALATE_VERDICTS = ("BLACKOUT", "DEGRADED")
ALL_VERDICTS = ("BLACKOUT", "DEGRADED", "OK", "UNKNOWN")

# Exit codes of check_blackout.py (kept in sync with its docstring).
EXIT_TO_VERDICT = {0: "OK", 1: "DEGRADED", 2: "BLACKOUT", 3: "UNKNOWN"}


def read_verdict(monitor_path: Path | str, timeout: int = 300) -> tuple[str, int | None]:
    """Run the monitor and return (verdict, score).

    Unparseable output, a missing script, or a timeout is UNKNOWN --
    never acted on.
    """
    monitor_path = Path(monitor_path)
    if not monitor_path.exists():
        return "UNKNOWN", None
    try:
        proc = subprocess.run(
            [sys.executable, str(monitor_path), "--json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN", None
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        return "UNKNOWN", None
    verdict = str(result.get("verdict", "UNKNOWN")).upper()
    if verdict not in ALL_VERDICTS:
        return "UNKNOWN", None
    score = result.get("score")
    return verdict, int(score) if isinstance(score, int) else None


def should_escalate(verdict: str) -> bool:
    """True only for BLACKOUT or DEGRADED."""
    return verdict in ESCALATE_VERDICTS


@dataclass
class SearchState:
    """Backoff state: consecutive empty rounds and the last run time."""

    path: Path
    empty_rounds: int = 0
    last_run_at: float = 0.0

    @classmethod
    def load(cls, path: Path | str) -> "SearchState":
        path = Path(path)
        state = cls(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    state.empty_rounds = int(data.get("empty_rounds", 0))
                    state.last_run_at = float(data.get("last_run_at", 0.0))
            except (ValueError, OSError):
                pass
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"empty_rounds": self.empty_rounds, "last_run_at": self.last_run_at}),
            encoding="utf-8",
        )

    def backoff_seconds(self) -> int:
        """Wait before the next round after `empty_rounds` empty rounds."""
        wait = kit_config.BACKOFF_BASE_SECONDS * (2 ** self.empty_rounds)
        return min(wait, kit_config.BACKOFF_MAX_SECONDS)

    def due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.last_run_at) >= self.backoff_seconds()

    def record(self, found_new: bool, now: float | None = None) -> None:
        self.last_run_at = time.time() if now is None else now
        self.empty_rounds = 0 if found_new else self.empty_rounds + 1
        self.save()


def duckduckgo_search(query: str, limit: int = 10, timeout: int = 30) -> list[str]:
    """Best-effort web search via DuckDuckGo's HTML endpoint (stdlib
    only). Returns result URLs. Any failure returns [] -- the loop must
    never crash on a search provider being down during a blackout.
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:
        return []
    import re

    found = re.findall(r'result__a[^>]*href="([^"]+)"', html)
    out: list[str] = []
    for href in found:
        if "duckduckgo.com/l/?uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(
                urllib.parse.urlparse(href).query).get("uddg", [""])[0])
        if href.startswith("http") and href not in out:
            out.append(href)
        if len(out) >= limit:
            break
    return out


@dataclass
class SearchLoop:
    """One gated search round: verdict check -> search -> dedupe."""

    cfg: kit_config.KitConfig = field(default_factory=kit_config.KitConfig.load)
    registry: PublishedRegistry | None = None
    search_state: SearchState | None = None
    web_search: object = None  # callable(query) -> list[str]; default DDG

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = PublishedRegistry(self.cfg.state_dir / "published.json")
        if self.search_state is None:
            self.search_state = SearchState.load(self.cfg.state_dir / "search-state.json")
        if self.web_search is None:
            self.web_search = duckduckgo_search

    def web_findings(self) -> list[Finding]:
        """Search the web for new methods; each fresh result URL is a
        'notes' finding (a pointer to read, not a copied method)."""
        findings: list[Finding] = []
        search = self.web_search  # type: ignore[assignment]
        for query in self.cfg.search_queries:
            for url in search(query) or []:
                findings.append(Finding(kind="notes", text=url, source=f"web:{query}"))
        return findings

    def telegram_findings(self, miner: object | None) -> list[Finding]:
        """Mine configured Telegram sources. `miner` is a TelegramMiner
        (or None when the session is not logged in yet); an absent miner
        simply contributes nothing -- the loop still runs on web search.
        """
        if miner is None:
            return []
        import asyncio

        return asyncio.run(miner.fetch_all())  # type: ignore[attr-defined]

    def run_round(self, miner: object | None = None) -> list[Finding]:
        """Run one gated round. Returns the NEW findings (after dedupe).

        Does nothing at all unless the monitor verdict escalates AND
        the backoff interval has elapsed.
        """
        verdict, _score = read_verdict(self.cfg.monitor_path)
        if not should_escalate(verdict):
            return []
        assert self.search_state is not None and self.registry is not None
        if not self.search_state.due():
            return []
        findings = self.web_findings()
        findings.extend(self.telegram_findings(miner))
        new_findings = self.registry.new_findings(findings)
        self.search_state.record(found_new=bool(new_findings))
        return new_findings

    @staticmethod
    def extract_from_text(text: str, source: str) -> list[Finding]:
        """Extract findings from a block of text (shared with mining)."""
        return extract_findings(text, source)
