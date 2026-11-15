"""Compact data/tier1_state.json by pruning entries that are no longer live.

Loads the current feeds (core groups + slices), computes the live
fingerprint set, drops every state entry whose fingerprint is not in
that set (and entries older than the retention window), and writes the
smaller file back. Run with --dry-run to report what would be pruned
without writing.

Usage:
  python scripts/prune_tier1_state.py [--state-path data/tier1_state.json]
      [--subs-dir subs] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.models import SourceConfig  # noqa: E402
from src.parsers import parse_proxy_uri  # noqa: E402
from src.tier1 import TIER_GROUPS, load_state, save_state  # noqa: E402
from scripts.tier1_pattn import load_slice_feeds, read_feed_uris  # noqa: E402

SOURCE = SourceConfig("tier1", "Tier 1", "https://github.com/mani-moh/navaroute")


def live_fingerprints(subs_dir: Path) -> set[str]:
    """Fingerprints of every config in the current feeds (core + slices)."""
    uri_to_fp: dict[str, str] = {}
    for group in TIER_GROUPS:
        for uri in read_feed_uris(subs_dir / f"{group}.txt"):
            if uri in uri_to_fp:
                continue
            node = parse_proxy_uri(uri, SOURCE)
            if node is not None:
                uri_to_fp[uri] = node.semantic_fingerprint()
    for uris in load_slice_feeds(subs_dir).values():
        for uri in uris:
            if uri in uri_to_fp:
                continue
            node = parse_proxy_uri(uri, SOURCE)
            if node is not None:
                uri_to_fp[uri] = node.semantic_fingerprint()
    return set(uri_to_fp.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=REPO_ROOT / "data" / "tier1_state.json")
    parser.add_argument("--subs-dir", type=Path, default=REPO_ROOT / "subs")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be pruned without writing")
    args = parser.parse_args(argv)

    if not args.state_path.exists():
        print(f"prune: no state file at {args.state_path}; nothing to do")
        return 0
    old_size = args.state_path.stat().st_size
    old_count = len(json.loads(args.state_path.read_text(encoding="utf-8"))["entries"])

    now = datetime.now(timezone.utc)
    live = live_fingerprints(args.subs_dir)
    pruned = load_state(args.state_path, live_fingerprints=live, now=now)
    new_count = len(pruned)
    print(f"prune: live fingerprints: {len(live)}")
    print(f"prune: entries {old_count} -> {new_count} "
          f"(dropped {old_count - new_count})")
    if args.dry_run:
        print("prune: dry run, file unchanged")
        return 0
    save_state(args.state_path, pruned)
    new_size = args.state_path.stat().st_size
    print(f"prune: size {old_size} -> {new_size} bytes "
          f"(saved {old_size - new_size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
