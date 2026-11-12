"""Strict public evidence and owner-only direct Git API publication, never scheduled."""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import json
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.healthcheck import build_xray_outbound, core_environment
from src.models import FINGERPRINT_VERSION, ProxyNode
from src.network import _Connection, _end, _remaining, _url
from src.parsers import parse_proxy_uri
from src.project import DEFAULT_BRANCH, DEFAULT_REPOSITORY, OWNER_LOGIN

OWNER_ID = 72442823
REPOSITORY_ID = 1451928662
RESULT_BRANCH = "iran-results"
CODEOWNERS = b"* @mani-moh\n"
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_API_INPUT_BYTES = 96 * 1024 * 1024
MAX_CANDIDATES = 100000
RESULT_PATHS = {"data/iran-verification.json", "subs/iran-tested.txt", "subs/iran-tested_base64.txt"}
PREFIX = f"repos/{DEFAULT_REPOSITORY}"


class IranResultsError(ValueError):
    """Safe diagnostic; no raw URI, subprocess output, profile or environment data."""


def _sha(value, length=40):
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise IranResultsError("Invalid immutable identity")
    return value


def _json_bytes(value, limit):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise IranResultsError("Invalid public JSON") from None
    if len(encoded) > limit:
        raise IranResultsError("Public payload exceeds byte limit")
    return encoded


def _command(argv, *, payload=None, cwd=None, env=None, max_bytes=MAX_API_BYTES, timeout=30):
    # A bounded pipe reader kills overflowing output, not merely a bounded read of an unlimited spool.
    with tempfile.TemporaryFile() as input_file:
        if payload is not None:
            input_file.write(payload)
            input_file.seek(0)
        proc = None
        deadline = time.monotonic() + timeout
        try:
            proc = subprocess.Popen(argv, stdin=input_file if payload is not None else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd, env=env)
            result = queue.Queue(maxsize=1)
            def read():
                try:
                    result.put(proc.stdout.read(max_bytes + 1))
                except OSError:
                    result.put(None)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            content = result.get(timeout=max(.001, deadline - time.monotonic()))
            if content is None:
                raise IranResultsError("Command response unavailable")
            if len(content) > max_bytes:
                raise IranResultsError("Command response exceeds byte limit")
            if proc.wait(timeout=max(.001, deadline - time.monotonic())) != 0:
                raise IranResultsError("Required command failed")
            return content
        except (OSError, subprocess.SubprocessError, queue.Empty):
            raise IranResultsError("Required command unavailable") from None
        finally:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=2)
                except (OSError, subprocess.SubprocessError):
                    raise IranResultsError("Owned command cleanup failed") from None
                if proc.stdout is not None:
                    proc.stdout.close()


