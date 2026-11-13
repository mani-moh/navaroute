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

    def test_case_varied_data_roles_reject_clean_and_unchanged_final_inventory(self):
        for path in ("DATA/discovery_checkpoint.json", "Data/credentials.json", "DATA/discovery_state.json"):
            for staged in (False, True):
                with self.subTest(path=path, staged=staged):
                    self.calls.clear()
                    with patch.object(self.guard.subprocess, "run", side_effect=self.git({path: b"must-not-read"}, ["subs/all.txt"])), contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(self.guard.main(["check"] + (["--staged"] if staged else [])), 1)
                    self.assertEqual([call[1] for call in self.calls], ["ls-files"])

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
        for token in ("nvapi-" + "A"*12, "sk-or-v1-" + "A"*12, "ghp_" + "B"*22, "github_pat_" + "C"*24, "AKIA" + "D"*16):
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
        self.assertIn('fetch-depth: 2', text)
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


class TestWorkflowSummaryContracts(unittest.TestCase):
    WORKFLOWS = ("ci.yml", "update-subs.yml", "daily-deep-discovery.yml")
    ALLOWED_ACTIONS = (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    )

    def texts(self):
        return {name: (ROOT / ".github/workflows" / name).read_text(encoding="utf-8") for name in self.WORKFLOWS}

    def test_every_job_ends_with_always_summary_and_captured_baseline(self):
        for name, text in self.texts().items():
            with self.subTest(workflow=name):
                self.assertIn("RUN_START_SHA=$(git rev-parse HEAD)", text)
                summary_step = text[text.rindex("- name: Workflow run summary"):]
                self.assertIn("if: always()", summary_step)
                self.assertIn("shell: bash", summary_step)
                self.assertIn("RUN_STATUS: ${{ job.status }}", summary_step)
                self.assertIn("scripts/workflow_summary.py", summary_step)
                self.assertIn("GITHUB_STEP_SUMMARY", summary_step)
                self.assertIn("no repository details were collected", summary_step)
                self.assertNotIn("- name:", summary_step[summary_step.index("run: |") + 6:],
                                 "summary step must be the final step of the job")

    def test_capture_step_follows_checkout_and_precedes_summary(self):
        for name, text in self.texts().items():
            with self.subTest(workflow=name):
                capture = text.index("RUN_START_SHA=$(git rev-parse HEAD)")
                checkout = text.index("uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262")
                summary = text.rindex("scripts/workflow_summary.py")
                self.assertGreater(capture, checkout)
                self.assertGreater(summary, capture)

    def test_capture_and_summary_steps_run_under_bash(self):
        for name, text in self.texts().items():
            with self.subTest(workflow=name):
                self.assertIn("shell: bash", text.split("- name: Capture run start SHA")[1].split("- name:")[0])
                summary_step = text[text.rindex("- name: Workflow run summary"):]
                self.assertIn("shell: bash", summary_step)

    def test_checkout_or_setup_failure_gets_self_contained_shell_fallback(self):
        for name, text in self.texts().items():
            with self.subTest(workflow=name):
                summary_step = text[text.rindex("- name: Workflow run summary"):]
                self.assertIn("command -v python3", summary_step)
                self.assertIn("[ -f scripts/workflow_summary.py ]", summary_step)
                self.assertIn("print_fallback", summary_step)
                self.assertIn("tee -a \"$GITHUB_STEP_SUMMARY\"", summary_step)

    def test_only_ci_uses_fetch_depth_two_and_writers_stay_at_zero(self):
        for name, text in self.texts().items():
            with self.subTest(workflow=name):
                if name == "ci.yml":
                    self.assertEqual(text.count("fetch-depth:"), 1)
                    self.assertIn("fetch-depth: 2", text)
                else:
                    self.assertNotIn("fetch-depth: 2", text)
                    self.assertIn("fetch-depth: 0", text)

    def test_ci_keeps_two_fetch_depth_and_no_triggers_or_permissions_added(self):
        text = self.texts()["ci.yml"]
        self.assertIn('fetch-depth: 2', text)
        self.assertNotIn("paths:", text)
        self.assertNotIn("branches:", text)
        self.assertNotIn("cron:", text)
        self.assertIn("contents: read", text)
        self.assertIn("not-applicable", text)

    def test_writers_keep_full_history_cron_concurrency_master_only_and_record_publication(self):
        for name, cron in (("update-subs.yml", "0 */6 * * *"), ("daily-deep-discovery.yml", "0 2 * * *")):
            text = self.texts()[name]
            with self.subTest(workflow=name):
                self.assertIn(f'cron: "{cron}"', text)
                self.assertIn("fetch-depth: 0", text)
                self.assertIn("group: navaroute-git-writer", text)
                self.assertIn("github.ref == 'refs/heads/master'", text)
                self.assertIn("RUN_PUBLICATION=pushed", text)
                self.assertIn("RUN_PUBLICATION=no-changes", text)
                self.assertIn("RUN_PUBLICATION=failed", text)

    def test_no_new_actions_secrets_or_boundaries_are_introduced(self):
        for name, text in self.texts().items():
            with self.subTest(workflow=name):
                uses = [line.split("uses:", 1)[1].strip() for line in text.splitlines() if "uses:" in line]
                self.assertTrue(uses, "expected the two pinned actions to remain")
                for action in uses:
                    bare = action.split(" #", 1)[0]
                    self.assertIn(bare, self.ALLOWED_ACTIONS)
                self.assertNotIn("Iran", text)
                self.assertNotIn("iran", text)
                self.assertNotIn("migration", text.lower())
                for line in text.splitlines():
                    if "secrets." in line:
                        self.assertRegex(line, r"secrets\.(GITHUB_TOKEN|NVIDIA_API_KEY|OPENROUTER_API_KEY)\b")


if __name__ == "__main__":
    unittest.main()
