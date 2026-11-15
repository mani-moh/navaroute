"""Tests for the Blackout Kit (tools/blackout_kit/).

Stdlib only: no third-party packages are installed in CI, so the AES
tests use fixed NIST vectors and round-trips rather than the
`cryptography` oracle (the oracle check was run during development:
200 random cases, all matching).
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_mirror_page as bmp  # noqa: E402
from blackout_kit import crypto as bk_crypto  # noqa: E402
from blackout_kit import feeds as bk_feeds  # noqa: E402
from blackout_kit import publish as bk_publish  # noqa: E402
from blackout_kit import search_loop as bk_search  # noqa: E402
from blackout_kit import telegram_mine as bk_mine  # noqa: E402
from blackout_kit.aesgcm import SBOX, aes_gcm_decrypt, aes_gcm_encrypt  # noqa: E402
from blackout_kit.config import KitConfig  # noqa: E402
from blackout_kit.feeds import Finding, PublishedRegistry  # noqa: E402

FORBIDDEN = (
    "vpn", "proxy", "bypass", "censor", "filter", "config", "subscription",
    "navaroute", "xray", "vless", "vmess", "trojan", "shadowsocks",
    "hysteria", "tuic", "sing-box", "clash", "iran", "telegram",
)

KEY = bytes(range(32))


def _visible_text(page: str) -> str:
    page = re.sub(r"onclick=\"copyText\(this,'[^']*'\)\"", "", page)
    page = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    page = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page)
    return page.lower()


class TestAesGcm(unittest.TestCase):
    def test_sbox_spot_values(self) -> None:
        self.assertEqual(SBOX[0x00], 0x63)
        self.assertEqual(SBOX[0x53], 0xED)
        self.assertEqual(SBOX[0xFF], 0x16)

    def test_nist_aes256_block_vector(self) -> None:
        from blackout_kit.aesgcm import aes_encrypt_block

        block = aes_encrypt_block(
            bytes(range(32)), bytes.fromhex("00112233445566778899aabbccddeeff")
        )
        self.assertEqual(block.hex(), "8ea2b7ca516745bfeafc49904b496089")

    def test_nist_gcm_test_case_3(self) -> None:
        # All-zero key/IV, 16 zero bytes of plaintext (NIST SP 800-38D).
        ct, tag = aes_gcm_encrypt(bytes(32), bytes(12), bytes(16))
        self.assertEqual(ct.hex(), "cea7403d4d606b6e074ec5d3baf39d18")
        self.assertEqual(tag.hex(), "d0d1c8a799996bf0265b98b5d48ab919")

    def test_round_trip_various_lengths(self) -> None:
        for size in (0, 1, 15, 16, 17, 100, 1000):
            pt = bytes((i * 7) % 256 for i in range(size))
            ct, tag = aes_gcm_encrypt(KEY, b"0123456789ab", pt, b"aad")
            self.assertEqual(aes_gcm_decrypt(KEY, b"0123456789ab", ct, tag, b"aad"), pt)

    def test_tamper_detection(self) -> None:
        ct, tag = aes_gcm_encrypt(KEY, b"0123456789ab", b"secret payload", b"")
        bad_ct = bytearray(ct)
        bad_ct[0] ^= 1
        with self.assertRaises(ValueError):
            aes_gcm_decrypt(KEY, b"0123456789ab", bytes(bad_ct), tag, b"")
        bad_tag = bytearray(tag)
        bad_tag[-1] ^= 1
        with self.assertRaises(ValueError):
            aes_gcm_decrypt(KEY, b"0123456789ab", ct, bytes(bad_tag), b"")

    def test_wrong_aad_fails(self) -> None:
        ct, tag = aes_gcm_encrypt(KEY, b"0123456789ab", b"data", b"feed-a")
        with self.assertRaises(ValueError):
            aes_gcm_decrypt(KEY, b"0123456789ab", ct, tag, b"feed-b")


class TestCryptoEnvelope(unittest.TestCase):
    def test_envelope_round_trip_and_feed_binding(self) -> None:
        plain = b"vless://example-entry\nhttps://example.invalid/app.apk\n"
        text = bk_crypto.encrypt_text(plain, KEY, "kit-2_base64.txt")
        self.assertTrue(text.startswith("Qksx"))  # base64 of "BK1"
        self.assertEqual(bk_crypto.decrypt_text(text, KEY, "kit-2_base64.txt"), plain)
        with self.assertRaises(ValueError):
            bk_crypto.decrypt_text(text, KEY, "kit-1_base64.txt")

    def test_nonce_differs_each_time(self) -> None:
        a = bk_crypto.encrypt_bytes(b"same", KEY, "kit-1_base64.txt")
        b = bk_crypto.encrypt_bytes(b"same", KEY, "kit-1_base64.txt")
        self.assertNotEqual(a, b)

    def test_key_text_round_trip(self) -> None:
        self.assertEqual(bk_crypto.key_from_text(bk_crypto.key_to_text(KEY)), KEY)
        with self.assertRaises(ValueError):
            bk_crypto.key_from_text("not-base64!!")
        with self.assertRaises(ValueError):
            bk_crypto.key_from_text(bk_crypto.key_to_text(bytes(16)))

    def test_key_file_mode_600_and_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "key"
            bk_crypto.save_key_file(KEY, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(bk_crypto.load_key_file(path), KEY)
            os.chmod(path, 0o644)
            with self.assertRaises(PermissionError):
                bk_crypto.load_key_file(path)

    def test_load_key_prefers_env(self) -> None:
        self.assertEqual(
            bk_crypto.load_key(env={"BLACKOUT_KEY": bk_crypto.key_to_text(KEY)}), KEY
        )

    def test_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "plain.txt"
            src.write_bytes(b"hello kit")
            enc = Path(d) / "enc.txt"
            out = Path(d) / "out.txt"
            bk_crypto.encrypt_file(src, enc, KEY, "kit-4_base64.txt")
            bk_crypto.decrypt_file(enc, out, KEY, "kit-4_base64.txt")
            self.assertEqual(out.read_bytes(), b"hello kit")


class TestFeedsAndRegistry(unittest.TestCase):
    def test_special_feed_names_and_neutral_labels(self) -> None:
        for filename, label, desc, _kind in bk_feeds.SPECIAL_FEEDS:
            self.assertTrue(filename.startswith("kit-"))
            self.assertTrue(filename.endswith("_base64.txt"))
            for word in FORBIDDEN:
                self.assertNotIn(word, label.lower())
                self.assertNotIn(word, desc.lower())

    def test_content_hash_stable(self) -> None:
        self.assertEqual(bk_feeds.content_hash("abc"), bk_feeds.content_hash(b"abc"))

    def test_is_newer(self) -> None:
        self.assertTrue(bk_feeds.is_newer(10.0, None))
        self.assertTrue(bk_feeds.is_newer(10.0, 9.0))
        self.assertFalse(bk_feeds.is_newer(10.0, 10.0))
        self.assertFalse(bk_feeds.is_newer(9.0, 10.0))

    def test_registry_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            registry = PublishedRegistry(Path(d) / "published.json")
            finding = Finding(kind="entries", text="vless://abc", source="@chan")
            self.assertEqual(registry.new_findings([finding, finding]), [finding])
            registry.mark_published(finding, "kit-2_base64.txt")
            self.assertEqual(registry.new_findings([finding]), [])
            registry.save()
            reloaded = PublishedRegistry(Path(d) / "published.json")
            self.assertTrue(reloaded.is_published(finding))
            self.assertEqual(len(reloaded), 1)

    def test_group_findings_dedupes_and_skips_unknown_kinds(self) -> None:
        findings = [
            Finding(kind="entries", text="vless://a"),
            Finding(kind="entries", text="vless://a"),
            Finding(kind="nope", text="x"),
        ]
        grouped = bk_publish.group_findings(findings)
        self.assertEqual(list(grouped), ["entries"])
        self.assertEqual(len(grouped["entries"]), 1)


class TestPublishNewestWins(unittest.TestCase):
    def test_writes_then_skips_identical_then_overwrites_newer(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            feed_dir = Path(d)
            findings = [Finding(kind="entries", text="vless://one", found_at=1000.0)]
            result = bk_publish.build_special_feeds(findings, KEY, feed_dir, now=1000.0)
            self.assertEqual(result.written, ["kit-2_base64.txt"])
            plain = bk_crypto.decrypt_text(
                (feed_dir / "kit-2_base64.txt").read_text(), KEY, "kit-2_base64.txt"
            )
            self.assertIn(b"vless://one", plain)

            # Same content, later build: skipped (identical).
            result = bk_publish.build_special_feeds(findings, KEY, feed_dir, now=2000.0)
            self.assertEqual(result.written, [])
            self.assertIn("kit-2_base64.txt", result.skipped_newer)

            # Newer content, later build: overwritten.
            findings2 = [Finding(kind="entries", text="vless://two", found_at=3000.0)]
            result = bk_publish.build_special_feeds(findings2, KEY, feed_dir, now=3000.0)
            self.assertEqual(result.written, ["kit-2_base64.txt"])

            # Older build must never overwrite a newer file.
            result = bk_publish.build_special_feeds(findings, KEY, feed_dir, now=1500.0)
            self.assertEqual(result.written, [])
            self.assertIn("kit-2_base64.txt", result.skipped_newer)
            plain = bk_crypto.decrypt_text(
                (feed_dir / "kit-2_base64.txt").read_text(), KEY, "kit-2_base64.txt"
            )
            self.assertIn(b"vless://two", plain)

    def test_empty_kinds_are_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = bk_publish.build_special_feeds(
                [Finding(kind="apps", text="https://example.invalid/a.apk")],
                KEY, Path(d), now=1000.0,
            )
            self.assertEqual(result.written, ["kit-3_base64.txt"])
            self.assertIn("kit-1_base64.txt", result.empty)
            self.assertFalse((Path(d) / "kit-1_base64.txt").exists())


class TestTelegramScope(unittest.TestCase):
    def test_allowed_sources(self) -> None:
        for source in ("me", "saved", "@somechannel", "somechannel",
                       "https://t.me/somechannel", "https://t.me/+AbCdEfGhIjKlMnOp"):
            self.assertTrue(bk_mine.is_allowed_source(source), source)

    def test_forbidden_sources(self) -> None:
        for source in ("", "+989123456789", "123456789", "204376459",
                       "some user", "http://example.invalid/x", "@ab"):
            self.assertFalse(bk_mine.is_allowed_source(source), source)

    def test_assert_allowed_source_raises(self) -> None:
        with self.assertRaises(ValueError):
            bk_mine.assert_allowed_source("+989123456789")
        with self.assertRaises(ValueError):
            bk_mine.assert_allowed_source("123456")

    def test_module_never_lists_dialogs(self) -> None:
        # Static guard: the miner must never enumerate dialogs (the only
        # way private chats could leak in). If this trips, the scope rule
        # has been broken. Docstrings/comments are stripped first: only
        # actual code counts.
        source = (Path(__file__).resolve().parent.parent
                  / "tools" / "blackout_kit" / "telegram_mine.py").read_text()
        source = re.sub(r'"""(?:.|\n)*?"""', "", source, count=1)  # module docstring
        source = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith("#")
        )
        for banned in ("iter_dialogs(", "get_dialogs(", "get_participants("):
            self.assertNotIn(banned, source)

    def test_miner_only_touches_configured_sources(self) -> None:
        class FakeMessage:
            def __init__(self, msg_id, text):
                self.id = msg_id
                self.text = text

        class FakeClient:
            def __init__(self):
                self.asked: list = []

            async def get_messages(self, entity, limit=100, min_id=0):
                self.asked.append(entity)
                if entity == "@publicchan":
                    return [FakeMessage(5, "try https://example.invalid/app.apk")]
                if entity == "me":
                    return [FakeMessage(9, "vless://saved-entry")]
                return []

            async def iter_dialogs(self, *a, **k):  # pragma: no cover
                raise AssertionError("miner must never list dialogs")

        with tempfile.TemporaryDirectory() as d:
            state = bk_mine.SourceState.load(Path(d) / "state.json")
            miner = bk_mine.TelegramMiner(FakeClient(), ["@publicchan", "me"], state)
            findings = asyncio.run(miner.fetch_all())
            self.assertEqual(miner.client.asked, ["@publicchan", "me"])
            kinds = {f.kind: f.text for f in findings}
            self.assertEqual(kinds["apps"], "https://example.invalid/app.apk")
            self.assertEqual(kinds["entries"], "vless://saved-entry")
            self.assertEqual(state.last_id["@publicchan"], 5)
            self.assertEqual(state.last_id["me"], 9)

    def test_extract_only_actionable_items(self) -> None:
        findings = bk_mine.extract_findings(
            "hello everyone, here is a long story about how the net went "
            "down yesterday and what we tried", "@chan")
        self.assertEqual(findings, [])
        findings = bk_mine.extract_findings(
            "new link https://example.invalid/app.apk and 1.2.3.4:8080", "@chan")
        self.assertEqual({f.kind for f in findings}, {"apps", "resolvers"})


class TestSessionEncryption(unittest.TestCase):
    def test_session_round_trip_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            session = Path(d) / "telegram.session"
            session.write_bytes(b"fake-session-bytes")
            enc = bk_mine.store_session_encrypted(session, KEY)
            self.assertFalse(session.exists())
            self.assertEqual(stat.S_IMODE(enc.stat().st_mode), 0o600)
            with bk_mine.decrypted_session(enc, KEY) as tmp:
                self.assertEqual(tmp.read_bytes(), b"fake-session-bytes")
                self.assertEqual(stat.S_IMODE(tmp.stat().st_mode), 0o600)
            self.assertFalse(tmp.exists())


class TestSearchLoopGating(unittest.TestCase):
    def _monitor_script(self, directory: Path, verdict: str) -> Path:
        script = directory / "check_blackout.py"
        script.write_text(
            "import json, sys\n"
            f"print(json.dumps({{'verdict': '{verdict}', 'score': 5}}))\n"
            "sys.exit(0)\n"
        )
        return script

    def test_verdict_reading(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = self._monitor_script(Path(d), "BLACKOUT")
            self.assertEqual(bk_search.read_verdict(path), ("BLACKOUT", 5))
            self.assertEqual(bk_search.read_verdict(Path(d) / "missing.py"), ("UNKNOWN", None))

    def test_should_escalate_only_blackout_or_degraded(self) -> None:
        self.assertTrue(bk_search.should_escalate("BLACKOUT"))
        self.assertTrue(bk_search.should_escalate("DEGRADED"))
        self.assertFalse(bk_search.should_escalate("OK"))
        self.assertFalse(bk_search.should_escalate("UNKNOWN"))

    def test_round_runs_only_when_escalated_and_due(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            cfg = KitConfig(
                sources=["me"],
                search_queries=["test query"],
                monitor_path=self._monitor_script(base, "BLACKOUT"),
                state_dir=base,
            )
            loop = bk_search.SearchLoop(cfg=cfg, web_search=lambda q: ["https://example.invalid/m"])
            found = loop.run_round(miner=None)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].kind, "notes")
            # Backoff: immediately after a round, not due yet.
            self.assertEqual(loop.run_round(miner=None), [])
            # OK verdict: no search at all, even when due.
            cfg_ok = KitConfig(
                sources=["me"], search_queries=["test query"],
                monitor_path=self._monitor_script(base, "OK"), state_dir=base,
            )
            loop_ok = bk_search.SearchLoop(cfg=cfg_ok, web_search=lambda q: ["https://x.invalid/y"])
            loop_ok.search_state.last_run_at = 0.0
            loop_ok.search_state.empty_rounds = 0
            self.assertEqual(loop_ok.run_round(miner=None), [])

    def test_backoff_grows_after_empty_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            state = bk_search.SearchState.load(Path(d) / "search.json")
            self.assertEqual(state.backoff_seconds(), 900)
            state.record(found_new=False)
            self.assertEqual(state.backoff_seconds(), 1800)
            state.empty_rounds = 99
            self.assertEqual(state.backoff_seconds(), 7200)
            state.record(found_new=True)
            self.assertEqual(state.empty_rounds, 0)


class TestMirrorPageKitFeeds(unittest.TestCase):
    def test_kit_rows_appear_with_neutral_labels(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            feeds_dir = Path(d) / "feeds"
            feeds_dir.mkdir()
            for name in ("tested_base64.txt", "kit-1_base64.txt", "kit-2_base64.txt",
                         "kit-3_base64.txt", "kit-4_base64.txt"):
                (feeds_dir / name).write_text("Qksx\n")
            out_path = bmp.build(feeds_dir, "https://example.invalid/files", Path(d) / "site")
            page = out_path.read_text(encoding="utf-8")
            for label in ("extra · 1", "extra · 2", "extra · 3", "extra · 4"):
                self.assertIn(label, page)
            for name in ("kit-1_base64.txt", "kit-4_base64.txt"):
                self.assertIn(f"https://example.invalid/files/{name}", page)
            visible = _visible_text(page)
            for word in FORBIDDEN:
                self.assertNotIn(word, visible, f"forbidden word {word!r} on mirror page")


class TestDecryptHelper(unittest.TestCase):
    def test_generated_helper_matches_checked_in_file(self) -> None:
        from blackout_kit import make_decrypt_helper

        kit_dir = Path(__file__).resolve().parent.parent / "tools" / "blackout_kit"
        self.assertEqual((kit_dir / "decrypt_helper.py").read_text(encoding="utf-8"),
                         make_decrypt_helper.build())

    def test_helper_decrypts_real_envelope(self) -> None:
        import subprocess

        kit_dir = Path(__file__).resolve().parent.parent / "tools" / "blackout_kit"
        with tempfile.TemporaryDirectory() as d:
            key_file = Path(d) / "key"
            bk_crypto.save_key_file(KEY, key_file)
            feed = Path(d) / "kit-2_base64.txt"
            feed.write_text(
                bk_crypto.encrypt_text(b"vless://helper-check\n", KEY, "kit-2_base64.txt"))
            proc = subprocess.run(
                [sys.executable, str(kit_dir / "decrypt_helper.py"),
                 "--key-file", str(key_file), str(feed)],
                capture_output=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(proc.stdout, b"vless://helper-check\n")
            # Wrong feed name must fail (AAD binding).
            proc = subprocess.run(
                [sys.executable, str(kit_dir / "decrypt_helper.py"),
                 "--key-file", str(key_file), "--feed-name", "kit-1_base64.txt", str(feed)],
                capture_output=True, timeout=60,
            )
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
