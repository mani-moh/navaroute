from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import categorizer as publisher
from src.models import ProxyNode, SourceStats
from src.parsers import parse_proxy_uri

START = "<!-- NAVAROUTE:STATS:START -->"
END = "<!-- NAVAROUTE:STATS:END -->"
HISTORICAL = "2024-01-02T03:04:05+00:00"


class TestPublisher(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.out = self.root / "feeds"
        self.readme = self.root / "README.md"
        self.readme.write_bytes(f"Maintained prose\r\n{START}\nold stats\n{END}\r\nSentinel prose\r\n".encode())
        (self.root / "STATUS.md").write_bytes(b"old status\r\n")
        self.now = datetime.now(timezone.utc).isoformat()

    def node(self, credential="public", scheme="trojan", **kwargs):
        if scheme == "ss":
            credential = "aes-128-gcm:" + credential
        if scheme == "tuic":
            credential = "user:" + credential
        return ProxyNode(scheme, "public.example", 443, credential, **kwargs)

    def publish(self, nodes=None, **kwargs):
        # Only the synthetic fixture root may receive documentation projections.
        with patch.object(publisher, "REPOSITORY_ROOT", self.root, create=True):
            return publisher.publish_subscriptions(
                [self.node()] if nodes is None else nodes, [],
                kwargs.pop("stats", []), str(self.out), **kwargs)

    def uris(self, name):
        return [line for line in (self.out / name).read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")]

    def bytes(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_disabled_and_failed_checks_never_promote_upstream_claims(self):
        for ran in (False, True):
            with self.subTest(ran=ran):
                n = self.node(is_verified_source=True, passed_healthcheck=True, latency_ms=5)
                counts = self.publish([n], healthcheck_ran=ran)
                self.assertEqual(counts["all.txt"], 1)
                self.assertEqual(counts["reachable.txt"], 0)
                self.assertEqual(counts["verified.txt"], 0)
                self.assertEqual(counts["top100.txt"], 0)
                self.assertEqual(self.uris("verified.txt"), [])
        dated = self.node(passed_healthcheck=True, latency_ms=5, healthcheck_kind="socket", healthchecked_at=self.now)
        self.assertEqual(self.publish([dated], healthcheck_ran=False)["reachable.txt"], 0)

    def test_only_timestamped_socket_passes_rank_latency_first_with_stable_ties(self):
        healthy = [self.node("slow", is_verified_source=True, passed_healthcheck=True,
                             latency_ms=90, healthcheck_kind="socket", healthchecked_at=self.now),
                   self.node("fast", passed_healthcheck=True, latency_ms=5,
                             healthcheck_kind="socket", healthchecked_at=self.now),
                   self.node("tie", passed_healthcheck=True, latency_ms=5,
                             healthcheck_kind="socket", healthchecked_at=self.now)]
        excluded = [self.node("legacy", passed_healthcheck=True, latency_ms=1),
                    self.node("xray", passed_healthcheck=True, latency_ms=1,
                              healthcheck_kind="xray", healthchecked_at=HISTORICAL),
                    self.node("undated", passed_healthcheck=True, latency_ms=1, healthcheck_kind="socket"),
                    self.node("nan", passed_healthcheck=True, latency_ms=float("nan"),
                              healthcheck_kind="socket", healthchecked_at=self.now),
                    self.node("future", passed_healthcheck=True, latency_ms=1,
                              healthcheck_kind="socket", healthchecked_at="2999-01-01T00:00:00+00:00")]
        for scheme in ("hysteria2", "tuic"):
            excluded.append(self.node("udp", scheme, passed_healthcheck=True, latency_ms=1,
                                      healthcheck_kind="socket", healthchecked_at=self.now))
        counts = self.publish(healthy + excluded, healthcheck_ran=True)
        self.assertEqual(counts["reachable.txt"], 3)
        ranked = [parse_proxy_uri(uri).credential for uri in self.uris("top100.txt")]
        self.assertEqual(set(ranked[:2]), {"fast", "tie"})
        self.assertEqual(ranked[-1], "slow")
        self.assertEqual(self.uris("reachable.txt"), self.uris("verified.txt"))
        self.publish(list(reversed(healthy + excluded)), healthcheck_ran=True)
        self.assertEqual([parse_proxy_uri(uri).credential for uri in self.uris("top100.txt")], ranked)

    def test_top100_is_bounded_and_plain_base64_connections_are_identical(self):
        nodes = [self.node(str(i), passed_healthcheck=True, latency_ms=i,
                           healthcheck_kind="socket", healthchecked_at=self.now) for i in range(105)]
        counts = self.publish(nodes, healthcheck_ran=True)
        self.assertEqual(counts["top100.txt"], 100)
        self.assertEqual(parse_proxy_uri(self.uris("top100.txt")[-1]).credential, "99")
        for name in ("all", "reachable", "verified", "top100", "lite500"):
            encoded = (self.out / f"{name}_base64.txt").read_bytes()
            self.assertEqual(base64.b64decode(encoded).decode().splitlines(), self.uris(f"{name}.txt"))

    def test_lite500_balances_six_protocols_without_dropping_small_buckets(self):
        protocols = ("vless", "vmess", "trojan", "ss", "hysteria2", "tuic")
        nodes = [self.node(str(i), p) for p in protocols for i in range(100)]
        counts = self.publish(nodes)
        lite = [parse_proxy_uri(uri).scheme for uri in self.uris("lite500.txt")]
        self.assertEqual(len(lite), 500)
        self.assertEqual([lite.count(p) for p in protocols], [84, 84, 83, 83, 83, 83])
        self.assertEqual([counts[f"protocols/{p}.txt"] for p in protocols], [100] * 6)

    def test_special_is_supplied_parameters_legacy_mirror_and_executable_json_retired(self):
        counts = self.publish([self.node(params={"fm": "1"}), self.node("ordinary")])
        self.assertIn("categories/navaroute-special.txt", counts)
        self.assertEqual(counts["categories/navaroute-special.txt"], 1)
        self.assertEqual(counts["categories/navaroute-special_base64.txt"], 1)
        self.assertEqual(self.uris("categories/navaroute-special.txt"), self.uris("categories/pattn-special.txt"))
        self.assertEqual((self.out / "categories/navaroute-special_base64.txt").read_bytes(),
                         (self.out / "categories/pattn-special_base64.txt").read_bytes())
        for name in ("supplied-parameters.txt", "supplied-parameters_base64.txt"):
            self.assertNotIn(f"categories/{name}", counts)
            self.assertFalse((self.out / "categories" / name).exists())
        self.assertEqual(json.loads((self.out / "categories/serverless-fragment.json").read_text()), [])
        text = self.readme.read_text(encoding="utf-8")
        self.assertIn("supplied", text.lower())
        self.assertIn("legacy", text.lower())
        self.assertIn("reachability—not credential", text.lower())
        self.assertNotIn("subscription-userinfo", (self.out / "all.txt").read_text())
        title = (self.out / "verified.txt").read_text().splitlines()[0].removeprefix("#profile-title: base64:")
        decoded_title = base64.b64decode(title).decode("utf-8")
        self.assertIn("NavaRoute", decoded_title)
        self.assertIn("Deprecated: reachability—not credential verified", decoded_title)

    def test_empty_invalid_or_total_fetch_failure_preserves_all_old_bytes(self):
        self.publish()
        before = self.bytes()
        for nodes, stats in (([], []), ([ProxyNode("trojan", "public.example", 0, "public")], []),
                             ([self.node(params={"nested": {"lost": True}})], []),
                             ([self.node()], [SourceStats("failed", "Fail", "https://secret.invalid/?token=secret", error="secret")])):
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                self.publish(nodes, stats=stats)
            self.assertEqual(self.bytes(), before)

    def test_late_render_failure_preserves_previous_feeds_and_docs(self):
        self.publish()
        before = self.bytes()
        bad = self.node("bad", params={"nested": {"unrepresentable": True}})
        with self.assertRaises(ValueError):
            self.publish([self.node("new"), bad])
        self.assertEqual(self.bytes(), before)

    def test_late_stage_write_failure_preserves_previous_generation(self):
        from src.storage import atomic_write_text
        self.publish()
        before = self.bytes()
        calls = []
        def fail_second(path, content):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("private stage error")
            atomic_write_text(path, content)
        with patch.object(publisher, "atomic_write_text", side_effect=fail_second, create=True):
            with self.assertRaises(OSError):
                self.publish([self.node("new")])
        self.assertEqual(self.bytes(), before)

    def test_late_replacement_failure_rolls_back_every_file_and_docs(self):
        self.publish()
        before = self.bytes()
        original = Path.replace
        projections = []
        def fail_late(path, target):
            if Path(target).is_relative_to(self.out) or Path(target) == self.root / "STATUS.md":
                projections.append(target)
                # Incremental publishing projects only changed feeds
                # (3rd projection here is all_base64.txt), so fail early.
                if len(projections) == 3:
                    raise OSError("late projection error")
            return original(path, target)
        with patch.object(Path, "replace", new=fail_late), self.assertRaises(OSError):
            self.publish([self.node("new")])
        self.assertEqual(self.bytes(), before)

    def test_failed_rollback_recovers_previous_generation_before_next_stage(self):
        self.publish()
        before = self.bytes()
        original = Path.replace
        calls = []
        def interrupted(path, target):
            if Path(target).is_relative_to(self.out):
                calls.append(target)
                if len(calls) >= 3:
                    raise OSError("storage temporarily unavailable")
            return original(path, target)
        with patch.object(Path, "replace", new=interrupted), self.assertRaises(OSError):
            self.publish([self.node("new")])
        with patch.object(publisher.tempfile, "TemporaryDirectory", side_effect=OSError("new staging unavailable")), self.assertRaises(OSError):
            self.publish([self.node("next")])
        self.assertEqual(self.bytes(), before)

    def test_stale_owned_countries_removed_unrelated_and_owner_files_unchanged(self):
        self.publish([self.node(str(i), country_code="DE") for i in range(5)])
        sentinels = {self.out / "countries/note.txt": b"unrelated", self.out / "countries/abc.txt": b"other",
                     self.out / "iran-tested/owner.json": b"evidence", self.root / "data/iran-verification/result.json": b"owner",
                     self.root / "iran-results/result.json": b"protected"}
        for path, value in sentinels.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        counts = self.publish()
        self.assertFalse((self.out / "countries/de.txt").exists())
        self.assertFalse((self.out / "countries/de_base64.txt").exists())
        self.assertNotIn("countries/de.txt", counts)
        self.assertTrue(all(path.read_bytes() == value for path, value in sentinels.items()))

    def test_missing_duplicate_or_reversed_markers_fail_before_projection(self):
        self.publish()
        for content in ("no markers", f"{START}{START}{END}", f"{START}{END}{END}", f"{END}{START}"):
            self.readme.write_text(content)
            before = self.bytes()
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.publish([self.node("new")])
            self.assertEqual(self.bytes(), before)

    def test_custom_paths_counts_links_digest_and_historical_time_are_consistent(self):
        original = self.readme.read_bytes()
        self.out = self.root / "custom/nested"
        stats = [SourceStats("ok", "OK", "https://private.invalid/?token=secret", success=True),
                 SourceStats("usable", "Usable", "https://example.com", success=True, raw_count=1, unique_count=1),
                 SourceStats("fail", "Failure", "https://example.com", error="secret credential URL")]
        counts = self.publish(healthcheck_ran=False, snapshot_time=HISTORICAL, stats=stats)
        self.assertEqual(counts["all.txt"], 1)
        self.assertFalse(any(key.startswith(("/", "custom/", "subs/")) or "\\" in key for key in counts))
        self.assertEqual({p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()}, set(counts))
        readme = self.readme.read_bytes()
        self.assertEqual(readme.split(START.encode())[0], original.split(START.encode())[0])
        self.assertEqual(readme.split(END.encode())[1], original.split(END.encode())[1])
        self.assertIn(b"custom/nested/all.txt", readme)
        self.assertIn(hashlib.sha256((self.out / "all.txt").read_bytes()).hexdigest().encode(), readme)
        for content in (readme.decode(), (self.root / "STATUS.md").read_text(), (self.out / "all.txt").read_text()):
            self.assertIn(HISTORICAL, content)
            self.assertNotIn("secret", content)
            self.assertNotIn("private.invalid", content)
        status = (self.root / "STATUS.md").read_text()
        self.assertIn("Fetch-successful Sources | 2", status)
        self.assertIn("Sources Yielding Usable Nodes | 1", status)
        self.assertNotIn("owner test", (readme.decode() + status).lower())

    def test_external_output_never_updates_repository_or_adjacent_docs(self):
        before = self.bytes()
        with tempfile.TemporaryDirectory() as outside:
            self.out = Path(outside) / "external"
            adjacent = Path(outside) / "README.md"
            adjacent.write_bytes(b"outside sentinel")
            counts = self.publish(update_docs=True)
            self.assertEqual(counts["all.txt"], 1)
            self.assertEqual(adjacent.read_bytes(), b"outside sentinel")
            self.assertFalse((Path(outside) / "STATUS.md").exists())
            self.assertNotIn(b"github", (self.out / "all.txt").read_bytes().lower())
        self.assertEqual(self.bytes(), before)

    def test_invalid_snapshot_times_preserve_old_generation(self):
        self.publish()
        before = self.bytes()
        for value in ("2024-01-01", "2024-01-01T00:00:00", "2024-01-01T00:00:00+01:00", "not-time", 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.publish(snapshot_time=value)
            self.assertEqual(self.bytes(), before)

    def test_plain_total_and_encoded_caps_fail_before_any_large_projection(self):
        self.publish()
        before = self.bytes()
        for name, limit in (("MAX_PLAIN_BYTES", 20), ("MAX_GENERATION_BYTES", 100), ("MAX_ENCODED_BYTES", 20)):
            with self.subTest(cap=name), patch.object(publisher, name, limit, create=True), self.assertRaises(ValueError):
                self.publish([self.node("new")], update_docs=False)
            self.assertEqual(self.bytes(), before)

    def test_protected_output_and_country_traversal_rejected_without_writes(self):
        before = self.bytes()
        for output in ("data", "data/iran-verification", "iran-results", "subs/iran-tested", "local_subs"):
            self.out = self.root / output
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.publish(update_docs=False)
            self.assertEqual(self.bytes(), before)
        self.out = self.root / "feeds"
        with self.assertRaises(ValueError):
            self.publish([self.node(str(i), country_code="../../private") for i in range(5)])
        self.assertEqual(self.bytes(), before)

    def assert_protected_short_name_rejected(self, relative):
        import ctypes
        from ctypes import wintypes
        protected = self.root / "iran-results"
        target = protected / relative
        target.mkdir(parents=True)
        (target / "all.txt").write_bytes(b"synthetic protected generation")
        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = get_short(str(protected), buffer, len(buffer))
        self.assertTrue(0 < size < len(buffer), "GetShortPathNameW failed for synthetic directory")
        short = Path(buffer.value)
        if short.name.lower() == protected.name.lower():
            self.skipTest("NTFS short names unavailable for the synthetic directory")
        self.assertTrue(short.name.upper().startswith("IRAN-R~"), "Expected an actual protected-directory short-name alias")
        self.assertEqual(short.resolve(), protected)
        self.out = short / relative
        before = self.bytes()
        with patch.object(publisher, "_recover", wraps=publisher._recover) as recover, \
             patch.object(publisher.tempfile, "TemporaryDirectory", wraps=publisher.tempfile.TemporaryDirectory) as stage:
            with self.assertRaises(ValueError):
                self.publish(update_docs=False)
            recover.assert_not_called()
            stage.assert_not_called()
        self.assertEqual(self.bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows NTFS short-name fixture")
    def test_protected_root_short_name_rejected_before_recovery_or_staging(self):
        self.assert_protected_short_name_rejected("")

    @unittest.skipUnless(os.name == "nt", "Windows NTFS short-name fixture")
    def test_protected_ancestor_short_name_rejected_before_recovery_or_staging(self):
        self.assert_protected_short_name_rejected("public/feeds")

    def test_actual_failed_socket_observation_remains_failed_in_aggregate(self):
        n = self.node(healthcheck_kind="socket", healthchecked_at=self.now)
        self.publish([n], healthcheck_ran=True)
        self.assertIn("[socket:failed]", urllib.parse.unquote(self.uris("all.txt")[0]))
        self.assertEqual(self.uris("reachable.txt"), [])

    def test_unowned_recovery_target_never_reads_private_file(self):
        transaction = self.root / ".feeds.navaroute-publish"
        transaction.mkdir()
        private = self.root / "private.env"
        private.write_bytes(b"private sentinel")
        (transaction / "manifest.json").write_text(json.dumps({"state": "prepared", "files": {"feed/../private.env": True}}))
        before = self.bytes()
        original = Path.open
        def no_private(path, *args, **kwargs):
            if path == private:
                raise AssertionError("Recovery attempted private read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", new=no_private), self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.bytes(), before)

    def test_unapproved_special_alias_is_not_an_owned_recovery_role(self):
        transaction = self.root / ".feeds.navaroute-publish"
        transaction.mkdir()
        (transaction / "manifest.json").write_text(json.dumps({
            "state": "prepared", "files": {"feed/categories/supplied-parameters.txt": False}}))
        before = self.bytes()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.bytes(), before)

    def test_lexically_internal_but_external_output_does_not_modify_docs(self):
        before = self.bytes()
        with tempfile.TemporaryDirectory(dir=self.root.parent) as outside:
            self.out = self.root / ".." / Path(outside).name / "external"
            self.publish()
            self.assertEqual(self.bytes(), before)

    def test_owned_target_hardlink_to_private_file_is_rejected_before_read(self):
        private = self.root / "private.env"
        private.write_bytes(b"private sentinel")
        self.out.mkdir()
        os.link(private, self.out / "all.txt")
        before = self.bytes()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.bytes(), before)

    def test_generation_timestamp_is_actual_utc_and_shared_by_headers_and_docs(self):
        before = datetime.now(timezone.utc)
        self.publish()
        after = datetime.now(timezone.utc)
        header = next(line for line in (self.out / "all.txt").read_text().splitlines() if line.startswith("# snapshot-time: "))
        timestamp = header.removeprefix("# snapshot-time: ")
        generated = datetime.fromisoformat(timestamp)
        self.assertGreaterEqual(generated, before)
        self.assertLessEqual(generated, after)
        self.assertEqual(generated.utcoffset().total_seconds(), 0)
        self.assertIn(timestamp, self.readme.read_text(encoding="utf-8"))
        self.assertIn(timestamp, (self.root / "STATUS.md").read_text(encoding="utf-8"))

    def test_unchanged_feeds_are_not_rewritten_on_a_later_run(self):
        report: dict = {}
        self.publish(snapshot_time=HISTORICAL, publish_report=report)
        feeds_before = {key: value for key, value in self.bytes().items() if key.startswith("feeds/")}
        self.publish(snapshot_time="2025-06-07T08:09:10+00:00", publish_report=report)
        feeds_after = {key: value for key, value in self.bytes().items() if key.startswith("feeds/")}
        self.assertEqual(feeds_after, feeds_before)
        self.assertGreater(report["skipped_files"], 0)
        self.assertGreater(report["skipped_bytes"], 0)
        # Docs still regenerate with the new run timestamp; only the
        # feed files keep the timestamp of their last content change.
        self.assertIn("2025-06-07T08:09:10+00:00", self.readme.read_text(encoding="utf-8"))

    def test_unchanged_country_feeds_are_skipped_not_deleted(self):
        nodes = [self.node(str(i), country_code="DE") for i in range(5)]
        nodes += [self.node(str(i), country_code="FR") for i in range(5, 10)]
        self.publish(nodes, snapshot_time=HISTORICAL)
        de_before = (self.out / "countries/de.txt").read_bytes()
        fr_before = (self.out / "countries/fr.txt").read_bytes()
        # Same nodes, later run: country feeds must survive untouched,
        # not be treated as stale files to delete or as missing stages.
        self.publish(nodes, snapshot_time="2025-06-07T08:09:10+00:00")
        self.assertEqual((self.out / "countries/de.txt").read_bytes(), de_before)
        self.assertEqual((self.out / "countries/fr.txt").read_bytes(), fr_before)

    def test_changed_feed_is_rewritten_while_unchanged_ones_are_skipped(self):
        self.publish([self.node("only", scheme="trojan")], snapshot_time=HISTORICAL)
        trojan_before = (self.out / "protocols/trojan.txt").read_bytes()
        all_before = (self.out / "all.txt").read_bytes()
        report: dict = {}
        self.publish([self.node("only", scheme="trojan"), self.node("extra", scheme="vless")],
                     snapshot_time="2025-06-07T08:09:10+00:00", publish_report=report)
        self.assertEqual((self.out / "protocols/trojan.txt").read_bytes(), trojan_before)
        self.assertNotEqual((self.out / "all.txt").read_bytes(), all_before)
        self.assertGreater(report["skipped_files"], 0)
        self.assertLess(report["written_files"], len(self.bytes()))

    def test_recovery_includes_old_docs_even_if_next_run_disables_doc_updates(self):
        self.publish()
        before = self.bytes()
        original = Path.replace
        def unavailable(path, target):
            if Path(target).is_relative_to(self.out):
                raise OSError("storage unavailable")
            return original(path, target)
        with patch.object(Path, "replace", new=unavailable), self.assertRaises(OSError):
            self.publish([self.node("new")])
        with patch.object(publisher.tempfile, "TemporaryDirectory", side_effect=OSError("stage unavailable")), self.assertRaises(OSError):
            self.publish([self.node("next")], update_docs=False)
        self.assertEqual(self.bytes(), before)

    def test_interrupted_committed_cleanup_keeps_complete_new_generation_on_recovery(self):
        self.publish()
        transaction = self.root / ".feeds.navaroute-publish"
        original = Path.unlink
        removed = []
        def stop_cleanup(path, *args, **kwargs):
            if path.is_relative_to(transaction / "old"):
                removed.append(path)
                if len(removed) == 2:
                    raise OSError("cleanup unavailable")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", new=stop_cleanup), self.assertRaises(OSError):
            self.publish([self.node("new")])
        complete = {key: value for key, value in self.bytes().items() if not key.startswith(".feeds.navaroute-publish/")}
        self.assertEqual(parse_proxy_uri(self.uris("all.txt")[0]).credential, "new")
        with patch.object(publisher, "atomic_write_text", side_effect=OSError("stage unavailable")), self.assertRaises(OSError):
            self.publish([self.node("next")])
        self.assertEqual(self.bytes(), complete)

    def test_empty_transaction_after_interrupted_final_cleanup_is_recoverable(self):
        self.publish()
        transaction = self.root / ".feeds.navaroute-publish"
        original = Path.rmdir
        def stop_final_cleanup(path):
            if path == transaction:
                raise OSError("final cleanup unavailable")
            return original(path)
        with patch.object(Path, "rmdir", new=stop_final_cleanup), self.assertRaises(OSError):
            self.publish([self.node("new")])
        complete = self.bytes()
        with patch.object(publisher, "atomic_write_text", side_effect=OSError("stage unavailable")), self.assertRaises(OSError):
            self.publish([self.node("next")])
        self.assertEqual(self.bytes(), complete)
        self.assertFalse(transaction.exists())

    def test_complete_rollback_cleanup_after_one_backup_unlink_recovers_automatically(self):
        self.publish(snapshot_time=HISTORICAL)
        before = self.bytes()
        transaction = self.root / ".feeds.navaroute-publish"
        original_replace, original_unlink = Path.replace, Path.unlink
        projected, backups = [], []
        def fail_projection(path, target):
            if Path(target).is_relative_to(self.out):
                projected.append(target)
                if len(projected) == 3:
                    raise OSError("late projection unavailable")
            return original_replace(path, target)
        def fail_cleanup(path, *args, **kwargs):
            if path.is_relative_to(transaction / "old"):
                backups.append(path)
                if len(backups) == 2:
                    raise OSError("cleanup unavailable after one backup unlink")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "replace", new=fail_projection), patch.object(Path, "unlink", new=fail_cleanup), self.assertRaises(OSError):
            self.publish([self.node("new")], snapshot_time=HISTORICAL)
        self.assertFalse(backups[0].exists())
        live = {key: value for key, value in self.bytes().items() if not key.startswith(".feeds.navaroute-publish/")}
        self.assertEqual(live, before)
        try:
            counts = self.publish(snapshot_time=HISTORICAL)
        except (ValueError, OSError) as exc:
            self.fail(f"Healthy automatic recovery failed: {exc}")
        self.assertEqual(counts["all.txt"], 1)
        self.assertEqual(self.bytes(), before)
        self.assertFalse(transaction.exists())

    def assert_committed_role_recovery(self, interruption):
        transaction = self.root / ".feeds.navaroute-publish"
        original = Path.rmdir
        self.publish(snapshot_time=HISTORICAL)
        def stop_role_cleanup(path):
            if path in (transaction / "old", transaction / "new"):
                raise interruption
            return original(path)
        with patch.object(Path, "rmdir", new=stop_role_cleanup), self.assertRaises(type(interruption)):
            self.publish([self.node("new")], snapshot_time=HISTORICAL)
        self.assertTrue((transaction / "manifest.json").exists(), "Manifest must survive remaining role directories")
        complete = {key: value for key, value in self.bytes().items() if not key.startswith(".feeds.navaroute-publish/")}
        self.assertEqual(parse_proxy_uri(self.uris("all.txt")[0]).credential, "new")
        self.publish([self.node("new")], snapshot_time=HISTORICAL)
        self.assertEqual(self.bytes(), complete)
        self.assertFalse(transaction.exists())

    def test_committed_role_directory_failure_keeps_manifest_until_healthy_recovery(self):
        self.assert_committed_role_recovery(OSError("role directory unavailable"))

    def test_committed_role_directory_process_interruption_keeps_manifest_until_healthy_recovery(self):
        self.assert_committed_role_recovery(KeyboardInterrupt("process interruption"))


class TestPublisherBranches(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.out = self.root / "feeds"
        self.readme = self.root / "README.md"
        self.readme.write_bytes(f"Maintained prose\r\n{START}\nold stats\n{END}\r\nSentinel prose\r\n".encode())
        (self.root / "STATUS.md").write_bytes(b"old status\r\n")
        self.now = datetime.now(timezone.utc).isoformat()

    def node(self, credential="public", scheme="trojan", **kwargs):
        return ProxyNode(scheme, "public.example", 443, credential, **kwargs)

    def _publish(self, nodes=None, **kwargs):
        with patch.object(publisher, "REPOSITORY_ROOT", self.root, create=True):
            return publisher.publish_subscriptions(
                [self.node()] if nodes is None else nodes, kwargs.pop("json_configs", []),
                kwargs.pop("stats", []), kwargs.pop("output_dir", str(self.out)), **kwargs)

    def _transaction(self, manifest, staged=None):
        txn = self.root / ".feeds.navaroute-publish"
        txn.mkdir(parents=True, exist_ok=True)
        (txn / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for role, files in (staged or {}).items():
            for key, size in files.items():
                path = txn / role / key
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as handle:
                    handle.truncate(size)
        return txn

    def test_plain_path_rejects_links_and_special_files(self):
        link = self.root / "link"
        link.symlink_to(self.readme)
        with self.assertRaises(ValueError):
            publisher._plain_path(link)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            publisher._plain_path(fifo)

    def test_target_rejects_existing_directory(self):
        (self.out / "all.txt").mkdir(parents=True)
        with self.assertRaises(ValueError):
            publisher._target("feed/all.txt", self.out, False)

    def test_recovery_manifest_oversize_and_bad_shape(self):
        txn = self.root / ".feeds.navaroute-publish"
        txn.mkdir(parents=True, exist_ok=True)
        (txn / "manifest.json").write_bytes(b"x" * (128 * 1024 + 1))
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)
        (txn / "manifest.json").write_text(json.dumps({"state": "prepared"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)

    def test_recovery_manifest_non_bool_and_bad_staged_file(self):
        txn = self._transaction({"state": "prepared", "files": {"feed/all.txt": "yes"}})
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)
        txn = self._transaction({"state": "prepared", "files": {"feed/all.txt": True}})
        (txn / "new" / "feed" / "all.txt").mkdir(parents=True)
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)

    def test_recovery_prepared_missing_old_file(self):
        txn = self._transaction({"state": "prepared", "files": {"feed/all.txt": True}},
                                {"new": {"feed/all.txt": 4}})
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)

    def test_recovery_total_exceeds_generation_limit(self):
        keys = ["feed/all.txt", "feed/reachable.txt", "feed/verified.txt", "feed/top100.txt",
                "feed/lite500.txt", "feed/protocols/vless.txt", "feed/protocols/vmess.txt",
                "feed/protocols/trojan.txt", "feed/protocols/ss.txt", "feed/protocols/hysteria2.txt",
                "feed/protocols/tuic.txt"]
        staged = {"new": {key: 100 * 1024 * 1024 for key in keys},
                  "old": {key: 100 * 1024 * 1024 for key in keys}}
        txn = self._transaction({"state": "prepared", "files": {key: True for key in keys}}, staged)
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)

    def test_recovery_unexpected_file(self):
        txn = self._transaction({"state": "committed", "files": {}})
        (txn / "stray.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            publisher._recover(txn, self.out, False)

    def test_recovery_prepared_new_file_unlinks_target(self):
        self.out.mkdir(parents=True, exist_ok=True)
        target = self.out / "all.txt"
        target.write_text("stale", encoding="utf-8")
        txn = self._transaction({"state": "prepared", "files": {"feed/all.txt": False}})
        publisher._recover(txn, self.out, False)
        self.assertFalse(target.exists())
        self.assertFalse(txn.exists())

    def test_previous_generation_exceeds_recovery_limits(self):
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / "all.txt").open("wb") as handle:
            handle.truncate(600 * 1024 * 1024)
        txn = self.root / ".feeds.navaroute-publish"
        with self.assertRaises(ValueError):
            publisher._project({"feed/all.txt": "x"}, self.out, False, txn)

    def test_bounded_lines_limit(self):
        with self.assertRaises(ValueError):
            publisher._bounded_lines(["x" * (64 * 1024 * 1024 + 1)])

    def test_publish_input_validation(self):
        with self.assertRaises(ValueError):
            self._publish(json_configs=[1])
        with self.assertRaises(ValueError):
            self._publish(stats=["not-a-stat"])

    def test_publish_rejects_repository_root_output(self):
        with self.assertRaises(ValueError):
            self._publish(output_dir=str(self.root))

    def test_publish_readme_too_big(self):
        with patch.object(publisher, "MAX_PLAIN_BYTES", 10):
            with self.assertRaises(ValueError):
                self._publish()

    def test_publish_rejects_duplicate_nodes(self):
        node = self.node()
        with self.assertRaises(ValueError):
            self._publish([node, node])

    def test_unreadable_feed_is_rewritten_not_skipped(self):
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "all.txt").write_bytes(b"\xff\xfe" * 10)
        self._publish()
        self.assertEqual((self.out / "all.txt").read_text(encoding="utf-8").splitlines()[0][:1], "#")
