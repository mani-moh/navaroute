import os
import subprocess
import unittest
from unittest.mock import patch

from src import project


class TestProject(unittest.TestCase):
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