def gh_api(path: str, *, method: str = "GET", payload=None):
    if not isinstance(path, str) or not (path == "user" or path.startswith(PREFIX + "/") or path == PREFIX) or re.search(r"[\s\x00-\x1f\x7f]", path):
        raise IranResultsError("Invalid canonical API endpoint")
    env = core_environment()
    # gh may use its authenticated credential store; environment authority is never accepted.
    env.update({k: v for k, v in os.environ.items() if k.upper() in {"PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "GH_TOKEN", "GITHUB_TOKEN"}})
    argv = ["gh", "api", "--hostname", "github.com", "--method", method,
            "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28", path]
    data = None
    if payload is not None:
        data = _json_bytes(payload, MAX_API_INPUT_BYTES)
        argv += ["--input", "-"]
    raw = _command(argv, payload=data, env=env)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise IranResultsError("Invalid GitHub response") from None
    if not isinstance(result, (dict, list)):
        raise IranResultsError("Invalid GitHub response shape")
    return result


def _pages(path):
    items = []
    for page in range(1, 11):
        rows = gh_api(f"{PREFIX}/{path}&per_page=100&page={page}" if "?" in path else f"{PREFIX}/{path}?per_page=100&page={page}")
        if not isinstance(rows, list) or len(rows) > 100:
            raise IranResultsError("Invalid access inventory")
        items.extend(rows)
        if len(rows) < 100:
            return items
    raise IranResultsError("Access inventory exceeds page limit")


def _content(path, revision):
    data = gh_api(f"{PREFIX}/contents/{path}?ref={_sha(revision)}")
    try:
        if data["type"] != "file" or data["encoding"] != "base64":
            raise ValueError
        return base64.b64decode(data["content"].replace("\n", ""), validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise IranResultsError("Protected file cannot be verified") from None


def authorize_owner(*, attest_access_reviewed: bool) -> dict:
    if attest_access_reviewed is not True:
        raise IranResultsError("Owner access-review attestation required")
    try:
        user = gh_api("user")
        if user["login"] != OWNER_LOGIN or type(user["id"]) is not int or user["id"] != OWNER_ID or user["type"] != "User":
            raise IranResultsError("Authenticated owner required")
        repo = gh_api(PREFIX)
        if (repo["id"] != REPOSITORY_ID or repo["full_name"] != DEFAULT_REPOSITORY or repo["private"] is not False
                or repo["owner"]["login"] != OWNER_LOGIN or repo["owner"]["id"] != OWNER_ID or repo["owner"]["type"] != "User"
                or repo["default_branch"] != DEFAULT_BRANCH or repo["permissions"]["admin"] is not True or repo["permissions"]["push"] is not True):
            raise IranResultsError("Canonical public owner-admin repository required")
        branch = gh_api(f"{PREFIX}/branches/{RESULT_BRANCH}")
        if branch["name"] != RESULT_BRANCH or branch["protected"] is not True:
            raise IranResultsError("Protected result branch required")
        head = _sha(branch["commit"]["sha"])
        protection = gh_api(f"{PREFIX}/branches/{RESULT_BRANCH}/protection")
        reviews = protection["required_pull_request_reviews"]
        bypass = reviews["bypass_pull_request_allowances"]
        if (protection["enforce_admins"]["enabled"] is not False or protection["allow_force_pushes"]["enabled"] is not False
                or protection["allow_deletions"]["enabled"] is not False or reviews["dismiss_stale_reviews"] is not True
                or reviews["require_code_owner_reviews"] is not True or reviews["require_last_push_approval"] is not True
                or type(reviews["required_approving_review_count"]) is not int or reviews["required_approving_review_count"] < 1
                or set(bypass) != {"users", "teams", "apps"} or any(bypass.values())):
            raise IranResultsError("Owner-review protection policy mismatch")
        actions = gh_api(f"{PREFIX}/actions/permissions/workflow")
        if actions["can_approve_pull_request_reviews"] is not False or actions["default_workflow_permissions"] != "read":
            raise IranResultsError("Actions PR creation and approval must remain blocked")
        collaborators = _pages("collaborators?affiliation=all")
        owner_seen = False
        for collaborator in collaborators:
            is_owner = collaborator["login"] == OWNER_LOGIN and collaborator["id"] == OWNER_ID and collaborator["type"] == "User"
            if collaborator["permissions"].get("admin"):
                if not is_owner:
                    raise IranResultsError("Unexpected visible administrator")
                owner_seen = True
        if not owner_seen or _pages("keys"):
            raise IranResultsError("Visible owner or deploy-key inventory mismatch")
        for ruleset in _pages("rulesets?includes_parents=true"):
            detail = gh_api(f"{PREFIX}/rulesets/{ruleset['id']}")
            if detail.get("bypass_actors"):
                raise IranResultsError("Unexpected ruleset bypass")
        secrets = gh_api(f"{PREFIX}/actions/secrets?per_page=100")
        if secrets["total_count"] != 1 or [s["name"] for s in secrets["secrets"]] != ["NVIDIA_API_KEY"]:
            raise IranResultsError("Visible Actions secret inventory mismatch")
        if _content(".github/CODEOWNERS", head) != CODEOWNERS:
            raise IranResultsError("Protected CODEOWNERS mismatch")
        # Owner attests inaccessible external/App/admin-token inventory; no 403 means 'verified'.
        return {"head": head}
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, IranResultsError):
            raise
        raise IranResultsError("Incomplete owner authorization or policy response") from None


def _tree(revision):
    response = gh_api(f"{PREFIX}/git/trees/{_sha(revision)}?recursive=1")
    if not isinstance(response, dict) or response.get("truncated") is not False or not isinstance(response.get("tree"), list):
        raise IranResultsError("Complete protected tree unavailable")
    return response


def verify_tester_checkout(root: Path, tester_revision: str, protected_head: str) -> None:
    """No master-code fallback: exact clean local code must match current protected code."""
    revision = _sha(tester_revision)
    root = root.resolve()
    env = core_environment()
    env["PATH"] = os.environ.get("PATH", "")

    def git(*args):
        return _command(["git", "-C", str(root), *args], env=env).decode("utf-8").strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root or git("rev-parse", "HEAD") != revision:
        raise IranResultsError("Pinned protected tester checkout required")
    if git("rev-parse", "--abbrev-ref", "HEAD") not in {"HEAD", RESULT_BRANCH}:
        raise IranResultsError("Use protected iran-results checkout, never master code")
    if git("status", "--porcelain", "--untracked-files=all", "--", "src", "scripts", ".github/CODEOWNERS"):
        raise IranResultsError("Tester code checkout must be clean")
    comparison = gh_api(f"{PREFIX}/compare/{revision}...{_sha(protected_head)}")
    if comparison.get("status") not in {"ahead", "identical"}:
        raise IranResultsError("Tester revision is not protected-branch history")
    remote = {row["path"]: (row["mode"], row["sha"]) for row in _tree(protected_head)["tree"]
              if row["path"].endswith(".py") or row["path"] in {"scripts/check_from_iran.ps1", ".github/CODEOWNERS"}}
    local = {}
    for entry in git("ls-tree", "-r", revision).splitlines():
        meta, path = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        if path.endswith(".py") or path in {"scripts/check_from_iran.ps1", ".github/CODEOWNERS"}:
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise IranResultsError("Unsupported executable-code tree entry")
            local[path] = (mode, sha)
            file = root / path
            if file.is_symlink() or not file.resolve().is_relative_to(root):
                raise IranResultsError("Local tester code content mismatch")
            content = file.read_bytes()
            if path == "scripts/check_from_iran.ps1":
                content = content.replace(b"\r\n", b"\n")  # .gitattributes checks out this wrapper as CRLF.
            if git_blob_sha(content) != sha:
                raise IranResultsError("Local tester code content mismatch")
    if local != remote or "src/iran_results.py" not in local or "scripts/test_iran_reachability.py" not in local:
        raise IranResultsError("Tester imports differ from protected code")
    for name, module in list(sys.modules.items()):
        if name == "src" or name.startswith("src."):
            path = getattr(module, "__file__", None)
            if path is None or not Path(path).resolve().is_relative_to(root) or Path(path).resolve().relative_to(root).as_posix() not in local:
                raise IranResultsError("Unreviewed tester import")


def verify_source_commit(source_sha: str) -> None:
    comparison = gh_api(f"{PREFIX}/compare/{_sha(source_sha)}...{DEFAULT_BRANCH}")
    if comparison.get("status") not in {"ahead", "identical"}:
        raise IranResultsError("Source snapshot is not official master history")


def _fetch_official_bytes(url: str) -> tuple[int, bytes]:
    # Reuse the reviewed transport directly: even environment-selected existing proxies are forbidden here.
    parsed, host, port = _url(url)
    deadline = _end(30)
    conn = _Connection(host, port, True, deadline, None)
    response = None
    try:
        conn.connect()
        conn.request("GET", parsed.path, headers={"Host": host, "Connection": "close", "Accept-Encoding": "identity"})
        # getresponse() closes the socket object for Connection: close; the response keeps the descriptor.
        response = conn.getresponse()
        _remaining(deadline)
        if response.status != 200:
            return response.status, b""  # Includes every redirect; never follow it or read a fallback.
        length = response.getheader("Content-Length")
        transfer = response.getheader("Transfer-Encoding")
        if (transfer is not None and (transfer.lower() != "chunked" or length is not None)
                or length is not None and (not re.fullmatch(r"[0-9]{1,20}", length) or int(length) > MAX_SNAPSHOT_BYTES)
                or response.getheader("Content-Encoding", "identity").lower() != "identity"):
            raise IranResultsError("Invalid or oversized official response framing")
        body = bytearray()
        while True:
            chunk = response.read1(min(65536, MAX_SNAPSHOT_BYTES + 1 - len(body)))
            _remaining(deadline)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_SNAPSHOT_BYTES:
                raise IranResultsError("Official snapshot exceeds byte limit")
        if length is not None and len(body) != int(length):
            raise IranResultsError("Official snapshot response incomplete")
        return 200, bytes(body)
    except (OSError, http.client.HTTPException):
        raise IranResultsError("Official direct download failed") from None
    finally:
        if conn.timer is not None:
            conn.timer.cancel()
        if response is not None:
            response.close()
        conn.close()
        conn.watched_socket = None


def load_official_snapshot(source_sha: str, source_digest: str) -> list[ProxyNode]:
    _sha(source_sha)
    _sha(source_digest, 64)
    try:
        status, body = _fetch_official_bytes(f"https://raw.githubusercontent.com/{DEFAULT_REPOSITORY}/{source_sha}/subs/all.txt")
        if status != 200 or len(body) > MAX_SNAPSHOT_BYTES or hashlib.sha256(body).hexdigest() != source_digest:
            raise IranResultsError("Official snapshot download or digest mismatch")
        text = body.decode("utf-8")
        nodes = {}
        candidates = 0
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            candidates += 1
            if candidates > MAX_CANDIDATES:
                raise IranResultsError("Official snapshot exceeds candidate limit")
            node = parse_proxy_uri(line)
            if node is None:
                raise IranResultsError("Official snapshot contains non-connection input")
            nodes.setdefault(node.semantic_fingerprint(), node)
        if not nodes:
            raise IranResultsError("Official snapshot contains no candidates")
        return list(nodes.values())
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError) as exc:
        if isinstance(exc, IranResultsError):
            raise
        raise IranResultsError("Official public snapshot unavailable or invalid") from None


def inspect_core(explicit_path: str) -> dict[str, str]:
    try:
        path = Path(explicit_path)
        if not path.is_absolute() or not path.is_file():
            raise IranResultsError("Supplied absolute standalone core path required")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise IranResultsError("Supplied core exceeds byte limit")
                digest.update(chunk)
        if size == 0:
            raise IranResultsError("Supplied core is empty")
        with tempfile.TemporaryDirectory(prefix="navaroute-core-check-") as tmp:
            version = _command([str(path), "version"], env=core_environment(), cwd=tmp, max_bytes=16384, timeout=10)
            match = re.search(rb"(?:^|\n)Xray ([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})(?:\s|$)", version)
            if not match:
                raise IranResultsError("Supplied core version unsupported")
            config = Path(tmp) / "config.json"
            config.write_text('{"log":{"loglevel":"none"},"outbounds":[{"protocol":"blackhole"}]}', encoding="utf-8")
            _command([str(path), "run", "-test", "-c", str(config)], env=core_environment(), cwd=tmp, max_bytes=16384, timeout=10)
        return {"version": "Xray " + match[1].decode("ascii"), "sha256": digest.hexdigest()}
    except (OSError, TypeError):
        raise IranResultsError("Supplied standalone core unavailable") from None


def _utc(value):
    if not isinstance(value, str) or len(value) > 40:
        raise IranResultsError("Invalid UTC evidence date")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.utcoffset() != timedelta(0) or "T" not in value:
            raise ValueError
        return date
    except ValueError:
        raise IranResultsError("Invalid UTC evidence date") from None


def validate_manifest(manifest: dict, nodes: list[ProxyNode], *, source_sha: str, source_digest: str) -> None:
    _sha(source_sha)
    _sha(source_digest, 64)
    _json_bytes(manifest, MAX_MANIFEST_BYTES)
    fields = {"schema_version", "fingerprint_version", "repository", "source_sha", "source_digest", "tester_revision",
              "started_at", "completed_at", "context", "core", "policy", "rounds", "counts", "observations"}
    try:
        if not isinstance(manifest, dict) or set(manifest) != fields:
            raise IranResultsError("Unknown or missing manifest fields")
        if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                or type(manifest["fingerprint_version"]) is not int or manifest["fingerprint_version"] != FINGERPRINT_VERSION
                or manifest["repository"] != DEFAULT_REPOSITORY or manifest["source_sha"] != source_sha
                or manifest["source_digest"] != source_digest or manifest["context"] != "owner-attested-direct-iran"
                or manifest["policy"] != "https-204-v1" or type(manifest["rounds"]) is not int or not 1 <= manifest["rounds"] <= 10):
            raise IranResultsError("Manifest provenance or policy mismatch")
        _sha(manifest["tester_revision"])
        if _utc(manifest["completed_at"]) < _utc(manifest["started_at"]):
            raise IranResultsError("Evidence dates are reversed")
        core = manifest["core"]
        if set(core) != {"version", "sha256"} or not isinstance(core["version"], str) or not re.fullmatch(r"Xray [0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", core["version"]):
            raise IranResultsError("Invalid sanitized core identity")
        _sha(core["sha256"], 64)
        counts = manifest["counts"]
        if set(counts) != {"candidates", "tested", "passed", "failed", "unsupported", "untested"} or any(type(n) is not int or not 0 <= n <= MAX_CANDIDATES for n in counts.values()):
            raise IranResultsError("Invalid bounded evidence counts")
        known = {n.semantic_fingerprint() for n in nodes}
        unsupported = {n.semantic_fingerprint() for n in nodes if build_xray_outbound(n) is None}
        observations = manifest["observations"]
        if not isinstance(observations, list) or len(observations) != len(known) or counts["candidates"] != len(known):
            raise IranResultsError("Evidence must cover its exact public snapshot")
        seen = set()
        actual = {status: 0 for status in ("passed", "failed", "unsupported", "untested")}
        for observation in observations:
            if not isinstance(observation, dict) or set(observation) != {"fingerprint", "status", "latency_ms"}:
                raise IranResultsError("Invalid observation fields")
            fingerprint = observation["fingerprint"]
            status = observation["status"]
            latency = observation["latency_ms"]
            if fingerprint not in known or fingerprint in seen or status not in actual:
                raise IranResultsError("Unknown or duplicated public observation")
            if fingerprint in unsupported and status != "unsupported":
                raise IranResultsError("Ineligible public connection must remain unsupported")
            if status == "passed":
                if type(latency) not in (int, float) or not math.isfinite(latency) or not 0 <= latency <= 30000:
                    raise IranResultsError("Invalid finite probe latency")
            elif latency is not None:
                raise IranResultsError("Unsuccessful observation cannot claim latency")
            seen.add(fingerprint)
            actual[status] += 1
        if any(counts[s] != count for s, count in actual.items()) or counts["tested"] != actual["passed"] + actual["failed"] or counts["tested"] == 0:
            raise IranResultsError("No genuine completed work or inconsistent evidence counts")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, IranResultsError):
            raise
        raise IranResultsError("Invalid public evidence structure") from None


