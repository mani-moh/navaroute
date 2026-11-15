"""Publish path: findings -> encrypted special feeds -> feed-data repo.

  findings (dedupe-filtered) -> one plaintext per feed kind -> AES-256-GCM
  envelope (crypto.encrypt_text) -> <feed-data clone>/kit-N_base64.txt
  -> git commit + push -> the feed-data sync/relay pushes to ArvanCloud.

Newest-wins: a feed file is only rewritten when the new content is
newer than the file already in the feed-data clone (compared by
content hash + mtime). A file that is already current is left alone;
mirror-only files are never deleted by this module (it writes files,
it never removes any).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import crypto
from .feeds import SPECIAL_FEEDS, Finding, PublishedRegistry

FEED_DATA_REPO = "mani-moh/feed-data"
DEFAULT_FEED_DATA_DIR = Path.home() / "workspace" / "projects" / "feed-data-git"

KIND_TO_FEED = {kind: filename for filename, _label, _desc, kind in SPECIAL_FEEDS}


def group_findings(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Group findings by feed kind, deduped by content hash."""
    grouped: dict[str, list[Finding]] = {}
    seen: set[str] = set()
    for finding in findings:
        if finding.kind not in KIND_TO_FEED:
            continue
        if finding.digest in seen:
            continue
        seen.add(finding.digest)
        grouped.setdefault(finding.kind, []).append(finding)
    return grouped


def render_feed_plaintext(findings: list[Finding], built_at: float | None = None) -> bytes:
    """Plaintext body of one feed file: one finding per line, newest
    first, with a build timestamp header line (a comment, not content).
    The header makes the file's freshness visible without decrypting
    the whole feed in the mirror tooling.
    """
    built_at = time.time() if built_at is None else built_at
    lines = [f"# built {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(built_at))}"]
    lines.extend(f.text for f in sorted(findings, key=lambda f: -f.found_at))
    return ("\n".join(lines) + "\n").encode("utf-8")


@dataclass
class PublishResult:
    written: list[str] = field(default_factory=list)
    skipped_newer: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)


def _split_header(plain: bytes) -> tuple[int | None, bytes]:
    """Split a feed plaintext into (build timestamp, body lines).

    The header carries freshness; the body carries the findings. Two
    builds with the same findings have the same body even though their
    headers differ.
    """
    lines = plain.split(b"\n", 1)
    if lines and lines[0].startswith(b"# built "):
        try:
            import calendar

            built = calendar.timegm(
                time.strptime(lines[0][len("# built "):].decode("ascii").strip(),
                              "%Y-%m-%dT%H:%M:%SZ")
            )
        except (ValueError, UnicodeDecodeError):
            built = None
        return built, lines[1] if len(lines) > 1 else b""
    return None, plain


def build_special_feeds(
    findings: list[Finding],
    key: bytes,
    feed_data_dir: Path | str = DEFAULT_FEED_DATA_DIR,
    now: float | None = None,
    registry: PublishedRegistry | None = None,
) -> PublishResult:
    """Write encrypted special-feed files into the feed-data clone.

    Newest-wins: an existing file whose mtime is newer than `now`
    (i.e. written after this build started) is left untouched; a file
    with identical content is left untouched too. Returns what happened
    per file. Nothing is deleted, nothing is committed here -- commit
    with commit_and_push().
    """
    feed_data_dir = Path(feed_data_dir)
    now = time.time() if now is None else now
    result = PublishResult()
    grouped = group_findings(findings)
    for _filename, _label, _desc, kind in SPECIAL_FEEDS:
        filename = KIND_TO_FEED[kind]
        items = grouped.get(kind, [])
        if not items:
            result.empty.append(filename)
            continue
        dest = feed_data_dir / filename
        plaintext = render_feed_plaintext(items, built_at=now)
        if dest.exists():
            # Newest-wins: the existing file carries its own build time
            # in the plaintext header. Never overwrite a newer build
            # with an older one; identical findings are also left alone
            # (the header timestamp alone is not a reason to rewrite).
            try:
                existing_plain = crypto.decrypt_text(
                    dest.read_text(encoding="ascii"), key, filename
                )
                _existing_built, existing_body = _split_header(existing_plain)
                _new_built, new_body = _split_header(plaintext)
                if existing_body == new_body:
                    result.skipped_newer.append(filename)
                    continue
                if _existing_built is not None and _existing_built >= int(now):
                    result.skipped_newer.append(filename)
                    continue
            except (ValueError, IndexError, OSError):
                pass  # existing file unreadable/corrupt: rewrite it
        dest.write_text(crypto.encrypt_text(plaintext, key, filename), encoding="ascii")
        result.written.append(filename)
        if registry is not None:
            for finding in items:
                registry.mark_published(finding, filename)
    if registry is not None:
        registry.save()
    return result


def commit_and_push(
    feed_data_dir: Path | str = DEFAULT_FEED_DATA_DIR,
    message: str = "Blackout Kit: update special feeds",
    branch: str | None = None,
) -> bool:
    """Commit changed kit files in the feed-data clone and push.

    Returns True when something was committed and pushed, False when
    there was nothing to commit. Raises on git failure.
    """
    feed_data_dir = Path(feed_data_dir)
    files = [name for name, *_ in SPECIAL_FEEDS]
    subprocess.run(["git", "add", "--", *files], cwd=feed_data_dir, check=True,
                   capture_output=True, text=True)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=feed_data_dir,
                            capture_output=True, text=True)
    if status.returncode == 0:
        return False
    args = ["git", "-c", "user.name=blackout-kit", "-c",
            "user.email=blackout-kit@users.noreply.github.com", "commit", "-m", message]
    subprocess.run(args, cwd=feed_data_dir, check=True, capture_output=True, text=True)
    push = ["git", "push"]
    if branch:
        push += ["origin", branch]
    subprocess.run(push, cwd=feed_data_dir, check=True, capture_output=True, text=True)
    return True
