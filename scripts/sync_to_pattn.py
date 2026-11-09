#!/usr/bin/env python3
r"""sync_to_pattn.py

Manages and synchronizes PattN Hub subscription groups in the PattN / v2rayN SQLite database:
D:\PattN-windows-64-desktop\PattN-windows-64\guiConfigs\guiNDB.db

Features:
- Automatic timestamped backup before any modifications (guiNDB.db.bak.<timestamp>).
- Upserts the 9 standardized PattN Hub subscription groups.
- Optionally adds local private BPB + Verified subscription (--use-local-private).
- Never deletes or disables existing private BPB subscriptions (normal, fragment, raw, warp, warp pro, oldraw).
- Optional --disable-redundant to disable individual public feeds superseded by PattN Hub.
- Supports --dry-run for safe preview.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

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

def _detect_current_branch() -> str:
    if env_branch := os.environ.get("GITHUB_REF_NAME"):
        return env_branch
    try:
        import subprocess
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "master"


try:
    from src.categorizer import (
        DEFAULT_REPO_SLUG,
        _parse_repo_slug_from_url,
        detect_repo_slug,
    )
except ImportError:
    DEFAULT_REPO_SLUG = "mani-moh/pattn"

    def _parse_repo_slug_from_url(url: str) -> Optional[str]:
        url = url.strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+)$", url, re.IGNORECASE)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
        return None

    def detect_repo_slug(default: str = DEFAULT_REPO_SLUG) -> str:
        """Detect repository owner/name slug in order:
        1. GITHUB_REPOSITORY environment variable
        2. Parsing `git remote get-url origin` if configured
        3. Defaulting to default ('mani-moh/pattn')
        """
        env_slug = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
        if env_slug:
            return env_slug

        try:
            import subprocess
            res = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                parsed = _parse_repo_slug_from_url(res.stdout.strip())
                if parsed:
                    return parsed
        except Exception:
            pass

        return default



DEFAULT_DB_PATH = Path(
    r"D:\PattN-windows-64-desktop\PattN-windows-64\guiConfigs\guiNDB.db"
)
DEFAULT_REPO_SLUG = detect_repo_slug()
DEFAULT_BRANCH = _detect_current_branch()

# User's private BPB entries that must NEVER be modified or deleted
PROTECTED_BPB_REMARKS = {
    "normal",
    "fragment",
    "raw",
    "warp",
    "warp pro",
    "oldraw",
}

SUBSCRIPTION_DEFINITIONS = [
    {
        "remarks": "⭐ PattN Hub - Verified",
        "sub_path": "subs/verified.txt",
        "memo": "Fastest verified nodes tested against Iranian ISP firewalls",
        "sort": 100,
    },
    {
        "remarks": "🌐 PattN Hub - All",
        "sub_path": "subs/all.txt",
        "memo": "Complete deduplicated proxy collection with PattN bypass variants",
        "sort": 101,
    },
    {
        "remarks": "⚡ PattN Hub - Top 100 Fast",
        "sub_path": "subs/top100.txt",
        "memo": "Top 100 lowest latency responsive endpoints",
        "sort": 102,
    },
    {
        "remarks": "🛡️ PattN Hub - PattN Special (F&F/OOB)",
        "sub_path": "subs/categories/pattn-special.txt",
        "memo": "PattN specialized bypass variants: Fast&Furious fragment, IPv6-OOB, SNI-Spoof",
        "sort": 103,
    },
    {
        "remarks": "🇮🇷 PattN Hub - Iran Optimized",
        "sub_path": "subs/categories/iran-optimized.txt",
        "memo": "Nodes optimized for MCI, MTN Irancell, and Rightel networks",
        "sort": 104,
    },
    {
        "remarks": "📡 PattN Hub - ConfigsHUB",
        "sub_path": "subs/categories/confighub.txt",
        "memo": "Live curated feeds aggregated from ConfigsHUB channels",
        "sort": 105,
    },
    {
        "remarks": "🔒 PattN Hub - Reality",
        "sub_path": "subs/categories/reality.txt",
        "memo": "VLESS-Reality TLS camouflage configurations",
        "sort": 106,
    },
    {
        "remarks": "☁️ PattN Hub - CDN (WS/gRPC)",
        "sub_path": "subs/categories/cdn-ws-grpc.txt",
        "memo": "Cloudflare and CDN fronted WebSocket and gRPC endpoints",
        "sort": 107,
    },
    {
        "remarks": "🧩 PattN Hub - JSON Fragment",
        "sub_path": "subs/categories/serverless-fragment.json",
        "memo": "Serverless outbound fragment JSON configurations for Iran bypass",
        "sort": 108,
    },
]


def generate_sub_id(existing_ids: set[str]) -> str:
    """Generate a unique 19-digit ID formatted as string matching PattN/v2rayN format."""
    while True:
        new_id = str(random.randint(4000000000000000000, 5999999999999999999))
        if new_id not in existing_ids:
            return new_id


def backup_database(db_path: Path) -> Path:
    """Creates a timestamped backup of the database before modification."""
    ts = int(time.time())
    backup_path = db_path.with_name(f"{db_path.name}.bak.{ts}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def sync_subscriptions(
    db_path: Path = DEFAULT_DB_PATH,
    repo_slug: str = DEFAULT_REPO_SLUG,
    branch: Optional[str] = None,
    use_local_private: bool = False,
    local_repo_root: Optional[Path] = None,
    disable_redundant: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Upserts PattN Hub subscriptions in guiNDB.db SubItem table."""
    if not db_path.exists():
        raise FileNotFoundError(f"PattN database not found at: {db_path}")

    repo_dir = local_repo_root or Path(__file__).resolve().parent.parent
    now_ts = int(time.time())
    target_branch = branch or DEFAULT_BRANCH

    # Build target subscriptions list
    subs_to_sync: list[dict] = []
    base_raw_url = f"https://raw.githubusercontent.com/{repo_slug}/{target_branch}"

    for sub_def in SUBSCRIPTION_DEFINITIONS:
        subs_to_sync.append(
            {
                "remarks": sub_def["remarks"],
                "url": f"{base_raw_url}/{sub_def['sub_path']}",
                "memo": sub_def["memo"],
                "sort": sub_def["sort"],
            }
        )

    # Optional private BPB + Verified subscription
    if use_local_private:
        priv_sub_path = repo_dir / "private_subs" / "private_verified.txt"
        priv_url = (
            f"file:///{str(priv_sub_path).replace('\\', '/')}"
            if priv_sub_path.exists()
            else f"{base_raw_url}/subs/verified.txt"
        )
        subs_to_sync.insert(
            0,
            {
                "remarks": "🔒 PattN Hub - Private BPB + Verified",
                "url": priv_url,
                "memo": "User private Cloudflare BPB configs at top + verified public configs",
                "sort": 99,
            },
        )

    if not dry_run:
        bak_file = backup_database(db_path)
        safe_print(f"[Backup] Created database backup at: {bak_file}")
    else:
        safe_print("[Dry-Run] Simulation mode active. No changes will be written to disk.")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Query existing rows
    cur.execute("SELECT * FROM SubItem")
    rows = cur.fetchall()
    existing_by_remarks = {row["Remarks"]: dict(row) for row in rows}
    existing_ids = {row["Id"] for row in rows}

    added_count = 0
    updated_count = 0
    disabled_count = 0

    # 1. Upsert PattN Hub subscription groups
    for item in subs_to_sync:
        remarks = item["remarks"]
        url = item["url"]
        memo = item["memo"]
        sort_order = item["sort"]

        if remarks in existing_by_remarks:
            row_id = existing_by_remarks[remarks]["Id"]
            safe_print(f"[Update] {remarks} (ID: {row_id}) -> {url}")
            if not dry_run:
                cur.execute(
                    """
                    UPDATE SubItem
                    SET Url = ?, Enabled = 1, Memo = ?, Sort = ?, UpdateTime = ?
                    WHERE Id = ?
                    """,
                    (url, memo, sort_order, now_ts, row_id),
                )
            updated_count += 1
        else:
            new_id = generate_sub_id(existing_ids)
            existing_ids.add(new_id)
            safe_print(f"[Add]    {remarks} (ID: {new_id}) -> {url}")
            if not dry_run:
                cur.execute(
                    """
                    INSERT INTO SubItem (
                        Id, Remarks, Url, MoreUrl, Enabled, UserAgent, RequestHeaders,
                        Sort, Filter, AutoUpdateInterval, UpdateTime, ConvertTarget,
                        PrevProfile, NextProfile, PreSocksPort, Memo, CustomCoreType,
                        OverrideAddress, OverridePort
                    ) VALUES (
                        ?, ?, ?, NULL, 1, '', NULL,
                        ?, NULL, 360, ?, NULL,
                        NULL, NULL, NULL, ?, NULL,
                        NULL, NULL
                    )
                    """,
                    (new_id, remarks, url, sort_order, now_ts, memo),
                )
            added_count += 1

    # 2. Optionally disable redundant individual public feeds
    if disable_redundant:
        hub_remarks = {item["remarks"] for item in subs_to_sync}
        for row in rows:
            rem = row["Remarks"] or ""
            # Never disable protected BPB entries or PattN Hub groups!
            if rem.strip().lower() in PROTECTED_BPB_REMARKS or rem in hub_remarks:
                continue

            # If enabled, disable redundant upstream source
            if row["Enabled"] == 1:
                safe_print(f"[Disable Redundant] Disabling superseded feed: '{rem}'")
                if not dry_run:
                    cur.execute(
                        "UPDATE SubItem SET Enabled = 0 WHERE Id = ?",
                        (row["Id"],),
                    )
                disabled_count += 1

    if not dry_run:
        conn.commit()
    conn.close()

    summary = {
        "added": added_count,
        "updated": updated_count,
        "disabled": disabled_count,
    }
    safe_print(
        f"\n[Summary] Added: {added_count}, Updated: {updated_count}, "
        f"Disabled Redundant: {disabled_count}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sync_to_pattn",
        description="Synchronize PattN Hub subscription groups into local PattN GUI database",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to guiNDB.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--repo-slug",
        default=DEFAULT_REPO_SLUG,
        help=f"GitHub repository slug (owner/repo) for subscription raw URLs (default: {DEFAULT_REPO_SLUG})",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help=f"Git branch for subscription raw URLs (default: auto-detected or {DEFAULT_BRANCH})",
    )
    parser.add_argument(
        "--use-local-private",
        action="store_true",
        help="Include local private BPB + Verified subscription pointing to local private_subs/",
    )
    parser.add_argument(
        "--disable-redundant",
        action="store_true",
        help="Disable redundant public feeds superseded by PattN Hub while preserving BPB configs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying the database or creating backups",
    )

    args = parser.parse_args()

    try:
        sync_subscriptions(
            db_path=args.db_path,
            repo_slug=args.repo_slug,
            branch=args.branch,
            use_local_private=args.use_local_private,
            disable_redundant=args.disable_redundant,
            dry_run=args.dry_run,
        )
        return 0
    except Exception as exc:
        print(f"Error during synchronization: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
