"""Telegram mining for the Blackout Kit (Telethon / MTProto user client).

SCOPE RULE -- hard limit, enforced in code and by test:
  * READ ONLY: public channels/groups (by @username or t.me link) and
    Ryan's own Saved Messages ("me").
  * NEVER: private 1:1 chats. The miner never iterates dialogs
    (no get_dialogs / iter_dialogs anywhere in this module), never
    accepts a user id or phone number as a source, and never reads a
    private chat. Republishing other people's private messages would
    be a privacy violation; this module cannot be pointed at them.

Telethon is an optional dependency: it is imported lazily inside the
functions that need it, so the rest of the kit (and CI, which installs
no third-party packages) works without it. Install with:
  pip install telethon

Session storage: Telethon's session file holds the auth key. After
login it is encrypted with the Blackout Kit key (AES-256-GCM, see
crypto.py) into <name>.session.enc (mode 600) and the plaintext
session file is deleted. The miner decrypts it to a temporary mode-600
file only while it runs, and removes it afterwards.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import crypto
from .feeds import Finding

# ---------------------------------------------------------------- scope

SAVED_MESSAGES = "me"

# A public source is a channel/group @username, a bare username, or a
# t.me link (public username link or an invite link to a group the user
# joined). Anything that looks like a person is rejected.
_USERNAME_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{4,31}$")
_TME_RE = re.compile(r"^https?://t\.me/(?:\+[A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?/?$")
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def normalize_source(raw: str) -> str:
    """Canonical form of a configured source ('@name' or t.me link)."""
    raw = (raw or "").strip()
    if raw in ("me", "saved", "Saved Messages"):
        return SAVED_MESSAGES
    if raw.startswith("@"):
        return raw
    match = re.match(r"^https?://t\.me/([A-Za-z0-9_+\-]{5,})(?:/\d+)?/?$", raw)
    if match:
        return "@" + match.group(1).lstrip("+") if not raw.split("/")[-1].startswith("+") else raw
    return raw


def is_allowed_source(raw: str) -> bool:
    """True only for Saved Messages or a public channel/group reference."""
    raw = (raw or "").strip()
    if not raw:
        return False
    if raw in ("me", "saved", "Saved Messages"):
        return True
    if _PHONE_RE.match(raw):
        return False  # a phone number is a person, never a source
    if raw.lstrip("-").isdigit():
        return False  # a numeric id is a user/chat, never a source
    if _USERNAME_RE.match(raw):
        return True
    if _TME_RE.match(raw):
        return True
    return False


def assert_allowed_source(raw: str) -> str:
    """Normalize + validate; raises ValueError for anything out of scope."""
    if not is_allowed_source(raw):
        raise ValueError(
            f"refusing out-of-scope Telegram source {raw!r}: only public "
            "channels/groups (by username or t.me link) and Saved Messages "
            "are allowed -- never private chats, user ids or phone numbers"
        )
    return normalize_source(raw)


# ---------------------------------------------------------------- extraction

_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_CONFIG_SCHEMES = (
    "vless://", "vmess://", "trojan://", "ss://", "ssr://",
    "hysteria2://", "hysteria://", "tuic://", "wireguard://", "socks://",
)
_CONFIG_RE = re.compile(
    r"(?:vless|vmess|trojan|ssr?|hysteria2?|tuic|wireguard|socks)://[^\s<>\"]+"
)
_IP_PORT_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")


def classify_url(url: str) -> str:
    """Feed kind for a URL: apps (.apk), entries (subscription/config
    links), resolvers (bare IP:port), notes (everything else)."""
    lower = url.lower().rstrip(".,)")
    if lower.endswith(".apk"):
        return "apps"
    if _IP_PORT_RE.search(lower):
        return "resolvers"
    if any(token in lower for token in ("sub", "subscribe", "config", ".txt")):
        return "entries"
    return "notes"


def extract_findings(text: str, source: str) -> list[Finding]:
    """Pull links / entries / APK links out of one message's text.

    Plain prose is NOT republished: only concrete, actionable items
    (a link, a config URI, an IP:port) become findings. Method notes
    come from the search loop, where they are written by the kit, not
    copied from someone's message.
    """
    findings: list[Finding] = []
    seen: set[str] = set()
    text = text or ""
    for match in _CONFIG_RE.finditer(text):
        uri = match.group(0).rstrip(".,)")
        if uri not in seen:
            seen.add(uri)
            findings.append(Finding(kind="entries", text=uri, source=source))
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,)")
        if url in seen:
            continue
        seen.add(url)
        findings.append(Finding(kind=classify_url(url), text=url, source=source))
    for match in _IP_PORT_RE.finditer(text):
        item = match.group(0)
        if item not in seen:
            seen.add(item)
            findings.append(Finding(kind="resolvers", text=item, source=source))
    return findings


# ---------------------------------------------------------------- session

SESSION_AAD_NAME = "telegram-session"


def store_session_encrypted(session_path: Path | str, key: bytes) -> Path:
    """Encrypt a Telethon .session file in place: <path>.enc (mode 600),
    plaintext session file deleted. Returns the .enc path."""
    session_path = Path(session_path)
    raw = session_path.read_bytes()
    enc_path = session_path.with_suffix(session_path.suffix + ".enc")
    enc_path.write_bytes(crypto.encrypt_bytes(raw, key, SESSION_AAD_NAME))
    os.chmod(enc_path, 0o600)
    session_path.unlink()
    return enc_path


@contextlib.contextmanager
def decrypted_session(enc_path: Path | str, key: bytes) -> Iterator[Path]:
    """Decrypt the session to a temporary mode-600 file, yield it, and
    remove it afterwards (also on error)."""
    enc_path = Path(enc_path)
    raw = crypto.decrypt_bytes(enc_path.read_bytes(), key, SESSION_AAD_NAME)
    fd, tmp = tempfile.mkstemp(prefix="bk-session-", suffix=".session")
    try:
        os.write(fd, raw)
        os.close(fd)
        os.chmod(tmp, 0o600)
        yield Path(tmp)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- miner


@dataclass
class SourceState:
    """Last-seen message id per source, so mining is incremental."""

    path: Path
    last_id: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> "SourceState":
        path = Path(path)
        state = cls(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    state.last_id = {str(k): int(v) for k, v in data.items()}
            except (ValueError, OSError):
                pass
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.last_id, indent=2, sort_keys=True), encoding="utf-8")


class TelegramMiner:
    """Reads configured public sources + Saved Messages via a Telethon
    client. The client is injected (a real Telethon client in production,
    a fake in tests). This class never lists dialogs: it only ever asks
    the client for the exact sources it was configured with.
    """

    def __init__(self, client: Any, sources: list[str], state: SourceState) -> None:
        self.client = client
        self.sources = [assert_allowed_source(s) for s in sources]
        self.state = state

    async def fetch_source(self, source: str, limit: int = 100) -> list[Finding]:
        """Fetch new messages from one allowed source and extract findings."""
        source = assert_allowed_source(source)
        entity = SAVED_MESSAGES if source == SAVED_MESSAGES else source
        min_id = self.state.last_id.get(source, 0)
        messages = await self.client.get_messages(entity, limit=limit, min_id=min_id)
        findings: list[Finding] = []
        newest = min_id
        for message in messages:
            msg_id = int(getattr(message, "id", 0) or 0)
            newest = max(newest, msg_id)
            text = getattr(message, "text", None) or getattr(message, "message", "") or ""
            findings.extend(extract_findings(text, source))
        if newest > min_id:
            self.state.last_id[source] = newest
        return findings

    async def fetch_all(self, limit: int = 100) -> list[Finding]:
        """Fetch every configured source once. Saved Messages included."""
        findings: list[Finding] = []
        for source in self.sources:
            findings.extend(await self.fetch_source(source, limit=limit))
        self.state.save()
        return findings


def build_client(session_path: Path | str, api_id: int, api_hash: str) -> Any:
    """Create a Telethon client for a (decrypted) session file.

    Imported lazily: raises a clear error when Telethon is not installed.
    """
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "Telethon is not installed; run: pip install telethon"
        ) from exc
    return TelegramClient(str(session_path), api_id, api_hash)
