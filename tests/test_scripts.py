from __future__ import annotations

import json
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
    PROTECTED_BPB_REMARKS,
    SUBSCRIPTION_DEFINITIONS,
    detect_repo_slug,
    generate_sub_id,
    sync_subscriptions,
)
from src.categorizer import detect_repo_slug as cat_detect_repo_slug
from src.main import build_arg_parser
from src.private_gist import load_public_subs_from_disk

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

    def test_workflow_uses_private_only_for_gist(self) -> None:
        """Verify that Secret Gist step uses --private-only without re-fetching public feeds."""
        self.assertIn("python -m src.main --private-only", self.workflow_content)
        self.assertNotIn("python -m src.main --include-private --no-healthcheck --no-docs", self.workflow_content)


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
            self.assertTrue(url.startswith(f"https://raw.githubusercontent.com/test/repo/{DEFAULT_BRANCH}/subs/"))
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

    def test_sync_with_use_local_private_and_custom_branch(self) -> None:
        """--use-local-private adds 10 items and respects custom branch."""
        summary = sync_subscriptions(
            db_path=self.db_path,
            repo_slug="test/repo",
            branch="feature-branch",
            use_local_private=True,
            dry_run=False,
        )
        self.assertEqual(summary["added"], 10)

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT Remarks, Url, Enabled FROM SubItem").fetchall()
        remarks_map = {r[0]: (r[1], r[2]) for r in rows}
        conn.close()

        # Check private BPB group exists
        priv_remark = "🔒 PattN Hub - Private BPB + Verified"
        self.assertIn(priv_remark, remarks_map)
        self.assertEqual(remarks_map[priv_remark][1], 1)

        # Check raw URLs used the custom branch
        ver_url, _ = remarks_map["⭐ PattN Hub - Verified"]
        self.assertTrue(ver_url.startswith("https://raw.githubusercontent.com/test/repo/feature-branch/subs/"))


class TestRepoSlugAndPrivateOnly(unittest.TestCase):
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

    def test_arg_parser_has_private_only_and_sources(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--private-only", "--private-sources", "custom.json"])
        self.assertTrue(args.private_only)
        self.assertEqual(args.private_sources, "custom.json")

    def test_load_public_subs_from_disk(self) -> None:
        """Verify loading existing subs/ retains verified health status."""
        nodes = load_public_subs_from_disk(subs_dir=BASE_DIR / "subs")
        self.assertGreater(len(nodes), 100)
        verified = [n for n in nodes if n.passed_healthcheck]
        self.assertEqual(len(verified), 237)
        for n in verified:
            self.assertFalse(n.is_private)
            self.assertTrue(n.passed_healthcheck)

    def test_load_public_subs_preserves_distinct_sni_paths(self) -> None:
        """Verify load_public_subs_from_disk does not drop distinct endpoints on same IP:port:cred."""
        import tempfile
        temp_dir = tempfile.TemporaryDirectory()
        td = Path(temp_dir.name)
        ver_file = td / "verified.txt"
        all_file = td / "all.txt"

        ver_file.write_text(
            "# header\n"
            "vless://uuid-1@1.2.3.4:443?sni=sni1.com&type=tcp#[✅] [🇩🇪 DE] VLESS-Reality | 50ms\n",
            encoding="utf-8",
        )
        all_file.write_text(
            "# header\n"
            "vless://uuid-1@1.2.3.4:443?sni=sni1.com&type=tcp#Unverified 1\n"
            "vless://uuid-1@1.2.3.4:443?sni=sni2.com&type=tcp#Unverified 2\n",
            encoding="utf-8",
        )

        merged = load_public_subs_from_disk(subs_dir=td)
        # Should contain verified node (sni1) + unverified node (sni2), NOT collapsed into 1 node!
        self.assertEqual(len(merged), 2)
        v1 = [n for n in merged if n.params.get("sni") == "sni1.com"][0]
        v2 = [n for n in merged if n.params.get("sni") == "sni2.com"][0]
        self.assertTrue(v1.passed_healthcheck)
        self.assertFalse(v2.passed_healthcheck)
        temp_dir.cleanup()

    def test_dynamic_privacy_audit_zero_leaks(self) -> None:
        """Dynamic privacy audit: ensures private tokens from sources.private.json
        never leak into git tracked files or git history."""
        priv_path = BASE_DIR / "sources.private.json"
        if not priv_path.exists():
            return
        import subprocess
        import urllib.parse
        data = json.loads(priv_path.read_text(encoding="utf-8"))
        private_identifiers = set()
        for s in data.get("sources", []):
            u = s.get("url", "")
            p = urllib.parse.urlparse(u)
            if p.netloc:
                host = p.netloc.split(":")[0]
                private_identifiers.add(host)
                prefix = host.split(".")[0]
                if prefix not in ("workers", "dev", "api", "sub"):
                    private_identifiers.add(prefix)
            if p.path:
                for seg in p.path.strip("/").split("/"):
                    if len(seg) >= 8 and seg not in ("sub", "subscription", "fragment", "warp-pro", "normal"):
                        private_identifiers.add(seg)

        # Check git ls-files
        res = subprocess.run(["git", "ls-files"], cwd=str(BASE_DIR), capture_output=True, text=True, check=True)
        tracked = [f.strip() for f in res.stdout.splitlines() if f.strip()]
        for f in tracked:
            txt = (BASE_DIR / f).read_text(encoding="utf-8", errors="ignore")
            for ident in private_identifiers:
                self.assertNotIn(ident, txt, f"Private token '{ident[:4]}***' leaked in tracked file '{f}'!")

        # Check git log
        res_log = subprocess.run(["git", "log", "-p"], cwd=str(BASE_DIR), capture_output=True, check=True)
        for ident in private_identifiers:
            self.assertNotIn(ident.encode("utf-8"), res_log.stdout, f"Private token '{ident[:4]}***' leaked in git commit log!")


if __name__ == "__main__":
    unittest.main()
