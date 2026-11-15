"""Tests for the ArvanCloud mirror relay (tools/relay/relay.py).

Everything runs offline: GitHub and S3 are fakes, the upstream fetch
and the public HEAD checks are mocked. No credentials are ever used
here -- the fake keys below are dummy values, and the tests assert
they never appear in the status output.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "relay"))

import relay  # noqa: E402

from datetime import datetime, timezone

FAKE_ACCESS = "TESTACCESSKEY00000000"
FAKE_SECRET = "test-secret-key-00000000000000000000000000000000"
FAKE_TOKEN = "test-token-00000000000000000000000000000000"

# Commit times / S3 LastModified used by the newest-wins tests.
OLD = datetime(2026, 11, 10, 0, 0, 0, tzinfo=timezone.utc)
NEW = datetime(2026, 11, 16, 0, 0, 0, tzinfo=timezone.utc)

FEED_A = "dmxlc3M6Ly8xMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTFA\n"
FEED_B = "dmxlc3M6Ly8yMjIyMjIyMi0yMjIyLTIyMjItMjIyMi0yMjIyMjIyMjIyMjJg\n"


class FakeGitHub:
    """In-memory feed-data repo: {name: (content bytes, sha)}.

    ``commit_times`` maps "owner/repo/path" to the most recent commit
    time of that file (used by the newest-wins rule).
    """

    def __init__(self, files: dict[str, bytes] | None = None,
                 token: str | None = FAKE_TOKEN,
                 commit_times: dict[str, datetime] | None = None) -> None:
        self.token = token
        self.files = {name: (data, f"sha-{name}")
                      for name, data in (files or {}).items()}
        self.commit_times = dict(commit_times or {})
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def file_commit_time(self, owner: str, repo: str,
                         path: str) -> datetime | None:
        return self.commit_times.get(f"{owner}/{repo}/{path}")

    def request_json(self, method: str, path: str,
                     payload: dict | None = None) -> dict | list:
        return {"full_name": "mani-moh/navaroute"}

    def list_contents(self, owner: str, repo: str, path: str = "") -> list[dict]:
        return [{"name": name, "sha": sha, "type": "file"}
                for name, (_data, sha) in self.files.items()]

    def get_file_bytes(self, owner: str, repo: str,
                       path: str) -> tuple[bytes | None, str | None]:
        entry = self.files.get(path)
        if entry is None:
            return None, None
        return entry

    def put_file(self, owner: str, repo: str, path: str, content: bytes,
                 message: str, sha: str | None, branch: str) -> None:
        self.files[path] = (content, f"sha-{path}-new")
        self.puts.append(path)

    def delete_file(self, owner: str, repo: str, path: str, sha: str,
                    message: str, branch: str) -> None:
        self.files.pop(path, None)
        self.deletes.append(path)


class FakeS3:
    """In-memory S3 bucket: {name: {"etag", "last_modified"}}.

    ``objects`` maps a name to (etag, last_modified); records uploads
    and deletes.
    """

    def __init__(self, objects: dict[str, tuple[str, datetime | None]] | None = None) -> None:
        self.objects = {name: {"etag": etag, "last_modified": when}
                        for name, (etag, when) in (objects or {}).items()}
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    def list_objects(self) -> dict[str, dict]:
        return {name: dict(entry) for name, entry in self.objects.items()}

    def upload(self, name: str, data: bytes, content_type: str) -> None:
        self.objects[name] = {"etag": f'"{relay.md5_hex(data)}"',
                              "last_modified": datetime.now(timezone.utc)}
        self.uploaded.append(name)

    def delete(self, names: list[str]) -> None:
        for name in names:
            self.objects.pop(name, None)
            self.deleted.append(name)


def _relay(github: FakeGitHub | None = None, s3: FakeS3 | None = None,
           config: dict | None = None) -> relay.Relay:
    cfg = {"ARVAN_ACCESS_KEY": FAKE_ACCESS, "ARVAN_SECRET_KEY": FAKE_SECRET}
    if config:
        cfg.update(config)
    return relay.Relay(cfg, github=github, s3=s3)


class TestConfig(unittest.TestCase):
    def test_parse_ignores_comments_blanks_and_strips_quotes(self) -> None:
        text = "# comment\n\nARVAN_ACCESS_KEY=\"AKIA123\"\nARVAN_SECRET_KEY='sec ret'\nGITHUB_TOKEN=tok123\n"
        parsed = relay.parse_config_text(text)
        self.assertEqual(parsed, {"ARVAN_ACCESS_KEY": "AKIA123",
                                  "ARVAN_SECRET_KEY": "sec ret",
                                  "GITHUB_TOKEN": "tok123"})

    def test_parse_ignores_lines_without_equals(self) -> None:
        self.assertEqual(relay.parse_config_text("just a line\nA=1\n"), {"A": "1"})

    def test_load_config_merges_env_and_files_first_wins(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env"
            path.write_text("ARVAN_ACCESS_KEY=filekey\nARVAN_SECRET_KEY=filesecret\n")
            with patch.dict("os.environ", {"ARVAN_ACCESS_KEY": "envkey"}, clear=False):
                merged = relay.load_config(str(path))
            self.assertEqual(merged["ARVAN_ACCESS_KEY"], "envkey")
            self.assertEqual(merged["ARVAN_SECRET_KEY"], "filesecret")

    def test_load_config_missing_files_is_empty(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k not in relay.CONFIG_KEYS}
        with patch.dict("os.environ", env, clear=True):
            # Pin the candidate list so the real credentials file (when
            # present on the machine) cannot leak into this test.
            with patch.object(relay, "config_candidates",
                              return_value=[Path("/nonexistent/path/env")]):
                merged = relay.load_config("/nonexistent/path/env")
        self.assertEqual(merged, {})


class TestFeedStems(unittest.TestCase):
    def test_stems_come_from_page_builder_not_hardcoded(self) -> None:
        builder = relay.load_page_builder()
        stems = relay.feed_stems(builder)
        self.assertIn("tested_base64", stems)
        self.assertIn("online_base64", stems)
        self.assertNotIn("reachable_base64", stems)
        self.assertNotIn("tier1_all_base64", stems)
        self.assertNotIn("verified_base64", stems)

    def test_md5_etag_matching(self) -> None:
        data = b"hello"
        self.assertTrue(relay.etag_matches(f'"{relay.md5_hex(data)}"', data))
        self.assertFalse(relay.etag_matches('"deadbeef"', data))
        self.assertFalse(relay.etag_matches(None, data))
        self.assertFalse(relay.etag_matches('"abc-def-1"', data))


class TestGitHubAPI(unittest.TestCase):
    def test_large_file_falls_back_to_download_url(self) -> None:
        """Files over ~1 MB get an empty content field; use download_url."""
        api = relay.GitHubAPI(token=FAKE_TOKEN)
        big = b"x" * 1200000
        entry = {"content": "", "sha": "sha-big",
                 "download_url": "https://example.invalid/big.txt", "size": 1200000}
        with patch.object(relay.GitHubAPI, "request_json",
                          return_value=entry):
            with patch.object(relay, "_request", return_value=big) as req:
                content, sha = api.get_file_bytes("mani-moh", "feed-data",
                                                  "big.txt")
        self.assertEqual(content, big)
        self.assertEqual(sha, "sha-big")
        self.assertEqual(req.call_args.args[0], "https://example.invalid/big.txt")

    def test_small_file_uses_inline_content(self) -> None:
        api = relay.GitHubAPI(token=FAKE_TOKEN)
        import base64
        entry = {"content": base64.b64encode(b"hello").decode(),
                 "sha": "sha-small", "size": 5}
        with patch.object(relay.GitHubAPI, "request_json",
                          return_value=entry):
            content, sha = api.get_file_bytes("mani-moh", "feed-data",
                                              "small.txt")
        self.assertEqual(content, b"hello")
        self.assertEqual(sha, "sha-small")


class TestStageA(unittest.TestCase):
    def test_adds_updates_and_deletes(self) -> None:
        github = FakeGitHub({
            "tested_base64.txt": FEED_A.encode(),
            "reachable_base64.txt": FEED_A.encode(),
            "other.txt": b"keep me\n",
        })
        upstream = {"tested_base64": FEED_A.encode(),
                    "online_base64": FEED_B.encode()}
        r = _relay(github=github)
        with patch.object(relay, "upstream_file",
                          side_effect=lambda stem: upstream.get(stem, FEED_A.encode())):
            # Stems outside the fake upstream return FEED_A; stage A
            # records them as updates, which this test does not assert on.
            result = r.stage_a()
        self.assertIn("tested_base64.txt", github.files)
        self.assertIn("online_base64.txt", github.files)
        self.assertNotIn("reachable_base64.txt", github.files)
        self.assertIn("other.txt", github.files)
        self.assertIn("online_base64.txt", result["updated"])
        self.assertIn("reachable_base64.txt", result["deleted"])
        self.assertNotIn("tested_base64.txt", result["updated"])

    def test_skipped_without_github_token(self) -> None:
        github = FakeGitHub(token=None)
        r = _relay(github=github)
        result = r.stage_a()
        self.assertEqual(result["status"], "skipped")


class TestStageB(unittest.TestCase):
    def _github_with_times(self, **times: datetime) -> FakeGitHub:
        commit_times = {f"mani-moh/feed-data/{name}.txt": when
                        for name, when in times.items()}
        return FakeGitHub(commit_times=commit_times)

    def test_push_uploads_newer_changed_files(self) -> None:
        s3 = FakeS3({
            "tested_base64.txt": (f'"{relay.md5_hex(FEED_A.encode())}"', OLD),
            "online_base64.txt": (f'"{relay.md5_hex(FEED_A.encode())}"', OLD),
        })
        files = {"tested_base64.txt": FEED_A.encode(),
                 "online_base64.txt": FEED_B.encode(),
                 "index.html": b"<html></html>"}
        github = self._github_with_times(tested_base64=NEW,
                                         online_base64=NEW)
        r = _relay(github=github, s3=s3)
        result = r.stage_b_push(files)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("tested_base64.txt", result["uploaded"])
        self.assertIn("online_base64.txt", result["uploaded"])
        self.assertIn("index.html", result["uploaded"])
        self.assertEqual(result["cleaned"], [])
        self.assertEqual(result["skipped"], [])

    def test_push_never_overwrites_newer_mirror_copy(self) -> None:
        """Rule (a): a newer mirror copy is not overwritten by older repo content."""
        s3 = FakeS3({
            "tested_base64.txt": (f'"{relay.md5_hex(FEED_A.encode())}"', NEW),
        })
        files = {"tested_base64.txt": FEED_B.encode()}
        github = self._github_with_times(tested_base64=OLD)
        r = _relay(github=github, s3=s3)
        result = r.stage_b_push(files)
        self.assertNotIn("tested_base64.txt", result["uploaded"])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("tested_base64.txt", result["skipped"][0])
        self.assertIn("mirror copy is newer", result["skipped"][0])
        # The mirror still holds its (newer) content.
        self.assertEqual(s3.objects["tested_base64.txt"]["etag"],
                         f'"{relay.md5_hex(FEED_A.encode())}"')

    def test_push_skips_when_repo_time_unknown(self) -> None:
        """No commit time available: never overwrite blindly."""
        s3 = FakeS3({
            "tested_base64.txt": (f'"{relay.md5_hex(FEED_A.encode())}"', OLD),
        })
        files = {"tested_base64.txt": FEED_B.encode()}
        r = _relay(github=FakeGitHub(), s3=s3)
        result = r.stage_b_push(files)
        self.assertNotIn("tested_base64.txt", result["uploaded"])
        self.assertEqual(len(result["skipped"]), 1)

    def test_push_preserves_mirror_only_files(self) -> None:
        """Rule (b): mirror-only files outside the retired list are left alone."""
        s3 = FakeS3({
            "tested_base64.txt": (f'"{relay.md5_hex(FEED_A.encode())}"', OLD),
            "special-feed_base64.txt": ('"abc"', OLD),
            "blackout-kit.txt": ('"def"', OLD),
        })
        files = {"tested_base64.txt": FEED_A.encode(),
                 "online_base64.txt": FEED_B.encode()}
        github = self._github_with_times(tested_base64=NEW,
                                         online_base64=NEW)
        r = _relay(github=github, s3=s3)
        result = r.stage_b_push(files)
        self.assertEqual(result["status"], "ok")
        self.assertIn("special-feed_base64.txt", s3.objects)
        self.assertIn("blackout-kit.txt", s3.objects)
        self.assertEqual(s3.deleted, [])
        self.assertEqual(result["cleaned"], [])

    def test_push_cleans_only_retired_names(self) -> None:
        """Rule (c): the known old names are removed; nothing else is."""
        s3 = FakeS3({
            "tested_base64.txt": (f'"{relay.md5_hex(FEED_A.encode())}"', OLD),
            "reachable_base64.txt": ('"abc"', OLD),
            "tier1_all_base64.txt": ('"abc"', OLD),
            "special-feed_base64.txt": ('"abc"', OLD),
        })
        files = {"tested_base64.txt": FEED_A.encode()}
        github = self._github_with_times(tested_base64=NEW)
        r = _relay(github=github, s3=s3)
        result = r.stage_b_push(files)
        self.assertEqual(sorted(result["cleaned"]),
                         ["reachable_base64.txt", "tier1_all_base64.txt"])
        self.assertNotIn("reachable_base64.txt", s3.objects)
        self.assertNotIn("tier1_all_base64.txt", s3.objects)
        self.assertIn("special-feed_base64.txt", s3.objects)

    def test_retired_in_mirror_requires_repo_to_lack_the_name(self) -> None:
        r = _relay(s3=FakeS3())
        present = {"reachable_base64.txt", "special-feed_base64.txt"}
        self.assertEqual(
            r._retired_in_mirror(present, {"reachable_base64.txt"}), [])
        self.assertEqual(
            r._retired_in_mirror(present, set()), ["reachable_base64.txt"])

    def test_should_upload_newest_wins(self) -> None:
        self.assertTrue(relay.should_upload(NEW, OLD, True)[0])
        self.assertFalse(relay.should_upload(OLD, NEW, True)[0])
        self.assertFalse(relay.should_upload(NEW, NEW, True)[0])
        self.assertFalse(relay.should_upload(NEW, OLD, False)[0])
        self.assertFalse(relay.should_upload(None, OLD, True)[0])
        self.assertTrue(relay.should_upload(NEW, None, True)[0])

    def test_check_reports_would_upload_without_credentials(self) -> None:
        files = {"tested_base64.txt": FEED_A.encode(),
                 "index.html": b"<html></html>"}
        r = _relay(s3=None)
        metas = {"tested_base64.txt": {
            "etag": f'"{relay.md5_hex(FEED_A.encode())}"',
            "last_modified": OLD}}
        with patch.object(relay, "_head_meta",
                          side_effect=lambda url: metas.get(
                              url.rsplit("/", 1)[-1])):
            result = r.stage_b_check(files)
        self.assertEqual(result["status"], "check")
        self.assertEqual(result["to_upload"], ["index.html"])
        self.assertEqual(result["to_clean"], [])
        self.assertEqual(result["skipped"], [])

    def test_check_skips_newer_mirror_copy(self) -> None:
        """Rule (a) in --check mode: a newer mirror copy is not reported for upload."""
        files = {"tested_base64.txt": FEED_B.encode()}
        github = self._github_with_times(tested_base64=OLD)
        r = _relay(github=github, s3=None)
        metas = {"tested_base64.txt": {
            "etag": f'"{relay.md5_hex(FEED_A.encode())}"',
            "last_modified": NEW}}
        with patch.object(relay, "_head_meta",
                          side_effect=lambda url: metas.get(
                              url.rsplit("/", 1)[-1])):
            result = r.stage_b_check(files)
        self.assertEqual(result["to_upload"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_check_reports_retired_names_to_clean(self) -> None:
        """Rule (c) in --check mode: retired names present are reported."""
        files = {"tested_base64.txt": FEED_A.encode()}
        r = _relay(s3=None)
        metas = {"reachable_base64.txt": {"etag": '"abc"',
                                          "last_modified": OLD}}
        with patch.object(relay, "_head_meta",
                          side_effect=lambda url: metas.get(
                              url.rsplit("/", 1)[-1])):
            result = r.stage_b_check(files)
        self.assertEqual(result["to_clean"], ["reachable_base64.txt"])

    def test_collect_files_builds_index_page(self) -> None:
        github = FakeGitHub({"tested_base64.txt": FEED_A.encode()})
        r = _relay(github=github)
        with patch.object(relay, "upstream_file",
                          return_value=FEED_A.encode()):
            files, skipped = r._collect_files()
        self.assertIn("index.html", files)
        page = files["index.html"].decode()
        self.assertIn("tested_base64.txt", page)
        self.assertIn("https://repoget.s3.ir-central1.arvanstorage.ir/", page)

    def test_collect_files_skips_missing_upstream_feed(self) -> None:
        """A FEEDS entry absent from feed-data and upstream is skipped."""
        github = FakeGitHub({"tested_base64.txt": FEED_A.encode()})
        r = _relay(github=github)
        missing = "online_base64"
        def _fetch(stem: str) -> bytes:
            if stem == missing:
                raise relay.RelayError(
                    "GET https://raw.githubusercontent.com/mani-moh/navaroute/"
                    f"master/subs/{missing}.txt -> HTTP 404")
            return FEED_A.encode()
        with patch.object(relay, "upstream_file", side_effect=_fetch):
            files, skipped = r._collect_files()
        self.assertIn(f"{missing}.txt", skipped)
        self.assertNotIn(f"{missing}.txt", files)
        self.assertIn("index.html", files)


class TestRun(unittest.TestCase):
    def test_check_mode_writes_nothing(self) -> None:
        github = FakeGitHub({"tested_base64.txt": FEED_A.encode()})
        s3 = FakeS3()
        r = _relay(github=github, s3=s3)
        with patch.object(relay, "upstream_file",
                          return_value=FEED_A.encode()):
            with patch.object(relay, "_reachable", return_value=True):
                with patch.object(relay, "_head_meta", return_value=None):
                    status = r.run(check_only=True)
        self.assertTrue(status["github_reachable"])
        self.assertTrue(status["arvan_reachable"])
        self.assertEqual(github.puts, [])
        self.assertEqual(s3.uploaded, [])
        self.assertEqual(status["stage_b"]["status"], "check")

    def test_status_file_has_no_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "status.json"
            status = {"started": "x", "github_reachable": True,
                      "stage_a": {"status": "ok", "updated": []}}
            relay.write_status(path, status)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(FAKE_ACCESS, text)
            self.assertNotIn(FAKE_SECRET, text)
            self.assertEqual(json.loads(text)["github_reachable"], True)

    def test_print_status_never_prints_secrets(self) -> None:
        status = {"github_reachable": True, "arvan_reachable": True,
                  "stage_a": {"status": "ok", "updated": [], "deleted": []},
                  "stage_b": {"status": "ok", "uploaded": [], "cleaned": [],
                              "skipped": []},
                  "errors": []}
        buf = io.StringIO()
        with redirect_stdout(buf):
            relay._print_status(status)
        output = buf.getvalue()
        self.assertNotIn(FAKE_ACCESS, output)
        self.assertNotIn(FAKE_SECRET, output)
        self.assertNotIn(FAKE_TOKEN, output)

    def test_github_reachable_uses_authenticated_api(self) -> None:
        """Authenticated repo lookup = reachable; API error = not."""
        r = _relay(github=FakeGitHub())
        self.assertTrue(r._github_reachable())
        with patch.object(FakeGitHub, "request_json",
                          side_effect=relay.RelayError("HTTP 403")):
            self.assertFalse(r._github_reachable())

    def test_build_s3_returns_none_without_keys(self) -> None:
        self.assertIsNone(relay.build_s3({}, "repoget", relay.S3_ENDPOINT))


if __name__ == "__main__":
    unittest.main()
