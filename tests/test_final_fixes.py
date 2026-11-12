"""Combined review regressions: real boundaries, synthetic external I/O only."""
import base64
import hashlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import test_iran_reachability as cli
from src import healthcheck as hc, iran_results as results, network
from src.discovery.agent import DiscoveryAgent
from src.discovery.nemotron_client import NemotronClient
from src.discovery.state_manager import DiscoveryStateManager
from src.fetcher import fetch_source, load_sources
from src.models import ProxyNode, SourceConfig
from src.parsers import detect_country, extract_nodes_from_content, parse_proxy_uri
from tests.test_discovery import OfflineDiscoveryTestCase
from tests.test_network import WireFixture, response

SHA = "a" * 40
CORE = {"version": "Xray 25.10.15", "sha256": "b" * 64}
PEER = "vless://synthetic@example.com:443?security=tls"


class ParserReviewTests(unittest.TestCase):
    def test_known_scalar_conflicts_reject_without_losing_valid_mixed_peer(self):
        for query in ("security=tls&security=none", "type=ws&type=grpc", "type=ws&net=grpc",
                      "path=%2Fa&path=%2Fb", "sni=a&sni=b", "fp=chrome&fp=firefox",
                      "serviceName=a&service_name=b", "dialMode=a&dial_mode=b", "plugin=a&plugin=b"):
            with self.subTest(query=query):
                bad = "vless://bad@example.com:443?" + query
                self.assertIsNone(parse_proxy_uri(bad))
                nodes, _ = extract_nodes_from_content(bad + "\n" + PEER, SourceConfig("fixture", "Fixture", "https://example.com/sub.txt"))
                self.assertEqual([node.credential for node in nodes], ["synthetic"])

    def test_scalar_alias_validation_reaches_mutated_canonical_serializer(self):
        node = ProxyNode("vless", "example.com", 443, "synthetic")
        node.params = {"type": "ws", "network": "grpc"}
        for operation in (node.semantic_fingerprint, lambda: node.to_uri(False)):
            with self.assertRaises(ValueError):
                operation()

    def test_ordered_lists_unknown_repetitions_and_identical_scalars_remain_lossless(self):
        node = parse_proxy_uri("vless://synthetic@example.com:443?security=tls&security=tls&type=ws&net=ws&alpn=h2&alpn=http%2F1.1&unknown=a&unknown=b")
        self.assertIsNotNone(node)
        self.assertEqual(node.params, {"security": "tls", "type": "ws", "alpn": ["h2", "http/1.1"], "unknown": ["a", "b"]})
        self.assertEqual(parse_proxy_uri(node.to_uri(False)).params, node.params)
        wg = parse_proxy_uri("wg://key@example.com:51820?address=10.0.0.2%2F32&address=fd00%3A%3A2%2F128")
        self.assertEqual(wg.params["address"], ["10.0.0.2/32", "fd00::2/128"])

    def test_json_trojan_omitted_security_equals_explicit_none_without_changing_bare_uri(self):
        base = {"protocol": "trojan", "settings": {"servers": [{"address": "example.com", "port": 443, "password": "synthetic"}]}}
        source = SourceConfig("fixture", "Fixture", "https://example.com/sub.txt")
        nodes = []
        for stream in (None, {}, {"network": "tcp"}, {"security": "none"}):
            outbound = dict(base)
            if stream is not None:
                outbound["streamSettings"] = stream
            peers, _ = extract_nodes_from_content(json.dumps({"outbounds": [outbound]}), source)
            self.assertEqual(len(peers), 1)
            node = peers[0]
            self.assertEqual(node.params.get("security"), "none")
            self.assertEqual(hc.build_xray_outbound(parse_proxy_uri(node.to_uri(False)))["streamSettings"]["security"], "none")
            nodes.append(node)
        self.assertEqual(nodes[0].semantic_fingerprint(), nodes[1].semantic_fingerprint())
        self.assertEqual(nodes[0].semantic_fingerprint(), nodes[3].semantic_fingerprint())
        self.assertEqual(hc.build_xray_outbound(parse_proxy_uri("trojan://synthetic@example.com:443"))["streamSettings"]["security"], "tls")

    def test_vmess_fragment_at_is_presentation_not_authority(self):
        payload = base64.b64encode(b'{"add":"example.com","port":443,"id":"synthetic"}').decode()
        ordinary = parse_proxy_uri("vmess://" + payload + "#owner")
        for fragment in ("owner@example", "owner%40example"):
            node = parse_proxy_uri("vmess://" + payload + "#" + fragment)
            self.assertIsNotNone(node)
            self.assertEqual(node.remark, "owner@example")
            self.assertEqual(node.semantic_fingerprint(), ordinary.semantic_fingerprint())

    def test_generated_country_bracket_wins_before_protocol_transport_tokens(self):
        for scheme, credential, params in (("ss", "aes-256-gcm:synthetic", {}), ("vless", "synthetic", {"type": "ws"})):
            node = ProxyNode(scheme, "example.com", 443, credential, params=params)
            self.assertEqual(parse_proxy_uri(node.to_uri()).country_code, "XX")
        for remark, expected in (("[🌐 XX] SS", "XX"), ("[🌐 XX] VLESS-ws", "XX"),
                                 ("[🇩🇪 DE] VLESS-ws", "DE"), ("Fast source [WS]", "WS"),
                                 ("Fast source [SS]", "SS"), ("[DE] [US]", "XX")):
            self.assertEqual(detect_country(remark), expected)


