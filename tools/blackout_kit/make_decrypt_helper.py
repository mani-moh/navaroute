"""Build the standalone decrypt helper (tools/blackout_kit/decrypt_helper.py).

The helper is a single file with no imports from this package, so a
user can download it next to a special-feed file and run it with a
bare Python 3 install:

  python3 decrypt_helper.py --key-file key.txt kit-2_base64.txt

It is generated from tools/blackout_kit/aesgcm.py (the AES-GCM core)
plus a small CLI wrapper, so the crypto in the helper can never drift
from the crypto in the kit. Regenerate after touching aesgcm.py:

  python3 -m tools.blackout_kit.make_decrypt_helper

The test suite asserts the checked-in helper matches a fresh build.
"""

from __future__ import annotations

import sys
from pathlib import Path

HEADER = '''#!/usr/bin/env python3
"""Standalone decrypt helper for Blackout Kit special feeds.

GENERATED FILE -- do not edit by hand. Regenerate with:
  python3 -m tools.blackout_kit.make_decrypt_helper

Usage:
  python3 decrypt_helper.py --key-file KEYFILE FEEDFILE [OUTFILE]

KEYFILE holds the base64 key (the same key the Telegram bot sends with
/key, or the ~/.config/blackout-kit/key file). FEEDFILE is a downloaded
special-feed file (e.g. kit-2_base64.txt). The decrypted content is
written to OUTFILE, or to stdout when OUTFILE is omitted.

Stdlib only; no third-party packages, no network access.
"""

'''

WRAPPER = '''

# ---------------------------------------------------------------- CLI

import sys
from pathlib import Path


def decrypt_bytes(envelope, key, feed_name):
    """Decrypt a BK1 envelope: magic || nonce(12) || ciphertext || tag(16)."""
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if len(envelope) < 3 + 12 + 16 or envelope[:3] != b"BK1":
        raise ValueError("not a BK1 envelope")
    nonce = envelope[3:15]
    ciphertext, tag = envelope[15:-16], envelope[-16:]
    aad = b"blackout-kit:" + feed_name.encode("utf-8")
    return aes_gcm_decrypt(key, nonce, ciphertext, tag, aad)


def _cli(argv=None):
    import argparse
    import base64

    parser = argparse.ArgumentParser(description="Decrypt a Blackout Kit special feed.")
    parser.add_argument("feed_file", help="downloaded special-feed file, e.g. kit-2_base64.txt")
    parser.add_argument("out_file", nargs="?", help="output file (default: stdout)")
    parser.add_argument("--key-file", required=True, help="file holding the base64 key")
    parser.add_argument("--feed-name", default=None,
                        help="feed filename for authentication (default: the feed file's name)")
    args = parser.parse_args(argv)

    try:
        key = base64.b64decode(Path(args.key_file).read_text(encoding="ascii").strip())
    except Exception as exc:
        print(f"cannot read key file: {exc}", file=sys.stderr)
        return 2
    if len(key) != 32:
        print("key file must hold a base64 32-byte key", file=sys.stderr)
        return 2
    feed_name = args.feed_name or Path(args.feed_file).name
    try:
        envelope = base64.b64decode("".join(Path(args.feed_file).read_text(encoding="utf-8").split()))
    except Exception as exc:
        print(f"cannot read feed file: {exc}", file=sys.stderr)
        return 2
    try:
        plain = decrypt_bytes(envelope, key, feed_name)
    except ValueError as exc:
        print(f"decryption failed: {exc}", file=sys.stderr)
        return 1
    if args.out_file:
        Path(args.out_file).write_bytes(plain)
    else:
        sys.stdout.buffer.write(plain)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
'''


def build() -> str:
    kit_dir = Path(__file__).resolve().parent
    aesgcm = (kit_dir / "aesgcm.py").read_text(encoding="utf-8")
    # Strip the aesgcm module docstring + __future__ import; the helper
    # carries its own header. Keep everything from the GF(2^8) section on.
    marker = "# ---------------------------------------------------------------- GF(2^8)"
    core = aesgcm[aesgcm.index(marker):]
    # The core uses `from __future__ import annotations`? No -- it only
    # uses plain functions, but keep the import line for safety.
    return HEADER + "from __future__ import annotations\n\n" + core + WRAPPER


def main() -> int:
    out = Path(__file__).resolve().parent / "decrypt_helper.py"
    out.write_text(build(), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
