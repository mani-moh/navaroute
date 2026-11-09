from __future__ import annotations

import argparse
import sys
import time

from src.categorizer import publish_subscriptions
from src.dedup import deduplicate_nodes
from src.fetcher import fetch_all_sources, load_sources
from src.healthcheck import check_nodes_health
from src.private_gist import publish_private_subs
from src.transform_pattn import generate_pattn_variants


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

    start_total = time.perf_counter()
    print("=" * 65)
    print("🌐 PattN High-Speed Proxy Aggregator & Bypass Pipeline")
    print("=" * 65)

    # 1. Load Sources
    sources = load_sources(
        sources_path=args.sources,
        include_private=args.include_private,
    )
    print(
        f"[1/6] Loaded {len(sources)} enabled sources "
        f"(include_private={args.include_private})"
    )

    # 2. Fetch Sources Concurrently
    print(f"[2/6] Concurrently fetching feeds (workers={args.max_workers}, timeout={args.timeout}s)...")
    raw_nodes, json_configs, stats = fetch_all_sources(
        sources=sources,
        max_workers=args.max_workers,
        timeout=args.timeout,
    )
    successful_sources = sum(1 for s in stats if s.success)
    print(
        f"      Fetched {len(raw_nodes)} raw nodes & {len(json_configs)} JSON configs "
        f"across {successful_sources}/{len(sources)} sources"
    )

    # 3. Deduplication
    print("[3/6] Running semantic deduplication...")
    deduped_nodes = deduplicate_nodes(raw_nodes)
    print(f"      Condensed {len(raw_nodes)} raw nodes -> {len(deduped_nodes)} unique nodes")

    # 4. PattN Bypass Variant Transformation
    if args.pattn_variants:
        print("[4/6] Synthesizing PattN bypass variants (F&F, IPv6-OOB, SNI-Spoof)...")
        variants = generate_pattn_variants(deduped_nodes, max_source_nodes=250)
        print(f"      Generated {len(variants)} specialized bypass variants")
        all_nodes = deduplicate_nodes(deduped_nodes + variants)
        print(f"      Total unique nodes with variants: {len(all_nodes)}")
    else:
        print("[4/6] PattN variants disabled, skipping.")
        all_nodes = deduped_nodes

    # 5. Live Multi-Stage Healthcheck
    if args.healthcheck:
        print(
            f"[5/6] Probing socket reachability on up to {args.max_test_nodes} "
            f"prioritized nodes..."
        )
        check_nodes_health(all_nodes, max_nodes=args.max_test_nodes, concurrency=64, timeout=3.5)
        passed_count = sum(1 for n in all_nodes if n.passed_healthcheck)
        print(f"      Healthcheck finished: {passed_count} nodes responsive")
    else:
        print("[5/6] Healthcheck disabled, skipping.")

    # 6. Publishing Subscriptions
    print(f"[6/6] Publishing public subscriptions to '{args.output_dir}/'...")
    pub_counts = publish_subscriptions(
        nodes=all_nodes,
        json_configs=json_configs,
        stats=stats,
        output_dir=args.output_dir,
        update_docs=not args.no_docs,
    )
    print(f"      Successfully generated {len(pub_counts)} subscription files")

    # Optional: Private Subscriptions & Secret Gist
    if args.include_private:
        print(f"[*] Publishing private BPB subscriptions to '{args.private_dir}/'...")
        priv_results = publish_private_subs(
            all_nodes=all_nodes,
            private_dir=args.private_dir,
            gist_token=args.gist_token,
            gist_id=args.gist_id,
        )
        print(
            f"    Private subs generated: "
            f"{priv_results.get('bpb_only')} | {priv_results.get('private_verified')}"
        )
        if gist_id := priv_results.get("gist_id"):
            print(f"    Secret Gist updated: {priv_results.get('gist_url', gist_id)}")

    elapsed = time.perf_counter() - start_total
    print("-" * 65)
    print(f"✅ Pipeline completed in {elapsed:.1f}s")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
