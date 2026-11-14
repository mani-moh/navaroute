"""Tier-1 PattN driver: Realping-test due configs on a GitHub Windows runner.

Reads the published feeds (subs/<group>.txt), picks the configs due
under the failure backoff, imports them into fresh headless PattN
WebAPIs (pinned P40 release), runs Realping, and writes:
  data/tier1_state.json      (per-fingerprint streak / last test / delay)
  data/tier1_results.json    (per-feed tested / passed / github_blocked)
  subs/tier1_<group>.txt and subs/tier1_<group>_base64.txt
    (the feed's configs that passed tier 1, plus the GitHub-blocked
    set, sorted by delay)

Speed: the server tests in batches of SpeedTestPageSize profiles
(default 16), all of a batch concurrently, and a batch waits for its
slowest member — so with 8,000 due profiles a run took ~90 minutes.
The driver writes page size 100 into each server's guiNConfig.json
and runs several servers side by side as a filling system: due
configs are split into chunks of the page size, and each server is
refilled with the next chunk the moment its chunk finishes, so a
slow config on one server never idles the others. The per-test 5 s
timeout is compiled into the P40 binary and cannot be lowered.

Usage (the workflow downloads and extracts the P40 zip first):
  python scripts/tier1_pattn.py --webapi-dir <extracted zip dir>
      [--per-run-cap 8000] [--speedtest-timeout 5400]
      [--speedtest-page-size 100] [--speedtest-workers 4]

Exit 0 even when some configs fail (failures are data, recorded in the
state); non-zero only when the run itself cannot proceed (no feeds,
no WebAPI worker starts, no profiles imported).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.models import SourceConfig  # noqa: E402
from src.parsers import parse_proxy_uri  # noqa: E402
from src.pattn_client import (DEFAULT_PORT, DEFAULT_SPEEDTEST_PAGE_SIZE,  # noqa: E402
                              DEFAULT_SPEEDTEST_WORKERS, PINNED_WEBAPI_VERSION,
                              PattnClient, PattnError, generate_api_key,
                              prepare_webapi_worker_dir, start_webapi,
                              stop_webapi, write_webapi_speedtest_config)
from src.tier1 import (DEFAULT_PER_RUN_CAP, TIER_GROUPS, TIER1_INTERVAL_HOURS,  # noqa: E402
                       build_results, build_variants, load_blocked, load_state,
                       passing_fingerprints, render_feed, save_state,
                       select_due, update_state)

STATE_PATH = REPO_ROOT / "data" / "tier1_state.json"
RESULTS_PATH = REPO_ROOT / "data" / "tier1_results.json"
BLOCKED_PATH = REPO_ROOT / "data" / "github_blocked.json"
SUBS_DIR = REPO_ROOT / "subs"
SOURCE = SourceConfig("tier1", "Tier 1", "https://github.com/mani-moh/navaroute")


def read_feed_uris(path: Path) -> list[str]:
    """The URI lines of a published feed file (headers and blanks skipped)."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if "://" in line and not line.startswith("#")]


def find_webapi_exe(webapi_dir: Path) -> Path:
    for name in ("v2rayN.WebAPI.exe", "v2rayN.WebAPI"):
        found = sorted(webapi_dir.rglob(name))
        if found:
            return found[0]
    raise PattnError(f"No v2rayN.WebAPI executable under {webapi_dir}")


def chunk_list(items: list[str], size: int) -> list[list[str]]:
    """Split items into consecutive chunks of at most ``size``."""
    if size < 1:
        raise ValueError("chunk size must be >= 1")
    return [items[start:start + size] for start in range(0, len(items), size)]


class Tier1Worker:
    """One WebAPI server: its process, client, and current chunk."""

    def __init__(self, index: int, exe: Path, port: int, process, client: PattnClient):
        self.index = index
        self.exe = exe
        self.port = port
        self.process = process
        self.client = client
        self.chunk: list[str] | None = None
        self.chunk_ids: list[str] = []
        self.chunk_attempts = 0
        self.still = 0
        self.last_untested: int | None = None
        self.alive = True
        self.tested_chunks = 0


