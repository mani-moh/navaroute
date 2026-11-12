#!/usr/bin/env python3
"""Shared privacy-safe per-workflow-run change summary (standard library only).

Writes a Markdown summary of what a job did to stdout and, when set, to
$GITHUB_STEP_SUMMARY. Only commit identities, the job result, repo-relative
sanitized paths and line deltas are printed — never file contents, URI
credentials, proxy URIs, tokens, private paths, environment dumps or raw
logs. Missing or shallow checkout history is reported as unavailable
instead of being guessed; exit code is always 0 so a summary failure can
never fail the job it describes.
"""
import os
import re
import subprocess
import sys

GIT_TIMEOUT_SECONDS = 30
SHA_RE = re.compile(r"[0-9a-fA-F]{4,40}")
SAFE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,59}")
STATUS_VALUES = ("success", "failure", "cancelled")
MAX_ENTRIES = 50
MAX_PATH = 200
# Characters that are structural in Markdown or unsafe in any shell paste.
BAD_PATH_CHARS = '`*_[]()<>|\\'


def git_stdout(*args):
    try:
        result = subprocess.run(["git", *args], capture_output=True, timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def safe_path(raw):
    text = raw.decode("utf-8", "replace")
    if len(text) > MAX_PATH:
        text = text[:MAX_PATH] + "…(truncated)"
    cleaned = "".join("_" if (ch in BAD_PATH_CHARS or ord(ch) < 32 or ord(ch) == 127) else ch for ch in text)
    return ("./" + cleaned) if cleaned.startswith("-") else cleaned


def parse_numstat(data):
    entries, total_added, total_deleted, files = [], 0, 0, 0
    for chunk in data.split(b"\0"):
        if not chunk:
            continue
        parts = chunk.split(b"\t", 2)
        if len(parts) != 3:
            continue
        raw_added, raw_deleted, raw_path = parts
        files += 1
        if raw_added == b"-":  # binary
            entries.append((None, None, safe_path(raw_path)))
            continue
        try:
            added, deleted = int(raw_added), int(raw_deleted)
        except ValueError:
            entries.append((None, None, safe_path(raw_path)))
            continue
        total_added += added
        total_deleted += deleted
        entries.append((added, deleted, safe_path(raw_path)))
    return entries, total_added, total_deleted, files


def delta_section(title, data, unavailable_reason=None):
    lines = [title]
    if unavailable_reason is not None:
        lines.append(f"- unavailable ({unavailable_reason})")
        return lines
    entries, total_added, total_deleted, files = parse_numstat(data or b"")
    if not entries:
        lines.append("- none")
        return lines
    shown = entries[:MAX_ENTRIES]
    for added, deleted, path in shown:
        lines.append(f"- `{path}` (binary)" if added is None else f"- `{path}` (+{added}/-{deleted})")
    if len(entries) > len(shown):
        lines.append(f"- … and {len(entries) - len(shown)} more (truncated)")
    lines.append(f"Total: +{total_added}/-{total_deleted} across {files} file{'s' if files != 1 else ''}")
    return lines


def is_ancestor(base):
    try:
        result = subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"],
                                capture_output=True, timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def commits_section(start_sha, head_present, boundary):
    if boundary is not None:
        return [f"Commits since checkout: unavailable ({boundary})"]
    if not start_sha:
        return ["Commits since checkout: unavailable (checkout baseline was not captured or not a recognized commit SHA)"]
    if head_present is None:
        return ["Commits since checkout: unavailable (final HEAD could not be resolved; not a git repository or git failed)"]
    lines = []
    count_out = git_stdout("rev-list", "--count", start_sha + "..HEAD")
    log_out = git_stdout("log", "--no-decorate", "--format=%h", start_sha + "..HEAD")
    if count_out is None or log_out is None:
        return ["Commits since checkout: unavailable (git could not read the range; history may be shallow)"]
    try:
        count = int(count_out.decode().strip())
    except ValueError:
        return ["Commits since checkout: unavailable (git could not count the range)"]
    lines.append(f"Commits since checkout ({count}):")
    if count == 0:
        lines.append("- none")
        return lines
    shorts = [line.decode("utf-8", "replace").strip() for line in log_out.splitlines() if line.strip()]
    for short in shorts[:MAX_ENTRIES]:
        if SHA_RE.fullmatch(short):
            lines.append(f"- `{short}`")
    if len(shorts) > MAX_ENTRIES:
        lines.append(f"- … and {len(shorts) - MAX_ENTRIES} more (truncated)")
    return lines


def untracked_section():
    data = git_stdout("ls-files", "--others", "--exclude-standard", "-z", "--")
    if data is None:
        return ["New untracked paths: unavailable (git could not list untracked files)"]
    paths = [safe_path(chunk) for chunk in data.split(b"\0") if chunk]
    lines = ["New untracked paths:"]
    if not paths:
        lines.append("- none")
        return lines
    for path in paths[:MAX_ENTRIES]:
        lines.append(f"- `{path}`")
    if len(paths) > MAX_ENTRIES:
        lines.append(f"- … and {len(paths) - MAX_ENTRIES} more (truncated)")
    return lines


def collect():
    lines = ["## NavaRoute workflow run summary", ""]
    status_raw = os.environ.get("RUN_STATUS", "").strip().lower()
    status = status_raw if status_raw in STATUS_VALUES else "unknown"
    publication_raw = os.environ.get("RUN_PUBLICATION", "").strip()
    if not publication_raw:
        publication = "not recorded"
    else:
        publication = publication_raw if SAFE_TOKEN_RE.fullmatch(publication_raw) else "unknown"
    lines.append(f"- Job result: {status}")
    lines.append(f"- Publication outcome: {publication}")

    start_raw = os.environ.get("RUN_START_SHA", "").strip()
    start_sha = start_raw if SHA_RE.fullmatch(start_raw) else None
    lines.append(f"- Checkout SHA: {start_sha or ('invalid (not a recognized commit SHA)' if start_raw else 'not captured')}")

    head_raw = git_stdout("rev-parse", "HEAD")
    head_sha = head_raw.decode("utf-8", "replace").strip() if head_raw else None
    if head_sha and not SHA_RE.fullmatch(head_sha):
        head_sha = None
    lines.append(f"- Final HEAD SHA: {head_sha or 'unavailable'}")
    lines.append("")

    boundary = None
    if start_raw and not start_sha:
        boundary = "checkout baseline was not a recognized commit SHA"
    elif start_sha:
        if git_stdout("rev-parse", "--verify", "--quiet", start_sha + "^{commit}") is None:
            boundary = "checkout baseline is not present in local history (shallow or rewritten checkout)"
        elif not is_ancestor(start_sha):
            boundary = "checkout baseline is not an ancestor of HEAD (history may be shallow or rewritten)"
    lines.extend(commits_section(start_sha, head_sha, boundary))
    lines.append("")
    if boundary:
        lines.extend(delta_section("Files changed since checkout:", None, boundary))
    elif start_sha and head_sha:
        lines.extend(delta_section("Files changed since checkout:", git_stdout("diff", "--numstat", "-z", "--no-renames", start_sha, "HEAD", "--")))
    elif not start_sha:
        lines.extend(delta_section("Files changed since checkout:", None, "checkout baseline was not captured"))
    else:
        lines.extend(delta_section("Files changed since checkout:", None, "final HEAD unavailable"))
    lines.append("")
    parent_raw = git_stdout("rev-parse", "--verify", "--quiet", "HEAD^") if head_sha else None
    if parent_raw is None:
        lines.append("Incoming change (HEAD vs its parent):")
        lines.append("- unavailable (HEAD has no parent in local history)")
    else:
        lines.extend(delta_section("Incoming change (HEAD vs its parent):", git_stdout("diff", "--numstat", "-z", "--no-renames", "HEAD^", "HEAD", "--")))
    lines.append("")
    if head_sha is None:
        lines.append("Tracked working-tree changes:")
        lines.append("- unavailable (final HEAD unavailable)")
    else:
        lines.extend(delta_section("Tracked working-tree changes:", git_stdout("diff", "--numstat", "-z", "--no-renames", "HEAD", "--")))
    lines.append("")
    lines.extend(untracked_section())
    return lines


def main():
    try:
        lines = collect()
    except Exception:
        status_raw = os.environ.get("RUN_STATUS", "").strip().lower()
        status = status_raw if status_raw in STATUS_VALUES else "unknown"
        lines = ["## NavaRoute workflow run summary", "", f"- Job result: {status}",
                 "- Repository details could not be collected (internal error)."]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            sys.stdout.write("Step summary file was not writable; summary printed above only.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
