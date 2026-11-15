"""Tests for the tier-2 owner-PC app (tools/tier2/tier2_pattn.py).

The WebAPI itself is not run here (no Windows host in CI); only the
upload path is tested, with the GitHub API (_gh) and raw fetches (_raw)
mocked. The Windows credential calls are guarded by os.name != "nt",
so the module imports cleanly on Linux.
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "tier2"))

import tier2_pattn  # noqa: E402
from src.models import SourceConfig  # noqa: E402
from src.parsers import parse_proxy_uri  # noqa: E402
from src.pattn_client import PattnError  # noqa: E402

SOURCE = SourceConfig("tier2", "Tier 2", "https://github.com/mani-moh/navaroute")

A = "vless://11111111-1111-1111-1111-111111111111@10.0.0.1:443#node-a"
B = "vless://22222222-2222-2222-2222-222222222222@10.0.0.2:443#node-b"
C = "vless://33333333-3333-3333-3333-333333333333@10.0.0.3:443#node-c"
D = "vless://44444444-4444-4444-4444-444444444444@10.0.0.4:443#node-d"


def _fp(uri: str) -> str:
    node = parse_proxy_uri(uri, SOURCE)
    assert node is not None
    return node.semantic_fingerprint()


def _feed_text(uris: list[str]) -> str:
    return "".join(uri + "\n" for uri in uris)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.app = tier2_pattn.Tier2App()
        # A passes (120 ms), B fails, C passes (50 ms), D never tested.
        self.app.results = {_fp(A): 120, _fp(B): None, _fp(C): 50}
        self.raw = {
            "/subs/all.txt": _feed_text([A, B, C, D]),
            "/subs/online.txt": _feed_text([A, B]),
            "/subs/online-week.txt": _feed_text([A, B, C]),
            "/subs/fastest-100.txt": _feed_text([A]),
            "/subs/balanced-500.txt": _feed_text([A, B, C, D]),
            "/subs/protocols/vless.txt": _feed_text([A, B, C]),
            "/subs/countries/de.txt": _feed_text([A, D]),
            "/subs/categories/reality.txt": _feed_text([B]),
            "/data/tier1_state.json": "{}",
            "/data/github_blocked.json": "{}",
        }
        self.tree = [
            {"type": "blob", "path": "subs/protocols/vless.txt"},
            {"type": "blob", "path": "subs/protocols/vless_base64.txt"},
            {"type": "blob", "path": "subs/protocols/vless-tested.txt"},
            {"type": "blob", "path": "subs/protocols/vless-tested-iran.txt"},
            {"type": "blob", "path": "subs/countries/de.txt"},
            {"type": "blob", "path": "subs/categories/reality.txt"},
            {"type": "blob", "path": "subs/all.txt"},
            {"type": "blob", "path": "README.md"},
        ]
        self.puts: dict[str, str] = {}
        self.pr_body = ""

    def _fake_gh(self, method, path, token, body=None):
        if method == "GET" and path == "/git/trees/master?recursive=1":
            return {"tree": self.tree}
        if method == "GET" and path == "/git/ref/heads/master":
            return {"object": {"sha": "abc123"}}
        if method == "POST" and path == "/git/refs":
            return {}
        if method == "GET" and path.startswith("/contents/"):
            raise PattnError("not found")
        if method == "PUT" and path.startswith("/contents/"):
            self.puts[path[len("/contents/"):]] = base64.b64decode(
                body["content"]).decode("utf-8")
            return {}
        if method == "POST" and path == "/pulls":
            self.pr_body = body["body"]
            return {"number": 7,
                    "html_url": "https://github.com/mani-moh/navaroute/pull/7"}
        raise AssertionError(f"unexpected _gh call: {method} {path}")

    def _fake_raw(self, path):
        if path in self.raw:
            return self.raw[path]
        raise PattnError(f"no such file: {path}")

    def _upload(self):
        with patch.object(tier2_pattn, "_gh", side_effect=self._fake_gh), \
                patch.object(tier2_pattn, "_raw", side_effect=self._fake_raw):
            return self.app.upload("fake-token")

    def test_upload_writes_slice_tested_iran_variants(self):
        url = self._upload()
        self.assertEqual(url, "https://github.com/mani-moh/navaroute/pull/7")
        # Core global variant: the passing configs, fastest delay first.
        self.assertEqual(self._rows("subs/tested-iran.txt"), [C, A])
        # Slice variants are the slice intersected with the Iran passing
        # set, sorted by Iran delay.
        self.assertEqual(self._rows("subs/protocols/vless-tested-iran.txt"), [C, A])
        self.assertEqual(self._rows("subs/countries/de-tested-iran.txt"), [A])
        # A slice with no passers still gets an empty file, not an error.
        self.assertEqual(self._rows("subs/categories/reality-tested-iran.txt"), [])
        # Base64 twins ride along for every variant.
        for name in ("tested-iran", "protocols/vless-tested-iran",
                     "countries/de-tested-iran", "categories/reality-tested-iran"):
            self.assertIn(f"subs/{name}_base64.txt", self.puts)
            decoded = base64.b64decode(
                self.puts[f"subs/{name}_base64.txt"]).decode("utf-8")
            self.assertEqual(decoded.splitlines(),
                             self._rows(f"subs/{name}.txt"))
        # The tree's base64 twins and existing tested variants are
        # outputs, never inputs: no variant is built from them.
        self.assertNotIn("subs/protocols/vless_base64-tested-iran.txt", self.puts)
        self.assertNotIn("subs/protocols/vless-tested-tested-iran.txt", self.puts)
        # iran_results.json stays core-only; slice variants are files only.
        results = json.loads(self.puts["data/iran_results.json"])
        self.assertEqual(set(results["feeds"]),
                         {"all", "online", "online-week", "fastest-100", "balanced-500"})
        self.assertEqual(results["feeds"]["all"], {"tested": 3, "passed": 2})
        self.assertEqual(results["feeds"]["online"], {"tested": 2, "passed": 1})
        self.assertEqual(results["totals"], {"tested": 12, "passed": 8})
        self.assertIn("3 slice -tested-iran variants", self.pr_body)

    def _rows(self, path: str) -> list[str]:
        text = self.puts[path]
        return [line for line in text.splitlines() if line and not line.startswith("#")]


if __name__ == "__main__":
    unittest.main()
