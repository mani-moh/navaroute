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
        "--no-docs",
        action="store_true",
        default=False,
        help="Disable automatic generation of STATUS.md and README.md",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        default=False,
        help="Run the autonomous Nemotron discovery agent before aggregation",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        default=False,
        help="Run ONLY the autonomous discovery agent and exit",
    )
    parser.add_argument(
        "--discovery-time",
        type=int,
        default=360,
        help="Max time budget in seconds for the discovery agent (default: 360)",
    )
    parser.add_argument(
        "--discovery-channels",
        type=int,
        default=100,
        help="Max channels to scan per discovery cycle (default: 100)",
    )
    parser.add_argument(
        "--discovery-api-requests",
        type=int,
        default=0,
        help="Target NVIDIA API requests for extended discovery runs (e.g. 500)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.discover or args.discover_only:
        from src.discovery.agent import DiscoveryAgent, load_local_dotenv

        load_local_dotenv()
        agent = DiscoveryAgent()
        safe_print("=" * 65)
        safe_print(f"🛰️ Running Autonomous Discovery Agent (model={agent.nemotron.model})...")
        safe_print("=" * 65)
        try:
            discovered = agent.run_cycle(
                max_channels=args.discovery_channels,
                max_time_seconds=args.discovery_time,
                max_api_requests=args.discovery_api_requests,
            )
            safe_print(
                f"✅ Discovery finished: {len(discovered)} active discovered sources "
                f"({agent.nemotron.api_requests_count} NVIDIA API requests used)."
            )
        except Exception as exc:
            safe_print(f"⚠️ Discovery encountered a non-fatal error: {exc}")

        if args.discover_only:
            return 0

    start_total = time.perf_counter()
    safe_print("=" * 65)
    safe_print("🌐 PattN High-Speed Proxy Aggregator & Bypass Pipeline")
    safe_print("=" * 65)

    # 1. Load Sources
    sources = load_sources(sources_path=args.sources)
    safe_print(f"[1/6] Loaded {len(sources)} enabled sources")

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

    elapsed = time.perf_counter() - start_total
    safe_print("-" * 65)
    safe_print(f"✅ Pipeline completed in {elapsed:.1f}s")
    safe_print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
