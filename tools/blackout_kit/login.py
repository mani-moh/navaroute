"""Interactive Telethon login for the Blackout Kit.

Run by Ryan, on the VM, once:

  python3 -m tools.blackout_kit.login --api-id <id> --api-hash <hash>

The script asks for his phone number, then for the login code Telegram
sends him (and his 2FA password, if he has one). The phone number and
the code are read from the terminal prompt only: they are never stored,
never logged, and never sent anywhere except to Telegram's own login
API. After login the session file is encrypted with the Blackout Kit
key and the plaintext session is deleted (see telegram_mine.py).

Prerequisites (Ryan):
  1. A Telegram API id/hash from https://my.telegram.org/apps
     (create an app once; the id/hash identify the app, not Ryan).
  2. The Blackout Kit key file already exists (~/.config/blackout-kit/key).
  3. Telethon installed: pip install telethon
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import crypto
from .telegram_mine import build_client, store_session_encrypted

DEFAULT_STATE_DIR = Path.home() / ".config" / "blackout-kit"
DEFAULT_SESSION = DEFAULT_STATE_DIR / "telegram.session"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Log the Blackout Kit Telegram miner in.")
    parser.add_argument("--api-id", required=True, type=int, help="API id from my.telegram.org")
    parser.add_argument("--api-hash", required=True, help="API hash from my.telegram.org")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION,
                        help="Session file path (default: %(default)s)")
    parser.add_argument("--key-file", type=Path, default=crypto.DEFAULT_KEY_PATH)
    args = parser.parse_args(argv)

    try:
        key = crypto.load_key_file(args.key_file)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"Cannot load the Blackout Kit key: {exc}", file=sys.stderr)
        print("Generate one first: python3 -m tools.blackout_kit.cli genkey", file=sys.stderr)
        return 2

    args.session.parent.mkdir(parents=True, exist_ok=True)
    phone = input("Phone number (international format, e.g. +98...): ").strip()
    if not phone:
        print("No phone number given; aborting.", file=sys.stderr)
        return 2

    client = build_client(args.session, args.api_id, args.api_hash)

    def code_callback() -> str:
        return input("Login code Telegram just sent you: ").strip()

    def password_callback() -> str:
        return getpass.getpass("Two-step verification password: ")

    # Telethon's start() sends the code request, then calls the callbacks.
    client.start(phone=phone, code_callback=code_callback, password=password_callback)
    me = client.get_me()
    print(f"Logged in as {getattr(me, 'username', None) or getattr(me, 'id', '?')}.")
    client.disconnect()

    enc_path = store_session_encrypted(args.session, key)
    print(f"Session encrypted and stored at {enc_path} (mode 600).")
    print("The plaintext session file was deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
