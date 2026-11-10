from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from scripts.sync_to_pattn import (
    DEFAULT_BRANCH,
    DEFAULT_REPO_SLUG,
    SUBSCRIPTION_DEFINITIONS,
    detect_repo_slug,
    generate_sub_id,
    sync_subscriptions,
)
from src.categorizer import detect_repo_slug as cat_detect_repo_slug
from src.main import build_arg_parser

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

    def test_workflow_is_fast_source_aggregation(self) -> None:
        """Verify update-subs.yml is a fast 6h updater without AI discovery overhead."""
        self.assertNotIn("--discover-only", self.workflow_content)
        self.assertIn("Run public aggregation pipeline", self.workflow_content)
        self.assertIn("python -m src.main", self.workflow_content)

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

    def test_daily_extended_discovery_workflow(self) -> None:
        """Verify daily-deep-discovery.yml is scheduled daily, runs 500 API requests, and masks secrets."""
        daily_path = BASE_DIR / ".github" / "workflows" / "daily-deep-discovery.yml"
        self.assertTrue(daily_path.exists(), "daily-deep-discovery.yml must exist")
        content = daily_path.read_text(encoding="utf-8")
        self.assertIn('cron: "0 2 * * *"', content)
        self.assertIn('default: "500"', content)
        self.assertIn("--discovery-api-requests", content)
        self.assertIn("::add-mask::$NVIDIA_API_KEY", content)
        self.assertIn("Security audit passed", content)

    def test_professional_repo_files_and_ci_workflow(self) -> None:
        """Verify LICENSE, .gitattributes, .env.example, docs, and ci.yml exist cleanly."""
        for rel in ("LICENSE", ".gitattributes", ".env.example", "CONTRIBUTING.md", "docs/ARCHITECTURE.md"):
            self.assertTrue((BASE_DIR / rel).is_file(), f"Missing standard file: {rel}")

        ci_path = BASE_DIR / ".github" / "workflows" / "ci.yml"
        self.assertTrue(ci_path.is_file(), "ci.yml must exist")
        ci_txt = ci_path.read_text(encoding="utf-8")
        self.assertIn("python -m unittest discover -s tests -v", ci_txt)
        self.assertIn("Static security scan passed", ci_txt)

        env_ex = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
        self.assertIn("NVIDIA_API_KEY=<your-nvidia-api-key>", env_ex)


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

        # Seed test data: 1 local file subscription + 2 public HTTP feeds
        seed_entries = [
            ("1001", "custom_local", "file:///local/sub.txt", 1),
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
        self.assertEqual(total_rows, 3)

    def test_sync_upserts_and_preserves_local_feeds(self) -> None:
        """Verify sync adds 9 PattN Hub items and preserves existing rows."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            dry_run=False,
        )
        self.assertEqual(summary["added"], 9)

        bak_files = list(self.db_path.parent.glob("*.bak.*"))
        self.assertEqual(len(bak_files), 1)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT Remarks, Url, Enabled FROM SubItem").fetchall()
        remarks_map = {r[0]: (r[1], r[2]) for r in rows}
        conn.close()

        self.assertIn("custom_local", remarks_map)
        self.assertEqual(remarks_map["custom_local"][1], 1)

        for sub_def in SUBSCRIPTION_DEFINITIONS:
            self.assertIn(sub_def["remarks"], remarks_map)
            url, enabled = remarks_map[sub_def["remarks"]]
            self.assertTrue(url.startswith(f"https://raw.githubusercontent.com/test/repo/{DEFAULT_BRANCH}/subs/"))
            self.assertEqual(enabled, 1)

    def test_sync_idempotence(self) -> None:
        """Running sync twice updates existing items without duplicating them."""
        sync_subscriptions(db_path=self.db_path, repo_slug="test/repo", dry_run=False)
        summary2 = sync_subscriptions(db_path=self.db_path, repo_slug="test/repo", dry_run=False)

        self.assertEqual(summary2["added"], 0)
        self.assertEqual(summary2["updated"], 9)

    def test_disable_redundant_preserves_file_urls(self) -> None:
        """--disable-redundant disables public HTTP feeds while keeping local file:// feeds enabled."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            disable_redundant=True,
            dry_run=False,
        )
        self.assertEqual(summary["disabled"], 2)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT Remarks, Enabled FROM SubItem").fetchall()
        remarks_enabled = {r[0]: r[1] for r in rows}
        conn.close()

        self.assertEqual(remarks_enabled["custom_local"], 1)
        self.assertEqual(remarks_enabled["public_feed_1"], 0)
        self.assertEqual(remarks_enabled["public_feed_2"], 0)

    def test_sync_with_use_local_iran_and_custom_branch(self) -> None:
        """--use-local-iran adds 10 items, points to local_subs/, and respects custom branch."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            branch="feature-branch",
            use_local_iran=True,
            dry_run=False,
        )
        self.assertEqual(summary["added"], 10)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT Remarks, Url, Enabled FROM SubItem").fetchall()
        conn.close()

        iran_rows = [r for r in rows if "Iran Live Verified" in r[0]]
        self.assertEqual(len(iran_rows), 1)
        self.assertIn("local_subs/iran_verified.txt", iran_rows[0][1])
        self.assertEqual(iran_rows[0][2], 1)

        ver_rows = [r for r in rows if "PattN Hub - Verified" in r[0]]
        self.assertEqual(len(ver_rows), 1)
        self.assertTrue(ver_rows[0][1].startswith("https://raw.githubusercontent.com/test/repo/feature-branch/subs/"))


class TestRepoSlugAndCli(unittest.TestCase):
    def test_default_repo_slug(self) -> None:
        """Verify default repo slug is 'mani-moh/pattn' when no env var or remote exists."""
        self.assertEqual(detect_repo_slug(), "mani-moh/pattn")
        self.assertEqual(DEFAULT_REPO_SLUG, "mani-moh/pattn")
        self.assertEqual(cat_detect_repo_slug(), "mani-moh/pattn")

    def test_detect_repo_slug_with_env(self) -> None:
        """Verify GITHUB_REPOSITORY environment variable takes precedence."""
        import os
        old_env = os.environ.get("GITHUB_REPOSITORY")
        try:
            os.environ["GITHUB_REPOSITORY"] = "custom-owner/custom-repo"
            self.assertEqual(detect_repo_slug(), "custom-owner/custom-repo")
            self.assertEqual(cat_detect_repo_slug(), "custom-owner/custom-repo")
        finally:
            if old_env is not None:
                os.environ["GITHUB_REPOSITORY"] = old_env
            else:
                os.environ.pop("GITHUB_REPOSITORY", None)

    def test_parse_repo_slug_from_url(self) -> None:
        from scripts.sync_to_pattn import _parse_repo_slug_from_url
        self.assertEqual(_parse_repo_slug_from_url("https://github.com/user/project.git"), "user/project")
        self.assertEqual(_parse_repo_slug_from_url("git@github.com:user/project.git"), "user/project")
        self.assertEqual(_parse_repo_slug_from_url("https://github.com/user/project"), "user/project")
        self.assertEqual(_parse_repo_slug_from_url("https://github.com/user/project/"), "user/project")
        self.assertEqual(_parse_repo_slug_from_url("https://github.com/user/project.git/"), "user/project")
        self.assertEqual(_parse_repo_slug_from_url("git@github.com:user/project.git/"), "user/project")
        self.assertEqual(_parse_repo_slug_from_url("ssh://git@github.com/user/project.git"), "user/project")
        self.assertIsNone(_parse_repo_slug_from_url("https://gitlab.com/user/project.git"))

    @patch("subprocess.run")
    def test_detect_repo_slug_from_git_remote(self, mock_run) -> None:
        """Verify detect_repo_slug extracts owner/repo from 'git remote get-url origin'."""
        import subprocess
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "git@github.com:ci-owner/ci-repo.git\n"
        mock_run.return_value = mock_proc

        import os
        old_env = os.environ.pop("GITHUB_REPOSITORY", None)
        try:
            self.assertEqual(detect_repo_slug(), "ci-owner/ci-repo")
            self.assertEqual(cat_detect_repo_slug(), "ci-owner/ci-repo")
        finally:
            if old_env is not None:
                os.environ["GITHUB_REPOSITORY"] = old_env

    def test_arg_parser_flags(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--max-test-nodes", "500", "--discover", "--no-healthcheck"])
        self.assertEqual(args.max_test_nodes, 500)
        self.assertTrue(args.discover)
        self.assertFalse(args.healthcheck)


class TestIranReachabilityChecker(unittest.TestCase):
    def test_check_from_iran_ps1_exists(self) -> None:
        ps1 = BASE_DIR / "scripts" / "check_from_iran.ps1"
        self.assertTrue(ps1.is_file())
        txt = ps1.read_text(encoding="utf-8")
        self.assertIn("scripts/test_iran_reachability.py", txt)
        self.assertIn("--sync-pattn-db", txt)
        self.assertIn("--from-github", txt)

    def test_vpn_safety_guard_blocks_when_proxy_active(self) -> None:
        from scripts.test_iran_reachability import detect_active_local_vpn, run_iran_check

        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:10809"}):
            signals = detect_active_local_vpn(ports_to_check=())
            self.assertTrue(any("HTTP_PROXY" in s for s in signals))

        with patch("scripts.test_iran_reachability.detect_active_local_vpn", return_value=["Mock VPN active"]):
            rc = run_iran_check(force=False)
            self.assertEqual(rc, 2)

    @patch("scripts.test_iran_reachability.check_nodes_health")
    @patch("scripts.test_iran_reachability.find_local_xray_binary", return_value="mock_xray.exe")
    @patch("scripts.test_iran_reachability.detect_active_local_vpn", return_value=[])
    def test_run_iran_check_writes_verified_outputs_offline(
        self, _mock_vpn, _mock_xray, mock_hc
    ) -> None:
        from scripts.test_iran_reachability import run_iran_check

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subs_dir = root / "subs"
            subs_dir.mkdir(parents=True)
            (subs_dir / "verified.txt").write_text(
                "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443?security=tls#IranOK\n"
                "trojan://deadpass@5.6.7.8:443?security=tls#BlockedNode\n",
                encoding="utf-8",
            )

            def fake_hc(nodes, **kwargs):
                self.assertTrue(kwargs.get("use_xray"))
                self.assertEqual(kwargs.get("rounds"), 2)
                for n in nodes:
                    if n.address == "1.2.3.4":
                        n.passed_healthcheck = True
                        n.latency_ms = 142.5
                    else:
                        n.passed_healthcheck = False

            mock_hc.side_effect = fake_hc
            rc = run_iran_check(max_nodes=50, rounds=2, sync_pattn_db=False, repo_root=root)
            self.assertEqual(rc, 0)

            out_plain = root / "local_subs" / "iran_verified.txt"
            out_b64 = root / "local_subs" / "iran_verified_base64.txt"
            self.assertTrue(out_plain.is_file())
            self.assertTrue(out_b64.is_file())
            txt = out_plain.read_text(encoding="utf-8")
            self.assertIn("1.2.3.4:443", txt)
            self.assertNotIn("5.6.7.8:443", txt)

    def test_sync_subscriptions_with_use_local_iran(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "guiNDB.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                """
                CREATE TABLE SubItem (
                    Id TEXT PRIMARY KEY, Remarks TEXT, Url TEXT, MoreUrl TEXT,
                    Enabled INTEGER, UserAgent TEXT, RequestHeaders TEXT, Sort INTEGER,
                    Filter TEXT, AutoUpdateInterval INTEGER, UpdateTime INTEGER,
                    ConvertTarget TEXT, PrevProfile TEXT, NextProfile TEXT,
                    PreSocksPort INTEGER, Memo TEXT, CustomCoreType TEXT,
                    OverrideAddress TEXT, OverridePort INTEGER
                )
                """
            )
            conn.commit()
            conn.close()

            summary = sync_subscriptions(
                db_path=db,
                repo_slug="test/repo",
                use_local_iran=True,
                local_repo_root=root,
            )
            self.assertEqual(summary["added"], 10)
            conn = sqlite3.connect(str(db))
            remarks = [r[0] for r in conn.execute("SELECT Remarks FROM SubItem").fetchall()]
            conn.close()
            self.assertIn("🇮🇷 PattN Hub - Iran Live Verified", remarks)


if __name__ == "__main__":
    unittest.main()
