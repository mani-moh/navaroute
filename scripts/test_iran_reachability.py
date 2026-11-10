#!/usr/bin/env python3
"""Explicit owner command. Run this reviewed code from protected iran-results only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--xray-bin", required=True, help="Absolute path to supplied trusted standalone Xray")
    parser.add_argument("--source-sha", required=True, help="Immutable official master snapshot commit")
    parser.add_argument("--source-digest", required=True, help="Pinned SHA-256 of official subs/all.txt bytes")
    parser.add_argument("--tester-revision", required=True, help="Reviewed protected checkout commit")
    parser.add_argument("--attest-direct-iran", action="store_true", help="Owner attests direct Iranian ISP; no VPN inspection")
    parser.add_argument("--attest-access-reviewed", action="store_true", help="Owner attests NVIDIA-only secret and no external/untrusted owner/admin tokens")
    parser.add_argument("--publish", action="store_true", help="Explicit owner Git API publication, never Actions PRs")
    parser.add_argument("--retry-manifest", type=Path, help="Retry the same sanitized dated evidence, without a retest")
    for name, default in (("max-nodes", 500), ("rounds", 2), ("batch-size", 48), ("concurrency", 24)):
        parser.add_argument("--" + name, type=int, default=default)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser


def _check_local_pin_before_imports(revision: str) -> dict:
    # The operator supplies the reviewed pin; remote protected-tree checks follow before probes.
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}}
    def git(*args, limit=16384):
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(["git", "-C", str(REPO_ROOT), *args], stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.DEVNULL, env=env, timeout=10, check=False)
            output.seek(0)
            content = output.read(limit + 1)
        if result.returncode != 0 or len(content) > limit:
            raise ValueError
        return content.strip()
    if git("rev-parse", "HEAD") != revision.encode() or git("rev-parse", "--abbrev-ref", "HEAD") not in {b"HEAD", b"iran-results"}:
        raise ValueError
    if git("status", "--porcelain", "--untracked-files=all", "--", "src", "scripts", ".github/CODEOWNERS"):
        raise ValueError
    if Path(git("rev-parse", "--show-toplevel").decode("utf-8")).resolve() != REPO_ROOT:
        raise ValueError
    sources = {}
    for entry in git("ls-tree", "-r", revision, limit=2 * 1024 * 1024).decode("utf-8").splitlines():
        meta, path = entry.split("\t", 1)
        if not path.endswith(".py"):
            continue
        mode, kind, sha = meta.split()
        file = REPO_ROOT / path
        if mode not in {"100644", "100755"} or kind != "blob" or file.is_symlink() or not file.resolve().is_relative_to(REPO_ROOT):
            raise ValueError
        with file.open("rb") as stream:
            source = stream.read(2 * 1024 * 1024 + 1)
        if len(source) > 2 * 1024 * 1024 or hashlib.sha1(b"blob " + str(len(source)).encode("ascii") + b"\0" + source).hexdigest() != sha:
            raise ValueError
        if path.startswith("src/"):
            parts = Path(path).with_suffix("").parts
            package = parts[-1] == "__init__"
            name = ".".join(parts[:-1] if package else parts)
            if name in sources or not all(part.isidentifier() for part in name.split(".")):
                raise ValueError
            sources[name] = (str(file.resolve()), source, package)
    if "src" not in sources:
        raise ValueError
    return sources


def _install_pinned_imports(sources: dict) -> None:
    if any(name == "src" or name.startswith("src.") for name in sys.modules):
        raise ImportError("Project code was imported before the pinned-source gate")
    class SourceOnlyLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            return compile(sources[fullname][1], self.path, "exec", dont_inherit=True)
    class PinnedFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "src" and not fullname.startswith("src."):
                return None
            if fullname not in sources:
                raise ModuleNotFoundError("Project import is not in the reviewed pin")
            origin, _, package = sources[fullname]
            return importlib.util.spec_from_file_location(fullname, origin, loader=SourceOnlyLoader(fullname, origin),
                submodule_search_locations=[str(Path(origin).parent)] if package else None)
    sys.meta_path.insert(0, PinnedFinder())


if __name__ == "__main__":
    command_arguments = vars(build_arg_parser().parse_args())
    try:
        _install_pinned_imports(_check_local_pin_before_imports(command_arguments["tester_revision"]))
    except (OSError, ValueError, ImportError, subprocess.SubprocessError):
        print("Error: use the clean pinned reviewed iran-results checkout; no project code imported.")
        sys.exit(1)

# Standalone project imports use the pinned finder, not a directory added to default dependency lookup.
from src.healthcheck import CoreInfrastructureError, check_nodes_health, utc_now, validate_limits
from src.iran_results import (IranResultsError, _json_bytes, _sha, MAX_MANIFEST_BYTES, authorize_owner,
    build_manifest, inspect_core, load_official_snapshot, publish_results, render_result_files,
    verify_source_commit, verify_tester_checkout)
from src.storage import atomic_write_text


def run_iran_check(*, xray_bin: str, source_sha: str, source_digest: str, tester_revision: str,
                   attest_direct_iran: bool = False, attest_access_reviewed: bool = False,
                   publish: bool = False, max_nodes: int = 500, rounds: int = 2, batch_size: int = 48,
                   timeout: float = 5.0, concurrency: int = 24, recovery_dir: Path | None = None,
                   retry_manifest: Path | None = None) -> int:
    try:
        validate_limits(max_nodes, concurrency, timeout, batch_size, rounds)
        _sha(source_sha)
        _sha(source_digest, 64)
        _sha(tester_revision)
        if attest_direct_iran is not True or attest_access_reviewed is not True:
            raise IranResultsError("Direct-Iran and access-review owner attestations required")
        authority = authorize_owner(attest_access_reviewed=attest_access_reviewed)
        verify_tester_checkout(REPO_ROOT, tester_revision, authority["head"])
        if not xray_bin:
            raise IranResultsError("Explicit standalone core required")
        core = inspect_core(xray_bin)
        verify_source_commit(source_sha)
        nodes = load_official_snapshot(source_sha, source_digest)
        directory = recovery_dir if recovery_dir is not None else REPO_ROOT / "local_subs" / "iran-results"
        captured_head = authority["head"]
        if retry_manifest is not None:
            if not publish:
                raise IranResultsError("Dated retry requires explicit publication intent")
            retry_manifest = retry_manifest.resolve()
            if retry_manifest.parent != directory.resolve() or not retry_manifest.is_file() or retry_manifest.suffix != ".json":
                raise IranResultsError("Retry must use owned sanitized recovery storage")
            _sha(retry_manifest.stem, 64)
            with retry_manifest.open("rb") as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 513)
            if len(raw) > MAX_MANIFEST_BYTES + 512 or hashlib.sha256(raw).hexdigest() != retry_manifest.stem:
                raise IranResultsError("Recovery manifest exceeds byte limit")
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or set(envelope) != {"captured_head", "manifest"}:
                raise IranResultsError("Invalid dated recovery evidence")
            captured_head = _sha(envelope["captured_head"])
            manifest = envelope["manifest"]
            if manifest.get("tester_revision") != tester_revision or manifest.get("core") != core:
                raise IranResultsError("Recovery provenance does not match supplied tester/core")
        else:
            started_at = utc_now()
            summary = check_nodes_health(nodes, max_nodes=max_nodes, concurrency=concurrency, timeout=timeout,
                xray_bin=xray_bin, use_xray=True, batch_size=batch_size, rounds=rounds)
            manifest = build_manifest(nodes, source_sha=source_sha, source_digest=source_digest, tester_revision=tester_revision,
                started_at=started_at, completed_at=utc_now(), core=core, rounds=rounds,
                unsupported_fingerprints=summary.get("unsupported_fingerprints", ()))
        render_result_files(manifest, nodes, source_sha=source_sha, source_digest=source_digest)
        # Only allowlisted sanitized evidence is saved; no local subscription/input/client files.
        directory.mkdir(parents=True, exist_ok=True)
        encoded = _json_bytes({"captured_head": captured_head, "manifest": manifest}, MAX_MANIFEST_BYTES + 512).decode("utf-8") + "\n"
        recovery = directory / (hashlib.sha256(encoded.encode()).hexdigest() + ".json")
        atomic_write_text(recovery, encoded)
        if publish:
            publish_results(manifest, nodes, source_sha=source_sha, source_digest=source_digest,
                captured_head=captured_head, attest_access_reviewed=attest_access_reviewed, checkout_root=REPO_ROOT)
            print("Published: exact protected reference and public content confirmed.")
        else:
            print("Local test completed; sanitized dated recovery evidence saved. Not uploaded.")
        return 0
    except (IranResultsError, CoreInfrastructureError) as exc:
        print(f"Error: {exc}")
        return 1
    except Exception:
        print("Error: standalone operation failed; no publication confirmed.")
        return 1
    except KeyboardInterrupt:
        print("Cancelled; no publication confirmed.")
        return 130


def main(argv: list[str] | None = None) -> int:
    return run_iran_check(**vars(build_arg_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(run_iran_check(**command_arguments))
