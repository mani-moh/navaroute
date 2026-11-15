"""Special-feed definitions, newest-wins, and the published registry.

Special feeds live ONLY in the feed-data repo / ArvanCloud mirror (they
are mirror-only files: never delete them from the mirror, and never
overwrite a newer copy with older content). Their filenames and the
labels shown on the mirror page are deliberately neutral -- see
scripts/build_mirror_page.py and the forbidden-word test in
tests/test_mirror_page.py; nothing here may name a product, protocol
or purpose.

Feed kinds (filename -> what goes inside, encrypted):
  kit-1  resolver lists (one entry per line, encrypted)
  kit-2  connection entries (one entry per line, encrypted)
  kit-3  app files (download links for installer files, one per line)
  kit-4  method notes (short plain-language how-to notes, encrypted)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

# (filename, mirror-page label, mirror-page description, kind)
SPECIAL_FEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("kit-1_base64.txt", "extra · 1", "more links, set 1", "resolvers"),
    ("kit-2_base64.txt", "extra · 2", "more links, set 2", "entries"),
    ("kit-3_base64.txt", "extra · 3", "more links, set 3", "apps"),
    ("kit-4_base64.txt", "extra · 4", "more links, set 4", "notes"),
)

SPECIAL_FILENAMES = tuple(feed[0] for feed in SPECIAL_FEEDS)
FEED_BY_FILENAME = {feed[0]: feed for feed in SPECIAL_FEEDS}


def content_hash(content: bytes | str) -> str:
    """Stable SHA-256 hex of a finding/feed payload (dedupe key)."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def is_newer(new_mtime: float, existing_mtime: float | None) -> bool:
    """Newest-wins: publish only when the new content is strictly newer
    than what is already published (or nothing is published yet)."""
    if existing_mtime is None:
        return True
    return new_mtime > existing_mtime


@dataclass
class Finding:
    """One extracted item (a link, an entry, a note) before publishing."""

    kind: str  # one of the SPECIAL_FEEDS kinds: resolvers/entries/apps/notes
    text: str
    source: str = ""  # where it came from (channel username, search query)
    found_at: float = 0.0

    def __post_init__(self) -> None:
        self.text = self.text.strip()
        if not self.found_at:
            self.found_at = time.time()

    @property
    def digest(self) -> str:
        return content_hash(f"{self.kind}\n{self.text}")


class PublishedRegistry:
    """Tracks published findings by content hash so nothing is published
    twice. Stored as JSON next to the kit state (VM only, never in the
    repo). Format: {digest: {"kind", "source", "first_seen", "feed"}}."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entries = data
            except (ValueError, OSError):
                self._entries = {}

    def is_published(self, finding: Finding) -> bool:
        return finding.digest in self._entries

    def mark_published(self, finding: Finding, feed_filename: str) -> None:
        self._entries[finding.digest] = {
            "kind": finding.kind,
            "source": finding.source,
            "first_seen": finding.found_at,
            "feed": feed_filename,
        }

    def new_findings(self, findings: list[Finding]) -> list[Finding]:
        """Drop findings already published (dedupe), including duplicates
        within this batch."""
        out: list[Finding] = []
        seen: set[str] = set()
        for finding in findings:
            if finding.digest in self._entries or finding.digest in seen:
                continue
            seen.add(finding.digest)
            out.append(finding)
        return out

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries, indent=2, sort_keys=True), encoding="utf-8")
        return self.path

    def __len__(self) -> int:
        return len(self._entries)
