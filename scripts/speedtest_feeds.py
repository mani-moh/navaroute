"""Throughput (download speed) testing driver for subs/speed-100.txt.

Selects due configs from tested/fastest-100/online pools, measures
fixed-size HTTPS download throughput via an owned Xray binary, updates
pruned state in data/speed_state.json, and publishes subs/speed-100.txt
(+ _base64.txt twin) and data/speed_results.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.healthcheck import CoreInfrastructureError  # noqa: E402
from src.speedtest import (DEFAULT_CANDIDATE_CAP, DEFAULT_MAX_RUNTIME,  # noqa: E402
                           DEFAULT_PER_RUN_CAP, DEFAULT_SPEED_BATCH_SIZE,
                           DEFAULT_SPEED_TEST_URL, DEFAULT_SPEED_TIMEOUT,
                           DEFAULT_SPEED_WORKERS, SPEED_FEED_LIMIT,
                           SPEED_FEED_NAME, SPEED_INTERVAL_HOURS,
                           build_speed_results, collect_candidates,
                           load_speed_state, measure_batch_xray,
                           prune_speed_state, rank_top_speed,
                           render_speed_feed, save_speed_state,
                           select_due_speed, update_speed_state)
from src.storage import atomic_write_text  # noqa: E402

DEFAULT_STATE_PATH = REPO_ROOT / "data" / "speed_state.json"
DEFAULT_RESULTS_PATH = REPO_ROOT / "data" / "speed_results.json"
DEFAULT_SUBS_DIR = REPO_ROOT / "subs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xray-bin", required=True, help="absolute path to xray binary")
    parser.add_argument("--candidate-cap", type=int, default=DEFAULT_CANDIDATE_CAP)
    parser.add_argument("--per-run-cap", type=int, default=DEFAULT_PER_RUN_CAP)
    parser.add_argument("--timeout", type=float, default=DEFAULT_SPEED_TIMEOUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_SPEED_WORKERS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_SPEED_BATCH_SIZE)
    parser.add_argument("--max-runtime", type=float, default=DEFAULT_MAX_RUNTIME)
    parser.add_argument("--test-url", default=DEFAULT_SPEED_TEST_URL)
    parser.add_argument("--subs-dir", type=Path, default=DEFAULT_SUBS_DIR)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--results-path", type=Path, default=DEFAULT_RESULTS_PATH)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    candidates = collect_candidates(args.subs_dir, max_candidates=args.candidate_cap)
    if not candidates:
        print("speedtest: no candidate configs found; aborting", file=sys.stderr)
        return 1

    live_fps = {str(c["fingerprint"]) for c in candidates}
    state = load_speed_state(args.state_path, live_fingerprints=live_fps, now=now)
    due = select_due_speed(candidates, state, now, args.per_run_cap)
    print(f"speedtest: {len(candidates)} candidates, {len(state)} in state, {len(due)} due")

    tested_fps: set[str] = set()
    if due:
        due_nodes = [entry["node"] for entry in due]
        try:
            batch_result = measure_batch_xray(
                due_nodes,
                str(Path(args.xray_bin).resolve()),
                timeout=args.timeout,
                test_url=args.test_url,
                workers=args.workers,
                batch_size=args.batch_size,
                max_runtime=args.max_runtime,
            )
        except CoreInfrastructureError as exc:
            print(f"speedtest: core error: {exc}; publishing from prior state", file=sys.stderr)
        else:
            run_outcomes: dict[str, tuple[float, float] | None] = {}
            for idx in batch_result.attempted:
                fp = str(due[idx]["fingerprint"])
                tested_fps.add(fp)
                run_outcomes[fp] = batch_result.passed.get(idx)
            state = update_speed_state(state, run_outcomes, now)

    state = prune_speed_state(state, live_fps, now=now)
    save_speed_state(args.state_path, state)

    top_uris = rank_top_speed(candidates, state, limit=SPEED_FEED_LIMIT)
    plain, encoded = render_speed_feed(top_uris, timestamp, SPEED_INTERVAL_HOURS)
    args.subs_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.subs_dir / f"{SPEED_FEED_NAME}.txt", plain)
    atomic_write_text(args.subs_dir / f"{SPEED_FEED_NAME}_base64.txt", encoded)

    report = build_speed_results(candidates, state, tested_fps, now, len(top_uris))
    args.results_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.results_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    print(
        f"speedtest: tested {report['tested']}, passing {report['passed']}, "
        f"published {report['top_feed_count']} in {SPEED_FEED_NAME}.txt "
        f"(top {report['top_speed_mbps']} Mbps)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
