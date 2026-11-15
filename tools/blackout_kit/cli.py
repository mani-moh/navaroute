"""Command-line entry points for the Blackout Kit.

  python3 -m tools.blackout_kit.cli genkey [--key-file PATH]
      Generate the ONE key and store it mode 600 (never printed).
  python3 -m tools.blackout_kit.cli check
      Print the blackout monitor verdict (BLACKOUT/DEGRADED/OK/UNKNOWN).
  python3 -m tools.blackout_kit.cli search [--publish]
      Run one gated search round; with --publish, write new findings
      to the encrypted special feeds in the feed-data clone.
  python3 -m tools.blackout_kit.cli encrypt FILE --feed kit-2_base64.txt
      Encrypt a file to stdout (base64 envelope).
  python3 -m tools.blackout_kit.cli decrypt FILE --feed kit-2_base64.txt
      Decrypt a base64 envelope file to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import crypto, publish
from .config import KitConfig
from .feeds import PublishedRegistry
from .search_loop import SearchLoop, read_verdict


def _cmd_genkey(args: argparse.Namespace) -> int:
    key_path = Path(args.key_file)
    if key_path.exists():
        print(f"Key file already exists: {key_path} -- refusing to overwrite.", file=sys.stderr)
        print("Delete it deliberately first if a fresh key is really wanted.", file=sys.stderr)
        return 2
    key = crypto.generate_key()
    crypto.save_key_file(key, key_path)
    print(f"Key generated and stored at {key_path} (mode 600).")
    print("It is NOT printed here. The Telegram bot sends it to the owner with /key")
    print("once BLACKOUT_KEY is set on the Lambda (see docs/BLACKOUT-KIT.md).")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    cfg = KitConfig.load(args.config)
    verdict, score = read_verdict(cfg.monitor_path)
    print(f"{verdict} (score {score})")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    cfg = KitConfig.load(args.config)
    registry = PublishedRegistry(cfg.state_dir / "published.json")
    loop = SearchLoop(cfg=cfg, registry=registry)
    new_findings = loop.run_round(miner=None)
    print(f"New findings: {len(new_findings)}")
    for finding in new_findings:
        print(f"  [{finding.kind}] {finding.text[:100]}")
    if args.publish and new_findings:
        key = crypto.load_key()
        result = publish.build_special_feeds(new_findings, key, registry=registry)
        print(f"Wrote: {result.written or 'none'}; skipped (newer): {result.skipped_newer or 'none'}")
        if result.written and not args.no_commit:
            pushed = publish.commit_and_push(message="Blackout Kit: update special feeds")
            print("Committed + pushed to feed-data." if pushed else "Nothing to commit.")
    return 0


def _cmd_encrypt(args: argparse.Namespace) -> int:
    key = crypto.load_key()
    sys.stdout.write(crypto.encrypt_text(Path(args.file).read_bytes(), key, args.feed))
    return 0


def _cmd_decrypt(args: argparse.Namespace) -> int:
    key = crypto.load_key()
    try:
        sys.stdout.buffer.write(
            crypto.decrypt_text(Path(args.file).read_text(encoding="utf-8"), key, args.feed)
        )
    except ValueError as exc:
        print(f"decryption failed: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blackout Kit tools.")
    parser.add_argument("--config", type=Path, default=KitConfig.__dataclass_fields__["state_dir"].default / "config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    genkey = sub.add_parser("genkey", help="generate the kit key (mode 600, never printed)")
    genkey.add_argument("--key-file", type=Path, default=crypto.DEFAULT_KEY_PATH)
    genkey.set_defaults(func=_cmd_genkey)

    check = sub.add_parser("check", help="print the blackout monitor verdict")
    check.set_defaults(func=_cmd_check)

    search = sub.add_parser("search", help="run one gated search round")
    search.add_argument("--publish", action="store_true", help="publish new findings to feed-data")
    search.add_argument("--no-commit", action="store_true", help="write files but do not commit")
    search.set_defaults(func=_cmd_search)

    enc = sub.add_parser("encrypt", help="encrypt a file to a base64 envelope on stdout")
    enc.add_argument("file", type=Path)
    enc.add_argument("--feed", required=True, help="feed filename, e.g. kit-2_base64.txt")
    enc.set_defaults(func=_cmd_encrypt)

    dec = sub.add_parser("decrypt", help="decrypt a base64 envelope file to stdout")
    dec.add_argument("file", type=Path)
    dec.add_argument("--feed", required=True, help="feed filename, e.g. kit-2_base64.txt")
    dec.set_defaults(func=_cmd_decrypt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