def prepare_workers(webapi_dir: Path, exe: Path, count: int, page_size: int,
                    api_key: str) -> list[Tier1Worker]:
    """Start ``count`` WebAPI servers (worker 0 on port 5080, the rest
    on 5081, ...), each with the page size written into its own
    guiNConfig.json. A worker that fails to start is skipped with a
    warning; when none starts the run cannot proceed."""
    workers: list[Tier1Worker] = []
    for index in range(max(1, count)):
        try:
            worker_exe = prepare_webapi_worker_dir(webapi_dir, exe, index)
            write_webapi_speedtest_config(worker_exe, page_size)
            port = DEFAULT_PORT + index
            process = start_webapi(worker_exe, api_key, port=port)
            client = PattnClient(f"http://127.0.0.1:{port}", api_key)
            client.login()
        except PattnError as exc:
            print(f"tier1: worker {index} failed to start: {exc}", file=sys.stderr)
            continue
        workers.append(Tier1Worker(index, worker_exe, port, process, client))
        print(f"tier1: worker {index} up on port {port} (page size {page_size})")
    if not workers:
        raise PattnError("No WebAPI worker started")
    return workers


def _assign_chunk(worker: Tier1Worker, chunk: list[str], attempt: int) -> None:
    """Import the chunk into the worker and start its Realping test.

    The chunk's IndexIds are the profiles the import added (diffed
    against what the worker already held, so a reused worker's earlier
    chunks are not mistaken for this one). When the server adds nothing
    new — it deduped the chunk, or the profiles were already there —
    every profile it holds now counts as this chunk, so the wait still
    observes real delays instead of finishing instantly.
    """
    worker.chunk = chunk
    worker.chunk_attempts = attempt
    worker.still = 0
    worker.last_untested = None
    client = worker.client
    before = {p.get("IndexId") for p in client.profiles() if p.get("IndexId")}
    sent = client.import_profiles(chunk)
    imported = client.profiles()
    testable = sum(1 for p in imported if p.get("CanTest") is not False)
    core_types: dict[str, int] = {}
    for p in imported:
        core = str(p.get("CoreType") or "?")
        core_types[core] = core_types.get(core, 0) + 1
    print(f"tier1: worker {worker.index}: imported {sent} profiles "
          f"(WebAPI {PINNED_WEBAPI_VERSION}), testable {testable}/{len(imported)}, "
          f"core types {core_types}")
    ids = [p["IndexId"] for p in imported
           if p.get("IndexId") and p["IndexId"] not in before]
    if not ids:
        ids = [p["IndexId"] for p in imported if p.get("IndexId")]
    worker.chunk_ids = ids
    client.start_speedtest()
    print(f"tier1: worker {worker.index}: operations right after start: "
          f"{client.operations()}")


def _chunk_done(worker: Tier1Worker) -> tuple[bool, int]:
    """(done, untested) for the worker's current chunk.

    Done when every IndexId of the chunk has a Delay, or the test
    stopped making progress: three polls with an unchanged untested
    count while no speed test is running (the same rule the old
    single-server wait used).
    """
    profiles = worker.client.profiles()
    by_id = {p.get("IndexId"): p for p in profiles if p.get("IndexId")}
    untested = sum(1 for index_id in worker.chunk_ids
                   if (by_id.get(index_id) or {}).get("Delay", 0) == 0)
    if untested == 0:
        return True, 0
    if untested == worker.last_untested:
        worker.still += 1
        if worker.still >= 3 and not worker.client.speedtest_running():
            return True, untested
    else:
        worker.still = 0
    worker.last_untested = untested
    return False, untested


def _collect_chunk(worker: Tier1Worker) -> list[dict]:
    """The worker's chunk profiles, for the merged result."""
    profiles = worker.client.profiles()
    wanted = set(worker.chunk_ids)
    collected = [p for p in profiles if p.get("IndexId") in wanted]
    if not collected:
        collected = list(profiles)
    worker.tested_chunks += 1
    worker.chunk = None
    worker.chunk_ids = []
    return collected


