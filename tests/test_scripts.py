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
        self.assertIn("run_discovery:", self.workflow_content)

    def test_workflow_includes_discovery_step_and_masking(self) -> None:
        """Verify update-subs.yml executes discovery before aggregation and masks NVIDIA_API_KEY."""
        self.assertIn("Run Autonomous Discovery Agent", self.workflow_content)
        self.assertIn("NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}", self.workflow_content)
        self.assertIn("::add-mask::$NVIDIA_API_KEY", self.workflow_content)
        self.assertIn("--discover-only", self.workflow_content)
        self.assertIn("sources.discovered.json", self.workflow_content)
        self.assertIn("DISCOVERY.md", self.workflow_content)

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

    def test_daily_extended_discovery_workflow(self) -> None:
        """Verify daily-deep-discovery.yml is scheduled daily, runs 500 API requests, and masks secrets."""
        daily_path = BASE_DIR / ".github" / "workflows" / "daily-deep-discovery.yml"
        self.assertTrue(daily_path.exists(), "daily-deep-discovery.yml must exist")
        content = daily_path.read_text(encoding="utf-8")
        self.assertIn('cron: "0 2 * * *"', content)
        self.assertIn('default: "500"', content)
        self.assertIn("--discovery-api-requests", content)
        self.assertIn("::add-mask::$NVIDIA_API_KEY", content)
        self.assertIn("Dynamic privacy & security audit passed", content)

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
        self.assertIn("NVIDIA_API_KEY=nvapi-your-api-key-here", env_ex)


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
        ver_lines = [
            l for l in (BASE_DIR / "subs" / "verified.txt").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#") and "://" in l
        ]
        self.assertGreater(len(verified), 50)
        self.assertEqual(len(verified), len(ver_lines))
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


