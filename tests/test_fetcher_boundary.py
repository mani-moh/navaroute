import contextlib
import concurrent.futures
import gc
import json
import os
import tempfile
import threading
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from src.fetcher import _fetch_url, fetch_all_sources, fetch_source, load_sources
from src.models import SourceConfig
from src.network import NetworkError
from tests.test_network import WireFixture, response


class FetcherWireTests(WireFixture):
    # Real source wrapper/parser/transport on the same bounded wire fixtures.
    def test_non_2xx_body_is_not_parsed_as_success(self):
        self.responses = [response(body=b"vless://test@8.8.8.8:443", status=404)]
        nodes, configs, stats = fetch_source(SourceConfig("bad", "Bad", "http://public.example/feed"))
        self.assertEqual((nodes, configs), ([], []))
        self.assertFalse(stats.success)
        self.assertEqual(stats.status_code, 404)
        self.assertNotIn("secret", stats.url)
        self.assertNotIn("secret", stats.error or "")

    def test_successful_source_passes_real_body_to_parser(self):
        self.responses = [response(body=b"vless://00112233-4455-6677-8899-aabbccddeeff@8.8.8.8:443?security=tls")]
        nodes, _, stats = fetch_source(SourceConfig("ok", "OK", "http://public.example/feed"))
        self.assertTrue(stats.success)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].address, "8.8.8.8")

    def test_error_stats_do_not_expose_userinfo_query_or_fragment(self):
        _, _, stats = fetch_source(SourceConfig("secret", "Secret", "http://user:synthetic-secret@public.example/feed?token=secret#secret"))
        self.assertFalse(stats.success)
        self.assertEqual(self.sockets, [])
        self.assertEqual(stats.url, "http://public.example/feed")
        self.assertNotIn("secret", stats.error or "")

    def test_source_cap_rejects_32_mib_plus_one_without_body_read(self):
        self.responses = [b"HTTP/1.1 200 OK\r\nContent-Length: 33554433\r\n\r\n"]
        with self.assertRaises(NetworkError):
            _fetch_url("http://public.example/feed")
        self.assertEqual(self.sockets[0].read_sizes, [])


class FetcherLocalTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        (self.root / "data").mkdir()

    def test_cache_is_read_only_under_resolved_owned_data_root(self):
        (self.root / "data" / "feed.txt").write_text("synthetic", encoding="utf-8")
        (self.root / "private.txt").write_text("never returned", encoding="utf-8")
        self.assertEqual(_fetch_url("data/feed.txt"), (200, "synthetic"))
        for url in ("data/../private.txt", "data\\..\\private.txt", str(self.root / "private.txt")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _fetch_url(url)

    def test_cache_symlink_escape_is_rejected(self):
        (self.root / "private.txt").write_text("never returned", encoding="utf-8")
        link = self.root / "data" / "escape.txt"
        try:
            link.symlink_to(self.root / "private.txt")
        except OSError:
            # Windows without link privilege: emulate only filesystem resolution.
            original = Path.resolve
            original_open = Path.open
            original_is_file = Path.is_file
            def resolve(path, *args, **kwargs):
                if path.name == "escape.txt":
                    return self.root / "private.txt"
                return original(path, *args, **kwargs)
            self.stack.enter_context(patch.object(Path, "resolve", resolve))
            self.stack.enter_context(patch.object(Path, "is_file", lambda path: path.name == "escape.txt" or original_is_file(path)))
            def open_file(path, *args, **kwargs):
                return original_open(self.root / "private.txt" if path.name == "escape.txt" else path, *args, **kwargs)
            self.stack.enter_context(patch.object(Path, "open", open_file))
        with self.assertRaises(ValueError):
            _fetch_url("data/escape.txt")

    def test_data_root_itself_cannot_resolve_outside_cwd(self):
        (self.root / "data" / "feed.txt").write_text("never returned", encoding="utf-8")
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path.name == "data":
                return self.root.parent / "outside-owned-root"
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve), self.assertRaises(ValueError):
            _fetch_url("data/feed.txt")

    def test_data_root_cannot_alias_an_unowned_sibling_directory(self):
        (self.root / "data" / "feed.txt").write_text("never returned", encoding="utf-8")
        (self.root / "private").mkdir()
        (self.root / "private" / "feed.txt").write_text("never returned", encoding="utf-8")
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path.name == "data":
                return self.root / "private"
            if path.name == "feed.txt":
                return self.root / "private" / "feed.txt"
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve), self.assertRaises(ValueError):
            _fetch_url("data/feed.txt")

    def test_local_oversize_never_returns_truncated_feed(self):
        with (self.root / "data" / "large.txt").open("wb") as stream:
            stream.truncate(33554433)
        with self.assertRaises(ValueError):
            _fetch_url("data/large.txt")

    def test_cache_read_failure_is_explicit_and_does_not_expose_path_material(self):
        with self.assertRaises(NetworkError) as error:
            _fetch_url("data/missing-token-secret.txt")
        self.assertNotIn("secret", str(error.exception))

    def test_invalid_utf8_is_not_replaced_into_a_successful_feed(self):
        (self.root / "data" / "bad.txt").write_bytes(b"vless://secret\xff@8.8.8.8:443")
        with self.assertRaises(ValueError) as error:
            _fetch_url("data/bad.txt")
        self.assertNotIn("secret", str(error.exception))

    def test_malformed_middle_registry_row_does_not_drop_valid_peers(self):
        rows = [{"id": "first", "url": "https://public.example/a"}, {"url": "missing id"},
                None, {"id": "last", "url": "https://public.example/b"}]
        Path("sources.json").write_text(json.dumps({"sources": rows}), encoding="utf-8")
        Path("sources.discovered.json").write_text(json.dumps({"sources": rows}), encoding="utf-8")
        with self.assertLogs("src.fetcher", level="WARNING") as logs:
            sources = load_sources()
        self.assertEqual([source.id for source in sources], ["first", "last"])
        self.assertNotIn("missing id", " ".join(logs.output))

    def test_unreadable_or_malformed_registry_is_fatal(self):
        for content in ("{", "[]", '{"sources": {}}', '{"sources": null}'):
            Path("sources.json").write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(ValueError):
                load_sources()
        Path("sources.json").unlink()
        with self.assertRaises(ValueError):
            load_sources()
        Path("sources.json").write_text('{"sources": []}', encoding="utf-8")
        Path("sources.discovered.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_sources()

    def test_inaccessible_discovered_registry_is_not_treated_as_missing(self):
        Path("sources.json").write_text('{"sources": []}', encoding="utf-8")
        original_exists = Path.exists
        original_open = Path.open
        def exists(path):
            return False if path.name == "sources.discovered.json" else original_exists(path)
        def open_file(path, *args, **kwargs):
            if path.name == "sources.discovered.json":
                raise PermissionError("synthetic inaccessible registry")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "exists", exists), patch.object(Path, "open", open_file), self.assertRaises(ValueError):
            load_sources()


