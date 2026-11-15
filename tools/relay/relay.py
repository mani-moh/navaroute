"""ArvanCloud mirror relay: keep the mirror fresh from GitHub, on any host.

The mirror is ArvanCloud Object Storage (bucket ``repoget``,
region ``ir-central1``). Its source of truth is the private repo
``mani-moh/feed-data`` (branch ``main``), which holds the base64 feed
files; the mirror page (``index.html``) is generated from those files
by ``scripts/build_mirror_page.py``. The existing GitHub Actions
workflow in feed-data does the same sync, but this relay is an
independent path that does not depend on Actions: it runs on the Muse
VM (every 15 minutes) and on the AWS EC2 server (systemd timer), so
the mirror keeps updating even if Actions is down -- and, during a
blackout, it is the path that carries fresh content found outside
Iran into the mirror.

Two stages, both idempotent (nothing is re-uploaded when the content
is unchanged):

Stage A -- upstream -> feed-data (GitHub): copy the current base64
feed files (the FEEDS list from ``build_mirror_page.py``) from the
public navaroute repo into feed-data, and delete ``*_base64.txt``
files there that are no longer in the list (clean break after the
feeds rename). Needs GitHub write access (a token or ``gh``). Runs
even without S3 credentials, so content keeps flowing via Actions
while the S3 keys are not yet configured.

Stage B -- feed-data -> ArvanCloud S3: upload the feed files plus the
generated ``index.html`` to the bucket, using newest-wins: a file is
uploaded only when its repo version is strictly NEWER than the
mirror's copy (Git commit time of that file in feed-data, falling
back to the upstream commit time, vs the S3 object's LastModified)
and the content differs. A mirror copy that is newer or equal is
never overwritten -- this matters during a blackout, when the mirror
can hold fresh Blackout Kit content that the repo does not have yet.
Mirror-only files are never deleted: the relay is upload-only, and
the Blackout Kit special feeds (which live only in the mirror) are
left alone. The single exception is a one-time, explicit cleanup of
the known retired feed names from the feeds rename (PR #45) --
``reachable``, ``active``, ``top100``, ``lite500``, ``verified``,
``tier1_*``, ``iran_*`` -- deleted only when the repo no longer
contains them, and nothing else. Needs the S3 key pair. Without it,
runs in ``--check`` mode: verifies reachability and reports what
WOULD change, uploading nothing.

Credentials are read from (in order) ``--config``, ``$ARVAN_RELAY_CONFIG``,
``~/.config/arvan-relay/env``, and
``~/workspace/your_files/arvancloud-credentials.txt``. Keys:
``ARVAN_ACCESS_KEY``, ``ARVAN_SECRET_KEY``, optional ``GITHUB_TOKEN``.
Values are never printed or logged. The relay never writes secrets to
the status file, stdout, or the repo.

Usage:
  python3 tools/relay/relay.py [--check] [--config PATH] [--status PATH]
                               [--no-stage-a]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# -- constants (hosts and buckets, not feed names) ----------------------
UPSTREAM_OWNER = "mani-moh"
UPSTREAM_REPO = "navaroute"
UPSTREAM_BRANCH = "master"
FEED_DATA_OWNER = "mani-moh"
FEED_DATA_REPO = "feed-data"
FEED_DATA_BRANCH = "main"
BUCKET = "repoget"
REGION = "ir-central1"
S3_ENDPOINT = "https://s3.ir-central1.arvanstorage.ir"
MIRROR_BASE_URL = "https://repoget.s3.ir-central1.arvanstorage.ir"
GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
USER_AGENT = "navaroute-relay/1.0"

CONFIG_KEYS = ("ARVAN_ACCESS_KEY", "ARVAN_SECRET_KEY", "GITHUB_TOKEN")

# Retired feed file names from the feeds rename (PR #45): the only
# names the relay is ever allowed to delete from the mirror, and only
# when the repo no longer contains them. Mirror-only files outside
# this list (e.g. the Blackout Kit special feeds) are never deleted.
RETIRED_NAMES = frozenset({
    "reachable_base64.txt", "active_base64.txt", "top100_base64.txt",
    "lite500_base64.txt", "verified_base64.txt",
    "tier1_all_base64.txt", "tier1_reachable_base64.txt",
    "tier1_active_base64.txt", "tier1_top100_base64.txt",
    "tier1_lite500_base64.txt",
    "iran_all_base64.txt", "iran_reachable_base64.txt",
    "iran_active_base64.txt", "iran_top100_base64.txt",
    "iran_lite500_base64.txt",
})


class RelayError(Exception):
    """A relay step failed (network, API, or missing input)."""


# -- config -------------------------------------------------------------
def parse_config_text(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignore blanks and '#' comments."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
            value = value[1:-1]
        if key and value:
            values[key] = value
    return values


def config_candidates(explicit: str | None = None) -> list[Path]:
    """Config file locations, in precedence order (first wins per key)."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get("ARVAN_RELAY_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    home = Path.home()
    candidates.append(home / ".config" / "arvan-relay" / "env")
    candidates.append(home / "workspace" / "your_files" / "arvancloud-credentials.txt")
    return candidates