def run_speedtests(workers: list[Tier1Worker], chunks: list[list[str]],
                   timeout: int, poll: int = 10) -> list[dict]:
    """Feed chunks to workers, refilling each the moment its chunk
    finishes — the filling system. Returns the merged profile list.

    A worker that dies mid-chunk has its chunk requeued once; a chunk
    that fails twice is dropped (its configs stay untested, exactly as
    a stalled single-server run records them). The timeout bounds the
    whole testing phase; on expiry the profiles collected so far are
    returned. With no workers nothing can be tested.
    """
    if not workers:
        return []
    queue: list[tuple[list[str], int]] = [(chunk, 1) for chunk in chunks]
    collected: list[dict] = []
    deadline = time.monotonic() + timeout
    while queue or any(w.chunk is not None for w in workers if w.alive):
        for worker in workers:
            if not worker.alive:
                continue
            try:
                if worker.chunk is None and queue:
                    chunk, attempt = queue.pop(0)
                    _assign_chunk(worker, chunk, attempt)
                    print(f"tier1: worker {worker.index}: chunk attempt {attempt} "
                          f"({len(chunk)} profiles) started")
                elif worker.chunk is not None:
                    done, untested = _chunk_done(worker)
                    if done:
                        got = _collect_chunk(worker)
                        collected.extend(got)
                        print(f"tier1: worker {worker.index}: chunk finished, "
                              f"{len(got)} profiles, {untested} untested")
            except PattnError as exc:
                print(f"tier1: worker {worker.index} failed: {exc}", file=sys.stderr)
                worker.alive = False
                if worker.chunk is not None:
                    if worker.chunk_attempts < 2:
                        queue.append((worker.chunk, worker.chunk_attempts + 1))
                        print(f"tier1: worker {worker.index}: chunk requeued "
                              f"(attempt {worker.chunk_attempts + 1})")
                    else:
                        print(f"tier1: worker {worker.index}: chunk dropped after "
                              f"{worker.chunk_attempts} attempts", file=sys.stderr)
                    worker.chunk = None
                    worker.chunk_ids = []
                try:
                    stop_webapi(worker.process)
                except Exception:
                    pass
        if not any(w.alive for w in workers):
            # Nothing can ever make progress again; the queued chunks
            # stay untested, exactly as a run whose only server died.
            print(f"tier1: all workers dead; {len(queue)} chunks untested",
                  file=sys.stderr)
            break
        if not queue and not any(w.chunk is not None for w in workers if w.alive):
            break
        if time.monotonic() >= deadline:
            print("tier1: speed test timeout reached; collecting partial results",
                  file=sys.stderr)
            for worker in workers:
                if worker.alive and worker.chunk is not None:
                    try:
                        collected.extend(_collect_chunk(worker))
                    except PattnError:
                        pass
            break
        time.sleep(poll)
    return collected


