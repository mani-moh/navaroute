"""Tier-2 owner-PC app: Realping-test configs from Iran and upload the results.

A single Windows .exe (built by .github/workflows/build-tier2.yml with
PyInstaller). It opens a small dashboard in the browser and drives the
headless PattN WebAPI on this PC -- the same engine behind PattN.exe --
so the test runs from Ryan's own connection in Iran, not a datacenter.

Modes:
  * "Test tier-1 passers" (default): downloads subs/tier1_all.txt and
    Realping-tests the configs that passed tier 1 on GitHub.
  * "Test all configs": downloads subs/all.txt and tests everything,
    chunked and resumable (a full sweep is ~64k+ configs). This is the
    option that finds configs GitHub blocks but Iran reaches.

Upload ("Upload to repo") commits to a branch + PR, using a fine-grained
PAT (repo mani-moh/navaroute, contents + pull requests) entered once in
the UI and stored in Windows Credential Manager -- never in the repo,
never in a file. It uploads:
  subs/iran_<group>.txt and subs/iran_<group>_base64.txt
    (per-feed Iran-verified variants, sorted by Iran delay)
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
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.pattn_client import (PINNED_WEBAPI_URL, PattnClient, PattnError,  # noqa: E402
                              generate_api_key, start_webapi, stop_webapi)
from src.tier1 import (BLOCKED_MIN_STREAK, TIER_GROUPS,  # noqa: E402
                       IRAN_INTERVAL_HOURS, load_blocked, load_state,
                       render_feed)

OWNER = "mani-moh"
REPO = "navaroute"
RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/master"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
CRED_TARGET = "NavaRouteTier2:github-pat"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NavaRouteTier2"
PATTN_FOLDER = Path(r"D:\PattN-windows-64-desktop\PattN-windows-64")
CHUNK = 2000  # configs per import/test chunk; progress is saved per chunk


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


# -- the app ------------------------------------------------------------
class Tier2App:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: dict = {"status": "idle", "mode": None, "tested": 0,
                            "passed": 0, "failed": 0, "total": 0,
                            "message": "Ready.", "log": []}
        self.results: dict[str, int | None] = {}  # fingerprint -> Iran delay / None
        self.uri_by_fp: dict[str, str] = {}
        self.thread: threading.Thread | None = None

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
            json.dumps({"results": self.results}, ensure_ascii=False), encoding="utf-8")

    def _load_progress(self, mode: str) -> dict[str, int | None]:
        path = self._progress_path(mode)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: v for k, v in data.get("results", {}).items()
                    if isinstance(k, str) and (v is None or isinstance(v, int))}
        except (OSError, UnicodeError, ValueError):
            return {}

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
    def start(self, mode: str) -> None:
        with self.lock:
            if self.state["status"] == "running":
                return
            self.state.update({"status": "running", "mode": mode, "tested": 0,
                               "passed": 0, "failed": 0, "total": 0})
        self.thread = threading.Thread(target=self._run, args=(mode,), daemon=True)
        self.thread.start()

    def _run(self, mode: str) -> None:
        try:
            self._log(f"Downloading the {'tier-1' if mode == 'tier1' else 'full'} list...")
            feed_text = _raw("/subs/tier1_all.txt" if mode == "tier1" else "/subs/all.txt")
            uris = _feed_uris(feed_text)
            # Fingerprints: the same canonical values the repo uses.
            sys.path.insert(0, str(REPO_ROOT))
            from src.parsers import parse_proxy_uri
            from src.models import SourceConfig
            source = SourceConfig("tier2", "Tier 2", "https://github.com/mani-moh/navaroute")
            self.uri_by_fp = {}
            endpoints: dict[str, tuple[str, str, int]] = {}
            for uri in uris:
                node = parse_proxy_uri(uri, source)
                if node is None:
                    continue
                fingerprint = node.semantic_fingerprint()
                self.uri_by_fp[fingerprint] = uri
                endpoints[fingerprint] = (node.scheme, node.address, node.port)
            self.results = self._load_progress(mode)
            todo = [fp for fp in self.uri_by_fp if fp not in self.results]
            with self.lock:
                self.state["total"] = len(self.uri_by_fp)
                self.state["tested"] = len(self.results)
            self._log(f"{len(todo)} configs left to test ({len(self.results)} already done).")
            exe = self._webapi_exe()
            api_key = generate_api_key()
            process = start_webapi(exe, api_key)
            try:
                client = PattnClient(api_key=api_key)
                client.login()
                client.apply_iran_preset()
                self._log("Iran preset applied; testing in chunks of "
                          f"{CHUNK} (progress is saved per chunk)...")
                for start in range(0, len(todo), CHUNK):
                    chunk = todo[start:start + CHUNK]
                    chunk_uris = [self.uri_by_fp[fp] for fp in chunk]
                    existing = client.profiles()
                    if existing:
                        client.delete_profiles(
                            [p["IndexId"] for p in existing if p.get("IndexId")])
                    client.import_profiles(chunk_uris)
                    client.start_speedtest()
                    profiles = self._wait(client, len(chunk_uris))
                    delays: dict[tuple[str, str, int], int] = {}
                    for profile in profiles:
                        delay = profile.get("Delay")
                        if isinstance(delay, int) and delay != 0:
                            delays[(str(profile.get("Protocol", "")).lower(),
                                    str(profile.get("Address", "")).lower(),
                                    profile.get("Port"))] = delay
                    for fingerprint in chunk:
                        delay = delays.get(endpoints[fingerprint])
                        self.results[fingerprint] = delay if (isinstance(delay, int) and delay > 0) else None
                    with self.lock:
                        self.state["tested"] = len(self.results)
                        self.state["passed"] = sum(1 for d in self.results.values() if d is not None)
                        self.state["failed"] = sum(1 for d in self.results.values() if d is None)
                    self._save_progress(mode)
                    self._log(f"Tested {len(self.results)}/{len(self.uri_by_fp)} "
                              f"({self.state['passed']} passing so far).")
            finally:
                stop_webapi(process)
            with self.lock:
                self.state["status"] = "done"
            self._log("Done. Review the results, then use 'Upload to repo'.")
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI, not swallowed
            with self.lock:
                self.state["status"] = "error"
            self._log(f"Error: {exc}")

    def _wait(self, client: PattnClient, expected: int, timeout: int = 3600) -> list[dict]:
        import time as _time
        deadline = _time.monotonic() + timeout
        last_untested = None
        still = 0
        while _time.monotonic() < deadline:
            profiles = client.profiles()
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
            _time.sleep(15)
        return client.profiles()

    # -- upload --------------------------------------------------------
    def upload(self, token: str) -> str:
        if not self.results:
            raise PattnError("Nothing to upload yet: run a test first.")
        if not token:
            raise PattnError("No GitHub token saved: enter your PAT first.")
        self._log("Downloading the feed files and tier-1 state...")
        feed_uris: dict[str, list[str]] = {}
        for group in TIER_GROUPS:
            feed_uris[group] = _feed_uris(_raw(f"/subs/{group}.txt"))
        uri_to_fp: dict[str, str] = {}
        sys.path.insert(0, str(REPO_ROOT))
        from src.parsers import parse_proxy_uri
        from src.models import SourceConfig
        source = SourceConfig("tier2", "Tier 2", "https://github.com/mani-moh/navaroute")
        for uris in feed_uris.values():
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
        # Per-feed Iran variants, sorted by Iran delay.
        files: dict[str, str] = {}
        results_feeds: dict[str, dict[str, int]] = {}
        total_tested = total_passed = 0
        for group, uris in feed_uris.items():
            group_fps = {uri_to_fp[uri] for uri in uris if uri in uri_to_fp}
            group_tested = len(group_fps & set(self.results))
            group_passing = sorted(
                ((self.results[fp], fp_to_uri[fp])
                 for fp in group_fps & passing if fp in fp_to_uri),
                key=lambda item: (item[0], item[1]))
            group_uris = [uri for _, uri in group_passing]
            plain, encoded = render_feed(group_uris, f"Iran Verified — {group.title()}",
                                         timestamp, IRAN_INTERVAL_HOURS)
            files[f"subs/iran_{group}.txt"] = plain
            files[f"subs/iran_{group}_base64.txt"] = encoded
            results_feeds[group] = {"tested": group_tested, "passed": len(group_uris)}
            total_tested += group_tested
            total_passed += len(group_uris)
        files["data/iran_results.json"] = json.dumps(
            {"version": 1, "generated": timestamp, "feeds": results_feeds,
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
        pr = _gh("POST", "/pulls", token,
                 {"title": f"Tier 2 Iran-verified feeds ({now.strftime('%Y-%m-%d %H:%M')} UTC)",
                  "head": branch, "base": "master",
                  "body": ("Iran Realping results from the owner PC: "
                           f"{total_passed} passing of {total_tested} tested; "
                           f"{len(new_blocked)} configs in the GitHub-blocked list.")})
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
button{padding:.6em 1em;margin:.3em .3em .3em 0;cursor:pointer}input{width:100%;padding:.5em}
#log{background:#111;color:#ddd;padding:1em;height:220px;overflow:auto;white-space:pre-wrap;font-size:.85em}
.bar{height:12px;background:#eee}.bar>i{display:block;height:12px;background:#2a7}</style></head>
<body><h1>NavaRoute Tier 2 — Iran Tester</h1>
<p>Tests configs from Iran with the PattN engine and uploads the passing ones as Iran-verified feeds.</p>
<h3>GitHub token</h3><p><input id="pat" type="password" placeholder="Fine-grained PAT (repo mani-moh/navaroute: contents + pull requests)">
<button onclick="saveToken()">Save token (Windows Credential Manager)</button> <span id="tok"></span></p>
<h3>Test</h3>
<button onclick="start('tier1')">Test tier-1 passers</button>
<button onclick="start('all')">Test all configs (full sweep, resumable)</button>
<div class="bar"><i id="fill" style="width:0%"></i></div>
<p id="status">Ready.</p>
<h3>Upload</h3><button onclick="upload()">Upload to repo (branch + PR)</button>
<h3>Log</h3><div id="log"></div>
<script>
async function api(p,m,b){const r=await fetch(p,{method:m||'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});return r.json()}
async function saveToken(){const v=document.getElementById('pat').value;if(!v)return;await api('/api/token','POST',{token:v});document.getElementById('pat').value='';refresh()}
async function start(m){await api('/api/start','POST',{mode:m})}
async function upload(){const r=await api('/api/upload','POST',{});alert(r.url||r.error||'done')}
async function refresh(){const s=await api('/api/status');document.getElementById('status').textContent=s.status+': '+s.message+' ('+s.tested+'/'+s.total+' tested, '+s.passed+' passing)';document.getElementById('fill').style.width=(s.total?Math.round(100*s.tested/s.total):0)+'%';document.getElementById('log').textContent=s.log.join('\\n');document.getElementById('tok').textContent=s.token_saved?'Token saved.':''}
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
            self.app.start(str(body.get("mode", "tier1")))
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
                url = self.app.upload(token or "")
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
