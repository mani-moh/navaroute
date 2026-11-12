from __future__ import annotations

import argparse
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from src.categorizer import publish_subscriptions
from src.dedup import deduplicate_nodes
from src.fetcher import fetch_all_sources, load_sources
from src.healthcheck import check_nodes_health
from src.project import PROJECT_NAME

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
        prog="navaroute",
        description="NavaRoute Public Proxy Subscription Aggregator",
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

    try:
        return _run(args)
    except Exception as exc:
        # Exception messages can contain source URLs, credentials or core logs.
        frames = traceback.extract_tb(exc.__traceback__)
        source_root = Path(__file__).resolve().parent
        frame = next((frame for frame in reversed(frames) if Path(frame.filename).resolve(strict=False).is_relative_to(source_root)), frames[-1] if frames else None)
        function = re.sub(r"[^A-Za-z0-9_.<>-]", "_", frame.name if frame else "unknown")[:80] or "unknown"
        line = frame.lineno if frame else 0
        safe_print(f"Pipeline failed ({type(exc).__name__} at <{function}>:{line}); publication stopped. Any interrupted persistence must recover before the next run.")
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.discover or args.discover_only:
        from src.discovery.agent import DiscoveryAgent, load_local_dotenv

        load_local_dotenv()
        agent = DiscoveryAgent()
        safe_print("Running requested discovery...")
        discovered = agent.run_cycle(
            max_channels=args.discovery_channels,
            max_time_seconds=args.discovery_time,
            max_api_requests=args.discovery_api_requests,
        )
        safe_print(f"Discovery finished: {len(discovered)} active discovered sources.")

        if args.discover_only:
            return 0

    start_total = time.perf_counter()
    safe_print("=" * 65)
    safe_print(f"🌐 {PROJECT_NAME} Public Subscription Aggregator")
    safe_print("=" * 65)

    # 1. Load Sources
    sources = load_sources(sources_path=args.sources)
    if not sources:
        raise ValueError("No enabled valid sources")
    safe_print(f"[1/6] Loaded {len(sources)} enabled sources")

    # 2. Fetch Sources Concurrently
    safe_print(f"[2/6] Concurrently fetching feeds (workers={args.max_workers}, timeout={args.timeout}s)...")
    raw_nodes, json_configs, stats = fetch_all_sources(
        sources=sources,
        max_workers=args.max_workers,
        timeout=args.timeout,
    )
    successful_sources = sum(1 for s in stats if s.success)
    if not successful_sources or not raw_nodes:
        # Privacy-safe census: error strings carry fixed literals/type names only, never URLs.
        failure_kinds: dict[str, int] = {}
        for item in stats:
            if not item.success:
                kind = item.error or "unknown"
                failure_kinds[kind] = failure_kinds.get(kind, 0) + 1
        for kind, count in sorted(failure_kinds.items(), key=lambda pair: (-pair[1], pair[0])):
            safe_print(f"      failure category: {kind} x{count}")
        raise ValueError("No usable source yield; previous publication retained")
    safe_print(
        f"      Fetched {len(raw_nodes)} raw nodes & {len(json_configs)} JSON configs "
        f"across {successful_sources}/{len(sources)} sources"
    )

    # 3. Deduplication
    safe_print("[3/6] Running semantic deduplication...")
    deduped_nodes = deduplicate_nodes(raw_nodes)
    safe_print(f"      Condensed {len(raw_nodes)} raw nodes -> {len(deduped_nodes)} unique nodes")

    all_nodes = deduped_nodes

    # 5. Socket reachability (not credential or end-to-end verification)
    if args.healthcheck:
        safe_print(
            f"[5/6] Probing socket reachability on up to {args.max_test_nodes} "
            f"prioritized nodes..."
        )
        health_summary = check_nodes_health(
            [n for n in all_nodes if n.scheme not in ("hysteria2", "tuic", "wireguard")],
            max_nodes=args.max_test_nodes,
            concurrency=64,
            timeout=3.5,
        )
        passed_count = health_summary["passed"]
        safe_print(f"      Healthcheck finished: {passed_count} nodes responsive")
    else:
        safe_print("[5/6] Healthcheck disabled, skipping.")

    # 6. Publishing Subscriptions
    safe_print("[6/6] Publishing public subscriptions...")
    pub_counts = publish_subscriptions(
        nodes=all_nodes,
        json_configs=json_configs,
        stats=stats,
        output_dir=args.output_dir,
        update_docs=not args.no_docs,
        healthcheck_ran=args.healthcheck,
    )
    safe_print(f"      Successfully generated {len(pub_counts)} subscription files")

    elapsed = time.perf_counter() - start_total
    safe_print("-" * 65)
    safe_print(f"✅ Pipeline completed in {elapsed:.1f}s")
    safe_print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