def count_delays(profiles: list[dict]) -> dict[str, int]:
    """How many profiles passed (Delay > 0), failed (Delay -1), or were
    never actually tested (Delay 0 / missing). The untested count is the
    tell-tale for a WebAPI that did not test at all: a run with many
    failures and zero untested tested genuinely dead configs, while a
    run with many untested never got a real answer."""
    counts = {"passed": 0, "failed": 0, "untested": 0}
    for profile in profiles:
        delay = profile.get("Delay")
        if isinstance(delay, int) and delay > 0:
            counts["passed"] += 1
        elif delay == -1:
            counts["failed"] += 1
        else:
            counts["untested"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webapi-dir", required=True, help="extracted P40 WebAPI directory")
    parser.add_argument("--per-run-cap", type=int, default=DEFAULT_PER_RUN_CAP)
    parser.add_argument("--speedtest-timeout", type=int, default=5400)
    parser.add_argument("--speedtest-page-size", type=int,
                        default=DEFAULT_SPEEDTEST_PAGE_SIZE,
                        help="profiles per Realping batch (written into each "
                             "server's guiNConfig.json)")
    parser.add_argument("--speedtest-workers", type=int,
                        default=DEFAULT_SPEEDTEST_WORKERS,
                        help="WebAPI servers to run side by side (filling system)")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    feed_uris: dict[str, list[str]] = {}
    uri_to_fp: dict[str, str] = {}
    endpoints: dict[str, tuple[str, str, int]] = {}
    for group in TIER_GROUPS:
        uris = read_feed_uris(SUBS_DIR / f"{group}.txt")
        if not uris:
            # A feed may not exist yet (active.txt only appears once the
            # aggregation run has published it); test the rest.
            print(f"tier1: feed {group}.txt is missing or empty; skipping", file=sys.stderr)
            continue
        feed_uris[group] = uris
        for uri in uris:
            if uri in uri_to_fp:
                continue
            node = parse_proxy_uri(uri, SOURCE)
            if node is None:
                continue
            uri_to_fp[uri] = node.semantic_fingerprint()
            endpoints[node.semantic_fingerprint()] = (
                node.scheme, node.address, node.port)
    if not feed_uris:
        print("tier1: no feeds to test; aborting", file=sys.stderr)
        return 1
    known = [{"uri": uri, "fingerprint": fp} for uri, fp in uri_to_fp.items()]
    state = load_state(STATE_PATH)
    blocked = load_blocked(BLOCKED_PATH)
    due = select_due(known, state, now, args.per_run_cap)
    if not due:
        print("tier1: nothing due this run")
        return 0
    print(f"tier1: {len(known)} known configs, {len(due)} due (cap {args.per_run_cap})")

    exe = find_webapi_exe(Path(args.webapi_dir))
    api_key = generate_api_key()
    workers = prepare_workers(Path(args.webapi_dir), exe, args.speedtest_workers,
                              args.speedtest_page_size, api_key)
    try:
        due_uris = [entry["uri"] for entry in due]
        chunks = chunk_list(due_uris, args.speedtest_page_size)
        print(f"tier1: {len(due_uris)} due in {len(chunks)} chunks of up to "
              f"{args.speedtest_page_size} across {len(workers)} workers")
        profiles = run_speedtests(workers, chunks, args.speedtest_timeout)
    finally:
        for worker in workers:
            stop_webapi(worker.process)

    delays: dict[tuple[str, str, int], int] = {}
    for profile in profiles:
        delay = profile.get("Delay")
        if isinstance(delay, int) and delay != 0:
            key = (str(profile.get("Protocol", "")).lower(),
                   str(profile.get("Address", "")).lower(),
                   profile.get("Port"))
            delays[key] = delay
    results: dict[str, int | None] = {}
    for entry in due:
        fingerprint = entry["fingerprint"]
        endpoint = endpoints.get(fingerprint)
        delay = delays.get(endpoint) if endpoint else None
        results[fingerprint] = delay if (isinstance(delay, int) and delay > 0) else None
    state = update_state(state, results, now)
    save_state(STATE_PATH, state)

    passing = passing_fingerprints(state)
    variants = build_variants(feed_uris, uri_to_fp, state, blocked)
    tested = {entry["fingerprint"] for entry in due}
    passed_this_run = {fp for fp, delay in results.items() if delay is not None}
    report = build_results(feed_uris, uri_to_fp, tested, passing, blocked, now)
    RESULTS_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    for group, uris in variants.items():
        title = f"Tier 1 — {group.title()} (PattN Realping)"
        plain, encoded = render_feed(uris, title, timestamp, TIER1_INTERVAL_HOURS)
        (SUBS_DIR / f"tier1_{group}.txt").write_text(plain, encoding="utf-8")
        (SUBS_DIR / f"tier1_{group}_base64.txt").write_text(encoded, encoding="utf-8")
    passed_count = len(passed_this_run)
    delay_counts = count_delays(profiles)
    if delay_counts["untested"]:
        # The speedtest service logs its failures server-side only; print
        # the recent lines unfiltered (the filter semantics are
        # unverified) so the next run's log shows why nothing was tested.
        # Ask the worker with the most untested profiles — its log is
        # the one most likely to explain the stall.
        log_client = None
        most_untested = -1
        for worker in workers:
            if not worker.alive:
                continue
            try:
                remaining = worker.client.profiles()
            except PattnError:
                continue
            untested_here = sum(1 for p in remaining if p.get("Delay", 0) == 0)
            if untested_here > most_untested:
                most_untested = untested_here
                log_client = worker.client
        fetched = log_client.logs(limit=50) if log_client is not None else []
        print(f"tier1: webapi log entries fetched: {len(fetched)}")
        for entry in fetched:
            message = entry.get("Message")
            if isinstance(message, str) and message:
                print(f"tier1: webapi log [{entry.get('Source', '?')}]: {message[:300]}")
    print(f"tier1: tested {len(tested)}, passed {passed_count}, "
          f"failed {len(tested) - passed_count}, "
          f"untested {delay_counts['untested']}, "
          f"blocked-in-variants {report['totals']['github_blocked']}, "
          f"variant sizes: " + ", ".join(f"{g}={len(v)}" for g, v in variants.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
