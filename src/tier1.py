"""Tier-1 / tier-2 (PattN) testing: selection, state, variants, results.

Tier 1 runs the headless PattN WebAPI on a GitHub Windows runner every
2 hours and Realping-tests configs (full end-to-end delay, not the
socket probe the 6-hourly aggregation does). Tier 2 is the owner-PC
app (tools/tier2/) that re-tests from Iran and uploads the results.

Failure backoff (tier 1): a config is due again after an interval that
grows with its consecutive-failure streak, so healthy configs are
re-tested often and dead ones fade out of the rotation instead of
burning the whole budget every run:
  streak 0 -> due every run, 1 -> 4h, 2 -> 8h, 3 -> 16h, 4 -> 32h, 5+ -> 48h.

GitHub-blocked special treatment: data/github_blocked.json (written by
tier 2) lists fingerprints that consistently fail on GitHub (streak >= 2)
but pass from Iran. Those configs are force-included in every tier-1
variant even when GitHub keeps failing them, and they never accumulate
a GitHub failure streak in the output sense -- they are counted
separately (github_blocked) so the dashboard can tell them apart.

Per-feed variants: for each non-deprecated group (all, reachable,
active, top100, lite500 -- verified is a deprecated mirror), a tier-1
variant and an Iran (tier-2) variant, each in plain and Base64:
  subs/tier1_<group>.txt, subs/tier1_<group>_base64.txt,
  subs/iran_<group>.txt,  subs/iran_<group>_base64.txt.
tier1_all / iran_all are the global tier feeds. Variants are the feed's
configs intersected with the tier's passing set (plus the blocked set
for tier 1), sorted by delay.

State files are fingerprints only (SHA-256 of the canonical connection,
the same values that key the published feeds and the health history),
never credentials, so they are safe to commit through the privacy gate.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.project import PROJECT_NAME
from src.storage import atomic_write_text

TIER_GROUPS = ("all", "reachable", "active", "top100", "lite500")
TIER1_INTERVAL_HOURS = 2
IRAN_INTERVAL_HOURS = 24
STATE_VERSION = 1
BLOCKED_VERSION = 1
STATE_MAX_BYTES = 8 * 1024 * 1024
BLOCKED_MIN_STREAK = 2  # "consistently failing on GitHub" means streak >= 2
# Consecutive-failure streak -> hours before the config is due again.
BACKOFF_HOURS = {0: 0, 1: 4, 2: 8, 3: 16, 4: 32}
BACKOFF_CAP_HOURS = 48
DEFAULT_PER_RUN_CAP = 8000


def backoff_hours(streak: int) -> int:
    if streak <= 0:
        return 0
    return BACKOFF_HOURS.get(streak, BACKOFF_CAP_HOURS)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must be UTC")
    return parsed


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


# -- state (data/tier1_state.json) ------------------------------------
def load_state(path: Path) -> dict[str, dict]:
    """Fingerprint -> {"failures": int, "last_test": ISO, "last_delay": int|None}."""
    if not path.exists():
        return {}
    if path.stat().st_size > STATE_MAX_BYTES:
        raise ValueError("Tier-1 state exceeds size limit")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid tier-1 state") from exc
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION or not isinstance(data.get("entries"), dict):
        raise ValueError("Invalid tier-1 state")
    entries: dict[str, dict] = {}
    for fingerprint, entry in data["entries"].items():
        if not isinstance(fingerprint, str) or not isinstance(entry, dict):
            raise ValueError("Invalid tier-1 state")
        failures = entry.get("failures")
        last_test = entry.get("last_test")
        last_delay = entry.get("last_delay")
        if type(failures) is not int or failures < 0 or not isinstance(last_test, str):
            raise ValueError("Invalid tier-1 state")
        if last_delay is not None and (type(last_delay) is not int or last_delay < 0):
            raise ValueError("Invalid tier-1 state")
        _parse_time(last_test)
        entries[fingerprint] = {"failures": failures, "last_test": last_test, "last_delay": last_delay}
    return entries


def save_state(path: Path, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": STATE_VERSION,
               "entries": {fp: {"failures": e["failures"], "last_test": e["last_test"],
                                "last_delay": e["last_delay"]}
                           for fp, e in sorted(entries.items())}}
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def update_state(state: dict[str, dict], results: dict[str, int | None],
                 now: datetime) -> dict[str, dict]:
    """Fold one run's results (fingerprint -> delay ms, None = failed) into state."""
    timestamp = _iso(now)
    for fingerprint, delay in results.items():
        if delay is None:
            entry = state.get(fingerprint)
            failures = (entry["failures"] + 1) if entry else 1
            state[fingerprint] = {"failures": failures, "last_test": timestamp,
                                  "last_delay": None}
        else:
            state[fingerprint] = {"failures": 0, "last_test": timestamp, "last_delay": delay}
    return state


def passing_fingerprints(state: dict[str, dict]) -> set[str]:
    """Fingerprints whose last tier-1 test passed (streak 0 with a delay)."""
    return {fp for fp, e in state.items()
            if e["failures"] == 0 and e["last_delay"] is not None}


