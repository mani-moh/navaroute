from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.sync_to_pattn import (
    DEFAULT_REPO_SLUG,
    PROTECTED_BPB_REMARKS,
    SUBSCRIPTION_DEFINITIONS,
    generate_sub_id,
    sync_subscriptions,
)

BASE_DIR = Path(__file__).resolve().parent.parent


class TestWorkflowSyntaxAndSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow_path = BASE_DIR / ".github" / "workflows" / "update-subs.yml"
        self.assertTrue(self.workflow_path.exists(), "update-subs.yml must exist")
        self.workflow_content = self.workflow_path.read_text(encoding="utf-8")

    def test_workflow_triggers_and_inputs(self) -> None:
        """Verify cron schedule and workflow_dispatch parameters."""
        self.assertIn('cron: "0 */6 * * *"', self.workflow_content)
        self.assertIn("workflow_dispatch:", self.workflow_content)
        self.assertIn("run_healthcheck:", self.workflow_content)
        self.assertIn("generate_pattn_variants:", self.workflow_content)
        self.assertIn("max_test_nodes:", self.workflow_content)
        self.assertIn("sync_private_gist:", self.workflow_content)

    def test_workflow_privacy_isolation(self) -> None:
        """Verify that zero private domains, tokens, or URLs appear in update-subs.yml."""
        priv_path = BASE_DIR / "sources.private.json"
        if priv_path.exists():
            priv_data = json.loads(priv_path.read_text(encoding="utf-8"))
            for src in priv_data.get("sources", []):
                url = src.get("url", "")
                m = re.search(r"https?://([^/:]+)", url)
                if m:
                    domain = m.group(1).lower()
                    self.assertNotIn(
                        domain,
                        self.workflow_content.lower(),
                        f"Private domain '{domain}' must never appear in update-subs.yml!",
                    )

    def test_trigger_script_exists_and_valid(self) -> None:
        """Verify trigger_update.ps1 exists and defines expected parameters."""
        ps1_path = BASE_DIR / "scripts" / "trigger_update.ps1"
        self.assertTrue(ps1_path.exists(), "trigger_update.ps1 must exist")
        content = ps1_path.read_text(encoding="utf-8")
        self.assertIn("[switch]$Local", content)
        self.assertIn("[switch]$NoHealthcheck", content)
        self.assertIn("[switch]$NoPattnVariants", content)
        self.assertIn("[switch]$SyncPattnDb", content)
        self.assertIn('"workflow", "run", "update-subs.yml"', content)


class TestSyncToPattn(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "guiNDB.db"

        # Initialize mock SQLite schema matching PattN / v2rayN SubItem table
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE "SubItem" (
                "Id" varchar primary key not null,
                "Remarks" varchar,
                "Url" varchar,
                "MoreUrl" varchar,
                "Enabled" integer,
                "UserAgent" varchar,
                "RequestHeaders" varchar,
                "Sort" integer,
                "Filter" varchar,
                "AutoUpdateInterval" integer,
                "UpdateTime" integer,
                "ConvertTarget" varchar,
                "PrevProfile" varchar,
                "NextProfile" varchar,
                "PreSocksPort" integer,
                "Memo" varchar,
                "CustomCoreType" integer,
                "OverrideAddress" varchar,
                "OverridePort" integer
            )
            """
        )

        # Seed test data: 6 protected BPB entries + 2 public feeds
        seed_entries = [
            ("1001", "normal", "https://priv1.example/sub/mock-normal", 1),
            ("1002", "fragment", "https://priv1.example/sub/mock-fragment", 1),
            ("1003", "raw", "https://priv1.example/sub/mock-raw", 1),
            ("1004", "warp", "https://priv1.example/sub/mock-warp", 1),
            ("1005", "warp pro", "https://priv1.example/sub/mock-wp", 1),
            ("1006", "oldraw", "https://priv2.example/sub/mock-oldraw", 1),
            ("2001", "public_feed_1", "https://pub.example/sub1.txt", 1),
            ("2002", "public_feed_2", "https://pub.example/sub2.txt", 1),
        ]
        for row in seed_entries:
            cur.execute(
                """
                INSERT INTO SubItem (Id, Remarks, Url, Enabled, Sort, AutoUpdateInterval, UpdateTime)
                VALUES (?, ?, ?, ?, 0, 360, 0)
                """,
                row,
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dry_run_makes_zero_disk_changes(self) -> None:
        """Verify dry-run returns correct summary but does not modify database."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            dry_run=True,
        )
        self.assertEqual(summary["added"], 9)
        self.assertEqual(summary["updated"], 0)

        # Confirm no backup file created and no rows added
        bak_files = list(self.db_path.parent.glob("*.bak.*"))
        self.assertEqual(len(bak_files), 0)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        total_rows = cur.execute("SELECT COUNT(*) FROM SubItem").fetchone()[0]
        conn.close()
        self.assertEqual(total_rows, 8)

    def test_sync_upserts_and_protects_bpb(self) -> None:
        """Verify sync adds 9 PattN Hub items and preserves all 6 BPB rows."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            dry_run=False,
        )
        self.assertEqual(summary["added"], 9)

        # Confirm backup file created
        bak_files = list(self.db_path.parent.glob("*.bak.*"))
        self.assertEqual(len(bak_files), 1)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT Remarks, Url, Enabled FROM SubItem").fetchall()
        remarks_map = {r[0]: (r[1], r[2]) for r in rows}
        conn.close()

        # All 6 protected BPB entries still enabled and unmodified
        for bpb in PROTECTED_BPB_REMARKS:
            self.assertIn(bpb, remarks_map)
            self.assertEqual(remarks_map[bpb][1], 1)

        # All 9 PattN Hub groups present
        for sub_def in SUBSCRIPTION_DEFINITIONS:
            self.assertIn(sub_def["remarks"], remarks_map)
            url, enabled = remarks_map[sub_def["remarks"]]
            self.assertTrue(url.startswith("https://raw.githubusercontent.com/test/repo/main/subs/"))
            self.assertEqual(enabled, 1)

    def test_sync_idempotence(self) -> None:
        """Running sync twice updates existing items without duplicating them."""
        sync_subscriptions(db_path=self.db_path, repo_slug="test/repo", dry_run=False)
        summary2 = sync_subscriptions(db_path=self.db_path, repo_slug="test/repo", dry_run=False)

        self.assertEqual(summary2["added"], 0)
        self.assertEqual(summary2["updated"], 9)

    def test_disable_redundant_preserves_bpb(self) -> None:
        """--disable-redundant disables other public feeds but never disables BPB entries."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            disable_redundant=True,
            dry_run=False,
        )
        self.assertEqual(summary["disabled"], 2)  # public_feed_1 and public_feed_2

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT Remarks, Enabled FROM SubItem").fetchall()
        remarks_enabled = {r[0]: r[1] for r in rows}
        conn.close()

        # BPB entries MUST still be enabled
        for bpb in PROTECTED_BPB_REMARKS:
            self.assertEqual(remarks_enabled[bpb], 1, f"BPB entry '{bpb}' must remain enabled!")

        # Superseded feeds MUST be disabled
        self.assertEqual(remarks_enabled["public_feed_1"], 0)
        self.assertEqual(remarks_enabled["public_feed_2"], 0)


if __name__ == "__main__":
    unittest.main()