def build_manifest(nodes, *, source_sha, source_digest, tester_revision, started_at, completed_at, core, rounds, unsupported_fingerprints=()):
    unsupported = set(unsupported_fingerprints)
    observations = []
    for node in nodes:
        fingerprint = node.semantic_fingerprint()
        if fingerprint in unsupported or build_xray_outbound(node) is None:
            status = "unsupported"
        elif node.healthcheck_kind == "xray" and node.healthchecked_at is not None:
            status = "passed" if node.passed_healthcheck else "failed"
        else:
            status = "untested"
        observations.append({"fingerprint": fingerprint, "status": status, "latency_ms": node.latency_ms if status == "passed" else None})
    observations.sort(key=lambda row: row["fingerprint"])
    counts = {status: sum(row["status"] == status for row in observations) for status in ("passed", "failed", "unsupported", "untested")}
    counts.update(candidates=len(nodes), tested=counts["passed"] + counts["failed"])
    manifest = dict(schema_version=1, fingerprint_version=FINGERPRINT_VERSION, repository=DEFAULT_REPOSITORY,
                    source_sha=source_sha, source_digest=source_digest, tester_revision=tester_revision,
                    started_at=started_at, completed_at=completed_at, context="owner-attested-direct-iran", core=core,
                    policy="https-204-v1", rounds=rounds, counts=counts, observations=observations)
    validate_manifest(manifest, nodes, source_sha=source_sha, source_digest=source_digest)
    return manifest