class WireReviewTests(WireFixture):
    def test_exact_cumulative_framing_budget_includes_terminators(self):
        for cap, accepted in ((10, True), (9, False)):
            self.responses = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\n\r\n"]
            with patch.object(network._Response, "MAX_FRAMING_BYTES", cap):
                if accepted:
                    self.assertEqual(network.fetch_bytes("http://public.example/sub.txt", max_bytes=1), (200, b"x"))
                else:
                    with self.assertRaises(network.NetworkError):
                        network.fetch_bytes("http://public.example/sub.txt", max_bytes=1)

    def test_cumulative_trailers_fail_independently_of_small_decoded_body(self):
        wire = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\n" + (b"X: " + b"a" * 1024 + b"\r\n") * 200 + b"\r\n"
        self.responses = [wire]
        with self.assertRaises(network.NetworkError):
            network.fetch_bytes("http://public.example/sub.txt", max_bytes=1)
        self.assertTrue(self.sockets[-1].closed)

    def test_cumulative_chunk_extensions_and_separators_are_bounded(self):
        self.responses = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + (b"1;" + b"a" * 1024 + b"\r\nx\r\n") * 100 + b"0\r\n\r\n"]
        with self.assertRaises(network.NetworkError):
            network.fetch_bytes("http://public.example/sub.txt", max_bytes=100)

    def test_loader_and_direct_fetch_reject_private_queries_before_wire_preserving_public_peers(self):
        rows = {"curated": {"sources": [{"id": "curated-secret", "url": "http://public.example/sub.txt?token=synthetic-private"}, {"id": "public", "url": "http://public.example/sub.txt"}]},
                "discovered": {"sources": [{"id": "discovered-secret", "url": "http://public.example/sub.txt?key=synthetic-private"}]}}
        with patch.object(Path, "open", autospec=True, side_effect=lambda p, *a, **k: io.BytesIO(json.dumps(rows[p.name]).encode())), self.assertLogs("src.fetcher", level="WARNING") as logs:
            sources = load_sources("curated", "discovered")
        self.assertEqual([source.id for source in sources], ["public"])
        self.assertNotIn("synthetic-private", " ".join(logs.output))
        self.responses = [response(PEER.encode())]
        nodes, _, stats = fetch_source(sources[0])
        self.assertEqual((stats.success, len(nodes)), (True, 1))
        before = len(self.sockets)
        nodes, _, stats = fetch_source(SourceConfig("direct", "Direct", "http://public.example/sub.txt?token=synthetic-private"))
        self.assertFalse(stats.success)
        self.assertEqual(nodes, [])
        self.assertEqual(len(self.sockets), before)
        self.assertFalse(any(b"synthetic-private" in wire.sent for wire in self.sockets))

    def test_source_config_rejects_unapproved_publicness_but_retains_owned_cache_and_pagination(self):
        for url in ("https://example.com/sub?token=x", "https://example.com/sub#x", "http://127.0.0.1/sub", "https://bad_host.example/sub"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                SourceConfig.from_dict({"id": "fixture", "url": url})
        for url in ("data/discovered_ocr_configs.txt", "https://t.me/s/proxychan?before=123", "https://example.com/sub"):
            self.assertEqual(SourceConfig.from_dict({"id": "fixture", "url": url}).url, url)

    def test_official_direct_download_shares_trailer_bound_and_small_trailer_control(self):
        for trailers, rejected in ((b"X-Fixture: ok\r\n", False), ((b"X: " + b"a" * 1024 + b"\r\n") * 200, True)):
            self.responses = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\n" + trailers + b"\r\n"]
            context = type("TLSFixture", (), {"wrap_socket": lambda _, sock, **kw: sock})()
            with patch("src.network.ssl.create_default_context", return_value=context), patch.object(results, "MAX_SNAPSHOT_BYTES", 1):
                if rejected:
                    with self.assertRaises(results.IranResultsError):
                        results._fetch_official_bytes("https://public.example/sub.txt")
                else:
                    self.assertEqual(results._fetch_official_bytes("https://public.example/sub.txt"), (200, b"x"))


class OwnerReviewTests(unittest.TestCase):
    payload = b"trojan://private@127.0.0.1:443\nvless://invalid@bad_host.example:443\ntrojan://eligible@1.1.1.1:443\n"

    def test_mixed_snapshot_reaches_real_selection_manifest_reload_and_dated_retry(self):
        digest = hashlib.sha256(self.payload).hexdigest()
        work = []
        def resolve(host, port, deadline, **kw):
            self.assertEqual(host, "1.1.1.1")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
        def batch(items, *args):
            self.assertEqual(len(items), 1)
            index, outbound = items[0]
            self.assertEqual(outbound["settings"]["servers"][0]["address"], "1.1.1.1")
            work.append(index)
            return hc.BatchResult(passed={index: 12.0}, attempted={index})
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli, "authorize_owner", return_value={"head": SHA}), \
                patch.object(cli, "verify_tester_checkout"), patch.object(cli, "verify_source_commit"), \
                patch.object(cli, "inspect_core", return_value=CORE), patch.object(results, "_fetch_official_bytes", return_value=(200, self.payload)), \
                patch.object(hc, "_resolve", side_effect=resolve), patch.object(hc, "_run_xray_items", side_effect=batch), \
                patch("builtins.print"):
            options = dict(xray_bin=str(Path(tmp) / "synthetic-core"), source_sha=SHA, source_digest=digest,
                           tester_revision=SHA, attest_direct_iran=True, attest_access_reviewed=True,
                           recovery_dir=Path(tmp), max_nodes=1, rounds=1)
            self.assertEqual(cli.run_iran_check(**options), 0)
            recovery = next(Path(tmp).glob("*.json"))
            envelope = json.loads(recovery.read_bytes())
            manifest = envelope["manifest"]
            self.assertEqual(manifest["counts"], {"candidates": 3, "tested": 1, "passed": 1, "failed": 0, "unsupported": 2, "untested": 0})
            reloaded = results.load_official_snapshot(SHA, digest)
            files = results.render_result_files(manifest, reloaded, source_sha=SHA, source_digest=digest)
            self.assertEqual(base64.b64decode(files["subs/iran-tested_base64.txt"]), b"trojan://eligible@1.1.1.1:443\n")
            def upload(m, nodes, **kwargs):
                self.assertEqual(kwargs["captured_head"], SHA)
                self.assertEqual(m, manifest)
                # Actual publisher's official reload/render, API edges mocked as a confirmed no-op.
                with patch.object(results, "authorize_owner", return_value={"head": SHA}), patch.object(results, "verify_tester_checkout"), patch.object(results, "verify_source_commit"), \
                     patch.object(results, "gh_api", return_value={"object": {"sha": SHA}}), patch.object(results, "_matches", return_value=True):
                    return results.publish_results(m, nodes, **kwargs)
            with patch.object(cli, "publish_results", side_effect=upload):
                self.assertEqual(cli.run_iran_check(**options, retry_manifest=recovery, publish=True), 0)
            self.assertEqual(work, [0])
            self.assertEqual(json.loads(recovery.read_bytes()), envelope)

    def test_manifest_cannot_claim_work_for_endpoint_ineligible_members(self):
        nodes = [parse_proxy_uri(row) for row in self.payload.decode().splitlines()]
        from tests.test_iran_results import manifest_for
        manifest = manifest_for(nodes)
        with self.assertRaises(results.IranResultsError):
            results.validate_manifest(manifest, nodes, source_sha=SHA, source_digest="b" * 64)

    def test_current_tracked_public_snapshot_loads_all_parsed_fingerprint_members(self):
        root = Path(__file__).resolve().parents[1]
        payload = (root / "subs/all.txt").read_bytes()
        with patch.object(results, "_fetch_official_bytes", return_value=(200, payload)):
            nodes = results.load_official_snapshot(SHA, hashlib.sha256(payload).hexdigest())
        ledger = json.loads((root / "docs/migration-manifest.json").read_bytes())
        rows = [line for line in payload.decode().splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(nodes), len(rows))
        self.assertEqual(len(nodes), ledger["accepted_public_connections"])
        self.assertEqual(len({node.semantic_fingerprint() for node in nodes}), len(rows))
        self.assertTrue(any(hc.build_xray_outbound(node) is None for node in nodes))


