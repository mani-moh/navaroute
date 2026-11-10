"""Project identity and repository detection for generated public links."""

import os
import re
import subprocess
from urllib.parse import urlsplit

PROJECT_NAME = "NavaRoute"
DEFAULT_REPOSITORY = "mani-moh/navaroute"
OWNER_LOGIN = "mani-moh"
DEFAULT_BRANCH = "master"


def _is_repo_slug(value: str) -> bool:
    return (
        re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/[A-Za-z0-9_.-]{1,100}",
            value,
        ) is not None
        and value.rsplit("/", 1)[-1] not in (".", "..")
    )


def detect_repo_slug() -> str:
    """Honor the environment/remote for links; this does not authorize an owner."""
    environment_slug = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if _is_repo_slug(environment_slug):
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
        remote = result.stdout.strip()
        # urlsplit removes raw controls; reject rather than silently normalize them.
        if re.search(r"[\x00-\x20\x7f]", remote):
            return DEFAULT_REPOSITORY
        if remote.lower().startswith("git@github.com:"):
            remote = "ssh://" + remote.replace(":", "/", 1)
        try:
            parsed = urlsplit(remote)
            if parsed.scheme not in ("https", "http", "ssh", "git") or parsed.hostname != "github.com":
                return DEFAULT_REPOSITORY
        except ValueError:
            return DEFAULT_REPOSITORY
        slug = parsed.path.removeprefix("/").removesuffix("/").removesuffix(".git")
        if _is_repo_slug(slug):
            return slug
    return DEFAULT_REPOSITORY