class TestCiAndPrivacyHardening(unittest.TestCase):
    def test_sources_private_json_exists_and_ignored_passes_when_file_absent(self) -> None:
        """Verify test_sources_private_json_exists_and_ignored passes in clean CI checkout without sources.private.json."""
        from tests.test_core import TestSourcesRegistry

        test_case = TestSourcesRegistry()
        with patch.object(Path, "exists", return_value=False):
            # Must pass without raising AssertionError even when file does not exist
            test_case.test_sources_private_json_exists_and_ignored()

    def test_sources_private_json_validates_sources_when_present(self) -> None:
        """Verify test_sources_private_json_exists_and_ignored still enforces 6 sources if file exists."""
        from tests.test_core import TestSourcesRegistry

        test_case = TestSourcesRegistry()
        bad_json = json.dumps({"sources": [{"id": "s1"}]})

        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value=bad_json):
                with self.assertRaises(AssertionError):
                    test_case.test_sources_private_json_exists_and_ignored()

    def test_secret_gist_masked_in_github_actions_ci(self) -> None:
        """Verify Secret Gist ID & URL are masked and not printed in cleartext when GITHUB_ACTIONS=true."""
        import os
        from src.private_gist import log_gist_result

        priv_results = {
            "gist_id": "secret_gist_id_12345",
            "gist_url": "https://gist.github.com/secret_gist_id_12345",
        }

        printed_lines: list[str] = []
        old_env = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            log_gist_result(priv_results, safe_print_fn=printed_lines.append)

            # Verification of masking commands and masked log
            self.assertIn("::add-mask::secret_gist_id_12345", printed_lines)
            self.assertIn("::add-mask::https://gist.github.com/secret_gist_id_12345", printed_lines)
            self.assertIn(
                "    Secret Gist updated successfully (URL masked in public CI logs)",
                printed_lines,
            )
            # Must NOT print the unmasked "Secret Gist updated: ..." log
            for line in printed_lines:
                self.assertNotIn("Secret Gist updated: https://", line)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)

    def test_secret_gist_unmasked_outside_github_actions(self) -> None:
        """Verify Secret Gist URL is logged normally when GITHUB_ACTIONS is not set."""
        import os
        from src.private_gist import log_gist_result

        priv_results = {
            "gist_id": "secret_gist_id_12345",
            "gist_url": "https://gist.github.com/secret_gist_id_12345",
        }

        printed_lines: list[str] = []
        old_env = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ.pop("GITHUB_ACTIONS", None)
            log_gist_result(priv_results, safe_print_fn=printed_lines.append)

            self.assertIn(
                "    Secret Gist updated: https://gist.github.com/secret_gist_id_12345",
                printed_lines,
            )
            # Must NOT emit CI add-mask commands
            for line in printed_lines:
                self.assertNotIn("::add-mask::", line)
                self.assertNotIn("URL masked in public CI logs", line)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env

    def test_secret_gist_masking_edge_case_only_id(self) -> None:
        """Verify masking works when gist_url is missing or equal to gist_id."""
        import os
        from src.private_gist import log_gist_result

        priv_results = {"gist_id": "only_id_9999"}
        printed_lines: list[str] = []
        old_env = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            log_gist_result(priv_results, safe_print_fn=printed_lines.append)

            self.assertEqual(printed_lines[0], "::add-mask::only_id_9999")
            self.assertEqual(
                printed_lines[1],
                "    Secret Gist updated successfully (URL masked in public CI logs)",
            )
            self.assertEqual(len(printed_lines), 2)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)

    def test_main_include_private_masks_in_github_actions(self) -> None:
        """Verify src.main with --include-private masks Secret Gist URL in CI."""
        import os
        from src.main import main

        old_env = os.environ.get("GITHUB_ACTIONS")
        captured_lines: list[str] = []

        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            with patch("src.main.safe_print", side_effect=captured_lines.append):
                with patch("src.main.load_sources", return_value=[]):
                    with patch("src.main.fetch_all_sources", return_value=([], [], [])):
                        with patch("src.main.deduplicate_nodes", return_value=[]):
                            with patch("src.main.publish_subscriptions", return_value={}):
                                with patch(
                                    "src.main.publish_private_subs",
                                    return_value={
                                        "gist_id": "main_gist_ci_111",
                                        "gist_url": "https://gist.github.com/main_gist_ci_111",
                                        "bpb_only": "p1",
                                        "private_verified": "p2",
                                    },
                                ):
                                    rc = main([
                                        "--include-private",
                                        "--no-healthcheck",
                                        "--no-pattn-variants",
                                        "--no-docs",
                                    ])
                                    self.assertEqual(rc, 0)

            self.assertIn("::add-mask::main_gist_ci_111", captured_lines)
            self.assertIn("::add-mask::https://gist.github.com/main_gist_ci_111", captured_lines)
            self.assertIn(
                "    Secret Gist updated successfully (URL masked in public CI logs)",
                captured_lines,
            )
            for line in captured_lines:
                self.assertNotIn("Secret Gist updated: https://", line)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)

    def test_run_private_sync_masks_in_github_actions(self) -> None:
        """Verify run_private_sync and --private-only mask Secret Gist URL in CI."""
        import os
        from src.private_gist import run_private_sync

        old_env = os.environ.get("GITHUB_ACTIONS")
        captured: list[str] = []

        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            with patch("src.private_gist.load_sources", return_value=[]):
                with patch("src.private_gist.fetch_all_sources", return_value=([], [], [])):
                    with patch("src.private_gist.deduplicate_nodes", return_value=[]):
                        with patch("src.private_gist.load_public_subs_from_disk", return_value=[]):
                            with patch(
                                "src.private_gist.publish_private_subs",
                                return_value={
                                    "gist_id": "sync_gist_ci_222",
                                    "gist_url": "https://gist.github.com/sync_gist_ci_222",
                                    "bpb_only": "p1",
                                    "private_verified": "p2",
                                },
                            ):
                                results = run_private_sync(
                                    safe_print_fn=captured.append,
                                    pattn_variants=False,
                                )
                                self.assertEqual(results["gist_id"], "sync_gist_ci_222")

            self.assertIn("::add-mask::sync_gist_ci_222", captured)
            self.assertIn("::add-mask::https://gist.github.com/sync_gist_ci_222", captured)
            self.assertIn(
                "    Secret Gist updated successfully (URL masked in public CI logs)",
                captured,
            )
            for line in captured:
                self.assertNotIn("Secret Gist updated: https://", line)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)

    def test_main_private_only_masks_in_github_actions(self) -> None:
        """Verify src.main with --private-only (the exact command run in update-subs.yml)
        masks Secret Gist URL in CI."""
        from src.main import main

        old_env = os.environ.get("GITHUB_ACTIONS")
        captured_lines: list[str] = []

        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            with patch("src.main.safe_print", side_effect=captured_lines.append):
                with patch("src.private_gist.load_sources", return_value=[]):
                    with patch("src.private_gist.fetch_all_sources", return_value=([], [], [])):
                        with patch("src.private_gist.deduplicate_nodes", return_value=[]):
                            with patch("src.private_gist.load_public_subs_from_disk", return_value=[]):
                                with patch(
                                    "src.private_gist.publish_private_subs",
                                    return_value={
                                        "gist_id": "priv_only_gist_333",
                                        "gist_url": "https://gist.github.com/priv_only_gist_333",
                                        "bpb_only": "p1",
                                        "private_verified": "p2",
                                    },
                                ):
                                    rc = main(["--private-only", "--no-pattn-variants"])
                                    self.assertEqual(rc, 0)

            self.assertIn("::add-mask::priv_only_gist_333", captured_lines)
            self.assertIn("::add-mask::https://gist.github.com/priv_only_gist_333", captured_lines)
            self.assertIn(
                "    Secret Gist updated successfully (URL masked in public CI logs)",
                captured_lines,
            )
            for line in captured_lines:
                self.assertNotIn("Secret Gist updated: https://", line)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)

    def test_secret_gist_masks_raw_urls_in_github_actions(self) -> None:
        """Verify that raw Gist file URLs in priv_results are also masked in CI."""
        from src.private_gist import log_gist_result

        priv_results = {
            "gist_id": "raw_gist_444",
            "gist_url": "https://gist.github.com/raw_gist_444",
            "gist_raw_bpb_only": "https://gist.githubusercontent.com/raw/bpb_only.txt",
            "gist_raw_private_verified": "https://gist.githubusercontent.com/raw/pv.txt",
        }
        captured: list[str] = []
        old_env = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            log_gist_result(priv_results, safe_print_fn=captured.append)

            self.assertIn("::add-mask::raw_gist_444", captured)
            self.assertIn("::add-mask::https://gist.github.com/raw_gist_444", captured)
            self.assertIn(
                "::add-mask::https://gist.githubusercontent.com/raw/bpb_only.txt",
                captured,
            )
            self.assertIn(
                "::add-mask::https://gist.githubusercontent.com/raw/pv.txt",
                captured,
            )
            self.assertIn(
                "    Secret Gist updated successfully (URL masked in public CI logs)",
                captured,
            )
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)

    def test_secret_gist_masking_none_or_empty(self) -> None:
        """Verify log_gist_result handles None or empty results gracefully."""
        from src.private_gist import log_gist_result

        captured: list[str] = []
        log_gist_result(None, safe_print_fn=captured.append)
        self.assertEqual(len(captured), 0)

        log_gist_result({}, safe_print_fn=captured.append)
        self.assertEqual(len(captured), 0)

        log_gist_result({"gist_id": "", "gist_url": ""}, safe_print_fn=captured.append)
        self.assertEqual(len(captured), 0)

    def test_secret_gist_masking_edge_case_only_url(self) -> None:
        """Verify masking works when only gist_url is provided without gist_id."""
        from src.private_gist import log_gist_result

        priv_results = {"gist_url": "https://gist.github.com/user/only_url_8888"}
        captured: list[str] = []
        old_env = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ["GITHUB_ACTIONS"] = "true"
            log_gist_result(priv_results, safe_print_fn=captured.append)

            self.assertIn("::add-mask::https://gist.github.com/user/only_url_8888", captured)
            self.assertIn(
                "    Secret Gist updated successfully (URL masked in public CI logs)",
                captured,
            )
            self.assertEqual(len(captured), 2)
        finally:
            if old_env is not None:
                os.environ["GITHUB_ACTIONS"] = old_env
            else:
                os.environ.pop("GITHUB_ACTIONS", None)


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

            out_plain = root / "private_subs" / "iran_verified.txt"
            out_b64 = root / "private_subs" / "iran_verified_base64.txt"
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