class ChannelReviewTests(OfflineDiscoveryTestCase):
    def agent(self, seeds=()):
        return DiscoveryAgent(state_file="data/state.json", initial_seeds=list(seeds), nemotron_client=NemotronClient(api_key=""))

    def test_candidate_admission_preserves_waiting_order_for_empty_and_known_mentions(self):
        manager = DiscoveryStateManager("data/state.json")
        manager.state["candidate_queue"] = ["untouched", "proxyhigh", "proxylast"]
        manager.state["candidate_scores"] = {"untouched": 1, "proxyhigh": 6, "proxylast": 6}
        for candidates, high_score in (([], 6), (["@ProxyHigh", "https://t.me/s/ProxyHigh"], 8)):
            with self.subTest(candidates=candidates):
                manager.add_candidates(candidates)
                manager.save()
                manager = DiscoveryStateManager("data/state.json")
                self.assertEqual(manager.state["candidate_queue"], ["untouched", "proxyhigh", "proxylast"])
                self.assertEqual(manager.state["candidate_scores"], {"untouched": 1, "proxyhigh": high_score, "proxylast": 6})

    def test_new_admission_batch_has_priority_only_behind_existing_waiting_work(self):
        manager = DiscoveryStateManager("data/state.json")
        manager.state["candidate_queue"] = ["untouched", "proxyhigh"]
        manager.state["candidate_scores"] = {"untouched": 1, "proxyhigh": 20, "seenagain": 3}
        manager.state["visited_channels"] = ["visitedproxy"]
        manager.state["rejected_channels"] = ["rejectedproxy"]
        manager.add_candidates(["chana", "seenagain", "freshproxy", "chanb", "chana", "tieproxy",
                                "proxyhigh", "visitedproxy", "rejectedproxy", "joinchat", "contestchan", "bad?token=x"])
        manager.save()
        reloaded = DiscoveryStateManager("data/state.json")
        self.assertEqual(reloaded.state["candidate_queue"], ["untouched", "proxyhigh", "freshproxy", "tieproxy", "seenagain", "chana", "chanb"])
        self.assertEqual(reloaded.state["candidate_scores"], {"untouched": 1, "proxyhigh": 21, "seenagain": 4,
                         "chana": 2, "freshproxy": 6, "chanb": 1, "tieproxy": 6})

    def test_parsed_deep_page_timeouts_do_not_starve_unequal_score_sixth_across_twelve_reloads(self):
        for mention in ("", " @proxyhigh"):
            with self.subTest(mention=mention):
                handles = ["proxyhigh", "proxyone", "proxytwo", "proxythree", "proxyfour", "proxyfive"]
                agent = self.agent()
                agent.state_manager.state["candidate_queue"] = list(handles)
                agent.state_manager.state["candidate_scores"] = dict.fromkeys(handles[:5], 6) | {"proxyfive": 1}
                agent.state_manager.save()
                first_pages, deep_pages, queues = [], [], []
                def page(handle, before_id=None):
                    if before_id is None:
                        first_pages.append(handle)
                        post = 1 if handle == "proxyfive" else 100
                        return f'<div data-post="{handle}/{post}">none{mention}</div>'
                    self.assertEqual(before_id, 100)
                    deep_pages.append(handle)
                    agent.tg_scraper.last_outcome = "retryable"
                    return None
                for cycle in range(12):
                    agent = self.agent()
                    before = len(first_pages)
                    with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", side_effect=page):
                        agent.run_cycle(max_channels=1, max_time_seconds=30)
                    reloaded = DiscoveryStateManager("data/state.json")
                    self.assertEqual(len(first_pages) - before, 1)
                    self.assertEqual(set(reloaded.state["candidate_queue"]) | set(reloaded.state["visited_channels"]), set(handles))
                    self.assertEqual(len(reloaded.state["candidate_queue"]), len(set(reloaded.state["candidate_queue"])))
                    self.assertTrue(all(reloaded.get_channel_oldest_post_id(handle) is None for handle in handles[:5]))
                    queues.append(reloaded.state["candidate_queue"])
                self.assertEqual(first_pages, handles + handles[:5] + ["proxyhigh"])
                self.assertEqual(deep_pages, handles[:5] + handles[:5] + ["proxyhigh"])
                self.assertEqual(queues[4], ["proxyfive", "proxyhigh", "proxyone", "proxytwo", "proxythree", "proxyfour"])
                self.assertEqual(queues[-1], ["proxyone", "proxytwo", "proxythree", "proxyfour", "proxyhigh"])
                self.assertEqual(reloaded.state["visited_channels"], ["proxyfive"])
                self.assertEqual(reloaded.state["sources_metadata"]["proxyfive"]["oldest_post_id"], 1)
                self.assertEqual(reloaded.state["candidate_scores"], dict.fromkeys(handles[1:5], 6) | {"proxyhigh": 15 if mention else 6, "proxyfive": 1})
                for path in ("data/state.json", "sources.discovered.json", "DISCOVERY.md"):
                    Path(path).unlink(missing_ok=True)

    def test_failed_child_survives_changed_channel_body_and_real_checkpoint_reload(self):
        parent, child = "https://t.me/s/proxychan", "https://example.com/sub.txt"
        agent = self.agent(["proxychan"])
        with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">' + child + '</div>'), patch("src.discovery.agent.fetch_bytes", side_effect=network.NetworkError("synthetic")):
            agent.run_cycle(max_channels=1)
        saved = DiscoveryStateManager("data/state.json").state
        self.assertEqual(saved.get("pending_children", {}).get(parent), [{"kind": "subscription", "url": child}])
        self.assertNotIn("proxychan", saved["visited_channels"])
        agent = self.agent()
        with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">changed</div>'), patch("src.discovery.agent.fetch_bytes", return_value=(200, PEER.encode())):
            sources = agent.run_cycle(max_channels=1)
        self.assertIn(child, {source["url"] for source in sources})
        self.assertIn("proxychan", agent.state_manager.state["visited_channels"])
        self.assertFalse(agent.state_manager.state.get("pending_children"))

    def test_over_five_children_retain_suffix_and_progress_after_reload_under_probe_cap(self):
        parent = "https://t.me/s/proxychan"
        children = [f"https://example.com/sub{i}.txt" for i in range(8)]
        attempts = []
        def probe(url, **kwargs):
            attempts.append(url)
            return 200, PEER.encode()
        agent = self.agent(["proxychan"])
        with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">' + ' '.join(children) + '</div>'), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent.run_cycle(max_channels=1)
        self.assertEqual(attempts, children[:5])
        saved = DiscoveryStateManager("data/state.json").state
        self.assertEqual([child["url"] for child in saved.get("pending_children", {}).get(parent, [])], children[5:])
        self.assertNotIn("proxychan", saved["visited_channels"])
        agent = self.agent()
        with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">changed</div>'), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent.run_cycle(max_channels=1)
        # The normal external phase refreshes five admitted feeds before channel replay.
        self.assertEqual(attempts, children[:5] + children[:5] + children[5:])
        self.assertTrue(all(agent.state_manager.is_url_visited(url) for url in children))
        self.assertIn("proxychan", agent.state_manager.state["visited_channels"])

    def test_channel_retry_rotation_reaches_untouched_peer_without_same_cycle_retries(self):
        handles = [f"proxy{i}" for i in range(6)]
        for cap in (1, 10):
            with self.subTest(cap=cap):
                agent = self.agent()
                agent.state_manager.state["candidate_queue"] = list(handles)
                attempts = []
                def page(handle, **kwargs):
                    attempts.append(handle)
                    return '<div data-post="proxy5/1">none</div>' if handle == "proxy5" else None
                for _ in range(6 if cap == 1 else 2):
                    before = len(attempts)
                    with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", side_effect=page):
                        agent.run_cycle(max_channels=cap)
                    current = attempts[before:]
                    self.assertLessEqual(len(current), cap)
                    self.assertEqual(len(current), len(set(current)))
                    agent = self.agent()
                self.assertIn("proxy5", agent.state_manager.state["visited_channels"])
                self.assertTrue(set(handles[:5]).issubset(agent.state_manager.state["candidate_queue"]))
                # Independent cap cases use a new empty owned state generation.
                for path in ("data/state.json", "sources.discovered.json", "DISCOVERY.md"):
                    Path(path).unlink(missing_ok=True)

    def test_deadline_rotates_attempted_failure_behind_unattempted_suffix(self):
        agent = self.agent(["proxyone", "proxytwo", "proxythree"])
        with patch("src.discovery.agent.time.monotonic", return_value=0) as clock:
            def page(*args, **kwargs):
                clock.return_value = 10
                return None
            with patch.object(agent, "_expand_queries_via_nemotron"), patch.object(agent.tg_scraper, "fetch_channel_page", side_effect=page):
                agent.run_cycle(max_channels=3, max_time_seconds=10)
        self.assertEqual(DiscoveryStateManager("data/state.json").state["candidate_queue"], ["proxytwo", "proxythree", "proxyone"])

    def test_real_expansion_replays_saved_telegram_suffix_across_three_reloads(self):
        parent = "https://t.me/s/proxychan"
        children = [f"https://example.com/sub{i}.txt" for i in range(8)]
        attempts = []
        def search(url, **kwargs):
            self.assertNotEqual(url, parent, "channel owner must not be consumed as a generic webpage")
            return 200, b'{"items":[]}' if "api.github.com" in url else b""
        def probe(url, **kwargs):
            attempts.append(url)
            return 200, PEER.encode()
        with patch("src.discovery.agent.time.monotonic", return_value=0) as clock, patch("src.discovery.search_engine.fetch_bytes", side_effect=search), patch("src.discovery.agent.fetch_bytes", side_effect=probe):
            agent = self.agent(["proxychan"])
            parse = agent.tg_scraper.parse_channel_page
            def expire_after_parse(*args, **kwargs):
                result = parse(*args, **kwargs)
                clock.return_value = 10
                return result
            with patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">' + ' '.join(children) + '</div>'), patch.object(agent.tg_scraper, "parse_channel_page", side_effect=expire_after_parse):
                agent.run_cycle(max_channels=1, max_time_seconds=10)
            self.assertEqual(attempts, [])
            saved = DiscoveryStateManager("data/state.json").state
            self.assertEqual([work["url"] for work in saved["pending_children"][parent]], children)
            for phase in range(2):
                clock.return_value = 0
                agent = self.agent()
                before = len(attempts)
                with patch.object(agent.tg_scraper, "fetch_channel_page", return_value='<div data-post="proxychan/1">changed</div>'):
                    agent.run_cycle(max_channels=1, max_time_seconds=10)
                self.assertEqual(attempts[before:], children[:5] if phase == 0 else children[:5] + children[5:])
                self.assertEqual("proxychan" in agent.state_manager.state["visited_channels"], phase == 1)
        self.assertTrue(all(agent.state_manager.is_url_visited(child) for child in children))
        self.assertFalse(agent.state_manager.state.get("pending_children"))
