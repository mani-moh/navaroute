"""Tier-2 owner-PC app: Realping-test configs from Iran and upload the results.

A single Windows .exe (built by .github/workflows/build-tier2.yml with
PyInstaller). It opens a small dashboard in the browser and drives the
headless PattN WebAPI on this PC -- the same engine behind PattN.exe --
so the test runs from Ryan's own connection in Iran, not a datacenter.

Modes:
  * "Test tier-1 passers" (default): downloads subs/tested.txt and
    tests the configs that passed tier 1 on GitHub.
  * "Test all configs": downloads subs/all.txt and tests everything,
    chunked and resumable (a full sweep is ~64k+ configs). This is the
    option that finds configs GitHub blocks but Iran reaches.
  * "Only last run's failures": retests just the configs that failed
    the last run (saved progress). "Only untested": tests just the
    configs never tested yet.

Test modes (what a "pass" means):
  * Realping (default, the authority): an end-to-end HTTPS GET through
    the proxy. A Realping pass proves the proxy carries web traffic.
  * UDP: the WebAPI's Udptest action (UDP over SOCKS5, NTP-based).
    A UDP pass proves UDP forwarding works and tracks speed, but does
    NOT prove the proxy can do HTTPS/web traffic — only Realping does.
  * UDP-then-Realping: UDP first, then Realping confirms the UDP
    passers, so the published set stays Realping-proven while the
    slow Realping step only runs on configs that already forward UDP.
  The P40 WebAPI accepts Action "Udptest" (verified live against the
  P40 Linux build on 2026-11-16: HTTP 202, speedtest.started); if a
  build ever refuses it, the app falls back to Realping and says so.
  The run's mode is recorded in data/iran_results.json ("mode").

Options (all in the dashboard, before starting):
  * parallelism: how many configs the server tests at once
    (SpeedTestPageSize, default 100; written into the WebAPI's
    guiNConfig.json before the server starts).
  * max-delay cutoff (ms, 0 = none): a config slower than this counts
    as a failure even if the proxy answers.
  * Stop: a run can be stopped cleanly at any time (Stop button);
    progress is saved per chunk and the run resumes where it left off.
  * ETA: live progress (tested/total, elapsed, configs/min, time
    remaining) plus a pre-start estimate from the last run's rate,
    persisted under %LOCALAPPDATA%\\NavaRouteTier2\\last-rate.json.

Upload ("Upload to repo") commits to a branch + PR, using a fine-grained
PAT (repo mani-moh/navaroute, contents + pull requests) entered once in
the UI and stored in Windows Credential Manager -- never in the repo,
never in a file. By default it uploads every group; the UI lets the
owner pick which groups go up instead of all-or-nothing. It uploads:
  subs/tested-iran.txt and subs/tested-iran_base64.txt
  subs/<group>-tested-iran.txt and subs/<group>-tested-iran_base64.txt
    (per-feed Iran-verified variants, sorted by Iran delay)
  subs/protocols/<proto>-tested-iran.txt, subs/countries/<cc>-tested-iran.txt,
  subs/categories/<name>-tested-iran.txt (and _base64 twins)
    (the same intersection for every slice feed)
  data/iran_results.json   (per-feed {tested, passed})
  data/github_blocked.json (configs that consistently fail tier 1 on
    GitHub -- streak >= 2 -- but pass from Iran; tier 1 force-includes
    these in its variants even when GitHub keeps failing them)

The WebAPI (pinned P40 release) is downloaded once and cached under
%LOCALAPPDATA%\\NavaRouteTier2; the cores are reused from the owner's
PattN folder (D:\\PattN-windows-64-desktop\\PattN-windows-64) when it
exists, so nothing is re-downloaded. The Iran regional preset is applied
before testing. Stdlib only, so the .exe bundles with no extra deps.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.pattn_client import (PINNED_WEBAPI_URL, PattnClient, PattnError,  # noqa: E402
                              generate_api_key, start_webapi, stop_webapi,
                              write_webapi_speedtest_config)
from src.tier1 import (BLOCKED_MIN_STREAK, TIER_GROUPS,  # noqa: E402
                       IRAN_INTERVAL_HOURS, load_blocked, load_state,
                       render_feed, variant_name)

OWNER = "mani-moh"
REPO = "navaroute"
RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/master"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
CRED_TARGET = "NavaRouteTier2:github-pat"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NavaRouteTier2"
PATTN_FOLDER = Path(r"D:\PattN-windows-64-desktop\PattN-windows-64")
CHUNK = 2000  # configs per import/test chunk; progress is saved per chunk
# Run options (see the module docstring). "source" picks the feed,
# "scope" picks which of its configs are (re)tested, "test_mode" picks
# what a pass means, "page_size" is how many configs the server tests
# at once, "max_delay" (ms, 0 = none) is the delay cutoff.
SOURCES = ("tier1", "all")
SCOPES = ("remaining", "failed", "untested", "all")
TEST_MODES = ("realping", "udp", "udp-realping")
TEST_MODE_LABELS = {"realping": "Realping", "udp": "UDP",
                    "udp-realping": "UDP then Realping"}
UDP_ACTION = "Udptest"  # accepted by the P40 WebAPI (verified 2026-11-16)
DEFAULT_PAGE_SIZE = 100
DEFAULT_OPTIONS = {"source": "tier1", "scope": "remaining",
                   "test_mode": "realping", "page_size": DEFAULT_PAGE_SIZE,
                   "max_delay": 0}


# -- Windows Credential Manager (ctypes, stdlib only) ------------------
class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong),
                ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
                ("LastWritten", ctypes.c_ulonglong), ("CredentialBlobSize", ctypes.c_ulong),
                ("CredentialBlob", ctypes.c_void_p), ("Persist", ctypes.c_ulong),
                ("AttributeCount", ctypes.c_ulong), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


def cred_save(target: str, secret: str) -> bool:
    if os.name != "nt":
        return False
    advapi32 = ctypes.windll.advapi32
    blob = secret.encode("utf-16-le")
    cred = _Credential()
    cred.Flags = 0
    cred.Type = 1  # CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob), ctypes.c_void_p)
    cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    return bool(advapi32.CredWriteW(ctypes.byref(cred), 0))


def cred_load(target: str) -> str | None:
    if os.name != "nt":
        return None
    advapi32 = ctypes.windll.advapi32
    out = ctypes.POINTER(_Credential)()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(out)):
        return None
    try:
        cred = out.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return blob.decode("utf-16-le")
    finally:
        advapi32.CredFree(ctypes.byref(out))


# -- GitHub API (urllib) ------------------------------------------------
def _gh(method: str, path: str, token: str, body: object = None) -> object:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PattnError(f"GitHub {method} {path} -> HTTP {exc.code}: "
                         f"{exc.read().decode('utf-8', 'replace')[:300]}") from None


def _raw(path: str) -> str:
    with urllib.request.urlopen(RAW + path, timeout=60) as response:
        return response.read().decode("utf-8")


def _feed_uris(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()
            if "://" in line and not line.startswith("#")]


# -- pure helpers (unit-tested in tests/test_tier2.py) ------------------
def select_todo(all_fps: list[str], results: dict[str, int | None],
                scope: str) -> list[str]:
    """Fingerprints to test for a scope, given saved results (delay/None).

    "remaining" resumes everything not currently passing (untested +
    failed); "failed" retests only last run's failures; "untested"
    tests only configs never tested; "all" retests everything.
    """
    if scope == "remaining":
        return [fp for fp in all_fps if results.get(fp) is None]
    if scope == "failed":
        return [fp for fp in all_fps if fp in results and results[fp] is None]
    if scope == "untested":
        return [fp for fp in all_fps if fp not in results]
    if scope == "all":
        return list(all_fps)
    raise PattnError(f"Unknown scope: {scope!r}")


def passes_delay(delay: int | None, max_delay: int) -> bool:
    """True when a measured delay counts as a pass under the cutoff.

    A delay must be a positive int; with a cutoff set (max_delay > 0)
    anything slower counts as a failure even if the proxy answered.
    """
    return (isinstance(delay, int) and delay > 0
            and (max_delay <= 0 or delay <= max_delay))


def configs_per_min(tested: int, elapsed_s: float) -> float:
    """Observed test rate; 0 when nothing has been tested yet."""
    if tested <= 0 or elapsed_s <= 0:
        return 0.0
    return tested * 60.0 / elapsed_s


def estimate_seconds(total: int, rate_per_min: float) -> float | None:
    """Seconds ``total`` configs take at ``rate_per_min``; None if unknown."""
    if total <= 0 or rate_per_min <= 0:
        return None
    return total * 60.0 / rate_per_min


def format_duration(seconds: float | None) -> str:
    """'1h 5m' / '12m 30s' / '45s' / '—' when there is no estimate."""
    if seconds is None:
        return "—"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def validate_options(options: dict) -> dict:
    """Merge user options over the defaults, rejecting bad values."""
    merged = {**DEFAULT_OPTIONS, **(options or {})}
    if merged["source"] not in SOURCES:
        raise PattnError(f"Unknown source: {merged['source']!r}")
    if merged["scope"] not in SCOPES:
        raise PattnError(f"Unknown scope: {merged['scope']!r}")
    if merged["test_mode"] not in TEST_MODES:
        raise PattnError(f"Unknown test mode: {merged['test_mode']!r}")
    try:
        merged["page_size"] = int(merged["page_size"])
        merged["max_delay"] = int(merged["max_delay"])
    except (TypeError, ValueError):
        raise PattnError("page_size and max_delay must be numbers") from None
    if merged["page_size"] < 1:
        raise PattnError(f"page_size must be >= 1, got {merged['page_size']}")
    if merged["max_delay"] < 0:
        raise PattnError(f"max_delay must be >= 0, got {merged['max_delay']}")
    return merged


# -- the app ------------------------------------------------------------
class Tier2App:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: dict = {"status": "idle", "mode": None, "tested": 0,
                            "passed": 0, "failed": 0, "total": 0,
                            "message": "Ready.", "log": [],
                            "test_mode": "realping", "source": "tier1",
                            "scope": "remaining", "page_size": DEFAULT_PAGE_SIZE,
                            "max_delay": 0, "elapsed_s": 0, "rate_per_min": 0.0,
                            "eta_s": None, "estimated_s": None,
                            "last_rate_per_min": None, "udp_available": None,
                            "run_mode": None}
        self.results: dict[str, int | None] = {}  # fingerprint -> Iran delay / None
        self.uri_by_fp: dict[str, str] = {}
        self.thread: threading.Thread | None = None
        self.options: dict = dict(DEFAULT_OPTIONS)
        self.run_mode: str | None = None  # effective mode of the last run
        self.udp_available: bool | None = None  # None = untested on this PC
        self._stop_requested = threading.Event()
        self._client: PattnClient | None = None
        self._run_started = 0.0  # monotonic() at the current run's start
        self._run_tested_base = 0  # tested count when the current run started
        self._run_done = 0  # configs tested so far in the current run

    # -- progress ------------------------------------------------------
    def _log(self, message: str) -> None:
        with self.lock:
            self.state["log"].append(message)
            self.state["log"] = self.state["log"][-200:]
            self.state["message"] = message

    def _progress_path(self, mode: str) -> Path:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return CACHE_DIR / f"progress-{mode}.json"

    def _save_progress(self, mode: str) -> None:
        self._progress_path(mode).write_text(
            json.dumps({"results": self.results,
                        "mode": self.run_mode or "realping"},
                       ensure_ascii=False), encoding="utf-8")

    def _load_progress(self, mode: str) -> tuple[dict[str, int | None], str | None]:
        """Saved (results, test mode) for a source; ({}, None) when none."""
        path = self._progress_path(mode)
        if not path.exists():
            return {}, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results = {k: v for k, v in data.get("results", {}).items()
                       if isinstance(k, str) and (v is None or isinstance(v, int))}
            saved_mode = data.get("mode")
            return results, saved_mode if saved_mode in TEST_MODES else None
        except (OSError, UnicodeError, ValueError):
            return {}, None

    # -- last-run rate (pre-start ETA) ---------------------------------
    def _rate_path(self) -> Path:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return CACHE_DIR / "last-rate.json"

    def _save_rate(self, tested: int, elapsed_s: float) -> None:
        if tested <= 0 or elapsed_s <= 0:
            return
        self._rate_path().write_text(json.dumps(
            {"configs_per_min": configs_per_min(tested, elapsed_s),
             "tested": tested, "elapsed_s": round(elapsed_s, 1),
             "at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False), encoding="utf-8")

    def _load_last_rate(self) -> dict | None:
        path = CACHE_DIR / "last-rate.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rate = float(data.get("configs_per_min", 0))
            return data if rate > 0 else None
        except (OSError, UnicodeError, ValueError):
            return None

    # -- WebAPI --------------------------------------------------------
    def _webapi_exe(self) -> Path:
        for candidate in (PATTN_FOLDER / "v2rayN.WebAPI.exe",
                          CACHE_DIR / "webapi" / "v2rayN.WebAPI.exe"):
            if candidate.exists():
                return candidate
        self._log("Downloading the PattN WebAPI (P40, once)...")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = CACHE_DIR / "webapi.zip"
        urllib.request.urlretrieve(PINNED_WEBAPI_URL, zip_path)
        extract = CACHE_DIR / "webapi"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract)
        for found in sorted(extract.rglob("v2rayN.WebAPI.exe")):
            return found
        raise PattnError("WebAPI exe not found in the P40 zip")

    # -- the test run --------------------------------------------------
    def start(self, options: dict | str | None = None) -> bool:
        """Start a run; ``options`` is a dict (or a legacy "tier1"/"all"
        source string). Returns False when a run is already going."""
        if isinstance(options, str):
            options = {"source": options}
        options = dict(options or {})
        if "mode" in options and "source" not in options:
            options["source"] = options.pop("mode")  # legacy {"mode": ...} body
        merged = validate_options(options or {})
        with self.lock:
            if self.state["status"] == "running":
                return False
            self.options = merged
            self.state.update({"status": "running", "mode": merged["source"],
                               "tested": 0, "passed": 0, "failed": 0, "total": 0,
                               "test_mode": merged["test_mode"],
                               "source": merged["source"], "scope": merged["scope"],
                               "page_size": merged["page_size"],
                               "max_delay": merged["max_delay"],
                               "elapsed_s": 0, "rate_per_min": 0.0,
                               "eta_s": None, "estimated_s": None})
        self._stop_requested.clear()
        self.thread = threading.Thread(target=self._run, args=(merged,), daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        """Ask a running test to stop cleanly; progress stays saved."""
        self._stop_requested.set()
        client = self._client
        if client is not None:
            client.stop_speedtest()
        with self.lock:
            if self.state["status"] == "running":
                self.state["message"] = "Stopping… (progress is saved)"

    def _update_live_progress(self, done_this_run: int) -> None:
        """Refresh tested/rate/ETA; ``done_this_run`` counts configs the
        current run has tested so far (across phases and chunks)."""
        with self.lock:
            total = self.state["total"]
            tested = min(self._run_tested_base + done_this_run, total)
            elapsed = time.monotonic() - self._run_started
            rate = configs_per_min(done_this_run, elapsed)
            self.state.update({"tested": tested, "elapsed_s": round(elapsed),
                               "rate_per_min": round(rate, 1),
                               "eta_s": estimate_seconds(max(total - tested, 0), rate)})

    def _run(self, options: dict) -> None:
        self._run_started = time.monotonic()
        self._run_done = 0
        source = options["source"]
        scope = options["scope"]
        test_mode = options["test_mode"]
        page_size = options["page_size"]
        max_delay = options["max_delay"]
        try:
            self._log("Downloading the tested list...")
            feed_text = _raw("/subs/tested.txt" if source == "tier1" else "/subs/all.txt")
            uris = _feed_uris(feed_text)
            # Fingerprints: the same canonical values the repo uses.
            sys.path.insert(0, str(REPO_ROOT))
            from src.parsers import parse_proxy_uri
            from src.models import SourceConfig
            source_cfg = SourceConfig("tier2", "Tier 2",
                                      "https://github.com/mani-moh/navaroute")
            self.uri_by_fp = {}
            endpoints: dict[str, tuple[str, str, int]] = {}
            for uri in uris:
                node = parse_proxy_uri(uri, source_cfg)
                if node is None:
                    continue
                fingerprint = node.semantic_fingerprint()
                self.uri_by_fp[fingerprint] = uri
                endpoints[fingerprint] = (node.scheme, node.address, node.port)
            self.results, _saved_mode = self._load_progress(source)
            self.run_mode = test_mode
            if scope == "all":
                self.results = {}
            todo = select_todo(list(self.uri_by_fp), self.results, scope)
            self._run_tested_base = len(self.results)
            last_rate = self._load_last_rate()
            estimated = (estimate_seconds(len(todo), last_rate["configs_per_min"])
                         if last_rate else None)
            with self.lock:
                self.state["total"] = len(self.uri_by_fp)
                self.state["tested"] = len(self.results)
                self.state["estimated_s"] = estimated
                self.state["last_rate_per_min"] = (
                    last_rate["configs_per_min"] if last_rate else None)
            self._log(f"{len(todo)} configs to test ({len(self.results)} already done; "
                      f"estimated {format_duration(estimated)}"
                      + (" from the last run's rate." if estimated is not None
                         else " — no previous run to estimate from."))
            if not todo:
                with self.lock:
                    self.state["status"] = "done"
                self._log("Nothing to test for this scope.")
                return
            exe = self._webapi_exe()
            try:
                write_webapi_speedtest_config(exe, page_size)
            except PattnError as exc:
                self._log(f"Could not set parallelism to {page_size} ({exc}); "
                          "continuing with the server's configured page size.")
            api_key = generate_api_key()
            process = start_webapi(exe, api_key)
            try:
                client = PattnClient(api_key=api_key)
                self._client = client
                client.login()
                client.apply_iran_preset()
                self._log(f"Iran preset applied; testing {page_size} at a time "
                          f"(progress is saved per chunk, mode: "
                          f"{TEST_MODE_LABELS[test_mode]})...")
                if test_mode == "udp-realping":
                    udp_results, effective = self._test_chunks(
                        client, todo, UDP_ACTION, max_delay, endpoints, source,
                        commit=False)
                    if effective == "Realping":
                        # Udptest was refused and the phase already fell
                        # back to Realping: nothing left to confirm.
                        self.results.update(udp_results)
                        self._save_progress(source)
                    else:
                        udp_passers = [fp for fp in todo
                                       if udp_results.get(fp) is not None]
                        self._log(f"UDP passed {len(udp_passers)}/{len(todo)}; "
                                  "confirming those with Realping...")
                        # The confirm phase is extra work on top of the
                        # screening pass: count it in the progress total
                        # so the live ETA stays honest through phase 2.
                        with self.lock:
                            self.state["total"] = (
                                len(self.uri_by_fp) + len(udp_passers))
                        real_results, _ = self._test_chunks(
                            client, udp_passers, "Realping", max_delay,
                            endpoints, source)
                        for fingerprint in todo:
                            self.results[fingerprint] = real_results.get(fingerprint)
                        self._save_progress(source)
                else:
                    action = UDP_ACTION if test_mode == "udp" else "Realping"
                    phase_results, effective = self._test_chunks(
                        client, todo, action, max_delay, endpoints, source)
                    self.results.update(phase_results)
                    if effective == "Realping" and test_mode == "udp":
                        self.run_mode = "realping"
                with self.lock:
                    self.state["tested"] = len(self.results)
                    self.state["passed"] = sum(
                        1 for d in self.results.values() if d is not None)
                    self.state["failed"] = sum(
                        1 for d in self.results.values() if d is None)
            finally:
                self._client = None
                stop_webapi(process)
            elapsed = time.monotonic() - self._run_started
            self._save_rate(self._run_done, elapsed)
            if self._stop_requested.is_set():
                with self.lock:
                    self.state["status"] = "stopped"
                self._log("Stopped by the user; progress is saved, resume any time.")
            else:
                with self.lock:
                    # All the work is done (including, for UDP-then-Realping,
                    # the confirm phase counted in the total): show 100%.
                    self.state["tested"] = self.state["total"]
                    self.state["status"] = "done"
                self._log("Done. Review the results, then use 'Upload to repo'.")
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI, not swallowed
            elapsed = time.monotonic() - self._run_started
            self._save_rate(self._run_done, elapsed)
            with self.lock:
                self.state["status"] = "error"
            self._log(f"Error: {exc}")

    def _test_chunks(self, client: PattnClient, todo: list[str], action: str,
                     max_delay: int,
                     endpoints: dict[str, tuple[str, str, int]],
                     source: str, commit: bool = True) -> tuple[dict[str, int | None], str]:
        """Test ``todo`` in chunks with ``action``; returns (results, action
        actually used). A refused Udptest falls back to Realping for the
        whole run (a "speedtest_busy" refusal is transient and re-raised).
        With commit=False (the UDP phase of UDP-then-Realping) results
        are returned but not written into self.results: only the
        Realping confirmation decides what gets published.
        """
        results: dict[str, int | None] = {}
        effective = action
        for start in range(0, len(todo), CHUNK):
            if self._stop_requested.is_set():
                break
            chunk = todo[start:start + CHUNK]
            chunk_uris = [self.uri_by_fp[fp] for fp in chunk]
            existing = client.profiles()
            if existing:
                client.delete_profiles(
                    [p["IndexId"] for p in existing if p.get("IndexId")])
            client.import_profiles(chunk_uris)
            try:
                client.start_speedtest(action=effective)
            except PattnError as exc:
                if effective == UDP_ACTION and "speedtest_busy" not in str(exc):
                    self.udp_available = False
                    with self.lock:
                        self.state["udp_available"] = False
                    self._log("This WebAPI refused Udptest; falling back to "
                              "Realping for this run.")
                    effective = "Realping"
                    self.run_mode = "realping"
                    client.start_speedtest(action=effective)
                else:
                    raise
            if effective == UDP_ACTION:
                self.udp_available = True
                with self.lock:
                    self.state["udp_available"] = True
            profiles = self._wait(client, len(chunk_uris),
                                  on_poll=lambda ps: self._update_live_progress(
                                      self._run_done + sum(
                                          1 for p in ps if p.get("Delay", 0) != 0)))
            if self._stop_requested.is_set():
                break
            delays: dict[tuple[str, str, int], int] = {}
            for profile in profiles:
                delay = profile.get("Delay")
                if isinstance(delay, int) and delay != 0:
                    delays[(str(profile.get("Protocol", "")).lower(),
                            str(profile.get("Address", "")).lower(),
                            profile.get("Port"))] = delay
            for fingerprint in chunk:
                delay = delays.get(endpoints[fingerprint])
                results[fingerprint] = (
                    delay if passes_delay(delay, max_delay) else None)
            self._run_done += len(chunk)
            if commit:
                self.results.update(results)
            with self.lock:
                self.state["tested"] = min(
                    self._run_tested_base + self._run_done, self.state["total"])
                self.state["passed"] = sum(
                    1 for d in self.results.values() if d is not None)
                self.state["failed"] = sum(
                    1 for d in self.results.values() if d is None)
                elapsed = time.monotonic() - self._run_started
                rate = configs_per_min(self._run_done, elapsed)
                self.state.update({"elapsed_s": round(elapsed),
                                   "rate_per_min": round(rate, 1),
                                   "eta_s": estimate_seconds(
                                       max(self.state["total"] - self.state["tested"], 0),
                                       rate)})
            if commit:
                self._save_progress(source)
            self._log(f"Tested {self.state['tested']}/{self.state['total']} "
                      f"({self.state['passed']} passing so far).")
        return results, effective

    def _wait(self, client: PattnClient, expected: int, timeout: int = 3600,
              on_poll: Callable[[list[dict]], None] | None = None,
              stop_event: threading.Event | None = None) -> list[dict]:
        deadline = time.monotonic() + timeout
        last_untested = None
        still = 0
        stop = stop_event if stop_event is not None else self._stop_requested
        while time.monotonic() < deadline:
            if stop.is_set():
                client.stop_speedtest()
                return client.profiles()
            profiles = client.profiles()
            if on_poll is not None:
                on_poll(profiles)
            untested = sum(1 for p in profiles if p.get("Delay", 0) == 0)
            if len(profiles) >= expected and untested == 0:
                return profiles
            if untested == last_untested:
                still += 1
                if still >= 3 and not client.speedtest_running():
                    return profiles
            else:
                still = 0
            last_untested = untested
            time.sleep(15)
        return client.profiles()

    # -- upload --------------------------------------------------------
    def upload(self, token: str, groups: list[str] | None = None) -> str:
        """Upload Iran results as a branch + PR.

        ``groups`` selects which feeds go up (core group names, and/or
        "slices" for every slice variant); None means all of them.
        The run's test mode is recorded in iran_results.json ("mode")
        so a UDP-only run is never mistaken for Realping-verified.
        """
        if not self.results:
            raise PattnError("Nothing to upload yet: run a test first.")
        if not token:
            raise PattnError("No GitHub token saved: enter your PAT first.")
        self._log("Downloading the feed files and tier-1 state...")
        feed_uris: dict[str, list[str]] = {}
        for group in TIER_GROUPS:
            feed_uris[group] = _feed_uris(_raw(f"/subs/{group}.txt"))
        # Slice feeds (protocols/countries/categories) for the per-slice
        # -tested-iran variants. Enumerated from the repo tree so a new
        # country appears without an app update; base64 twins and
        # existing tested variants are outputs, never inputs.
        slice_uris: dict[str, list[str]] = {}
        tree = _gh("GET", "/git/trees/master?recursive=1", token)
        entries = tree.get("tree", []) if isinstance(tree, dict) else []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            if not (path.startswith("subs/protocols/") or path.startswith("subs/countries/")
                    or path.startswith("subs/categories/")):
                continue
            if not path.endswith(".txt") or path.endswith("_base64.txt"):
                continue
            stem = path[:-len(".txt")]
            if stem.endswith("-tested") or stem.endswith("-tested-iran"):
                continue
            slice_uris[path[len("subs/"):-len(".txt")]] = _feed_uris(_raw(f"/{path}"))
        self._log(f"Downloaded {len(feed_uris)} core feeds and {len(slice_uris)} slice feeds.")
        uri_to_fp: dict[str, str] = {}
        sys.path.insert(0, str(REPO_ROOT))
        from src.parsers import parse_proxy_uri
        from src.models import SourceConfig
        source = SourceConfig("tier2", "Tier 2", "https://github.com/mani-moh/navaroute")
        for uris in list(feed_uris.values()) + list(slice_uris.values()):
            for uri in uris:
                if uri in uri_to_fp:
                    continue
                node = parse_proxy_uri(uri, source)
                if node is not None:
                    uri_to_fp[uri] = node.semantic_fingerprint()
        # Tier-1 state (for the blocked list) and the existing blocked file.
        try:
            tier1_state = load_state_from_text(_raw("/data/tier1_state.json"))
        except Exception:  # noqa: BLE001 - no tier-1 run yet: no blocked list
            tier1_state = {}
        try:
            blocked = load_blocked_from_text(_raw("/data/github_blocked.json"))
        except Exception:  # noqa: BLE001 - no blocked file yet
            blocked = {}
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        passing = {fp for fp, delay in self.results.items() if delay is not None}
        fp_to_uri = {fp: uri for uri, fp in uri_to_fp.items()}
        # Per-feed Iran variants, sorted by Iran delay. Core groups land
        # in iran_results.json; slice variants are files only.
        files: dict[str, str] = {}
        results_feeds: dict[str, dict[str, int]] = {}
        total_tested = total_passed = 0
        variant_uris = {**feed_uris, **slice_uris}
        selected = None if not groups else set(groups)
        if selected is not None and "slices" in selected:
            selected = (selected - {"slices"}) | set(slice_uris)
        if selected is not None:
            variant_uris = {feed: uris for feed, uris in variant_uris.items()
                            if feed in selected}
        mode = self.run_mode or "realping"
        for feed, uris in variant_uris.items():
            feed_fps = {uri_to_fp[uri] for uri in uris if uri in uri_to_fp}
            feed_tested = len(feed_fps & set(self.results))
            feed_passing = sorted(
                ((self.results[fp], fp_to_uri[fp])
                 for fp in feed_fps & passing if fp in fp_to_uri),
                key=lambda item: (item[0], item[1]))
            feed_group_uris = [uri for _, uri in feed_passing]
            stem = variant_name(feed, "tested-iran")
            plain, encoded = render_feed(feed_group_uris, f"Iran Verified — {stem}",
                                         timestamp, IRAN_INTERVAL_HOURS)
            files[f"subs/{stem}.txt"] = plain
            files[f"subs/{stem}_base64.txt"] = encoded
            if feed in feed_uris:
                results_feeds[feed] = {"tested": feed_tested, "passed": len(feed_group_uris)}
                total_tested += feed_tested
                total_passed += len(feed_group_uris)
        files["data/iran_results.json"] = json.dumps(
            {"version": 1, "generated": timestamp, "mode": mode,
             "feeds": results_feeds,
             "totals": {"tested": total_tested, "passed": total_passed}},
            ensure_ascii=False, indent=2, sort_keys=True)
        # GitHub-blocked list: keep what was not re-tested; re-derive the rest.
        tested_fps = set(self.results)
        new_blocked: dict[str, dict] = {}
        for fingerprint, entry in blocked.items():
            if fingerprint not in tested_fps:
                new_blocked[fingerprint] = entry
        for fingerprint, delay in self.results.items():
            if delay is None:
                new_blocked.pop(fingerprint, None)
                continue
            record = tier1_state.get(fingerprint)
            streak = record["failures"] if record else 0
            if streak >= BLOCKED_MIN_STREAK:
                old = blocked.get(fingerprint, {})
                new_blocked[fingerprint] = {
                    "iran_delay": delay,
                    "added": old.get("added", timestamp),
                    "last_iran_pass": timestamp}
        files["data/github_blocked.json"] = json.dumps(
            {"version": 1, "entries": {fp: e for fp, e in sorted(new_blocked.items())}},
            ensure_ascii=False, indent=2, sort_keys=True)
        # Branch + commit + PR.
        branch = f"tier2-iran-{now.strftime('%Y%m%d-%H%M')}"
        master = _gh("GET", "/git/ref/heads/master", token)
        _gh("POST", "/git/refs", token,
            {"ref": f"refs/heads/{branch}", "sha": master["object"]["sha"]})
        for path, content in files.items():
            try:
                existing = _gh("GET", f"/contents/{path}?ref={branch}", token)
                sha = existing.get("sha") if isinstance(existing, dict) else None
            except PattnError:
                sha = None
            body: dict[str, object] = {"message": f"Tier 2 Iran results ({branch})",
                                       "content": base64.b64encode(content.encode("utf-8")).decode(),
                                       "branch": branch}
            if sha:
                body["sha"] = sha
            _gh("PUT", f"/contents/{path}", token, body)
        uploaded_slices = sum(1 for feed in variant_uris if feed in slice_uris)
        pr_body = (f"Iran {TEST_MODE_LABELS[mode]} results from the owner PC: "
                   f"{total_passed} passing of {total_tested} tested; "
                   f"{len(new_blocked)} configs in the GitHub-blocked list; "
                   f"{uploaded_slices} slice -tested-iran variants.")
        if mode == "udp":
            pr_body += (" NOTE: UDP mode only — these configs proved UDP "
                        "forwarding, not HTTPS/web traffic (only Realping "
                        "proves that).")
        pr = _gh("POST", "/pulls", token,
                 {"title": f"Tier 2 Iran-verified feeds ({now.strftime('%Y-%m-%d %H:%M')} UTC)",
                  "head": branch, "base": "master",
                  "body": pr_body})
        self._log(f"Uploaded: PR #{pr.get('number')} ({pr.get('html_url')}).")
        return str(pr.get("html_url", ""))


def load_state_from_text(text: str) -> dict[str, dict]:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8") as handle:
        handle.write(text)
        path = Path(handle.name)
    try:
        return load_state(path)
    finally:
        path.unlink(missing_ok=True)


def load_blocked_from_text(text: str) -> dict[str, dict]:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8") as handle:
        handle.write(text)
        path = Path(handle.name)
    try:
        return load_blocked(path)
    finally:
        path.unlink(missing_ok=True)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>NavaRoute Tier 2 — Iran Tester</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2em auto;padding:0 1em}
button{padding:.6em 1em;margin:.3em .3em .3em 0;cursor:pointer}input,select{padding:.5em}
input[type=number]{width:6em}input[type=text],input[type=password]{width:100%}
#log{background:#111;color:#ddd;padding:1em;height:220px;overflow:auto;white-space:pre-wrap;font-size:.85em}
.bar{height:12px;background:#eee}.bar>i{display:block;height:12px;background:#2a7}
.note{font-size:.85em;color:#555}.warn{font-size:.85em;color:#a60}
label{display:block;margin:.4em 0}</style></head>
<body><h1>NavaRoute Tier 2 — Iran Tester</h1>
<p>Tests configs from Iran with the PattN engine and uploads the passing ones as Iran-verified feeds.</p>
<h3>GitHub token</h3><p><input id="pat" type="password" placeholder="Fine-grained PAT (repo mani-moh/navaroute: contents + pull requests)">
<button onclick="saveToken()">Save token (Windows Credential Manager)</button> <span id="tok"></span></p>
<h3>Test</h3>
<label>What to test <select id="source"><option value="tier1">Tier-1 passers (subs/tested.txt)</option><option value="all">All configs (subs/all.txt, full sweep)</option></select></label>
<label>Scope <select id="scope"><option value="remaining">Remaining (untested + last run's failures)</option><option value="failed">Only last run's failures</option><option value="untested">Only untested</option><option value="all">Everything (retest all)</option></select></label>
<label>Test mode <select id="testmode"><option value="realping">Realping (HTTPS — proves web traffic)</option><option value="udp">UDP (fast screen — does NOT prove web traffic)</option><option value="udp-realping">UDP then Realping (screen, then confirm)</option></select></label>
<p class="warn">UDP delay proves UDP forwarding works and tracks speed, but does NOT prove the proxy can do HTTPS/web traffic — only Realping does. UDP-passed configs are labelled as such (mode: udp) in iran_results.json; prefer UDP-then-Realping for anything you publish as Iran-verified.</p>
<p class="warn" id="udpnote" style="display:none">This WebAPI refused Udptest, so UDP modes are unavailable here — Realping will be used instead.</p>
<label>Configs at once (parallelism) <input id="pagesize" type="number" min="1" value="100"></label>
<label>Max delay (ms, 0 = no cutoff) <input id="maxdelay" type="number" min="0" value="0"></label>
<p id="estimate" class="note"></p>
<button onclick="start()">Start test</button>
<button onclick="stopRun()">Stop</button>
<div class="bar"><i id="fill" style="width:0%"></i></div>
<p id="status">Ready.</p>
<p id="eta" class="note"></p>
<h3>Upload</h3>
<p class="note">Pick which groups go up (all checked = everything):</p>
<div id="groups"></div>
<button onclick="upload()">Upload to repo (branch + PR)</button>
<h3>Log</h3><div id="log"></div>
<script>
const GROUPS=[['all','all'],['online','online'],['online-week','online-week'],['fastest-100','fastest-100'],['balanced-500','balanced-500'],['slices','all slice variants']];
document.getElementById('groups').innerHTML=GROUPS.map(([v,l])=>'<label><input type="checkbox" class="grp" value="'+v+'" checked> '+l+'</label>').join('');
async function api(p,m,b){const r=await fetch(p,{method:m||'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});return r.json()}
async function saveToken(){const v=document.getElementById('pat').value;if(!v)return;await api('/api/token','POST',{token:v});document.getElementById('pat').value='';refresh()}
function opts(){return {source:document.getElementById('source').value,scope:document.getElementById('scope').value,test_mode:document.getElementById('testmode').value,page_size:parseInt(document.getElementById('pagesize').value||'100',10),max_delay:parseInt(document.getElementById('maxdelay').value||'0',10)}}
async function start(){await api('/api/start','POST',opts())}
async function stopRun(){await api('/api/stop','POST',{})}
async function upload(){const checked=[...document.querySelectorAll('.grp:checked')].map(e=>e.value);const groups=checked.length===GROUPS.length?null:checked;const r=await api('/api/upload','POST',{groups});alert(r.url||r.error||'done')}
function fmt(s){if(s==null)return '—';s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor(s%3600/60),ss=s%60;if(h)return h+'h '+m+'m';if(m)return m+'m '+ss+'s';return ss+'s'}
async function refresh(){const s=await api('/api/status');document.getElementById('status').textContent=s.status+': '+s.message+' ('+s.tested+'/'+s.total+' tested, '+s.passed+' passing)';document.getElementById('fill').style.width=(s.total?Math.round(100*s.tested/s.total):0)+'%';document.getElementById('log').textContent=s.log.join('\\n');document.getElementById('tok').textContent=s.token_saved?'Token saved.':'';document.getElementById('eta').textContent=s.status==='running'?('Elapsed '+fmt(s.elapsed_s)+' · '+(s.rate_per_min||0)+' configs/min · ETA '+fmt(s.eta_s)):(s.estimated_s!=null?'Estimated duration for this run: '+fmt(s.estimated_s):'');document.getElementById('estimate').textContent=s.last_rate_per_min?('Last run: '+s.last_rate_per_min+' configs/min.'):'No previous run yet — no estimate available.';const udpBad=s.udp_available===false;document.getElementById('udpnote').style.display=udpBad?'block':'none';for(const o of document.getElementById('testmode').options){if(o.value!=='realping')o.disabled=udpBad}}
setInterval(refresh,2000);refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    app: Tier2App

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            with self.app.lock:
                snapshot = dict(self.app.state)
            snapshot["token_saved"] = cred_load(CRED_TARGET) is not None
            self._json(snapshot)
            return
        page = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/start":
            try:
                self.app.start(body if isinstance(body, dict) else {})
                self._json({"ok": True})
            except PattnError as exc:
                self._json({"error": str(exc)}, 400)
        elif self.path == "/api/stop":
            self.app.stop()
            self._json({"ok": True})
        elif self.path == "/api/token":
            token = str(body.get("token", "")).strip()
            if not token:
                self._json({"error": "Empty token"}, 400)
            elif cred_save(CRED_TARGET, token):
                self._json({"ok": True})
            else:
                self._json({"error": "Could not store the token (Windows Credential Manager unavailable)"}, 500)
        elif self.path == "/api/upload":
            token = cred_load(CRED_TARGET)
            try:
                groups = body.get("groups")
                if groups is not None and not isinstance(groups, list):
                    raise PattnError("groups must be a list of feed names")
                url = self.app.upload(token or "", groups)
                self._json({"ok": True, "url": url})
            except Exception as exc:  # noqa: BLE001 - shown in the UI
                self._json({"error": str(exc)}, 500)
        else:
            self._json({"error": "Unknown route"}, 404)

    def log_message(self, *args: object) -> None:  # keep the console quiet
        pass


def main() -> int:
    app = Tier2App()
    Handler.app = app
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("NavaRoute Tier 2 running at http://127.0.0.1:8765 (loopback only)")
    print("Press Ctrl+C to stop. Progress is saved per chunk and resumes.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