class FetcherAggregateTests(unittest.TestCase):
    def test_aggregate_cap_is_fatal_and_cancels_queued_sources(self):
        release = threading.Event()
        futures = []
        read = []
        class Executor(concurrent.futures.ThreadPoolExecutor):
            def submit(self, *args, **kwargs):
                future = super().submit(*args, **kwargs)
                futures.append(future)
                return future
            def shutdown(self, *args, **kwargs):
                release.set()
                return super().shutdown(*args, **kwargs)
        def transport(url, timeout):
            read.append(url)
            if url != "http://public.example/0":
                release.wait(0.1)
            return 200, "\n".join(("vless://first@8.8.8.8:443?security=tls", "vless://second@8.8.8.8:443?security=tls"))
        sources = [SourceConfig(str(i), str(i), f"http://public.example/{i}") for i in range(12)]
        with patch("src.fetcher.AGGREGATE_MAX_NODES", 1), \
                patch("src.fetcher.concurrent.futures.ThreadPoolExecutor", Executor), \
                patch("src.fetcher._fetch_url", transport), self.assertRaises(ValueError):
            fetch_all_sources(sources, max_workers=1)
        self.assertGreaterEqual(sum(future.cancelled() for future in futures), 10)
        self.assertLessEqual(len(read), 2)

    def test_completed_futures_are_released_during_aggregation(self):
        references = []
        retained = []
        class Future(concurrent.futures.Future):
            def result(self, *args, **kwargs):
                gc.collect()
                retained.append(sum(reference() is not None for reference in references))
                return super().result(*args, **kwargs)
        class ImmediateExecutor:
            def __init__(self, max_workers):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def submit(self, fn, source, timeout):
                future = Future()
                future.set_result(fn(source, timeout))
                references.append(weakref.ref(future))
                return future
        sources = [SourceConfig(str(i), str(i), "http://public.example/empty") for i in range(5)]
        with patch("src.fetcher.concurrent.futures.ThreadPoolExecutor", ImmediateExecutor), \
                patch("src.fetcher._fetch_url", return_value=(200, "")):
            nodes, configs, stats = fetch_all_sources(sources)
        self.assertEqual((nodes, configs), ([], []))
        self.assertEqual(len(stats), 5)
        self.assertLessEqual(retained[2], 3)

    def test_exact_aggregate_cap_retains_all_peers_without_truncation(self):
        sources = [SourceConfig(str(i), str(i), "http://public.example/feed") for i in range(2)]
        with patch("src.fetcher.AGGREGATE_MAX_NODES", 2), \
                patch("src.fetcher._fetch_url", return_value=(200, "vless://custom@8.8.8.8:443?security=tls")):
            nodes, _, stats = fetch_all_sources(sources, max_workers=1)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(stats), 2)
        self.assertTrue(all(stat.success for stat in stats))


class ReportErrorTokenContractTests(unittest.TestCase):
    """The empty-yield census renders stat.error verbatim; pin that it stays token-only."""

    def test_every_reachable_error_label_is_url_free(self):
        import urllib.error

        from src.fetcher import _report_error

        cases = [
            NetworkError("DNS resolution failed"),
            NetworkError("DNS deadline exceeded"),
            NetworkError("HTTP connection failed"),
            NetworkError("HTTP transport failed"),
            NetworkError("HTTP transport failed"),  # already covered; belt and braces
            NetworkError("Network timeout"),
            NetworkError("Redirect limit exceeded"),
            NetworkError("Unsupported response content encoding"),
            urllib.error.URLError("Connection refused"),
            urllib.error.URLError("custom reason that could embed a URL https://leak.example/x?token=leak"),
            ValueError("arbitrary structured data / some/path?u=1"),
            RuntimeError("arbitrary token=leak"),
        ]
        for exc in cases:
            label = _report_error(exc)
            self.assertLessEqual(len(label), 200)
            self.assertRegex(label, r"^[A-Za-z0-9()_.: -]+$")
            for forbidden in ("/", "?", "%", "@", "=", "\\", "\n", "leak"):
                self.assertNotIn(forbidden, label)


if __name__ == "__main__":
    unittest.main()
