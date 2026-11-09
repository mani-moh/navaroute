from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

from src.categorizer import publish_subscriptions
from src.dedup import deduplicate_nodes
from src.fetcher import fetch_all_sources, load_sources
from src.healthcheck import check_nodes_health
from src.private_gist import log_gist_result, publish_private_subs
from src.transform_pattn import generate_pattn_variants

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(*args: Any, **kwargs: Any) -> None:
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    file = kwargs.get("file", sys.stdout)
    text = sep.join(str(a) for a in args) + end
    try:
        file.write(text)
    except UnicodeEncodeError:
        encoding = getattr(file, "encoding", "ascii") or "ascii"
        file.write(text.encode(encoding, errors="replace").decode(encoding))
    file.flush()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pattn",
        description="PattN High-Speed Proxy Aggregator & Iranian ISP Bypass Pipeline",
    )
    parser.add_argument(
        "--sources",
        default="sources.json",
        help="Path to public sources registry JSON (default: sources.json)",
    )
    parser.add_argument(
        "--output-dir",
        default="subs",
        help="Output directory for public subscription files (default: subs)",
    )
    parser.add_argument(
        "--healthcheck",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable live socket healthcheck (default: enabled)",
    )
    parser.add_argument(
        "--max-test-nodes",
        type=int,
        default=800,
        help="Maximum prioritized nodes to probe during healthcheck (default: 800)",
    )
    parser.add_argument(
        "--use-xray",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use local Xray binary for deep SOCKS proxy health probing when available (default: disabled, uses fast socket handshake)",
    )
    parser.add_argument(
        "--pattn-variants",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate specialized PattN bypass variants (F&F, IPv6-OOB, SNI-Spoof) (default: enabled)",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        default=False,
        help="Load private BPB sources and generate private_subs/ without leaking to subs/ (default: false)",
    )
    parser.add_argument(
        "--private-sources",
        default="sources.private.json",
        help="Path to private sources JSON (default: sources.private.json)",
    )
    parser.add_argument(
        "--private-only",
        action="store_true",
        default=False,
        help="Fast mode: load ONLY private sources, read existing subs/ from disk, and publish to private_subs/ and Secret Gist without touching subs/ or docs",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Worker concurrency for source fetching (default: 16)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP request timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--gist-token",
        default=None,
        help="GitHub PAT token for Secret Gist upload (optional, falls back to GIST_TOKEN env var)",
    )
    parser.add_argument(
        "--gist-id",
        default=None,
        help="Existing GitHub Secret Gist ID to update (optional, falls back to GIST_ID env var)",
    )
    parser.add_argument(
        "--private-dir",
        default="private_subs",
        help="Output directory for git-ignored private subscriptions (default: private_subs)",
    )
    parser.add_argument(
        "--no-docs",
        action="store_true",
        default=False,
        help="Disable automatic generation of STATUS.md and README.md",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.private_only:
        from src.private_gist import run_private_sync

        start_total = time.perf_counter()
        run_private_sync(
            sources_path=args.sources,
            private_sources_path=args.private_sources,
            subs_dir=args.output_dir,
            private_dir=args.private_dir,
            gist_token=args.gist_token,
            gist_id=args.gist_id,
            max_workers=args.max_workers,
            timeout=args.timeout,
            pattn_variants=args.pattn_variants,
            safe_print_fn=safe_print,
        )
        elapsed = time.perf_counter() - start_total
        safe_print("-" * 65)
        safe_print(f"✅ Private sync completed in {elapsed:.1f}s")
        safe_print("=" * 65)
        return 0

    start_total = time.perf_counter()
    safe_print("=" * 65)
    safe_print("🌐 PattN High-Speed Proxy Aggregator & Bypass Pipeline")
    safe_print("=" * 65)

    # 1. Load Sources
    sources = load_sources(
        sources_path=args.sources,
        include_private=args.include_private,
        private_sources_path=args.private_sources,
    )
    safe_print(
        f"[1/6] Loaded {len(sources)} enabled sources "
        f"(include_private={args.include_private})"
    )

    # 2. Fetch Sources Concurrently
    safe_print(f"[2/6] Concurrently fetching feeds (workers={args.max_workers}, timeout={args.timeout}s)...")
    raw_nodes, json_configs, stats = fetch_all_sources(
        sources=sources,
        max_workers=args.max_workers,
        timeout=args.timeout,
    )
    successful_sources = sum(1 for s in stats if s.success)
    safe_print(
        f"      Fetched {len(raw_nodes)} raw nodes & {len(json_configs)} JSON configs "
        f"across {successful_sources}/{len(sources)} sources"
    )

    # 3. Deduplication
    safe_print("[3/6] Running semantic deduplication...")
    deduped_nodes = deduplicate_nodes(raw_nodes)
    safe_print(f"      Condensed {len(raw_nodes)} raw nodes -> {len(deduped_nodes)} unique nodes")

    # 4. PattN Bypass Variant Transformation
    if args.pattn_variants:
        safe_print("[4/6] Synthesizing PattN bypass variants (F&F, IPv6-OOB, SNI-Spoof)...")
        variants = generate_pattn_variants(deduped_nodes, max_source_nodes=250)
        safe_print(f"      Generated {len(variants)} specialized bypass variants")
        all_nodes = deduplicate_nodes(deduped_nodes + variants)
        safe_print(f"      Total unique nodes with variants: {len(all_nodes)}")
    else:
        safe_print("[4/6] PattN variants disabled, skipping.")
        all_nodes = deduped_nodes

    # 5. Live Multi-Stage Healthcheck
    if args.healthcheck:
        safe_print(
            f"[5/6] Probing socket reachability on up to {args.max_test_nodes} "
            f"prioritized nodes..."
        )
        check_nodes_health(
            all_nodes,
            max_nodes=args.max_test_nodes,
            concurrency=64,
            timeout=3.5,
            use_xray=args.use_xray,
        )
        passed_count = sum(1 for n in all_nodes if n.passed_healthcheck)
        safe_print(f"      Healthcheck finished: {passed_count} nodes responsive")
    else:
        safe_print("[5/6] Healthcheck disabled, skipping.")

    # 6. Publishing Subscriptions
    safe_print(f"[6/6] Publishing public subscriptions to '{args.output_dir}/'...")
    pub_counts = publish_subscriptions(
        nodes=all_nodes,
        json_configs=json_configs,
        stats=stats,
        output_dir=args.output_dir,
        update_docs=not args.no_docs,
    )
    safe_print(f"      Successfully generated {len(pub_counts)} subscription files")

    # Optional: Private Subscriptions & Secret Gist
    if args.include_private:
        safe_print(f"[*] Publishing private BPB subscriptions to '{args.private_dir}/'...")
        priv_results = publish_private_subs(
            all_nodes=all_nodes,
            private_dir=args.private_dir,
            gist_token=args.gist_token,
            gist_id=args.gist_id,
        )
        safe_print(
            f"    Private subs generated: "
            f"{priv_results.get('bpb_only')} | {priv_results.get('private_verified')}"
        )
        # Mask and log Secret Gist in CI (os.environ.get("GITHUB_ACTIONS") == "true")
        log_gist_result(priv_results, safe_print_fn=safe_print)

    elapsed = time.perf_counter() - start_total
    safe_print("-" * 65)
    safe_print(f"✅ Pipeline completed in {elapsed:.1f}s")
    safe_print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
