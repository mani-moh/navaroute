#!/usr/bin/env python3
r"""test_iran_reachability.py

Local in-Iran batched Xray healthcheck script.
Run this directly on your Windows machine inside Iran AFTER turning off your personal VPN
to filter GitHub-aggregated configs down to nodes that genuinely pass your Iranian ISP & DPI.

Outputs (git-ignored):
- local_subs/iran_verified.txt
- local_subs/iran_verified_base64.txt
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.categorizer import _detect_current_branch, detect_repo_slug
from src.dedup import deduplicate_nodes
from src.healthcheck import check_nodes_health
from src.models import ProxyNode, SourceConfig
from src.parsers import parse_proxy_uri

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(*args: Any, **kwargs: Any) -> None:
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    file = kwargs.get("file", sys.stdout)
    text = sep.join(str(a) for a in args) + end
    try:
        file.write(text)
    except UnicodeEncodeError:
        encoding = getattr(file, "encoding", "ascii") or "ascii"
        file.write(text.encode(encoding, errors="replace").decode(encoding))
    file.flush()


DEFAULT_XRAY_PATHS = [
    Path(r"D:\PattN-windows-64-desktop\PattN-windows-64\bin\xray\xray.exe"),
    Path(r"D:\PattN-windows-64-desktop\PattN-windows-64\bin\xray\p-xray-p.exe"),
]

COMMON_LOCAL_VPN_PORTS = (10808, 10809, 7890, 2080, 1080)

CANDIDATE_RELATIVE_FEEDS = [
    ("subs/verified.txt", True),
    ("subs/categories/pattn-special.txt", False),
    ("subs/categories/iran-optimized.txt", False),
    ("subs/categories/reality.txt", False),
    ("subs/categories/confighub.txt", False),
    ("subs/categories/cdn-ws-grpc.txt", False),
]


def find_local_xray_binary(explicit_path: Optional[str] = None) -> Optional[str]:
    """Finds an executable local Xray binary (explicit -> PattN desktop -> PATH)."""
    if explicit_path:
        p = Path(explicit_path)
        if p.is_file():
            return str(p)
        resolved = shutil.which(explicit_path)
        if resolved:
            return resolved
        return None

    for candidate in DEFAULT_XRAY_PATHS:
        if candidate.is_file():
            return str(candidate)

    for cmd in ("xray", "xray.exe", "p-xray-p", "p-xray-p.exe"):
        found = shutil.which(cmd)
        if found:
            return found

    return None


def detect_active_local_vpn(ports_to_check: tuple[int, ...] = COMMON_LOCAL_VPN_PORTS) -> list[str]:
    """Detects active local proxy env vars, Windows system proxy, or listening VPN ports."""
    signals: list[str] = []

    for env_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        val = (os.environ.get(env_var) or "").strip()
        if val:
            signals.append(f"Env var {env_var}={val}")

    if sys.platform == "win32":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if int(enabled) == 1:
                    server, _ = winreg.QueryValueEx(key, "ProxyServer")
                    signals.append(f"Windows System Proxy enabled ({server})")
        except Exception:
            pass

    for port in ports_to_check:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.12):
                signals.append(f"Local proxy listening on 127.0.0.1:{port}")
        except OSError:
            pass

    return signals


def _fetch_raw_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "PattN-Iran-Checker/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_candidate_nodes(
    repo_root: Path = REPO_ROOT,
    from_github: bool = False,
    repo_slug: Optional[str] = None,
    branch: Optional[str] = None,
) -> list[ProxyNode]:
    """Loads, parses, and deduplicates candidate nodes from local subs/ or GitHub."""
    parsed_nodes: list[ProxyNode] = []
    slug = repo_slug or detect_repo_slug()
    br = branch or _detect_current_branch()
    base_url = f"https://raw.githubusercontent.com/{slug}/{br}"

    for rel_path, is_verified in CANDIDATE_RELATIVE_FEEDS:
        content = ""
        if from_github:
            try:
                content = _fetch_raw_text(f"{base_url}/{rel_path}")
            except Exception:
                pass
        if not content:
            local_file = repo_root / rel_path
            if local_file.is_file():
                content = local_file.read_text(encoding="utf-8", errors="replace")
        if not content:
            continue

        src = SourceConfig(
            id=f"feed:{Path(rel_path).stem}",
            name=rel_path,
            url=rel_path,
            verified_upstream=is_verified,
            tags=["verified"] if is_verified else [],
        )
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "://" not in line:
                continue
            node = parse_proxy_uri(line, source=src)
            if node:
                if is_verified:
                    node.is_verified_source = True
                parsed_nodes.append(node)

    return deduplicate_nodes(parsed_nodes)


def write_iran_verified_outputs(
    survivors: list[ProxyNode],
    output_dir: Path = REPO_ROOT / "local_subs",
) -> tuple[Path, Path]:
    """Writes Iran-verified configs (plain + base64) to git-ignored local_subs/."""
    output_dir.mkdir(parents=True, exist_ok=True)
    plain_path = output_dir / "iran_verified.txt"
    b64_path = output_dir / "iran_verified_base64.txt"

    ordered = sorted(
        survivors,
        key=lambda n: n.latency_ms if n.latency_ms is not None else 99999.0,
    )

    lines = [
        "#profile-title: 🇮🇷 PattN Iran-Live Verified",
        "#profile-update-interval: 6",
        f"# Iran ISP verified survivors: {len(ordered)}",
    ]
    uri_lines: list[str] = []
    for n in ordered:
        uri = n.to_uri(include_formatted_remark=True)
        lines.append(uri)
        uri_lines.append(uri)

    plain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    b64_content = base64.b64encode("\n".join(uri_lines).encode("utf-8")).decode("ascii")
    b64_path.write_text(b64_content + "\n", encoding="utf-8")
    return plain_path, b64_path


def run_iran_check(
    max_nodes: int = 500,
    rounds: int = 2,
    batch_size: int = 48,
    timeout: float = 5.0,
    concurrency: int = 24,
    xray_bin: Optional[str] = None,
    from_github: bool = False,
    sync_pattn_db: bool = False,
    force: bool = False,
    repo_root: Path = REPO_ROOT,
) -> int:
    safe_print("=" * 65)
    safe_print("🇮🇷 PattN Local In-Iran End-to-End Xray Reachability Verifier")
    safe_print("=" * 65)

    # 1. VPN safety guard
    if not force:
        vpn_signals = detect_active_local_vpn()
        if vpn_signals:
            safe_print("[ERROR] Active local VPN / proxy detected:")
            for sig in vpn_signals:
                safe_print(f"  - {sig}")
            safe_print(
                "\nTurn OFF your personal VPN (including TUN mode) before running this test "
                "so results reflect real Iranian ISP reachability, or pass --force to override."
            )
            return 2

    safe_print("[Note] Ensure any TUN-mode VPN is OFF so traffic exits directly via your Iranian ISP.")

    # 2. Locate local Xray binary
    resolved_xray = find_local_xray_binary(xray_bin)
    if not resolved_xray:
        safe_print(
            "[ERROR] Could not find xray.exe or p-xray-p.exe. Pass --xray-bin <path> or install Xray in PATH."
        )
        return 1
    safe_print(f"[1/4] Using local Xray binary: {resolved_xray}")

    # 3. Load candidates
    candidates = load_candidate_nodes(
        repo_root=repo_root,
        from_github=from_github,
    )
    safe_print(f"[2/4] Loaded {len(candidates)} unique candidate configs (testing up to {max_nodes}, rounds={rounds})...")
    if not candidates:
        safe_print("[WARN] No candidate configs found.")
        return 1

    # 4. Run batched real-traffic Xray healthcheck
    check_nodes_health(
        candidates,
        max_nodes=max_nodes,
        concurrency=concurrency,
        timeout=timeout,
        xray_bin=resolved_xray,
        use_xray=True,
        batch_size=batch_size,
        rounds=rounds,
    )

    survivors = [n for n in candidates if n.passed_healthcheck]
    safe_print(f"[3/4] In-Iran Xray verification completed: {len(survivors)} live configs passed {rounds}/{rounds} rounds")

    # 5. Save outputs
    plain_path, b64_path = write_iran_verified_outputs(survivors, output_dir=repo_root / "local_subs")
    safe_print(f"[4/4] Saved Iran-verified feeds:")
    safe_print(f"      - {plain_path}")
    safe_print(f"      - {b64_path}")

    if sync_pattn_db:
        from scripts.sync_to_pattn import sync_subscriptions
        safe_print("\nSyncing 'Iran Live Verified' into local PattN database...")
        sync_subscriptions(
            use_local_iran=True,
            local_repo_root=repo_root,
        )

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_iran_reachability",
        description="Test proxy reachability directly from Iran using local Xray core",
    )
    parser.add_argument("--max-nodes", type=int, default=500, help="Max prioritized nodes to test (default: 500)")
    parser.add_argument("--rounds", type=int, default=2, help="Number of strict Xray probe rounds (default: 2)")
    parser.add_argument("--batch-size", type=int, default=48, help="Nodes per Xray process batch (default: 48)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-probe HTTP timeout in seconds (default: 5.0)")
    parser.add_argument("--concurrency", type=int, default=24, help="Concurrent probes per batch (default: 24)")
    parser.add_argument("--xray-bin", default=None, help="Explicit path to xray.exe or p-xray-p.exe")
    parser.add_argument("--from-github", action="store_true", help="Fetch latest subs/ from GitHub before testing")
    parser.add_argument("--sync-pattn-db", action="store_true", help="Upsert Iran-verified feed into PattN guiNDB.db")
    parser.add_argument("--force", action="store_true", help="Bypass active local proxy/VPN safety guard")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_iran_check(
        max_nodes=args.max_nodes,
        rounds=args.rounds,
        batch_size=args.batch_size,
        timeout=args.timeout,
        concurrency=args.concurrency,
        xray_bin=args.xray_bin,
        from_github=args.from_github,
        sync_pattn_db=args.sync_pattn_db,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
