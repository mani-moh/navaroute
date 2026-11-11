"""Offline publication boundaries: fake Git transport, real guards and retry loop."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/public_git.py"


class TestPublicGit(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "shared tracked/staged privacy and non-forced writer guard missing")
        spec = importlib.util.spec_from_file_location("public_git", SCRIPT)
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)
        self.calls = []
        self.timeouts = []

    def git(self, files, staged=(), failures=None):
        failures = failures or {}
        def run(argv, **kwargs):
            self.calls.append(argv)
            self.timeouts.append(kwargs.get("timeout"))
            command = argv[1:]
            code = failures.get(command[0], 0)
            if isinstance(code, subprocess.SubprocessError):
                raise code
            output = b""
            if command[0] == "ls-files":
                output = b"".join(f"100644 {'a'*40} 0\t{p}\0".encode() for p in files)
            elif command[0] == "diff":
                output = b"".join((p + "\0").encode() for p in staged)
            elif command[0] == "show":
                output = files[command[1][1:]]
            elif command[0] == "cat-file":
                output = str(len(files[command[2][1:]])).encode()
            return subprocess.CompletedProcess(argv, code, output, b"private-error-sentinel")
        return run

    def check(self, files, staged=(), failures=None):
        with patch.object(self.guard.subprocess, "run", side_effect=self.git(files, staged, failures)), \
             patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.guard.main(["check", "--staged"])
        self.assertNotIn("private-error-sentinel", output.getvalue())
        return result, output.getvalue()

    def test_clean_tracked_private_roles_rejected_before_any_content_read(self):
        for path in ("local_subs/a.txt", "private_subs/a.txt", "sources.private.json", ".env", ".env.production", "nested/.env.test",
                     "nested/guiNDB.db", "a.sqlite-wal", "__pycache__/a.pyc", "data/http_cache/a", "data/unknown.json"):
            with self.subTest(path=path):
                self.calls.clear()
                self.assertEqual(self.check({path: b"must-not-read"})[0], 1)
                self.assertFalse(any(c[1] in ("cat-file", "show") for c in self.calls))

    def test_tracked_backup_sqlite_and_recovery_roles_rejected_before_size_or_body(self):
        for path in ("backup.bak", "nested/backup.BAK", "a.sqlitebackup", "nested/a.SQLiteBackup", "nested/a.sqlite3-shm",
                     "nested/backup.bak/old/feed/all.txt", "nested/a.sqlitebackup/old/feed/all.txt",
                     ".subs.navaroute-publish/old/feed/all.txt", "nested/.custom.navaroute-publish/old/feed/all.txt",
                     "nested/.output.navaroute-recovery/old/feed/all.txt"):
            with self.subTest(path=path):
                self.calls.clear()
                result, _ = self.check({path: b"must-not-read"})
                self.assertFalse(any(c[1] in ("cat-file", "show") for c in self.calls))
                self.assertEqual(result, 1)

    def test_public_roles_and_untracked_private_sentinel_are_not_worktree_reads(self):
        files = {"README.md": b"public", ".env.example": b"placeholder", "data/discovery_state.json": b"{}", "subs/all.txt": b"public",
                 "example.egg-info/PKG-INFO": b"nonprivate ignored-role metadata"}
        with patch.object(Path, "read_bytes", side_effect=AssertionError("never read local files")):
            self.assertEqual(self.check(files, ["subs/all.txt", "data/discovery_state.json"])[0], 0)

    def test_final_staging_rejects_unowned_outputs_and_code_even_if_not_private(self):
        for path in ("src/main.py", "docs/ARCHITECTURE.md", "subs/iran-tested.txt", "subs/other.txt", "subs/countries/../../secret.txt", "iran-results/evidence.json"):
            with self.subTest(path=path):
                self.assertEqual(self.check({"README.md": b"ok"}, [path])[0], 1)

    def test_staged_rename_cannot_hide_forbidden_code_deletion(self):
        run = self.git({"subs/all.txt": b"public"})
        def rename_aware(argv, **kwargs):
            if argv[1] == "diff":
                self.calls.append(argv)
                paths = ("src/obsolete.py", "subs/all.txt") if "--no-renames" in argv else ("subs/all.txt",)
                return subprocess.CompletedProcess(argv, 0, b"".join((p + "\0").encode() for p in paths), b"private-error-sentinel")
            return run(argv, **kwargs)
        with patch.object(self.guard.subprocess, "run", side_effect=rename_aware), \
             patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.guard.main(["check", "--staged"]), 1)
        self.assertFalse(any(c[1] in ("cat-file", "show") for c in self.calls))
        self.assertNotIn("private-error-sentinel", output.getvalue())

    def test_final_staging_allows_only_exact_public_roles_including_deletions(self):
        paths = ["subs/all.txt", "subs/all_base64.txt", "subs/reachable.txt", "subs/categories/navaroute-special.txt", "subs/categories/pattn-special_base64.txt",
                 "subs/categories/serverless-fragment.json", "subs/protocols/vmess.txt", "subs/countries/de.txt", "README.md", "STATUS.md", "DISCOVERY.md",
                 "sources.discovered.json", "data/discovery_state.json", "data/discovered_ocr_configs.txt"]
        self.assertEqual(self.check({"README.md": b"ok"}, paths)[0], 0)

    def test_tokens_in_tracked_or_new_staged_blob_fail_without_matching_lines(self):
        for token in ("nvapi-" + "A"*12, "ghp_" + "B"*22, "github_pat_" + "C"*24, "AKIA" + "D"*16):
            for staged in ((), ["subs/all.txt"]):
                with self.subTest(kind=token.split("_")[0][:5], staged=bool(staged)):
                    result, output = self.check({"subs/all.txt": ("secret="+token).encode()}, staged)
                    self.assertEqual(result, 1)
                    self.assertNotIn(token, output)
                    self.assertNotIn("secret=", output)

    def test_environment_secret_is_checked_without_printing_or_command_arguments(self):
        secret = "synthetic-provider-secret-unique"
        with patch.object(self.guard.subprocess, "run", side_effect=self.git({"subs/all.txt": secret.encode()})), \
             patch.dict(os.environ, {"NVIDIA_API_KEY": secret}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.guard.main(["check"]), 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(secret, repr(self.calls))

    def test_git_scan_failure_is_not_success(self):
        for command in ("ls-files", "diff", "show"):
            self.assertEqual(self.check({"README.md": b"ok"}, failures={command: 128})[0], 1)

    def test_metadata_commands_receive_finite_timeouts(self):
        self.assertEqual(self.check({"README.md": b"ok"})[0], 0)
        self.assertEqual([c[1] for c in self.calls], ["ls-files", "diff", "cat-file", "show"])
        self.assertEqual(self.timeouts, [120] * 4)

    def test_timed_out_metadata_fails_quietly_before_blob_calls(self):
        failure = subprocess.TimeoutExpired(["git", "ls-files"], 120, output=b"private-error-sentinel", stderr=b"private-error-sentinel")
        self.assertEqual(self.check({"README.md": b"ok"}, failures={"ls-files": failure})[0], 1)
        self.assertEqual([c[1] for c in self.calls], ["ls-files"])
        self.assertEqual(self.timeouts, [120])

    def test_symlink_index_entry_cannot_hide_private_content(self):
        def run(argv, **kwargs):
            self.assertEqual(argv[1], "ls-files")
            return subprocess.CompletedProcess(argv, 0, f"120000 {'a'*40} 0\tsubs/all.txt\0".encode(), b"")
        with patch.object(self.guard.subprocess, "run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.guard.main(["check"]), 1)

    def push(self, pull=0, pushes=(0,), timeout=None):
        outcomes = iter(pushes)
        def run(argv, **kwargs):
            self.calls.append(argv)
            self.timeouts.append(kwargs.get("timeout"))
            if argv[1] == timeout:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"), output=b"private-error-sentinel", stderr=b"synthetic-token")
            if argv[1] == "pull":
                code = pull
            elif argv[1] == "push":
                code = next(outcomes)
            else:
                self.fail("unexpected writer operation")
            return subprocess.CompletedProcess(argv, code, b"human-commit", b"private-error-sentinel")
        with patch.object(self.guard.subprocess, "run", side_effect=run), \
             patch.dict(os.environ, {"GITHUB_TOKEN": "synthetic-token"}, clear=True), \
             patch.object(self.guard.time, "sleep"), contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.guard.main(["push"])
        self.assertNotIn("private-error-sentinel", output.getvalue())
        self.assertNotIn("synthetic-token", output.getvalue())
        self.assertTrue(all(c in (["git", "pull", "--rebase", "origin", "master"], ["git", "push", "origin", "HEAD:master"]) for c in self.calls))
        return result

    def test_rebase_failure_stops_before_push_and_preserves_conflict(self):
        self.assertEqual(self.push(pull=1), 1)
        self.assertEqual(len(self.calls), 1)

    def test_pull_and_push_receive_finite_timeouts_on_each_retry(self):
        self.assertEqual(self.push(pushes=(1, 0)), 0)
        self.assertEqual(self.timeouts, [120] * 4)

    def test_timed_out_pull_stops_before_push_without_clearing_commits(self):
        self.assertEqual(self.push(timeout="pull"), 1)
        self.assertEqual([c[1] for c in self.calls], ["pull"])
        self.assertEqual(self.timeouts, [120])

    def test_timed_out_push_fails_without_clearing_commits_or_secret_output(self):
        self.assertEqual(self.push(timeout="push"), 1)
        self.assertEqual([c[1] for c in self.calls], ["pull", "push"])
        self.assertEqual(self.timeouts, [120, 120])

    def test_push_exhaustion_returns_nonzero_after_five_nonforced_attempts(self):
        self.assertEqual(self.push(pushes=(1,)*5), 1)
        self.assertEqual(len(self.calls), 10)

    def test_concurrent_human_commit_is_rebased_before_each_nonforced_retry(self):
        self.assertEqual(self.push(pushes=(1, 0)), 0)
        self.assertEqual([c[1] for c in self.calls], ["pull", "push", "pull", "push"])


class TestWorkflowContracts(unittest.TestCase):
    def test_generated_empty_feed_header_separator_is_valid_whitespace_only_for_subs(self):
        result = subprocess.run(["git", "check-attr", "whitespace", "--", "subs/top100.txt", "subs/reachable.txt", "README.md"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        attributes = dict((path, value) for path, _, value in (line.split(": ", 2) for line in result.stdout.splitlines()))
        self.assertEqual(attributes["subs/top100.txt"], "-blank-at-eof")
        self.assertEqual(attributes["subs/reachable.txt"], "-blank-at-eof")
        self.assertEqual(attributes["README.md"], "unspecified")

    def test_unrestricted_push_and_pr_security_gate(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("paths:", text)
        self.assertNotIn("branches:", text)
        self.assertIn("python scripts/public_git.py check", text)
        self.assertIn('fetch-depth: 1', text)
        self.assertIn('persist-credentials: false', text)

    def test_scheduled_writers_share_gates_and_fail_closed_on_master(self):
        for name, cron in (("update-subs.yml", '0 */6 * * *'), ("daily-deep-discovery.yml", '0 2 * * *')):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn(f'cron: "{cron}"', text)
                self.assertIn("group: navaroute-git-writer", text)
                self.assertIn("github.ref == 'refs/heads/master'", text)
                self.assertIn("python scripts/public_git.py check --staged", text)
                self.assertLess(text.index("check --staged"), text.index("git commit"))
                self.assertIn("python scripts/public_git.py push", text)
                self.assertIn("git add -- subs/ README.md STATUS.md sources.discovered.json DISCOVERY.md data/discovery_state.json", text)
                for unsafe in ("git add data/", "git add .", "|| true", "|| echo", "[skip ci]", "--pattn-variants", "generate_pattn_variants", "Download Xray", "client", "pattn-git-writer"):
                    self.assertNotIn(unsafe, text)

    def test_retained_actions_are_exact_verified_pins_and_tokens_are_minimal(self):
        for file in (ROOT / ".github/workflows").glob("*.yml"):
            text = file.read_text(encoding="utf-8")
            self.assertIn("actions/checkout@11d5960a326750d5838078e36cf38b85af677262", text)
            self.assertIn("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065", text)
            self.assertIn("persist-credentials: false", text)
            self.assertNotIn("pull-requests: write", text)
            self.assertNotIn("write-all", text)


if __name__ == "__main__":
    unittest.main()