def load_config(explicit: str | None = None) -> dict[str, str]:
    """Merge credentials from env vars, then config files (first wins)."""
    merged: dict[str, str] = {}
    for key in CONFIG_KEYS:
        value = os.environ.get(key)
        if value:
            merged[key] = value
    for path in config_candidates(explicit):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, value in parse_config_text(text).items():
            merged.setdefault(key, value)
    return {key: merged[key] for key in CONFIG_KEYS if key in merged}


def _gh_token() -> str | None:
    """Best-effort GitHub token from the `gh` CLI (VM only)."""
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
        token = proc.stdout.strip()
        return token or None
    except Exception:
        return None


def resolve_github_token(config: dict[str, str]) -> str | None:
    """GitHub token: config/env first, then `gh auth token`, else None."""
    token = config.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    return _gh_token()


# -- HTTP helpers -------------------------------------------------------
def _request(url: str, method: str = "GET", data: bytes | None = None,
             headers: dict[str, str] | None = None,
             timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RelayError(f"{method} {url} -> HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RelayError(f"{method} {url} -> {exc.reason}") from exc


def _parse_http_date(value: str | None) -> datetime | None:
    """Parse an HTTP Last-Modified header (RFC 1123) to an aware datetime."""
    if not value:
        return None
    from email.utils import parsedate_to_datetime
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def _head_meta(url: str, timeout: int = 15) -> dict | None:
    """ETag + Last-Modified of a public URL via HEAD.

    Returns None when the object is missing or unreachable; otherwise
    {"etag": str | None, "last_modified": datetime | None}.
    """
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"etag": resp.headers.get("ETag"),
                    "last_modified": _parse_http_date(
                        resp.headers.get("Last-Modified"))}
    except Exception:
        return None


def _reachable(url: str, timeout: int = 15) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:
        return False


# -- GitHub API ---------------------------------------------------------
class GitHubAPI:
    """Minimal GitHub REST client (urllib; token optional for public reads)."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request_json(self, method: str, path: str,
                     payload: dict | None = None) -> dict | list:
        url = f"{GITHUB_API}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        body = _request(url, method=method, data=data, headers=headers)
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, (dict, list)):
            raise RelayError(f"unexpected GitHub response for {method} {path}")
        return parsed

    def list_contents(self, owner: str, repo: str, path: str = "") -> list[dict]:
        suffix = f"/{path}" if path else ""
        result = self.request_json("GET", f"/repos/{owner}/{repo}/contents{suffix}")
        if not isinstance(result, list):
            raise RelayError(f"expected a directory listing for {owner}/{repo}/{path}")
        return result

    def get_file_bytes(self, owner: str, repo: str, path: str) -> tuple[bytes | None, str | None]:
        """(content, sha) for a file; (None, None) when it does not exist.

        The contents API returns an empty ``content`` field for files
        larger than ~1 MB; those are fetched from ``download_url``
        instead (authenticated for a private repo).
        """
        try:
            entry = self.request_json("GET", f"/repos/{owner}/{repo}/contents/{path}")
        except RelayError as exc:
            if "HTTP 404" in str(exc):
                return None, None
            raise
        if not isinstance(entry, dict):
            raise RelayError(f"expected a file at {owner}/{repo}/{path}")
        content_b64 = entry.get("content") or ""
        if content_b64:
            content = base64.b64decode(content_b64)
            return content, entry.get("sha")
        download_url = entry.get("download_url")
        if download_url:
            data = _request(download_url, headers=self._headers())
            return data, entry.get("sha")
        raise RelayError(f"expected a file at {owner}/{repo}/{path}")

    def file_commit_time(self, owner: str, repo: str,
                         path: str) -> datetime | None:
        """Most recent commit time touching a file; None when unavailable.

        Uses the commits API (needs a token for a private repo; the
        public upstream works without one). Returns None on any error
        so callers can fall back or skip safely.
        """
        try:
            result = self.request_json(
                "GET", f"/repos/{owner}/{repo}/commits?path={path}&per_page=1")
        except RelayError:
            return None
        if not isinstance(result, list) or not result:
            return None
        date_str = (result[0].get("commit", {}).get("committer", {})
                    .get("date"))
        if not date_str:
            return None
        try:
            when = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when

    def put_file(self, owner: str, repo: str, path: str, content: bytes,
                 message: str, sha: str | None, branch: str) -> None:
        payload: dict[str, str] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        self.request_json("PUT", f"/repos/{owner}/{repo}/contents/{path}", payload)

    def delete_file(self, owner: str, repo: str, path: str, sha: str,
                    message: str, branch: str) -> None:
        payload = {"message": message, "sha": sha, "branch": branch}
        self.request_json("DELETE", f"/repos/{owner}/{repo}/contents/{path}", payload)


# -- feed list (dynamic: from the mirror page builder) ------------------
def load_page_builder() -> object:
    """Import build_mirror_page from the relay dir or the repo scripts/."""
    here = Path(__file__).resolve().parent
    candidates = [here, here.parent.parent / "scripts"]
    for directory in candidates:
        if (directory / "build_mirror_page.py").is_file():
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))
            import build_mirror_page  # noqa: PLC0415
            return build_mirror_page
    raise RelayError(
        "build_mirror_page.py not found next to relay.py or in repo scripts/")


def feed_stems(page_builder: object) -> list[str]:
    """Feed file stems served by the mirror, from the page builder."""
    feeds = getattr(page_builder, "FEEDS", ())
    return [stem for stem, _label, _desc in feeds]


def upstream_file(stem: str) -> bytes:
    """Fetch a base64 feed file from the public upstream repo."""
    url = f"{RAW_BASE}/{UPSTREAM_OWNER}/{UPSTREAM_REPO}/{UPSTREAM_BRANCH}/subs/{stem}.txt"
    return _request(url)


# -- change detection ---------------------------------------------------
def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def etag_matches(etag: str | None, data: bytes) -> bool:
    """True when an S3 ETag equals the content MD5 (single-part upload)."""
    if not etag:
        return False
    etag = etag.strip('"')
    if "-" in etag:
        return False
    return etag.lower() == md5_hex(data)


def should_upload(repo_time: datetime | None, s3_time: datetime | None,
                  content_differs: bool) -> tuple[bool, str]:
    """Newest-wins: upload only when the repo version is strictly newer
    than the mirror's copy AND the content differs.

    Returns (upload, reason). When the mirror's copy is newer or equal
    it is never overwritten; when either timestamp is unknown the
    upload is skipped rather than overwriting blindly. ``s3_time`` of
    None means the object does not exist in the mirror (a new file).
    """
    if s3_time is None:
        return True, "new file"
    if repo_time is None:
        return False, "repo timestamp unknown; not overwriting the mirror copy"
    if repo_time <= s3_time:
        return False, "mirror copy is newer or equal"
    if not content_differs:
        return False, "identical content"
    return True, "repo version is newer"


# -- S3 (boto3, imported lazily so --check and tests need no boto3) -----
class S3Store:
    """ArvanCloud S3 bucket access (boto3, S3-compatible endpoint)."""

    def __init__(self, access_key: str, secret_key: str,
                 bucket: str = BUCKET, endpoint: str = S3_ENDPOINT,
                 region: str = REGION, client: object | None = None) -> None:
        self.bucket = bucket
        self.endpoint = endpoint
        if client is not None:
            self.client = client
        else:
            import boto3  # noqa: PLC0415
            self.client = boto3.client(
                "s3", endpoint_url=endpoint, region_name=region,
                aws_access_key_id=access_key, aws_secret_access_key=secret_key)

    def list_objects(self) -> dict[str, dict]:
        """Object name -> {"etag": str, "last_modified": datetime | None}."""
        objects: dict[str, dict] = {}
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"Bucket": self.bucket}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for entry in page.get("Contents", []):
                when = entry.get("LastModified")
                if when is not None and getattr(when, "tzinfo", None) is None:
                    when = when.replace(tzinfo=timezone.utc)
                objects[entry["Key"]] = {
                    "etag": entry.get("ETag", ""), "last_modified": when}
            if not page.get("IsTruncated"):
                return objects
            token = page.get("NextContinuationToken")
            if not token:
                return objects

    def upload(self, name: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=name, Body=data,
            ACL="public-read", ContentType=content_type)

    def delete(self, names: list[str]) -> None:
        if not names:
            return
        self.client.delete_objects(
            Bucket=self.bucket,
            Delete={"Objects": [{"Key": name} for name in names]})


# -- the relay ----------------------------------------------------------
class Relay:
    """Stage A (upstream -> feed-data) + Stage B (feed-data -> S3)."""

    def __init__(self, config: dict[str, str], github: GitHubAPI | None = None,
                 s3: S3Store | None = None, bucket: str = BUCKET,
                 endpoint: str = S3_ENDPOINT,
                 base_url: str = MIRROR_BASE_URL) -> None:
        self.config = config
        self.github = github if github is not None else GitHubAPI(
            resolve_github_token(config))
        self.s3 = s3
        self.bucket = bucket
        self.endpoint = endpoint
        self.base_url = base_url
        self.page_builder = load_page_builder()

    # -- stage A --------------------------------------------------------
    def stage_a(self) -> dict:
        """Sync the mirror feed files into feed-data (add/update/delete)."""
        if not self.github.token:
            return {"status": "skipped", "reason": "no GitHub write access"}
        stems = feed_stems(self.page_builder)
        wanted = {f"{stem}.txt" for stem in stems}
        try:
            entries = self.github.list_contents(FEED_DATA_OWNER, FEED_DATA_REPO)
        except RelayError as exc:
            return {"status": "error", "error": str(exc)}
        current = {e["name"]: e.get("sha", "")
                   for e in entries if e.get("type") == "file"
                   and e["name"].endswith(".txt")}
        updated: list[str] = []
        deleted: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        for stem in stems:
            name = f"{stem}.txt"
            try:
                content = upstream_file(stem)
            except RelayError as exc:
                if "HTTP 404" in str(exc):
                    # Not in upstream (yet, or ever -- the Blackout Kit
                    # special feeds live only in the mirror/feed-data).
                    skipped.append(name)
                    continue
                errors.append(f"{name}: {exc}")
                continue
            existing, sha = self._feed_data_bytes(name)
            if existing == content:
                continue
            try:
                self.github.put_file(
                    FEED_DATA_OWNER, FEED_DATA_REPO, name, content,
                    f"Relay sync: update {name}", sha, FEED_DATA_BRANCH)
                updated.append(name)
            except RelayError as exc:
                errors.append(f"{name}: {exc}")
        for name, sha in current.items():
            if name not in wanted and name.endswith("_base64.txt"):
                try:
                    self.github.delete_file(
                        FEED_DATA_OWNER, FEED_DATA_REPO, name, sha,
                        f"Relay sync: remove retired {name}", FEED_DATA_BRANCH)
                    deleted.append(name)
                except RelayError as exc:
                    errors.append(f"{name}: {exc}")
        result: dict[str, object] = {
            "status": "ok" if not errors else "partial",
            "updated": updated, "deleted": deleted, "skipped": skipped,
            "errors": errors}
        return result

    def _feed_data_bytes(self, name: str) -> tuple[bytes | None, str | None]:
        return self.github.get_file_bytes(FEED_DATA_OWNER, FEED_DATA_REPO, name)

    # -- stage B --------------------------------------------------------
    def _collect_files(self) -> tuple[dict[str, bytes], list[str]]:
        """Mirror files to publish: feed contents + generated index.html.

        Returns (files, skipped). A feed listed in FEEDS but present in
        neither feed-data nor upstream (e.g. a Blackout Kit special feed
        not published yet) is skipped, not an error -- the page builder
        only links files that exist.
        """
        stems = feed_stems(self.page_builder)
        files: dict[str, bytes] = {}
        skipped: list[str] = []
        for stem in stems:
            name = f"{stem}.txt"
            content, _sha = self._feed_data_bytes(name)
            if content is None:
                try:
                    content = upstream_file(stem)
                except RelayError as exc:
                    if "HTTP 404" in str(exc):
                        skipped.append(name)
                        continue
                    raise
            files[name] = content
        with tempfile.TemporaryDirectory() as tmp:
            feeds_dir = Path(tmp) / "feeds"
            out_dir = Path(tmp) / "out"
            feeds_dir.mkdir()
            for name, content in files.items():
                (feeds_dir / name).write_bytes(content)
            page_path = self.page_builder.build(
                feeds_dir, self.base_url, out_dir)
            files["index.html"] = page_path.read_bytes()
        return files, skipped

    def _repo_time(self, stem: str) -> datetime | None:
        """Git commit time of a feed file in feed-data (fallback upstream)."""
        name = f"{stem}.txt"
        when = self.github.file_commit_time(FEED_DATA_OWNER, FEED_DATA_REPO,
                                           name)
        if when is None:
            when = self.github.file_commit_time(
                UPSTREAM_OWNER, UPSTREAM_REPO, f"subs/{name}")
        return when

    def _repo_times(self) -> dict[str, datetime | None]:
        """Repo commit time per mirror file name.

        Feed files use their own commit time; index.html uses the newest
        feed commit time (the page is generated from the feeds).
        """
        stems = feed_stems(self.page_builder)
        times: dict[str, datetime | None] = {}
        newest: datetime | None = None
        for stem in stems:
            when = self._repo_time(stem)
            times[f"{stem}.txt"] = when
            if when is not None and (newest is None or when > newest):
                newest = when
        times["index.html"] = newest
        return times

    def _retired_in_mirror(self, present: set[str],
                           repo_names: set[str]) -> list[str]:
        """Retired names present in the mirror and absent from the repo.

        The ONLY names the relay ever deletes from the mirror. Mirror-only
        files outside RETIRED_NAMES are left alone.
        """
        return sorted(name for name in RETIRED_NAMES
                      if name in present and name not in repo_names)

    def stage_b_check(self, files: dict[str, bytes]) -> dict:
        """What WOULD change in S3, via public HEAD (no credentials)."""
        repo_times = self._repo_times()
        to_upload: list[str] = []
        skipped: list[str] = []
        for name, data in files.items():
            meta = _head_meta(f"{self.base_url}/{name}")
            if meta is None:
                to_upload.append(name)
                continue
            differs = not etag_matches(meta.get("etag"), data)
            if not differs:
                continue
            upload, reason = should_upload(
                repo_times.get(name), meta.get("last_modified"), differs)
            if upload:
                to_upload.append(name)
            else:
                skipped.append(f"{name} ({reason})")
        present: set[str] = set()
        for name in RETIRED_NAMES:
            if _head_meta(f"{self.base_url}/{name}") is not None:
                present.add(name)
        to_clean = self._retired_in_mirror(present, set(files))
        return {"status": "check", "to_upload": to_upload,
                "skipped": skipped, "to_clean": to_clean}

    def stage_b_push(self, files: dict[str, bytes]) -> dict:
        """Upload newer files to S3; clean only the retired names (needs creds)."""
        if self.s3 is None:
            return {"status": "skipped", "reason": "no S3 credentials"}
        try:
            current = self.s3.list_objects()
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        repo_times = self._repo_times()
        uploaded: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        for name, data in files.items():
            entry = current.get(name)
            if entry is None:
                s3_time = None
                differs = True
            else:
                s3_time = entry.get("last_modified")
                differs = not etag_matches(entry.get("etag"), data)
            if not differs:
                continue
            upload, reason = should_upload(repo_times.get(name), s3_time,
                                           differs)
            if not upload:
                skipped.append(f"{name} ({reason})")
                continue
            content_type = ("text/html; charset=utf-8" if name == "index.html"
                            else "text/plain; charset=utf-8")
            try:
                self.s3.upload(name, data, content_type)
                uploaded.append(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        # One-time cleanup of the retired feed names only. Mirror-only
        # files outside RETIRED_NAMES are never deleted.
        to_clean = self._retired_in_mirror(set(current), set(files))
        cleaned: list[str] = []
        if to_clean:
            try:
                self.s3.delete(to_clean)
                cleaned = to_clean
            except Exception as exc:
                errors.append(f"clean retired names: {exc}")
        return {"status": "ok" if not errors else "partial",
                "uploaded": uploaded, "skipped": skipped,
                "cleaned": cleaned, "errors": errors}

    # -- orchestration --------------------------------------------------
    def run(self, check_only: bool = False,
            skip_stage_a: bool = False) -> dict:
        """Run both stages; return a status dict (never logs secrets)."""
        started = datetime.now(timezone.utc).isoformat()
        status: dict[str, object] = {
            "started": started, "check_only": check_only,
            "github_reachable": _reachable(f"{GITHUB_API}/"),
            "arvan_reachable": _reachable(f"{self.base_url}/index.html"),
            "stage_a": {"status": "skipped"},
            "stage_b": {"status": "skipped"},
            "errors": [],
        }
        if not status["github_reachable"]:
            status["errors"].append("github unreachable")
        if not status["arvan_reachable"]:
            status["errors"].append("arvancloud unreachable")
        if not skip_stage_a and not check_only:
            try:
                status["stage_a"] = self.stage_a()
            except RelayError as exc:
                status["stage_a"] = {"status": "error", "error": str(exc)}
                status["errors"].append(f"stage A: {exc}")
        try:
            files, skipped_feeds = self._collect_files()
            status["skipped_feeds"] = skipped_feeds
        except RelayError as exc:
            status["errors"].append(f"collect files: {exc}")
            status["finished"] = datetime.now(timezone.utc).isoformat()
            return status
        if check_only or self.s3 is None:
            status["stage_b"] = self.stage_b_check(files)
            if self.s3 is None and not check_only:
                status["stage_b"]["note"] = "no S3 credentials; check only"
        else:
            try:
                status["stage_b"] = self.stage_b_push(files)
            except RelayError as exc:
                status["stage_b"] = {"status": "error", "error": str(exc)}
                status["errors"].append(f"stage B: {exc}")
        status["finished"] = datetime.now(timezone.utc).isoformat()
        return status


def write_status(path: Path, status: dict) -> None:
    """Write the status JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _print_status(status: dict) -> None:
    print(f"github reachable: {status.get('github_reachable')}")
    print(f"arvancloud reachable: {status.get('arvan_reachable')}")
    stage_a = status.get("stage_a", {})
    if isinstance(stage_a, dict):
        print(f"stage A (feed-data): {stage_a.get('status')}"
              + (f" updated={len(stage_a.get('updated', []))}"
                 f" deleted={len(stage_a.get('deleted', []))}"
                 if stage_a.get("status") in ("ok", "partial") else "")
              + (f" reason={stage_a.get('reason')}"
                 if stage_a.get("reason") else ""))
    stage_b = status.get("stage_b", {})
    if isinstance(stage_b, dict):
        if stage_b.get("status") == "check":
            print(f"stage B (S3 check): would upload {stage_b.get('to_upload', [])}"
                  f", would clean {stage_b.get('to_clean', [])}"
                  + (f", skipped {stage_b.get('skipped', [])}"
                     if stage_b.get("skipped") else ""))
        else:
            print(f"stage B (S3): {stage_b.get('status')}"
                  + (f" uploaded={len(stage_b.get('uploaded', []))}"
                     f" cleaned={len(stage_b.get('cleaned', []))}"
                     if stage_b.get("status") in ("ok", "partial") else "")
                  + (f" reason={stage_b.get('reason')}"
                     if stage_b.get("reason") else ""))
            for entry in stage_b.get("skipped", []):
                print(f"  skipped: {entry}")
    for error in status.get("errors", []):
        print(f"error: {error}")


def build_s3(config: dict[str, str], bucket: str, endpoint: str) -> S3Store | None:
    """S3 client from config; None when the key pair is absent."""
    access = config.get("ARVAN_ACCESS_KEY")
    secret = config.get("ARVAN_SECRET_KEY")
    if not access or not secret:
        return None
    return S3Store(access, secret, bucket=bucket, endpoint=endpoint)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArvanCloud mirror relay.")
    parser.add_argument("--check", action="store_true",
                        help="dry run: verify reachability, change nothing")
    parser.add_argument("--config", default=None,
                        help="credentials file (default: standard locations)")
    parser.add_argument("--status", default=None,
                        help="write the run status JSON to this path")
    parser.add_argument("--no-stage-a", action="store_true",
                        help="skip the feed-data push")
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--endpoint", default=S3_ENDPOINT)
    parser.add_argument("--base-url", default=MIRROR_BASE_URL)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    try:
        s3 = None if args.check else build_s3(config, args.bucket, args.endpoint)
        relay = Relay(config, s3=s3, bucket=args.bucket,
                      endpoint=args.endpoint, base_url=args.base_url)
        status = relay.run(check_only=args.check,
                           skip_stage_a=args.no_stage_a)
    except RelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_status(status)
    if args.status:
        write_status(Path(args.status), status)
    if status.get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
