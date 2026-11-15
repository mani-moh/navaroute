"""Key handling and the BK1 envelope for Blackout Kit special feeds.

Envelope (all base64 text in the published file, one line per entry is
not assumed -- the whole file is one envelope):
  magic "BK1" (3 bytes) || nonce (12 random bytes) || ciphertext || tag (16)
  base64-encoded, wrapped at 76 columns, exactly like the other feed files.

The key is 32 random bytes (AES-256). It lives ONLY in a mode-600 file
on the VM (default ~/.config/blackout-kit/key) and in the BLACKOUT_KEY
env var on the Lambda that runs the Telegram bot. It is never written
to the repo, never printed, never logged.

Stdlib only: the AES-GCM core is tools/blackout_kit/aesgcm.py.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from .aesgcm import aes_gcm_decrypt, aes_gcm_encrypt

MAGIC = b"BK1"
NONCE_LEN = 12
TAG_LEN = 16
KEY_LEN = 32
# AAD binds the envelope to its feed name, so a blob cannot be replayed
# into a different feed file undetected.
AAD_PREFIX = b"blackout-kit:"

DEFAULT_KEY_PATH = Path.home() / ".config" / "blackout-kit" / "key"


def generate_key() -> bytes:
    """Return 32 fresh random bytes (AES-256)."""
    return secrets.token_bytes(KEY_LEN)


def key_to_text(key: bytes) -> str:
    """Base64 text form of the key (the form the bot sends, and the form
    the key file and BLACKOUT_KEY env var hold)."""
    if len(key) != KEY_LEN:
        raise ValueError("key must be 32 bytes")
    return base64.b64encode(key).decode("ascii")


def key_from_text(text: str) -> bytes:
    try:
        key = base64.b64decode(text.strip(), validate=True)
    except Exception as exc:
        raise ValueError("key is not valid base64") from exc
    if len(key) != KEY_LEN:
        raise ValueError("key must decode to 32 bytes")
    return key


def save_key_file(key: bytes, path: Path | str = DEFAULT_KEY_PATH) -> Path:
    """Write the key as base64 text, mode 600, creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (key_to_text(key) + "\n").encode("ascii"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path


def load_key_file(path: Path | str = DEFAULT_KEY_PATH) -> bytes:
    """Read the key from a mode-600 file; refuse files readable by others."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"key file not found: {path}")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(
            f"key file {path} is group/other-readable (mode {mode:03o}); "
            "run: chmod 600 " + str(path)
        )
    return key_from_text(path.read_text(encoding="ascii"))


def load_key(path: Path | str | None = None, env: dict | None = None) -> bytes:
    """Load the key: BLACKOUT_KEY env var first, else the key file."""
    env = os.environ if env is None else env
    text = (env.get("BLACKOUT_KEY") or "").strip()
    if text:
        return key_from_text(text)
    return load_key_file(path or DEFAULT_KEY_PATH)


def _aad(feed_name: str) -> bytes:
    return AAD_PREFIX + feed_name.encode("utf-8")


def encrypt_bytes(plaintext: bytes, key: bytes, feed_name: str) -> bytes:
    """Encrypt to raw envelope bytes (BK1 || nonce || ciphertext || tag)."""
    if len(key) != KEY_LEN:
        raise ValueError("key must be 32 bytes")
    nonce = secrets.token_bytes(NONCE_LEN)
    ciphertext, tag = aes_gcm_encrypt(key, nonce, plaintext, _aad(feed_name))
    return MAGIC + nonce + ciphertext + tag


def decrypt_bytes(envelope: bytes, key: bytes, feed_name: str) -> bytes:
    """Decrypt raw envelope bytes; raises ValueError on bad magic/tag."""
    if len(key) != KEY_LEN:
        raise ValueError("key must be 32 bytes")
    if len(envelope) < len(MAGIC) + NONCE_LEN + TAG_LEN:
        raise ValueError("envelope too short")
    if envelope[: len(MAGIC)] != MAGIC:
        raise ValueError("not a BK1 envelope")
    nonce = envelope[len(MAGIC) : len(MAGIC) + NONCE_LEN]
    ciphertext = envelope[len(MAGIC) + NONCE_LEN : -TAG_LEN]
    tag = envelope[-TAG_LEN:]
    return aes_gcm_decrypt(key, nonce, ciphertext, tag, _aad(feed_name))


def encrypt_text(plaintext: bytes, key: bytes, feed_name: str) -> str:
    """Encrypt and return base64 text wrapped at 76 columns + newline."""
    raw = encrypt_bytes(plaintext, key, feed_name)
    encoded = base64.b64encode(raw).decode("ascii")
    lines = [encoded[i : i + 76] for i in range(0, len(encoded), 76)]
    return "\n".join(lines) + "\n"


def decrypt_text(text: str, key: bytes, feed_name: str) -> bytes:
    """Decrypt base64 envelope text (whitespace-insensitive)."""
    compact = "".join(text.split())
    try:
        raw = base64.b64decode(compact, validate=True)
    except Exception as exc:
        raise ValueError("envelope is not valid base64") from exc
    return decrypt_bytes(raw, key, feed_name)


def encrypt_file(src: Path | str, dst: Path | str, key: bytes, feed_name: str) -> Path:
    """Encrypt a file's bytes into a base64 envelope file."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(encrypt_text(Path(src).read_bytes(), key, feed_name), encoding="ascii")
    return dst


def decrypt_file(src: Path | str, dst: Path | str, key: bytes, feed_name: str) -> Path:
    """Decrypt a base64 envelope file back to its original bytes."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(decrypt_text(Path(src).read_text(encoding="ascii"), key, feed_name))
    return dst
