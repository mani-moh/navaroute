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
        self.assertEqual(counts["categories/supplied-parameters.txt"], 1)
        self.assertEqual(self.uris("categories/supplied-parameters.txt"), self.uris("categories/pattn-special.txt"))
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
                if len(projections) == 8:
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
        with patch.object(publisher, "atomic_write_text", side_effect=OSError("new staging unavailable"), create=True), self.assertRaises(OSError):
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
        with patch.object(publisher, "atomic_write_text", side_effect=OSError("stage unavailable")), self.assertRaises(OSError):
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
