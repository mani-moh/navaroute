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

FAKE_ACCESS = "AKIAFAKEACCESSKEY0000"
FAKE_SECRET = "fake-secret-key-00000000000000000000000000000000"
FAKE_TOKEN = "ghp_faketoken000000000000000000000000000000"

FEED_A = "dmxlc3M6Ly8xMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTFA\n"
FEED_B = "dmxlc3M6Ly8yMjIyMjIyMi0yMjIyLTIyMjItMjIyMi0yMjIyMjIyMjIyMjJg\n"


class FakeGitHub:
    """In-memory feed-data repo: {name: (content bytes, sha)}."""

    def __init__(self, files: dict[str, bytes] | None = None,
                 token: str | None = FAKE_TOKEN) -> None:
        self.token = token
        self.files = {name: (data, f"sha-{name}")
                      for name, data in (files or {}).items()}
        self.puts: list[str] = []
        self.deletes: list[str] = []

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
    """In-memory S3 bucket: {name: etag}; records uploads/deletes."""

    def __init__(self, objects: dict[str, str] | None = None) -> None:
        self.objects = dict(objects or {})
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    def list_objects(self) -> dict[str, str]:
        return dict(self.objects)

    def upload(self, name: str, data: bytes, content_type: str) -> None:
        self.objects[name] = f'"{relay.md5_hex(data)}"'
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
    def test_push_uploads_only_changed_and_deletes_stale(self) -> None:
        s3 = FakeS3({"tested_base64.txt": f'"{relay.md5_hex(FEED_A.encode())}"',
                     "old_base64.txt": '"abc"',
                     "notes.txt": '"abc"'})
        files = {"tested_base64.txt": FEED_A.encode(),
                 "online_base64.txt": FEED_B.encode(),
                 "index.html": b"<html></html>"}
        r = _relay(s3=s3)
        result = r.stage_b_push(files)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("tested_base64.txt", result["uploaded"])
        self.assertIn("online_base64.txt", result["uploaded"])
        self.assertIn("index.html", result["uploaded"])
        self.assertIn("old_base64.txt", result["deleted"])
        self.assertIn("notes.txt", result["deleted"])

    def test_check_reports_would_upload_without_credentials(self) -> None:
        files = {"tested_base64.txt": FEED_A.encode(),
                 "index.html": b"<html></html>"}
        r = _relay(s3=None)
        etags = {"tested_base64.txt": f'"{relay.md5_hex(FEED_A.encode())}"'}
        with patch.object(relay, "_head_etag",
                          side_effect=lambda url: etags.get(url.rsplit("/", 1)[-1])):
            with patch.object(relay.Relay, "_public_object_names",
                              return_value=["tested_base64.txt"]):
                result = r.stage_b_check(files)
        self.assertEqual(result["status"], "check")
        self.assertEqual(result["to_upload"], ["index.html"])
        self.assertEqual(result["to_delete"], [])

    def test_collect_files_builds_index_page(self) -> None:
        github = FakeGitHub({"tested_base64.txt": FEED_A.encode()})
        r = _relay(github=github)
        with patch.object(relay, "upstream_file",
                          return_value=FEED_A.encode()):
            files = r._collect_files()
        self.assertIn("index.html", files)
        page = files["index.html"].decode()
        self.assertIn("tested_base64.txt", page)
        self.assertIn("https://repoget.s3.ir-central1.arvanstorage.ir/", page)


class TestRun(unittest.TestCase):
    def test_check_mode_writes_nothing(self) -> None:
        github = FakeGitHub({"tested_base64.txt": FEED_A.encode()})
        s3 = FakeS3()
        r = _relay(github=github, s3=s3)
        with patch.object(relay, "upstream_file",
                          return_value=FEED_A.encode()):
            with patch.object(relay, "_reachable", return_value=True):
                with patch.object(relay, "_head_etag", return_value=None):
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
                  "stage_b": {"status": "ok", "uploaded": [], "deleted": []},
                  "errors": []}
        buf = io.StringIO()
        with redirect_stdout(buf):
            relay._print_status(status)
        output = buf.getvalue()
        self.assertNotIn(FAKE_ACCESS, output)
        self.assertNotIn(FAKE_SECRET, output)
        self.assertNotIn(FAKE_TOKEN, output)

    def test_build_s3_returns_none_without_keys(self) -> None:
        self.assertIsNone(relay.build_s3({}, "repoget", relay.S3_ENDPOINT))


if __name__ == "__main__":
    unittest.main()