def render_result_files(manifest: dict, nodes: list[ProxyNode], *, source_sha: str, source_digest: str) -> dict[str, str]:
    validate_manifest(manifest, nodes, source_sha=source_sha, source_digest=source_digest)
    by_fingerprint = {node.semantic_fingerprint(): node for node in nodes}
    passed = sorted((row for row in manifest["observations"] if row["status"] == "passed"), key=lambda row: (row["latency_ms"], row["fingerprint"]))
    lines = []
    size = 0
    for row in passed:
        node = by_fingerprint[row["fingerprint"]]
        # Use snapshot serialization only; never accept a URI in submitted evidence.
        uri = node.raw_uri or node.to_uri(include_formatted_remark=False)
        parsed = parse_proxy_uri(uri)
        if parsed is None or parsed.semantic_fingerprint() != row["fingerprint"]:
            raise IranResultsError("Public subscription identity mismatch")
        size += len(uri.encode("utf-8")) + 1
        if size > MAX_FILE_BYTES:
            raise IranResultsError("Public result file exceeds byte limit")
        lines.append(uri)
    payload = "".join(line + "\n" for line in lines)
    title = f"#profile-title: NavaRoute owner-tested Iran — {manifest['completed_at']}\n# Snapshot: {source_sha}; passed: {len(lines)}\n"
    files = {"data/iran-verification.json": _json_bytes(manifest, MAX_MANIFEST_BYTES).decode("utf-8") + "\n",
             "subs/iran-tested.txt": title + payload,
             "subs/iran-tested_base64.txt": base64.b64encode(payload.encode("utf-8")).decode("ascii") + "\n"}
    if set(files) != RESULT_PATHS or any(len(text.encode("utf-8")) > MAX_FILE_BYTES for text in files.values()):
        raise IranResultsError("Public result files exceed limit")
    return files


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()


