"""Offline contracts for the shared privacy-safe workflow run summary.

Real Git in an isolated temporary fixture repository; no network, no mocks of Git.
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "workflow_summary.py"


def git(repo, *args):
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.com",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.com",
               GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=master", *args],
                          cwd=repo, capture_output=True, env=env, timeout=60)


def commit_all(repo, message="fixture"):
    git(repo, "add", "--", ".")
    result = git(repo, "commit", "-q", "-m", message)
    assert result.returncode == 0, result.stderr


def head(repo):
    return git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def write_file(repo, rel, content):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def run_summary(repo, **overrides):
    env = dict(os.environ)
    for key in ("RUN_START_SHA", "RUN_STATUS", "RUN_PUBLICATION", "GITHUB_STEP_SUMMARY"):
        env.pop(key, None)
    env.update({k: v for k, v in overrides.items() if v is not None})
    previous = os.getcwd()
    os.chdir(repo)
    try:
        spec = importlib.util.spec_from_file_location("workflow_summary", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            code = module.main()
    finally:
        os.chdir(previous)
    return code, out.getvalue()


class WorkflowSummaryFixture(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "shared workflow summary script missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.assertEqual(git(self.repo, "init", "-q").returncode, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def commit_repo(self):
        write_file(self.repo, "subs/all.txt", b"line1\nline2\n")
        write_file(self.repo, "README.md", b"hello\n")
        commit_all(self.repo)
        return head(self.repo)

    def summary_file(self):
        return Path(self._tmp.name) / "summary.md"

    def test_script_exists_and_writes_stdout_and_step_summary(self):
        start = self.commit_repo()
        target = self.summary_file()
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success",
                                RUN_PUBLICATION="not-applicable", GITHUB_STEP_SUMMARY=str(target))
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file(), "GITHUB_STEP_SUMMARY was not written")
        self.assertEqual(target.read_text(encoding="utf-8"), out)

    def test_no_changes_success_reports_identities_and_zero_deltas(self):
        start = self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success", RUN_PUBLICATION="not-applicable")
        self.assertEqual(code, 0)
        self.assertIn(f"Checkout SHA: {start}", out)
        self.assertIn(f"Final HEAD SHA: {start}", out)
        self.assertIn("Job result: success", out)
        self.assertIn("Publication outcome: not-applicable", out)
        self.assertIn("Commits since checkout (0):", out)
        self.assertIn("Files changed since checkout:\n- none", out)
        self.assertIn("Tracked working-tree changes:\n- none", out)
        self.assertIn("New untracked paths:\n- none", out)

    def test_committed_changes_since_checkout_show_counts(self):
        start = self.commit_repo()
        write_file(self.repo, "subs/all.txt", b"line1\nline2\nline3\n")
        commit_all(self.repo, "bot")
        head_now = head(self.repo)
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success", RUN_PUBLICATION="pushed")
        self.assertEqual(code, 0)
        self.assertIn("Commits since checkout (1):", out)
        self.assertIn(f"Final HEAD SHA: {head_now}", out)
        self.assertIn("- `subs/all.txt` (+1/-0)", out)
        self.assertIn("Total: +1/-0 across 1 file", out)

    def test_multiple_commits_are_listed_with_count(self):
        start = self.commit_repo()
        for number in range(3):
            write_file(self.repo, f"subs/feed{number}.txt", b"x\n")
            commit_all(self.repo, f"bot {number}")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("Commits since checkout (3):", out)
        self.assertGreaterEqual(out.count("- `"), 3)

    def test_tracked_working_tree_changes_are_reported(self):
        start = self.commit_repo()
        write_file(self.repo, "README.md", b"hello\nchanged\n")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="failure")
        self.assertEqual(code, 0)
        self.assertIn("Job result: failure", out)
        self.assertIn("Tracked working-tree changes:\n- `README.md` (+1/-0)", out)
        self.assertIn("Commits since checkout (0):", out)

    def test_staged_and_unstaged_tracked_changes_are_both_reported(self):
        start = self.commit_repo()
        write_file(self.repo, "README.md", b"hello\nstaged\n")
        git(self.repo, "add", "README.md")
        write_file(self.repo, "subs/all.txt", b"line1\nline2\nunstaged\n")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertIn("`README.md`", out)
        self.assertIn("`subs/all.txt`", out)

    def test_untracked_paths_are_listed_and_leading_dash_is_prefixed(self):
        start = self.commit_repo()
        write_file(self.repo, "-dash.txt", b"dash\n")
        write_file(self.repo, "plain.txt", b"plain\n")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("./-dash.txt", out)
        self.assertNotIn("`-dash.txt`", out)
        self.assertIn("`plain.txt`", out)

    def test_markdown_injection_in_filenames_is_neutralized(self):
        start = self.commit_repo()
        write_file(self.repo, "weird`](x.txt", b"injection\n")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertNotIn("`](x.txt", out)
        self.assertNotIn("](x.txt", out)
        self.assertIn("weird", out)

    def test_file_contents_and_absolute_repo_path_are_never_printed(self):
        start = self.commit_repo()
        token = "nvapi-" + "A" * 12
        write_file(self.repo, "subs/all.txt", ("secret=" + token).encode())
        commit_all(self.repo, "bot")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertNotIn(token, out)
        self.assertNotIn(str(self.repo), out)
        self.assertNotIn("secret=", out)

    def test_missing_baseline_reports_unavailable_without_inventing_counts(self):
        self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=None, RUN_STATUS="failure")
        self.assertEqual(code, 0)
        self.assertIn("Job result: failure", out)
        self.assertIn("unavailable", out.lower())
        self.assertNotIn("Commits since checkout (0):\n- none", out)

    def test_baseline_absent_from_history_is_reported_honestly(self):
        start = self.commit_repo()
        write_file(self.repo, "subs/all.txt", b"line1\nline2\nline3\n")
        commit_all(self.repo, "bot")
        code, out = run_summary(self.repo, RUN_START_SHA="0" * 40, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("unavailable", out.lower())
        self.assertNotIn("Commits since checkout (1):", out)

    def test_baseline_not_ancestor_of_head_reports_unavailable(self):
        # Unrelated (rebased) history: the baseline exists but is not an ancestor of HEAD.
        start = self.commit_repo()
        git(self.repo, "checkout", "-q", "--orphan", "other")
        write_file(self.repo, "subs/other.txt", b"other\n")
        commit_all(self.repo, "orphan")
        other = head(self.repo)
        git(self.repo, "checkout", "-q", "master")
        head_now = head(self.repo)
        self.assertEqual(head_now, start)
        code, out = run_summary(self.repo, RUN_START_SHA=other, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("unavailable", out.lower())
        self.assertNotIn("Commits since checkout (1):", out)

    def test_option_like_start_sha_is_rejected_not_executed(self):
        start = self.commit_repo()
        for hostile in ("--help", "-d", "HEAD;id", "refs/heads/master"):
            with self.subTest(value=hostile):
                code, out = run_summary(self.repo, RUN_START_SHA=hostile, RUN_STATUS="success")
                self.assertEqual(code, 0)
                self.assertIn("unavailable", out.lower())

    def test_status_and_publication_values_are_sanitized(self):
        start = self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success\n- INJECTED", RUN_PUBLICATION="pushed`\n- INJECTED")
        self.assertEqual(code, 0)
        self.assertNotIn("INJECTED", out)

    def test_missing_step_summary_file_env_still_succeeds(self):
        start = self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("NavaRoute workflow run summary", out)

    def test_publication_defaults_to_not_recorded(self):
        start = self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertIn("Publication outcome: not recorded", out)

    def test_publication_outcome_is_reported_when_supplied(self):
        start = self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success", RUN_PUBLICATION="pushed")
        self.assertIn("Publication outcome: pushed", out)

    def test_not_a_git_repository_reports_unavailable_and_exits_zero(self):
        plain = Path(self._tmp.name) / "not-a-repo"
        plain.mkdir()
        code, out = run_summary(plain, RUN_START_SHA="a" * 40, RUN_STATUS="failure")
        self.assertEqual(code, 0)
        self.assertIn("unavailable", out.lower())

    def test_binary_change_is_reported_without_line_counts(self):
        start = self.commit_repo()
        write_file(self.repo, "subs/blob.bin", bytes(range(256)) * 4)
        commit_all(self.repo, "bot")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("binary", out)

    def test_incoming_change_section_uses_parent(self):
        start = self.commit_repo()
        write_file(self.repo, "subs/all.txt", b"line1\nline2\nline3\n")
        commit_all(self.repo, "bot")
        code, out = run_summary(self.repo, RUN_START_SHA=start, RUN_STATUS="success")
        self.assertIn("Incoming change (HEAD vs its parent):", out)
        self.assertIn("- `subs/all.txt` (+1/-0)", out)

    def test_incoming_change_unavailable_without_parent(self):
        self.commit_repo()
        code, out = run_summary(self.repo, RUN_START_SHA=head(self.repo), RUN_STATUS="success")
        self.assertIn("Incoming change (HEAD vs its parent):\n- unavailable", out)

    def test_head_sha_unavailable_outside_repository(self):
        plain = Path(self._tmp.name) / "plain2"
        plain.mkdir()
        code, out = run_summary(plain, RUN_STATUS="success")
        self.assertEqual(code, 0)
        self.assertIn("Final HEAD SHA: unavailable", out)


class SanitizeUnitTests(unittest.TestCase):
    def test_path_sanitizer_neutralizes_control_and_markdown_characters(self):
        spec = importlib.util.spec_from_file_location("workflow_summary", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        safe = module.safe_path("a`b]c*d_e\\f|g<h>i\n".encode())
        for bad in "`]*\\|<>\n":
            self.assertNotIn(bad, safe)
        self.assertEqual(module.safe_path("-leading.txt".encode()), "./-leading.txt")


if __name__ == "__main__":
    unittest.main()
