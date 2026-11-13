"""Headless PattN (v2rayN.WebAPI) client, stdlib only.

The WebAPI is the headless engine behind the PattN GUI: an ASP.NET Core
server on 127.0.0.1:5080 that drives Xray / sing-box / mihomo / Aether.
This module is shared by the tier-1 GitHub runner (scripts/tier1_pattn.py)
and the tier-2 owner-PC app (tools/tier2/tier2_pattn.py).

Verified against the PattN source (patterniha/PattN, branch my-releases,
v2rayN/v2rayN.WebAPI/Api/WebApiEndpoints.cs, Contracts/ApiModels.cs,
Api/WebAuthEndpoints.cs):
  - GET  /api/health                              (no auth)
  - POST /api/auth/login  {"Key": "<V2RAYN_WEB_API_KEY>"} -> token in data
  - POST /api/profiles/import {"Content": "<URIs, one per line>",
                               "SubscriptionId": null, "IsSubscription": false}
  - POST /api/speedtests {"Action": "Realping", "ProfileIds": [...]}
       (omit ProfileIds to test every profile in the current group)
  - GET  /api/profiles -> ProfileView list (IndexId, Remarks, Protocol,
       Address, Port, Delay in ms, -1 when the test failed, CanTest)
  - DELETE /api/speedtests                       (stop a running test)
  - GET  /api/status                             (running operations)
  - POST /api/settings/regional-presets/Iran     (Iran preset, tier 2)

Realping is an end-to-end HTTPS GET through the proxy to a generate_204
URL, twice 100 ms apart, 5 s connect timeout, the minimum recorded;
both failing gives Delay -1. The server batches at most 16 profiles
per core process with 1 s between batches.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 5080
DEFAULT_BASE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
# Pinned WebAPI release: P41 ships no WebAPI assets, P37-P40 do.
PINNED_WEBAPI_VERSION = "7.25.6-P40"
PINNED_WEBAPI_URL = (
    "https://github.com/patterniha/PattN/releases/download/"
    "7.25.6-P40/v2rayN.WebAPI-windows-64.zip"
)
PINNED_WEBAPI_LINUX_URL = (
    "https://github.com/patterniha/PattN/releases/download/"
    "7.25.6-P40/v2rayN.WebAPI-linux-64.zip"
)
IMPORT_CHUNK = 500


class PattnError(ValueError):
    """The WebAPI refused or failed an operation."""


def _envelope(payload: object) -> object:
    """Unwrap the ApiEnvelope {"data": ...} the WebAPI returns, if present."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


class PattnClient:
    """Minimal HTTP client for one running v2rayN.WebAPI instance."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str = "",
                 timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._token: str | None = None

    # -- transport -----------------------------------------------------
    def _request(self, method: str, path: str, body: object = None,
                 auth: bool = True) -> object:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        if auth and self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise PattnError(f"WebAPI {method} {path} -> HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise PattnError(f"WebAPI {method} {path} unreachable: {exc.reason}") from None
        if not raw.strip():
            return None
        try:
            return _envelope(json.loads(raw))
        except ValueError:
            return raw

    # -- session -------------------------------------------------------
    def login(self) -> str:
        """Exchange the management key for a Bearer token."""
        if not self.api_key:
            raise PattnError("No WebAPI key: set V2RAYN_WEB_API_KEY first")
        result = self._request("POST", "/api/auth/login", {"Key": self.api_key},
                               auth=False)
        token = None
        if isinstance(result, str) and result:
            token = result
        elif isinstance(result, dict):
            for key in ("token", "accessToken", "sessionId", "session"):
                if isinstance(result.get(key), str) and result[key]:
                    token = result[key]
                    break
        if not token:
            raise PattnError("WebAPI login did not return a token")
        self._token = token
        return token

    def health(self) -> bool:
        try:
            self._request("GET", "/api/health", auth=False)
            return True
        except PattnError:
            return False

    # -- profiles ------------------------------------------------------
    def import_profiles(self, uris: list[str]) -> int:
        """Import URIs (one per line) in chunks; returns how many were sent."""
        sent = 0
        for start in range(0, len(uris), IMPORT_CHUNK):
            chunk = uris[start:start + IMPORT_CHUNK]
            self._request("POST", "/api/profiles/import",
                          {"Content": "\n".join(chunk),
                           "SubscriptionId": None, "IsSubscription": False})
            sent += len(chunk)
        return sent

    def profiles(self) -> list[dict]:
        result = self._request("GET", "/api/profiles")
        if not isinstance(result, list):
            raise PattnError("WebAPI /api/profiles did not return a list")
        return [p for p in result if isinstance(p, dict)]

    def delete_profiles(self, profile_ids: list[str]) -> None:
        if profile_ids:
            self._request("DELETE", "/api/profiles", {"ProfileIds": profile_ids})

    # -- speed tests ---------------------------------------------------
    def start_speedtest(self, profile_ids: list[str] | None = None,
                        action: str = "Realping") -> None:
        body: dict[str, object] = {"Action": action}
        if profile_ids:
            body["ProfileIds"] = profile_ids
        self._request("POST", "/api/speedtests", body)

    def stop_speedtest(self) -> None:
        try:
            self._request("DELETE", "/api/speedtests")
        except PattnError:
            pass

    def status(self) -> dict:
        result = self._request("GET", "/api/status")
        return result if isinstance(result, dict) else {}

    def speedtest_running(self) -> bool:
        """True while a speed test is still in progress (best effort)."""
        try:
            status = self.status()
        except PattnError:
            return False
        text = json.dumps(status).lower()
        return "speedtest" in text and ("running" in text or "busy" in text)

    def wait_for_speedtest(self, timeout: int = 5400, poll: int = 15) -> None:
        """Wait until no speed test is running (or the timeout expires)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.speedtest_running():
                # The status flag can clear before the last Delay lands;
                # one more short settle pass keeps the read consistent.
                time.sleep(5)
                if not self.speedtest_running():
                    return
            time.sleep(poll)
        raise PattnError("Speed test did not finish within the timeout")

    def apply_iran_preset(self) -> None:
        self._request("POST", "/api/settings/regional-presets/Iran", {})


def generate_api_key() -> str:
    """A per-run management key; loopback-only, never stored in the repo."""
    return secrets.token_urlsafe(32)


def start_webapi(exe_path: Path, api_key: str, port: int = DEFAULT_PORT,
                 cwd: Path | None = None) -> subprocess.Popen:
    """Start the WebAPI exe and wait for /api/health. Caller must stop it."""
    env = dict(os.environ)
    env["V2RAYN_WEB_API_KEY"] = api_key
    env.setdefault("ASPNETCORE_URLS", f"http://127.0.0.1:{port}")
    process = subprocess.Popen(
        [str(exe_path)], env=env, cwd=str(cwd) if cwd else None,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = PattnClient(f"http://127.0.0.1:{port}", api_key)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PattnError(f"WebAPI exited early (code {process.returncode})")
        if client.health():
            return process
        time.sleep(2)
    process.terminate()
    raise PattnError("WebAPI did not become healthy within 120s")


def stop_webapi(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