def _matches(head, expected):
    rows = _tree(head)["tree"]
    found = {row["path"]: row["sha"] for row in rows if row["path"] in RESULT_PATHS and row.get("type") == "blob" and row.get("mode") == "100644"}
    return found == expected


def publish_results(manifest: dict, nodes: list[ProxyNode], *, source_sha: str, source_digest: str,
                    captured_head: str, attest_access_reviewed: bool, checkout_root: Path | None = None) -> str:
    validate_manifest(manifest, nodes, source_sha=source_sha, source_digest=source_digest)
    authority = authorize_owner(attest_access_reviewed=attest_access_reviewed)
    verify_tester_checkout(checkout_root or Path(__file__).resolve().parents[1], manifest["tester_revision"], authority["head"])
    verify_source_commit(source_sha)
    official_nodes = load_official_snapshot(source_sha, source_digest)
    files = render_result_files(manifest, official_nodes, source_sha=source_sha, source_digest=source_digest)
    expected = {path: git_blob_sha(text.encode("utf-8")) for path, text in files.items()}
    head = _sha(gh_api(f"{PREFIX}/git/ref/heads/{RESULT_BRANCH}")["object"]["sha"])
    if _matches(head, expected):
        if _sha(gh_api(f"{PREFIX}/git/ref/heads/{RESULT_BRANCH}")["object"]["sha"]) != head:
            raise IranResultsError("Result reference changed during confirmation")
        return head
    if head != _sha(captured_head) or authority["head"] != head:
        raise IranResultsError("Protected result head changed; no overwrite or rebase")
    base_tree = _sha(gh_api(f"{PREFIX}/git/commits/{head}")["tree"]["sha"])
    entries = []
    for path, text in sorted(files.items()):
        blob = gh_api(f"{PREFIX}/git/blobs", method="POST", payload={"encoding": "base64", "content": base64.b64encode(text.encode("utf-8")).decode("ascii")})
        if _sha(blob["sha"]) != expected[path]:
            raise IranResultsError("Created public blob content mismatch")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = _sha(gh_api(f"{PREFIX}/git/trees", method="POST", payload={"base_tree": base_tree, "tree": entries})["sha"])
    identity = {"name": OWNER_LOGIN, "email": f"{OWNER_ID}+{OWNER_LOGIN}@users.noreply.github.com", "date": manifest["completed_at"]}
    commit = _sha(gh_api(f"{PREFIX}/git/commits", method="POST", payload={"message": f"Owner Iran results {manifest['completed_at']}",
        "tree": tree, "parents": [head], "author": identity, "committer": identity})["sha"])
    if _sha(gh_api(f"{PREFIX}/git/commits/{commit}")["tree"]["sha"]) != tree or not _matches(commit, expected):
        raise IranResultsError("Created result commit content mismatch")
    if _sha(gh_api(f"{PREFIX}/git/ref/heads/{RESULT_BRANCH}")["object"]["sha"]) != head:
        raise IranResultsError("Concurrent protected update; no overwrite")
    updated = gh_api(f"{PREFIX}/git/refs/heads/{RESULT_BRANCH}", method="PATCH", payload={"sha": commit, "force": False})
    if _sha(updated["object"]["sha"]) != commit or _sha(gh_api(f"{PREFIX}/git/ref/heads/{RESULT_BRANCH}")["object"]["sha"]) != commit or not _matches(commit, expected):
        raise IranResultsError("Publication not confirmed")
    if _sha(gh_api(f"{PREFIX}/git/ref/heads/{RESULT_BRANCH}")["object"]["sha"]) != commit:
        raise IranResultsError("Published reference changed during confirmation")
    return commit