def select_due(known: list[dict[str, str]], state: dict[str, dict],
               now: datetime, limit: int) -> list[dict[str, str]]:
    """Pick the configs to test this run, in priority order, capped at limit.

    Never-tested first, then the lowest failure streak, then the oldest
    last test. A config whose backoff interval has not elapsed is skipped.
    """
    due: list[tuple[int, float, dict[str, str]]] = []
    for entry in known:
        record = state.get(entry["fingerprint"])
        if record is None:
            due.append((0, 0.0, entry))
            continue
        interval = timedelta(hours=backoff_hours(record["failures"]))
        if now - _parse_time(record["last_test"]) >= interval:
            due.append((record["failures"],
                        _parse_time(record["last_test"]).timestamp(), entry))
    due.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in due[:limit]]


# -- GitHub-blocked list (data/github_blocked.json, written by tier 2) --
def load_blocked(path: Path) -> dict[str, dict]:
    """Fingerprint -> {"iran_delay": int, "added": ISO, "last_iran_pass": ISO}."""
    if not path.exists():
        return {}
    if path.stat().st_size > STATE_MAX_BYTES:
        raise ValueError("GitHub-blocked list exceeds size limit")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid GitHub-blocked list") from exc
    if not isinstance(data, dict) or data.get("version") != BLOCKED_VERSION or not isinstance(data.get("entries"), dict):
        raise ValueError("Invalid GitHub-blocked list")
    entries: dict[str, dict] = {}
    for fingerprint, entry in data["entries"].items():
        if not isinstance(fingerprint, str) or not isinstance(entry, dict):
            raise ValueError("Invalid GitHub-blocked list")
        iran_delay = entry.get("iran_delay")
        added = entry.get("added")
        last_pass = entry.get("last_iran_pass")
        if type(iran_delay) is not int or iran_delay < 0 or not isinstance(added, str) or not isinstance(last_pass, str):
            raise ValueError("Invalid GitHub-blocked list")
        _parse_time(added)
        _parse_time(last_pass)
        entries[fingerprint] = {"iran_delay": iran_delay, "added": added, "last_iran_pass": last_pass}
    return entries


def save_blocked(path: Path, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": BLOCKED_VERSION,
               "entries": {fp: {"iran_delay": e["iran_delay"], "added": e["added"],
                                "last_iran_pass": e["last_iran_pass"]}
                           for fp, e in sorted(entries.items())}}
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))


# -- variants and results ----------------------------------------------
def build_variants(feed_uris: dict[str, list[str]], uri_to_fp: dict[str, str],
                   state: dict[str, dict], blocked: dict[str, dict]) -> dict[str, list[str]]:
    """Per-group tier-1 variants: the feed's configs that passed tier 1,
    plus the GitHub-blocked set, sorted by delay (lowest first)."""
    passing = passing_fingerprints(state)
    variants: dict[str, list[str]] = {}
    for group, uris in feed_uris.items():
        scored: list[tuple[int, str]] = []
        for uri in uris:
            fingerprint = uri_to_fp.get(uri)
            if fingerprint is None:
                continue
            if fingerprint in passing:
                # passing_fingerprints guarantees a delay exists.
                scored.append((state[fingerprint]["last_delay"], uri))
            elif fingerprint in blocked:
                scored.append((blocked[fingerprint]["iran_delay"], uri))
        scored.sort(key=lambda item: (item[0], item[1]))
        variants[group] = [uri for _, uri in scored]
    return variants


def build_results(feed_uris: dict[str, list[str]], uri_to_fp: dict[str, str],
                  tested: set[str], passed: set[str], blocked: dict[str, dict],
                  now: datetime) -> dict:
    """Per-feed {tested, passed, github_blocked}, plus totals.

    tested is what this run tested in the feed; passed is what is
    currently passing tier 1 in the feed (the state after this run);
    github_blocked counts blocked configs in the feed that are in the
    variants only because they are blocked (they did not pass tier 1).
    """
    feeds: dict[str, dict[str, int]] = {}
    total_tested = total_passed = total_blocked = 0
    for group, uris in feed_uris.items():
        fps = {uri_to_fp[uri] for uri in uris if uri in uri_to_fp}
        group_tested = len(fps & tested)
        group_passed = len(fps & passed)
        group_blocked = len({fp for fp in fps if fp in blocked and fp not in passed})
        feeds[group] = {"tested": group_tested, "passed": group_passed,
                        "github_blocked": group_blocked}
        total_tested += group_tested
        total_passed += group_passed
        total_blocked += group_blocked
    return {"version": 1, "generated": _iso(now),
            "feeds": feeds,
            "totals": {"tested": total_tested, "passed": total_passed,
                       "github_blocked": total_blocked}}


def render_feed(uris: list[str], title: str, timestamp: str, interval_hours: int) -> tuple[str, str]:
    """(plain, base64) feed text in the publisher's format: a header over
    the newline-joined URIs, and the Base64 of the body alone."""
    header = (f"#profile-title: base64:{base64.b64encode(f'{PROJECT_NAME} {title}'.encode()).decode()}\n"
              f"#profile-update-interval: {interval_hours}\n# snapshot-time: {timestamp}\n\n")
    body = "\n".join(uris)
    plain = header + body + ("\n" if uris else "")
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return plain, encoded
