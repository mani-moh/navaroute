"""Tier-1 PattN driver: Realping-test due configs on a GitHub Windows runner.

Reads the published feeds (subs/<group>.txt), picks the configs due
under the failure backoff, imports them into a fresh headless PattN
WebAPI (pinned P40 release), runs Realping, and writes:
  data/tier1_state.json      (per-fingerprint streak / last test / delay)
  data/tier1_results.json    (per-feed tested / passed / github_blocked)
  subs/tier1_<group>.txt and subs/tier1_<group>_base64.txt
    (the feed's configs that passed tier 1, plus the GitHub-blocked
    set, sorted by delay)

Usage (the workflow downloads and extracts the P40 zip first):
  python scripts/tier1_pattn.py --webapi-dir <extracted zip dir>
      [--per-run-cap 8000] [--speedtest-timeout 5400]

Exit 0 even when some configs fail (failures are data, recorded in the
state); non-zero only when the run itself cannot proceed (no feeds,
WebAPI will not start, no profiles imported).
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
from src.pattn_client import (PINNED_WEBAPI_VERSION, PattnClient, PattnError,  # noqa: E402
                              generate_api_key, start_webapi, stop_webapi)
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


def wait_for_delays(client: PattnClient, expected: int, timeout: int,
                    poll: int = 15) -> list[dict]:
    """Poll /api/profiles until every imported profile has a Delay (!= 0),
    the test stops making progress, or the timeout expires. Delay -1 is a
    failed test, > 0 is a pass; 0 means not yet tested."""
    deadline = time.monotonic() + timeout
    last_untested = None
    still = 0
    while time.monotonic() < deadline:
        profiles = client.profiles()
        untested = sum(1 for p in profiles if p.get("Delay", 0) == 0)
        if len(profiles) >= expected and untested == 0:
            return profiles
        if untested == last_untested:
            still += 1
            if still >= 3 and not client.speedtest_running():
                print(f"tier1: speed test not running; operations now: {client.operations()}")
                return profiles
        else:
            still = 0
        last_untested = untested
        time.sleep(poll)
    return client.profiles()


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
    process = start_webapi(exe, api_key)
    try:
        client = PattnClient(api_key=api_key)
        client.login()
        existing = client.profiles()
        if existing:
            client.delete_profiles(
                [p["IndexId"] for p in existing if p.get("IndexId")])
        due_uris = [entry["uri"] for entry in due]
        sent = client.import_profiles(due_uris)
        print(f"tier1: imported {sent} profiles (WebAPI {PINNED_WEBAPI_VERSION})")
        imported = client.profiles()
        testable = sum(1 for p in imported if p.get("CanTest") is not False)
        core_types: dict[str, int] = {}
        for p in imported:
            core = str(p.get("CoreType") or "?")
            core_types[core] = core_types.get(core, 0) + 1
        print(f"tier1: imported profiles testable {testable}/{len(imported)}, core types {core_types}")
        client.start_speedtest()
        print(f"tier1: operations right after start: {client.operations()}")
        profiles = wait_for_delays(client, sent, args.speedtest_timeout)
    finally:
        stop_webapi(process)

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
        fetched = client.logs(limit=50)
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
