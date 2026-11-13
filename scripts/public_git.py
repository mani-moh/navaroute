"""NavaRoute Actions privacy gate and bounded non-forced master writer (stdlib only)."""
import argparse
import base64
import os
from pathlib import PurePosixPath
import re
import subprocess
import time

PUBLIC_FILES = {"README.md", "STATUS.md", "DISCOVERY.md", "sources.json",
                "data/discovery_state.json", "data/discovered_ocr_configs.txt", "data/healthcheck_history.json",
                "data/tier1_state.json", "data/tier1_results.json",
                "data/github_blocked.json", "data/iran_results.json"}
FEEDS = r"(?:all|reachable|active|verified|top100|lite500|tier1_(?:all|reachable|active|top100|lite500)|iran_(?:all|reachable|active|top100|lite500)|protocols/(?:vless|vmess|trojan|ss|hysteria2|tuic)|categories/(?:navaroute-special|pattn-special|iran-optimized|reality|cdn-ws-grpc|confighub|whitelist)|countries/[a-z]{2})(?:_base64)?\.txt"
PRIVATE_DIRS = {"local_subs", "private_subs", "iran-results", "iran-tested", "iran-verification",
                ".superpowers", "__pycache__", ".pytest_cache", ".venv", "venv", ".cache", "cache", "http_cache"}
SECRET = re.compile(rb"nvapi-[A-Za-z0-9_-]{10,}|sk-or-v1-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}")
MAX_BLOB_BYTES = 128 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 120


def git(*args, env=None):
    result = subprocess.run(["git", *args], capture_output=True, env=env, timeout=GIT_TIMEOUT_SECONDS)
    if result.returncode:
        raise ValueError("Git operation failed; no publication confirmed")
    return result.stdout


def private_path(path):
    parts = PurePosixPath(path).parts
    name = parts[-1].lower() if parts else ""
    return (not parts or "\\" in path or path.startswith("/") or any(p in (".", "..") for p in path.split("/"))
            or any(p.lower() in PRIVATE_DIRS for p in parts)
            or any(re.fullmatch(r"\..*\.navaroute-.*", p.lower()) for p in parts[:-1])
            or name.startswith(".env") and path != ".env.example"
            or name.startswith("sources.private.")
            or any(re.search(r"\.db(?:$|[.-])|\.sqlite|\.(?:pyc|pyo|exe|zip|bak)$", p.lower()) for p in parts)
            or path.lower().startswith("data/") and path not in PUBLIC_FILES)


def public_output(path):
    return (not private_path(path) and (path in PUBLIC_FILES
            or path == "subs/categories/serverless-fragment.json"
            or re.fullmatch("subs/" + FEEDS, path) is not None))


def check(staged=False):
    entries = git("ls-files", "--stage", "-z").split(b"\0")
    paths = []
    for entry in filter(None, entries):
        metadata, path = entry.decode("utf-8").split("\t", 1)
        mode, _, stage = metadata.split()
        if mode not in ("100644", "100755") or stage != "0" or private_path(path):
            raise ValueError("Tracked private, unsafe or conflicted role rejected; no file contents read")
        paths.append(path)
    if staged:
        changed = git("diff", "--cached", "--no-renames", "--name-only", "-z").decode("utf-8").split("\0")
        if any(not public_output(path) for path in filter(None, changed)):
            raise ValueError("Staged path outside explicit public output roles")
    secrets = [os.environ[k].encode() for k in ("NVIDIA_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN") if os.environ.get(k)]
    for path in paths:
        size = int(git("cat-file", "-s", ":" + path))
        if not 0 <= size <= MAX_BLOB_BYTES:
            raise ValueError("Tracked blob exceeds privacy scan bound")
        body = git("show", ":" + path)
        if len(body) != size:
            raise ValueError("Index changed during privacy scan")
        if SECRET.search(body) or any(secret in body for secret in secrets):
            raise ValueError("Potential secret pattern rejected; matching content suppressed")
    print("Static security scan passed: tracked roles and index blobs checked; no matching content printed.")


def push():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("Publication credential required")
    env = dict(os.environ)
    # Only the writer subprocess receives authentication; GITHUB_TOKEN remains repository-, not path-scoped.
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode(),
               GIT_TERMINAL_PROMPT="0")
    for attempt in range(5):
        try:
            git("pull", "--rebase", "origin", "master", env=env)
        except ValueError:
            # A failed pull/rebase must stop before any push, so a
            # conflict stays visible for a human instead of being
            # rebased over. The message is generic by design: git's own
            # diagnostics can carry private paths/content and are never
            # printed (see tests/test_workflow_security.py).
            raise ValueError("Pull/rebase failed; no push attempted; local commits retained") from None
        result = subprocess.run(["git", "push", "origin", "HEAD:master"], capture_output=True, env=env, timeout=GIT_TIMEOUT_SECONDS)
        if result.returncode == 0:
            print("Public master commit pushed without force.")
            return
        if attempt < 4:
            time.sleep(3)
    raise ValueError("Push attempts exhausted; local commits retained, no publication confirmed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "push"))
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(args.staged)
        else:
            push()
        return 0
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        # Only the generic exception text is shown: no ValueError in
        # this module embeds file contents, and git's own diagnostics
        # are never printed (see tests/test_workflow_security.py).
        # str(TimeoutExpired) is just the command and timeout, no output.
        print(f"ERROR: public Git gate/writer failed: {exc} (matching content suppressed; local commits/conflicts retained).")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
