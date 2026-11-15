"""Blackout Kit configuration: paths, sources, search queries.

The channel/group list ships EMPTY on purpose: Ryan adds the public
channels/groups he trusts (and his Saved Messages, "me") by editing
the JSON file at ~/.config/blackout-kit/config.json -- never by
committing names to the repo. Only public channels/groups and Saved
Messages are accepted; see telegram_mine.assert_allowed_source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / ".config" / "blackout-kit"
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "config.json"
DEFAULT_REGISTRY_PATH = DEFAULT_STATE_DIR / "published.json"
DEFAULT_SOURCE_STATE_PATH = DEFAULT_STATE_DIR / "telegram-state.json"
DEFAULT_SEARCH_STATE_PATH = DEFAULT_STATE_DIR / "search-state.json"
DEFAULT_MONITOR_PATH = (
    Path.home() / "workspace" / "projects" / "whitedns-blackout-check" / "check_blackout.py"
)

# Web-search queries used during a blackout escalation. Kept generic and
# method-focused; results are deduped and only genuinely new items are
# published.
DEFAULT_SEARCH_QUERIES: tuple[str, ...] = (
    "new free vpn method Iran 2026",
    "Iran internet shutdown bypass new method",
    "free proxy subscription Iran November 2026",
    "DNS over HTTPS resolver Iran",
    "PattNG new version download apk",
)

# Backoff: when a search round finds nothing new, wait this long before
# searching again (grows with each empty round, capped). Seconds.
BACKOFF_BASE_SECONDS = 900        # 15 minutes
BACKOFF_MAX_SECONDS = 2 * 3600    # 2 hours


@dataclass
class KitConfig:
    sources: list[str] = field(default_factory=lambda: ["me"])
    search_queries: list[str] = field(default_factory=lambda: list(DEFAULT_SEARCH_QUERIES))
    monitor_path: Path = DEFAULT_MONITOR_PATH
    state_dir: Path = DEFAULT_STATE_DIR

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "KitConfig":
        path = Path(path)
        cfg = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
            if isinstance(data, dict):
                if isinstance(data.get("sources"), list):
                    cfg.sources = [str(s) for s in data["sources"]]
                if isinstance(data.get("search_queries"), list):
                    cfg.search_queries = [str(q) for q in data["search_queries"]]
                if data.get("monitor_path"):
                    cfg.monitor_path = Path(str(data["monitor_path"]))
                if data.get("state_dir"):
                    cfg.state_dir = Path(str(data["state_dir"]))
        return cfg

    def save(self, path: Path | str = DEFAULT_CONFIG_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "sources": self.sources,
                    "search_queries": self.search_queries,
                    "monitor_path": str(self.monitor_path),
                    "state_dir": str(self.state_dir),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
