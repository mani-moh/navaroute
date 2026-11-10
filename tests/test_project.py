import os
import subprocess
import unittest
from unittest.mock import patch

from src import project


class TestProject(unittest.TestCase):
    INVALID_SLUGS = [
        "owner", "owner/repo/extra", "/repo", "owner/", "owner//repo",
        "../repo", "owner/.", "owner/..", "-owner/repo", "owner-/repo",
        "own--er/repo", "own_er/repo", "owner/repo?preview=1", "owner/repo#secret",
        "owner/[repo](https://example.com)", "owner/repo%2Fsecret", "owner/re\npo",
        "owner/re\tpo", "owner/r\u00e9po", "https://github.com/owner/repo",
        "a" * 40 + "/repo", "owner/" + "r" * 101,
    ]

    def test_slug_inputs_are_isolated(self):
        cases = [
            ({"GITHUB_REPOSITORY": " fork-owner/fork-repo "}, "", 1, "fork-owner/fork-repo"),
            ({}, "https://github.com/remote-owner/remote-repo.git\n", 0, "remote-owner/remote-repo"),
            ({}, "git@github.com:ssh-owner/ssh-repo.git/", 0, "ssh-owner/ssh-repo"),
            ({}, "ssh://git@github.com/ssh-owner/ssh-repo.git", 0, "ssh-owner/ssh-repo"),
            ({"GITHUB_REPOSITORY": "  "}, "https://github.com/user/project/", 0, "user/project"),
            ({}, "https://gitlab.com/user/project.git", 0, "mani-moh/navaroute"),
            ({}, "https://notgithub.com/user/project.git", 0, "mani-moh/navaroute"),
            ({}, "", 1, "mani-moh/navaroute"),
        ]
        for environment, remote, returncode, expected in cases:
            with self.subTest(environment=environment, remote=remote):
                result = subprocess.CompletedProcess(
                    ["git", "remote", "get-url", "origin"], returncode, stdout=remote
                )
                with patch.dict(os.environ, environment, clear=True), patch(
                    "subprocess.run", return_value=result
                ):
                    self.assertEqual(project.detect_repo_slug(), expected)

    def test_environment_does_not_require_git(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "fork/repository"}, clear=True), patch(
            "subprocess.run", side_effect=AssertionError("git must not be needed")
        ):
            self.assertEqual(project.detect_repo_slug(), "fork/repository")

    def test_missing_git_uses_official_default(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "subprocess.run", side_effect=FileNotFoundError("git is unavailable")
        ):
            self.assertEqual(project.detect_repo_slug(), "mani-moh/navaroute")

    def test_credentialed_origins_keep_configured_fork(self):
        origins = [
            "https://git-user@github.com/fork-owner/fork-repo.git",
            "https://git-user:synthetic-token@github.com/fork-owner/fork-repo.git",
            "ssh://git-user@github.com/fork-owner/fork-repo.git",
        ]
        for origin in origins:
            with self.subTest(origin=origin), patch.dict(os.environ, {}, clear=True), patch(
                "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=origin)
            ):
                slug = project.detect_repo_slug()
                self.assertEqual(slug, "fork-owner/fork-repo")
                self.assertNotIn("git-user", slug)
                self.assertNotIn("synthetic-token", slug)

    def test_remote_query_and_fragment_never_enter_slug(self):
        origins = [
            "https://github.com/fork-owner/fork-repo.git?access_token=synthetic-token",
            "https://github.com/fork-owner/fork-repo.git#synthetic-secret",
            "https://git-user:synthetic-token@github.com/fork-owner/fork-repo.git/?access_token=synthetic-token#synthetic-secret",
            "https://github.com/fork-owner/fork-repo.git#](https://example.com/private)",
            "git@github.com:fork-owner/fork-repo.git?access_token=synthetic-token#synthetic-secret",
        ]
        for origin in origins:
            with self.subTest(origin=origin), patch.dict(os.environ, {}, clear=True), patch(
                "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=origin)
            ):
                self.assertEqual(project.detect_repo_slug(), "fork-owner/fork-repo")

    def test_invalid_environment_falls_back_to_remote_or_default(self):
        for invalid_slug in self.INVALID_SLUGS:
            for returncode, expected in ((0, "remote-owner/remote-repo"), (1, "mani-moh/navaroute")):
                with self.subTest(slug=invalid_slug, returncode=returncode), patch.dict(
                    os.environ, {"GITHUB_REPOSITORY": invalid_slug}, clear=True
                ), patch("subprocess.run", return_value=subprocess.CompletedProcess(
                    [], returncode, stdout="https://github.com/remote-owner/remote-repo.git"
                )):
                    self.assertEqual(project.detect_repo_slug(), expected)

    def test_invalid_remote_segments_are_rejected(self):
        for invalid_slug in self.INVALID_SLUGS:
            for prefix in ("https://github.com/", "git@github.com:"):
                # URL queries/fragments are discarded, not interpreted as slug segments.
                if "?" in invalid_slug or "#" in invalid_slug:
                    continue
                origin = prefix + invalid_slug + ".git"
                with self.subTest(origin=origin), patch.dict(os.environ, {}, clear=True), patch(
                    "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=origin)
                ):
                    self.assertEqual(project.detect_repo_slug(), "mani-moh/navaroute")

    def test_invalid_url_hosts_or_syntax_are_rejected(self):
        origins = [
            "https://git-user:synthetic-token@github.com.evil.example/fork-owner/fork-repo.git",
            "https://github.com@evil.example/fork-owner/fork-repo.git",
            "https://github.com\\@evil.example/fork-owner/fork-repo.git",
            "https://[github.com/fork-owner/fork-repo.git",
            "file://github.com/fork-owner/fork-repo.git",
            "https://github.com//fork-owner/fork-repo.git",
            "https://github.com/fork-owner/fork-repo.git//",
        ]
        for origin in origins:
            with self.subTest(origin=origin), patch.dict(os.environ, {}, clear=True), patch(
                "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=origin)
            ):
                self.assertEqual(project.detect_repo_slug(), "mani-moh/navaroute")

    def test_valid_segment_boundaries_are_preserved(self):
        slugs = ["Org-1/Repo_name.v2-1", "owner/.github", "a" * 39 + "/" + "r" * 100]
        for expected in slugs:
            with self.subTest(slug=expected), patch.dict(
                os.environ, {"GITHUB_REPOSITORY": expected}, clear=True
            ), patch("subprocess.run", side_effect=AssertionError("valid environment must win")):
                self.assertEqual(project.detect_repo_slug(), expected)
            with self.subTest(origin=expected), patch.dict(os.environ, {}, clear=True), patch(
                "subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout="https://github.com/" + expected + ".git"
                )
            ):
                self.assertEqual(project.detect_repo_slug(), expected)
