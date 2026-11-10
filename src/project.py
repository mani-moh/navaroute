"""Project identity and repository detection for generated public links."""

import os
import re
import subprocess

PROJECT_NAME = "NavaRoute"
DEFAULT_REPOSITORY = "mani-moh/navaroute"
OWNER_LOGIN = "mani-moh"
DEFAULT_BRANCH = "master"


def detect_repo_slug() -> str:
    """Honor the environment/remote for links; this does not authorize an owner."""
    environment_slug = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if environment_slug:
        return environment_slug

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_REPOSITORY

    if result.returncode == 0:
        remote = result.stdout.strip().rstrip("/").removesuffix(".git")
        match = re.fullmatch(
            r"(?:https?://|git://|ssh://git@|git@)github\.com[:/]([^/\s]+)/([^/\s]+)",
            remote,
            re.IGNORECASE,
        )
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return DEFAULT_REPOSITORY
