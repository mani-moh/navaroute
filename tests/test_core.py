from __future__ import annotations

import base64
import io
import json
import subprocess
import unittest
import urllib.parse
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.dedup import deduplicate_nodes
from src.models import (
    ConnectionLimitError,
    ProxyNode,
    SourceConfig,
    country_code_to_flag,
    flag_to_country_code,
)
from src.parsers import (
    detect_country,
    extract_nodes_from_content,
    parse_proxy_uri,
)

BASE_DIR = Path(__file__).resolve().parent.parent


class TestSourcesRegistry(unittest.TestCase):
    def test_sources_json_valid_and_clean(self):
        """Verify sources.json has 60 valid public sources."""
        sources_path = BASE_DIR / "sources.json"
        assert sources_path.exists(), "sources.json must exist"

        content = sources_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert "sources" in data
        sources_list = data["sources"]
        assert len(sources_list) == 60, f"Expected 60 public sources, found {len(sources_list)}"

        ids = set()
        for s in sources_list:
            config = SourceConfig.from_dict(s)
            assert config.id, "Source must have an id"
            assert config.id not in ids, f"Duplicate source id: {config.id}"
            ids.add(config.id)
            assert config.url.startswith("http"), f"Source {config.id} has invalid URL: {config.url}"
            assert isinstance(config.tags, list)


class TestParsers(unittest.TestCase):
    def test_parse_vless_reality(self):
        uri = (
            "vless://9f8e7d6c-5b4a-3210-9876-543210fedcba@1.2.3.4:443?"
            "security=reality&type=grpc&serviceName=vless-grpc&pbk=some_pbk&sid=123456&fp=chrome"
            "#%F0%9F%87%A9%F0%9F%87%AA%20Germany%20Reality"
        )
        src = SourceConfig(id="test-src", name="Test", url="http://test", verified_upstream=True, tags=["verified"])
        node = parse_proxy_uri(uri, source=src)

        assert node is not None
        assert node.scheme == "vless"
        assert node.address == "1.2.3.4"
        assert node.port == 443
        assert node.credential == "9f8e7d6c-5b4a-3210-9876-543210fedcba"
        assert node.country_code == "DE"
        assert node.params.get("security") == "reality"
        assert node.params.get("serviceName") == "vless-grpc"
        assert node.is_reality() is True
        assert node.has_web_transport() is True
        assert node.is_verified_source is True

    def test_parse_standard_uri_with_path(self):
        uri = "vless://9f8e7d6c-5b4a-3210-9876-543210fedcba@1.2.3.4:443/custom/ws/path?type=ws&security=tls#Test"
        node = parse_proxy_uri(uri)
        assert node is not None
        assert node.address == "1.2.3.4"
        assert node.port == 443
        assert node.params.get("path") == "/custom/ws/path"

    def test_parse_vmess_base64_json(self):
        vmess_data = {
            "v": "2",
            "ps": "🇺🇸 USA VMess Node",
            "add": "198.51.100.1",
            "port": 8080,
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "net": "ws",
            "host": "cdn.example.com",
            "path": "/websocket",
            "tls": "tls",
            "sni": "cdn.example.com",
        }
        b64 = base64.b64encode(json.dumps(vmess_data).encode("utf-8")).decode("utf-8")
        uri = f"vmess://{b64}"

        node = parse_proxy_uri(uri)
        assert node is not None
        assert node.scheme == "vmess"
        assert node.address == "198.51.100.1"
        assert node.port == 8080
        assert node.credential == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert node.country_code == "US"
        assert node.params.get("type") == "ws"
        assert node.params.get("security") == "tls"
        assert node.has_web_transport() is True

        # Verify to_uri preserves/updates vmess JSON
        rebuilt = node.to_uri(include_formatted_remark=True)
        assert rebuilt.startswith("vmess://")
        rebuilt_b64 = rebuilt[len("vmess://"):]
        decoded = json.loads(base64.b64decode(rebuilt_b64 + "==").decode("utf-8"))
        assert "US" in decoded["ps"]
        assert "VMess-ws" in decoded["ps"]

    def test_parse_trojan(self):
        uri = "trojan://secretpassword@203.0.113.10:443?sni=trojan.server.nl&security=tls#🇳🇱%20Amsterdam"
        node = parse_proxy_uri(uri)
        assert node is not None
        assert node.scheme == "trojan"
        assert node.address == "203.0.113.10"
        assert node.port == 443
        assert node.credential == "secretpassword"
        assert node.country_code == "NL"
        assert node.params.get("sni") == "trojan.server.nl"

    def test_parse_shadowsocks_sip002_and_legacy(self):
        # SIP002
        userinfo_b64 = base64.b64encode(b"chacha20-ietf-poly1305:mypassword").decode("utf-8")
        sip002_uri = f"ss://{userinfo_b64}@192.0.2.10:8388#🇫🇮%20Finland%20Node"
        node1 = parse_proxy_uri(sip002_uri)
        assert node1 is not None
        assert node1.scheme == "ss"
        assert node1.address == "192.0.2.10"
        assert node1.port == 8388
        assert node1.credential == "chacha20-ietf-poly1305:mypassword"
        assert node1.country_code == "FI"

        # Legacy
        legacy_payload = base64.b64encode(b"aes-256-gcm:legacypass@192.0.2.20:8389").decode("utf-8")
        legacy_uri = f"ss://{legacy_payload}#Legacy%20Node%20[GB]"
        node2 = parse_proxy_uri(legacy_uri)
        assert node2 is not None
        assert node2.scheme == "ss"
        assert node2.address == "192.0.2.20"
        assert node2.port == 8389
        assert node2.credential == "aes-256-gcm:legacypass"
        assert node2.country_code == "GB"

        # Verify to_uri with empty raw_uri encodes SIP002 base64 userinfo
        node3 = ProxyNode(
            scheme="ss",
            address="192.0.2.30",
            port=8388,
            credential="aes-256-gcm:newpass",
            remark="Rebuilt SS",
        )
        uri3 = node3.to_uri(include_formatted_remark=False)
        assert uri3.startswith("ss://")
        cred_part = uri3.split("://")[1].split("@")[0]
        decoded_cred = base64.urlsafe_b64decode(cred_part + "==").decode("utf-8")
        assert decoded_cred == "aes-256-gcm:newpass"

    def test_parse_hysteria2_and_tuic(self):
        hy2_uri = "hy2://pass123@198.51.100.25:443?sni=fast.net#🇹🇷%20Turkey%20Hysteria2"
        node1 = parse_proxy_uri(hy2_uri)
        assert node1 is not None
        assert node1.scheme == "hysteria2"
        assert node1.address == "198.51.100.25"
        assert node1.country_code == "TR"

        tuic_uri = "tuic://uuid1:pass2@198.51.100.26:8443?alpn=h3#TUIC%20Node%20[CA]"
        node2 = parse_proxy_uri(tuic_uri)
        assert node2 is not None
        assert node2.scheme == "tuic"
        assert node2.address == "198.51.100.26"
        assert node2.country_code == "CA"

    def test_flag_emoji_unicode_math(self):
        # Unicode regional indicator math
        assert flag_to_country_code("🇩🇪") == "DE"
        assert flag_to_country_code("🇺🇸") == "US"
        assert flag_to_country_code("🇫🇮") == "FI"
        assert flag_to_country_code("🇮🇷") == "IR"
        assert flag_to_country_code("🇳🇱") == "NL"
        assert flag_to_country_code("🇬🇧") == "GB"
        assert flag_to_country_code("🇹🇷") == "TR"
        assert flag_to_country_code("🇷🇺") == "RU"
        assert flag_to_country_code("🇨🇦") == "CA"
        assert flag_to_country_code("🇸🇬") == "SG"
        assert flag_to_country_code("🇯🇵") == "JP"

        # Reverse check
        assert country_code_to_flag("DE") == "🇩🇪"
        assert country_code_to_flag("US") == "🇺🇸"
        assert country_code_to_flag("XX") == "🌐"

    def test_detect_country_stop_words_and_order(self):
        # "V2Ray Config By EbraSha" must NOT match "BY"
        assert detect_country("V2Ray Config By EbraSha") == "XX"
        # "Server in Germany" must match "DE" (not "IN")
        assert detect_country("Server in Germany") == "DE"
        # Explicit brackets match
        assert detect_country("Fast node [FR]") == "FR"
        # Explicit delimiters match
        assert detect_country("Fast node | NL |") == "NL"
        # Whitespace uppercase 2-letter code matches when not a stop word
        assert detect_country("Fast node TR best") == "TR"

    def test_special_parameter_detection(self):
        node = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"fm": "1"},
        )
        assert node.has_special_parameters() is True

        node2 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"dialMode": "1"},
        )
        assert node2.has_special_parameters() is True

        node3 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"cs": "test"},
        )
        assert node3.has_special_parameters() is True

        node4 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"fp": "unsafe"},
        )
        assert node4.has_special_parameters() is True

        node5 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            tags={"pattn"},
        )
        assert node5.has_special_parameters() is False

        normal_node = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
        )
        assert normal_node.has_special_parameters() is False


class TestContentExtraction(unittest.TestCase):
    def test_base64_decoding_and_extraction(self):
        uri1 = "vless://uuid1@1.1.1.1:443?type=tcp#Node1"
        uri2 = "trojan://pass2@2.2.2.2:443#Node2"
        plain_block = f"{uri1}\n{uri2}"
        b64_block = base64.b64encode(plain_block.encode("utf-8")).decode("utf-8")

        src = SourceConfig(id="b64-src", name="B64 Source", url="http://b64", format="base64")
        nodes, jsons = extract_nodes_from_content(b64_block, src)
        assert len(nodes) == 2
        assert nodes[0].scheme == "vless"
        assert nodes[1].scheme == "trojan"
        assert "b64-src" in nodes[0].source_ids

    def test_html_and_telegram_web_extraction(self):
        telegram_html = """
        <!DOCTYPE html>
        <html>
        <body>
        <div class="tgme_widget_message_text js-message_text" dir="auto">
            ⚡ Config update:<br/>
            <code>vless://uuid-tg@10.0.0.1:443?type=ws&amp;security=tls#%F0%9F%87%A9%F0%9F%87%AA%20TG%20Node</code><br/>
            Also try this one:<br/>
            trojan://tgpass@10.0.0.2:443#TG%20Trojan
        </div>
        </body>
        </html>
        """
        src = SourceConfig(id="tg-src", name="TG Source", url="https://t.me/s/ConfigsHUB", format="telegram_web")
        nodes, jsons = extract_nodes_from_content(telegram_html, src)
        assert len(nodes) == 2
        assert nodes[0].address == "10.0.0.1"
        assert nodes[0].params.get("security") == "tls"
        assert nodes[0].country_code == "DE"
        assert nodes[1].scheme == "trojan"
        assert nodes[1].address == "10.0.0.2"

    def test_xray_json_extraction(self):
        xray_content = {
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "direct",
                    "settings": None,
                },
                {
                    "protocol": "blackhole",
                    "tag": "block",
                },
                {
                    "protocol": "vless",
                    "tag": "🇮🇷 Iran Direct",
                    "settings": {
                        "vnext": [
                            {
                                "address": "ir.example.com",
                                "port": 443,
                                "users": [{"id": "uuid-xray-1", "flow": "xtls-rprx-vision"}],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": "yahoo.com",
                            "publicKey": "real_pbk",
                            "shortId": "abc123",
                        },
                    },
                },
            ]
        }
        src = SourceConfig(id="json-src", name="JSON Source", url="http://json", format="xray_json")
        nodes, jsons = extract_nodes_from_content(json.dumps(xray_content), src)
        assert jsons == []
        assert len(nodes) == 1
        assert nodes[0].scheme == "vless"
        assert nodes[0].address == "ir.example.com"
        assert nodes[0].params.get("flow") == "xtls-rprx-vision"
        assert nodes[0].params.get("security") == "reality"
        assert nodes[0].country_code == "IR"


class TestDeduplication(unittest.TestCase):
    def test_semantic_fingerprint_invariance(self):
        # Query params in different orders, different remarks, different address casing
        uri1 = "vless://my-uuid@EXAMPLE.COM:443?type=ws&security=reality&sni=sni.net&pbk=key1#RemarkOne"
        uri2 = "vless://my-uuid@example.com:443?pbk=key1&sni=SNI.NET&security=reality&type=ws#RemarkTwo"
        node1 = parse_proxy_uri(uri1)
        node2 = parse_proxy_uri(uri2)

        assert node1 is not None and node2 is not None
        assert node1.semantic_fingerprint() == node2.semantic_fingerprint()

    def test_deduplicate_nodes_merges_attributes(self):
        # Create duplicate nodes with different attributes
        uri_a = "vless://my-uuid@1.2.3.4:443?type=ws&security=reality&pbk=key1#%F0%9F%87%A9%F0%9F%87%AA%20DE%20Node"
        uri_b = "vless://my-uuid@1.2.3.4:443?security=reality&pbk=key1&type=ws#No%20Flag%20Node"

        src1 = SourceConfig(id="src-unverified", name="Unverified", url="http://1", verified_upstream=False, tags=["iran"])
        src2 = SourceConfig(id="src-verified", name="Verified", url="http://2", verified_upstream=True, tags=["verified"])

        node_a = parse_proxy_uri(uri_a, source=src1)
        node_a.latency_ms = 120.0

        node_b = parse_proxy_uri(uri_b, source=src2)
        node_b.latency_ms = 45.0
        node_b.passed_healthcheck = True
        node_b.healthcheck_kind = "socket"
        node_b.healthchecked_at = "2026-11-10T12:00:00Z"

        deduped = deduplicate_nodes([node_a, node_b])
        assert len(deduped) == 1
        merged = deduped[0]

        assert merged.source_ids == {"src-unverified", "src-verified"}
        assert "iran" in merged.tags and "verified" in merged.tags
        assert merged.is_verified_source is True
        assert merged.passed_healthcheck is True
        assert merged.latency_ms == 45.0
        assert merged.country_code == "DE"

        # Test to_uri output
        formatted_uri = merged.to_uri(include_formatted_remark=True)
        decoded_remark = urllib.parse.unquote(formatted_uri.split("#", 1)[1])
        assert "[source-claimed]" in decoded_remark
        assert "[socket:passed]" in decoded_remark
        assert "[🇩🇪 DE]" in decoded_remark
        assert "VLESS-Reality" in formatted_uri
        assert "45ms" in formatted_uri


class TestCanonicalConnections(unittest.TestCase):
    def test_every_connection_option_participates_in_identity(self):
        # Dropping a meaningful parameter must not merge different connections.
        base = ProxyNode("vless", "example.com", 443, " custom id ")
        for key, values in {
            "fp": ["chrome", "firefox"], "alpn": ["h2", "http/1.1"],
            "plugin": ["obfs-local;obfs=http", "obfs-local;obfs=tls"],
            "aid": ["0", "1"], "headers": [{"X-Test": "a"}, {"X-Test": "b"}],
            "encryption": ["none", "custom"], "path": ["/ws", " /ws ", "/ws/"],
            "unknown": ["", "a"], "flow": ["", "xtls-rprx-vision"],
            "security": ["tls", "TLS"], "type": ["ws", "WS"],
        }.items():
            with self.subTest(key=key):
                nodes = [base] + [replace(base, params={key: value}) for value in values]
                self.assertEqual(len(deduplicate_nodes(nodes)), len(nodes))
        self.assertNotEqual(base.semantic_fingerprint(), replace(base, credential="custom id").semantic_fingerprint())

    def test_raw_uri_cannot_override_changed_canonical_fields(self):
        node = parse_proxy_uri("trojan://old@old.example:443?path=%2Fold#old")
        node.address, node.port, node.credential = "new.example", 8443, " 密@码:/?# "
        node.params = {"path": " /新/?# ", "security": "tls"}
        rebuilt = parse_proxy_uri(node.to_uri(False))
        self.assertEqual((rebuilt.address, rebuilt.port, rebuilt.credential), ("new.example", 8443, " 密@码:/?# "))
        self.assertEqual(rebuilt.params, {"path": " /新/?# ", "security": "tls"})

    def test_supported_schemes_round_trip_encoded_auth_options_and_fragments(self):
        fixtures = [
            ("vless://custom%20id%3A%40%2F@EXAMPLE.com:443?path=%20%2Fws%2F%20&host=cdn.example#%E6%B5%8B%23%26", "custom id:@/"),
            ("trojan://%20p%40%3A%2F%3F%23%20@[2001:db8::1]:8443?security=tls&alpn=h2&alpn=http%2F1.1", " p@:/?# "),
            ("hy2://pass%3A%40@example.com:443?obfs-password=%20secret%20", "pass:@"),
            ("tuic://custom-id:p%3A%40%2F%3F%23%20@[2001:db8::2]:443?alpn=h3&alpn=h2", "custom-id:p:@/?# "),
            ("wg://key%2B%2F%3D@example.com:51820?address=10.0.0.2%2F32&address=fd00%3A%3A2%2F128", "key+/="),
            ("ss://aes-256-gcm:p%3A%40%2F%3F%23%20@[2001:db8::3]:8388?plugin=obfs-local%3Bobfs%3Dhttp", "aes-256-gcm:p:@/?# "),
        ]
        for uri, credential in fixtures:
            with self.subTest(uri=uri):
                node = parse_proxy_uri(uri)
                self.assertIsNotNone(node)
                self.assertEqual(node.credential, credential)
                rebuilt = parse_proxy_uri(node.to_uri(False))
                self.assertIsNotNone(rebuilt)
                self.assertEqual(rebuilt.credential, credential)
                self.assertEqual(rebuilt.params, node.params)
                self.assertEqual(rebuilt.remark, node.remark)
                self.assertEqual(rebuilt.semantic_fingerprint(), node.semantic_fingerprint())
        tuic = parse_proxy_uri(fixtures[3][0]).to_uri(False)
        self.assertIn("custom-id:p%3A%40%2F%3F%23%20@", tuic)

    def test_shadowsocks_2022_uses_percent_encoded_not_base64_userinfo(self):
        node = ProxyNode("ss", "example.com", 8388, "2022-blake3-aes-128-gcm:key+/=:second+/=")
        uri = node.to_uri(False)
        self.assertEqual(uri, "ss://2022-blake3-aes-128-gcm:key%2B%2F%3D%3Asecond%2B%2F%3D@example.com:8388")
        self.assertEqual(parse_proxy_uri(uri).credential, "2022-blake3-aes-128-gcm:key+/=:second+/=")

    def test_query_style_vmess_repeated_values_round_trip_or_reject_at_parse(self):
        node = parse_proxy_uri("vmess://custom@example.com:443?type=grpc&path=a&alpn=h2&alpn=h3")
        self.assertIsNotNone(node)
        rebuilt = parse_proxy_uri(node.to_uri(False))
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.params["path"], "a")
        self.assertEqual(rebuilt.params["alpn"], ["h2", "h3"])
        self.assertEqual(rebuilt.semantic_fingerprint(), node.semantic_fingerprint())
        self.assertIsNone(parse_proxy_uri("vmess://custom@example.com:443?type=grpc&path=a&path=b"))

    def test_repeated_and_blank_query_values_are_not_flattened(self):
        self.assertIsNone(parse_proxy_uri("vless://id@example.com:443?path=&path=%20%2Fx%20"))
        node = parse_proxy_uri("vless://id@example.com:443?path=&alpn=h2&alpn=h3&unknown=&unknown=%20%2Fx%20")
        self.assertEqual(node.params, {"path": "", "alpn": ["h2", "h3"], "unknown": ["", " /x "]})
        self.assertEqual(parse_proxy_uri(node.to_uri(False)).params, node.params)

    def test_authority_path_preserves_trailing_slash_and_whitespace(self):
        node = parse_proxy_uri("vless://custom@example.com:443/%20x%20/?type=ws")
        self.assertEqual(node.params["path"], "/ x /")
        self.assertIsNone(parse_proxy_uri("vless://custom@example.com:443/a?path=%2Fb"))

    def test_vmess_json_extra_fields_grpc_and_canonical_updates_round_trip(self):
        data = {"v": "2", "add": "vmess.example", "port": "443", "id": " custom ",
                "ps": "源 #名", "net": "grpc", "type": "none", "tls": "tls",
                "path": " service / ", "serviceName": " service / ", "aid": 0,
                "scy": "aes-128-gcm", "alpn": ["h2", "h3"], "fp": "chrome",
                "extra": {"headers": {"X-Token": " a "}, "list": ["", "x"]}}
        uri = "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()
        node = parse_proxy_uri(uri)
        self.assertEqual(node.params["extra"], data["extra"])
        self.assertEqual(node.params["aid"], "0")
        node.credential = "new custom"
        rebuilt_uri = node.to_uri(False)
        decoded = json.loads(base64.b64decode(rebuilt_uri[8:]))
        self.assertEqual(decoded["id"], "new custom")
        self.assertEqual(decoded["extra"], data["extra"])
        self.assertEqual(decoded["path"], " service / ")
        self.assertEqual(parse_proxy_uri(rebuilt_uri).semantic_fingerprint(), node.semantic_fingerprint())

    def test_vmess_conflicting_or_unrepresentable_reserved_fields_are_rejected(self):
        base = {"add": "example.com", "port": 443, "id": "custom"}
        for extras in [{"security": {"cipher": "custom"}}, {"headerType": {"required": "value"}},
                       {"network": {"required": "value"}}, {"aid": "NaN"},
                       {"tls": "tls", "security": "other"}]:
            with self.subTest(extras=extras):
                uri = "vmess://" + base64.b64encode(json.dumps(base | extras).encode()).decode()
                self.assertIsNone(parse_proxy_uri(uri))
        self.assertIsNone(parse_proxy_uri("vmess://custom@example.com:443?aid=NaN"))

    def test_invalid_shapes_ports_and_auth_are_rejected(self):
        for uri in ["vless://@example.com:443", "trojan://p@example.com:",
                    "vless://id@example.com:1.5", "vless://id@example.com:65536",
                    "vless://id@example.com:0", "vless://id@[not-ip]:443",
                    "tuic://password@example.com:443", "ss://password@example.com:8388",
                    "freedom://id@example.com:443"]:
            with self.subTest(uri=uri):
                self.assertIsNone(parse_proxy_uri(uri))
        for port in [True, 443.5, float("inf"), float("nan"), "1.5", "", 0, 65536]:
            data = {"add": "example.com", "port": port, "id": "custom"}
            uri = "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()
            with self.subTest(port=port):
                self.assertIsNone(parse_proxy_uri(uri))

    def test_observation_labels_do_not_promote_source_or_legacy_flags(self):
        node = ProxyNode("vless", "example.com", 443, "id", is_verified_source=True,
                         passed_healthcheck=True, latency_ms=10)
        label = node.format_remark()
        self.assertIn("source-claimed", label)
        self.assertNotIn("socket", label)
        self.assertNotIn("end-to-end", label)
        self.assertNotIn("10ms", label)
        fingerprint = node.semantic_fingerprint()
        node.healthcheck_kind, node.healthchecked_at = "socket", "2026-11-10T12:00:00Z"
        self.assertIn("socket", node.format_remark())
        self.assertNotIn("end-to-end", node.format_remark())
        node.healthcheck_kind = "xray"
        self.assertIn("end-to-end", node.format_remark())
        self.assertEqual(node.semantic_fingerprint(), fingerprint)

    def test_metadata_merging_is_deterministic_and_does_not_mix_observations(self):
        base = ProxyNode("vless", "example.com", 443, "id", params={"fp": "chrome"})
        a = replace(base, remark="z", country_code="DE", source_ids={"a"},
                    healthcheck_kind="socket", healthchecked_at="2026-11-10T12:00:00Z",
                    passed_healthcheck=True, latency_ms=1)
        b = replace(base, remark="a", country_code="US", source_ids={"b"},
                    healthcheck_kind="socket", healthchecked_at="2026-11-10T13:00:00Z",
                    passed_healthcheck=False, latency_ms=None, is_verified_source=True)
        first, second = deduplicate_nodes([a, b])[0], deduplicate_nodes([b, a])[0]
        self.assertEqual(first, second)
        self.assertEqual(first.params, {"fp": "chrome"})
        self.assertEqual(first.country_code, "XX")
        self.assertEqual(first.remark, "a")
        self.assertEqual(first.source_ids, {"a", "b"})
        self.assertFalse(first.passed_healthcheck)
        self.assertIsNone(first.latency_ms)
        self.assertEqual(first.healthchecked_at, "2026-11-10T13:00:00Z")

    def test_equal_connections_have_a_deterministic_canonical_representative(self):
        first = ProxyNode("vless", "example.com", 443, "id", params={"sni": "EXAMPLE.COM"})
        second = replace(first, params={"sni": "example.com"})
        self.assertEqual(deduplicate_nodes([first, second]), deduplicate_nodes([second, first]))

    def test_country_is_only_an_unambiguous_source_claim(self):
        for remark, want in [("North Korea", "KP"), ("South Korea", "KR"),
                             ("Korea", "XX"), ("America", "XX"), ("[IN]", "IN"),
                             ("[NO]", "NO"), ("country:IT", "IT"), ("[DE] [US]", "XX")]:
            with self.subTest(remark=remark):
                self.assertEqual(detect_country(remark), want)
        self.assertEqual(detect_country("unknown", "example.de", "example.ir"), "XX")

    def test_geographic_and_web_predicates_do_not_invent_optimization(self):
        node = ProxyNode("vless", "example.com", 443, "id", params={"fm": "1"}, tags={"cdn"})
        self.assertTrue(node.has_special_parameters())
        self.assertFalse(node.has_web_transport())
        self.assertFalse(node.is_iran_related())
        node.country_code = "IR"
        self.assertTrue(node.is_iran_related())


class TestStrictSourceConfig(unittest.TestCase):
    def test_invalid_middle_source_does_not_poison_valid_rows(self):
        valid = {"id": "source", "url": "https://example.com/feed", "tags": ["iran"]}
        for change in [{"id": 1}, {"url": []}, {"enabled": "false"},
                       {"verified_upstream": 1}, {"tags": "iran"}, {"tags": [1]},
                       {"format": []}, {"name": None}]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    SourceConfig.from_dict(valid | change)
        self.assertEqual(SourceConfig.from_dict(valid).tags, ["iran"])


class TestLosslessContent(unittest.TestCase):
    source = SourceConfig("fixture", "Fixture", "https://example.com/feed")

    def test_href_only_html_and_mixed_encoded_lines_survive(self):
        first = "vless://first@example.com:443?type=ws&security=tls#One"
        second = "trojan://second@example.com:8443#Two"
        markup = '<html><a href="' + first.replace("&", "&amp;") + '">import</a></html>'
        nodes, _ = extract_nodes_from_content(markup, self.source)
        self.assertEqual([node.credential for node in nodes], ["first"])
        content = first + "\n" + base64.b64encode(second.encode()).decode()
        nodes, _ = extract_nodes_from_content(content, self.source)
        self.assertEqual([node.credential for node in nodes], ["first", "second"])

    def test_json_users_are_independently_lossless_or_rejected(self):
        outbound = {"protocol": "vmess", "settings": {"vnext": [
            {"address": "example.com", "port": 443, "users": [
                {"id": "first", "alterId": 0, "security": "aes-128-gcm"},
                {"id": None}, {"id": "second", "alterId": 2, "security": "auto"},
                {"id": "unsupported", "unknownRequired": "must-preserve"}]}]},
            "streamSettings": {"network": "grpc", "security": "tls",
                "grpcSettings": {"serviceName": " svc / ", "multiMode": True},
                "tlsSettings": {"serverName": "EXAMPLE.com", "alpn": ["h2", "h3"], "fingerprint": "chrome"}}}
        nodes, executable = extract_nodes_from_content(json.dumps([{"outbounds": [outbound]}, None, {"outbounds": "bad"}]), self.source)
        self.assertEqual([node.credential for node in nodes], ["first", "second"])
        self.assertEqual(nodes[0].params["aid"], "0")
        self.assertEqual(nodes[1].params["aid"], "2")
        self.assertEqual(nodes[0].params["scy"], "aes-128-gcm")
        self.assertEqual(nodes[0].params["serviceName"], " svc / ")
        self.assertEqual(nodes[0].params["multiMode"], "true")
        self.assertEqual(nodes[0].params["alpn"], ["h2", "h3"])
        self.assertEqual(executable, [])
        for node in nodes:
            self.assertEqual(parse_proxy_uri(node.to_uri(False)).semantic_fingerprint(), node.semantic_fingerprint())

    def test_json_shadowsocks_is_normalized_and_preserves_plugin(self):
        ob = {"protocol": "shadowsocks", "settings": {"servers": [
            {"address": "example.com", "port": 8388, "method": "aes-256-gcm", "password": " p:@/ ",
             "plugin": "obfs-local", "pluginOpts": "obfs=http;obfs-host=example.com"}]}}
        nodes, raw = extract_nodes_from_content(json.dumps({"outbounds": [ob]}), self.source)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].scheme, "ss")
        self.assertEqual(nodes[0].credential, "aes-256-gcm: p:@/ ")
        self.assertEqual(nodes[0].params["plugin"], "obfs-local;obfs=http;obfs-host=example.com")
        self.assertEqual(raw, [])
        self.assertEqual(parse_proxy_uri(nodes[0].to_uri(False)).credential, "aes-256-gcm: p:@/ ")

    def test_json_trojan_tls_and_host_headers_are_preserved(self):
        ob = {"protocol": "trojan", "settings": {"servers": [
            {"address": "example.com", "port": 8443, "password": " p:@/ "},
            {"address": "bad.example", "port": 0, "password": "bad"}]},
            "streamSettings": {"network": "ws", "security": "tls",
                "wsSettings": {"path": " /x/ ", "headers": {"Host": "CDN.example"}},
                "tlsSettings": {"serverName": "SNI.example", "alpn": ["h2", "http/1.1"],
                    "fingerprint": "firefox", "allowInsecure": False, "disableSNI": True}}}
        nodes, _ = extract_nodes_from_content(json.dumps({"outbounds": [ob]}), self.source)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].params, {"type": "ws", "security": "tls", "path": " /x/ ",
            "host": "CDN.example", "sni": "SNI.example", "alpn": ["h2", "http/1.1"],
            "fp": "firefox", "allowInsecure": "false", "disableSNI": "true"})
        self.assertEqual(parse_proxy_uri(nodes[0].to_uri(False)).params, nodes[0].params)

    def test_malformed_json_shapes_and_global_transport_never_become_connections(self):
        good = {"protocol": "vless", "settings": {"vnext": [{"address": "example.com", "port": 443, "users": [{"id": "custom"}]}]}}
        for content in ['{"outbounds": "bad"}', '{"outbounds": [', '[1, null, []]',
                        '{"outbounds": [], "outbounds": []}',
                        json.dumps({"outbounds": [good], "transport": {"wsSettings": {"path": "/required"}}})]:
            with self.subTest(content=content):
                self.assertEqual(extract_nodes_from_content(content, self.source), ([], []))

    def test_candidate_limits_raise_without_returning_a_truncated_feed(self):
        first = "vless://first@example.com:443"
        second = "trojan://second@example.com:8443"
        for content in [first + "\n" + second,
                        first + "\n" + base64.b64encode(second.encode()).decode(),
                        "vless://@example.com:443\n" + first]:
            with self.subTest(content=content), patch("src.parsers.MAX_CONTENT_NODES", 1):
                with self.assertRaises(ValueError):
                    extract_nodes_from_content(content, self.source)
        ob = {"protocol": "vless", "settings": {"vnext": [
            {"address": "example.com", "port": 443, "users": [{"id": "first"}, {"id": "second"}]}]}}
        with patch("src.parsers.MAX_CONTENT_NODES", 1):
            with self.assertRaises(ValueError):
                extract_nodes_from_content(json.dumps({"outbounds": [ob]}), self.source)
            self.assertEqual(len(extract_nodes_from_content(first, self.source)[0]), 1)

    def test_oversized_uri_or_serialized_json_connection_is_an_explicit_resource_error(self):
        uri = "vless://id@example.com:443?path=" + "x" * 200
        node = ProxyNode("vless", "example.com", 443, "id", params={"path": "x" * 200})
        ob = {"protocol": "vless", "settings": {"vnext": [{"address": "example.com", "port": 443, "users": [{"id": "id"}]}]},
              "streamSettings": {"network": "ws", "wsSettings": {"path": "x" * 200}}}
        with patch("src.models.MAX_URI_BYTES", 128):
            for operation in [lambda: parse_proxy_uri(uri), lambda: node.to_uri(False),
                              lambda: extract_nodes_from_content(uri, self.source),
                              lambda: extract_nodes_from_content(json.dumps({"outbounds": [ob]}), self.source)]:
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    operation()
        unicode_uri = "vless://id@example.com:443?path=" + "界" * 10
        with patch("src.models.MAX_URI_BYTES", 50), self.assertRaises(ValueError):
            parse_proxy_uri(unicode_uri)

    def test_html_and_json_container_limits_reject_excess_before_expanding(self):
        uri = "vless://id@example.com:443"
        for content in [f'<html><a href="{uri}">first</a><a href="{uri}">second</a></html>',
                        json.dumps([{"outbounds": []}, {"outbounds": []}]),
                        json.dumps({"outbounds": [None, None]})]:
            with self.subTest(content=content), patch("src.parsers.MAX_CONTENT_NODES", 1), self.assertRaises(ValueError):
                extract_nodes_from_content(content, self.source)

    def test_punctuation_in_paths_is_connection_data_not_prose(self):
        node = parse_proxy_uri("vless://id@example.com:443/path.")
        self.assertEqual(node.params["path"], "/path.")
        self.assertEqual(parse_proxy_uri(node.to_uri(False)).params["path"], "/path.")

    def test_json_unsupported_settings_dependencies_and_invalid_peers_do_not_leak(self):
        good = {"protocol": "vless", "settings": {"vnext": [
            {"address": "example.com", "port": 443, "users": [{"id": "custom", "encryption": "none", "flow": ""}]}]}}
        bad_entries = [None, {"protocol": "freedom", "settings": good["settings"]},
                       good | {"proxySettings": {"tag": "upstream"}},
                       good | {"streamSettings": {"sockopt": {"dialerProxy": "other"}}},
                       good | {"streamSettings": {"security": "tls", "tlsSettings": {"certificates": [{}]}}},
                       good | {"streamSettings": {"network": "ws", "wsSettings": {"headers": {"X-Required": "token"}}}},
                       good | {"settings": {"vnext": "bad"}}]
        for port in [True, 1.5, float("inf"), float("nan"), "1.5", 0, 65536]:
            bad_entries.append(good | {"settings": {"vnext": [{"address": "example.com", "port": port, "users": [{"id": "bad"}]}]}})
        nodes, executable = extract_nodes_from_content(json.dumps({"outbounds": bad_entries + [good]}), self.source)
        self.assertEqual([node.credential for node in nodes], ["custom"])
        self.assertEqual(nodes[0].params["encryption"], "none")
        self.assertEqual(nodes[0].params["flow"], "")
        self.assertEqual(executable, [])

    def test_malformed_unicode_entries_do_not_discard_valid_json_peers(self):
        ob = {"protocol": "trojan", "settings": {"servers": [
            {"address": "example.com", "port": 443, "password": "bad\ud800"},
            {"address": "example.com", "port": 443, "password": "good"}]}}
        nodes, _ = extract_nodes_from_content(json.dumps({"outbounds": [ob]}), self.source)
        self.assertEqual([node.credential for node in nodes], ["good"])
        self.assertIsNone(parse_proxy_uri("trojan://bad\ud800@example.com:443"))


class TestTask3ReviewRegressions(unittest.TestCase):
    source = SourceConfig("fixture", "Fixture", "https://example.com/feed")

    def test_vmess_query_aliases_have_one_lossless_canonical_key(self):
        node = parse_proxy_uri("vmess://custom@example.com:443?tls=tls&net=grpc&headerType=none")
        self.assertIsNotNone(node)
        self.assertEqual(node.params, {"security": "tls", "type": "grpc", "headerType": "none"})
        rebuilt = parse_proxy_uri(node.to_uri(False))
        self.assertEqual(rebuilt.params, {"security": "tls", "type": "grpc", "headerType": "none"})
        self.assertEqual(rebuilt.semantic_fingerprint(), node.semantic_fingerprint())

    def test_vmess_reverse_alias_conflicts_never_overwrite_settings(self):
        for query in ["security=tls&tls=none", "tls=none&security=tls",
                      "headerType=none&header_type=http", "type=grpc&net=ws"]:
            with self.subTest(query=query):
                self.assertIsNone(parse_proxy_uri("vmess://custom@example.com:443?" + query))
        node = ProxyNode("vmess", "example.com", 443, "custom")
        node.params = {"security": "tls", "tls": "none"}
        with self.assertRaises(ValueError):
            node.to_uri(False)

    def test_single_alpn_json_connection_retains_one_stable_identity(self):
        outbound = {"protocol": "trojan", "settings": {"servers": [
            {"address": "example.com", "port": 443, "password": "custom"}]},
            "streamSettings": {"security": "tls", "tlsSettings": {"alpn": ["h2"]}}}
        nodes, _ = extract_nodes_from_content(json.dumps({"outbounds": [outbound]}), self.source)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].params, {"security": "tls", "alpn": "h2"})
        rebuilt = parse_proxy_uri(nodes[0].to_uri(False))
        self.assertEqual(rebuilt.params, {"security": "tls", "alpn": "h2"})
        self.assertEqual(rebuilt.semantic_fingerprint(), nodes[0].semantic_fingerprint())

    def test_singleton_uri_values_normalize_without_flattening_vmess_extras(self):
        for scheme, credential in [("vless", "custom"), ("trojan", "secret"), ("ss", "aes-256-gcm:secret"),
                                   ("hysteria2", "secret"), ("tuic", "custom:secret"), ("wireguard", "key")]:
            with self.subTest(scheme=scheme):
                node = ProxyNode(scheme, "example.com", 443, credential,
                                 params={"alpn": ["h2"], "unknown": [" x "]})
                self.assertEqual(node.params, {"alpn": "h2", "unknown": " x "})
                rebuilt = parse_proxy_uri(node.to_uri(False))
                self.assertEqual(rebuilt.params, {"alpn": "h2", "unknown": " x "})
                self.assertEqual(rebuilt.semantic_fingerprint(), node.semantic_fingerprint())
                node.params = {"alpn": ["h2"], "unknown": [" x "]}
                self.assertEqual(rebuilt.semantic_fingerprint(), node.semantic_fingerprint())
                self.assertEqual(parse_proxy_uri(node.to_uri(False)).params, rebuilt.params)
        vmess = ProxyNode("vmess", "example.com", 443, "custom", params={"alpn": ["h2"], "extra": [" x "]})
        self.assertEqual(vmess.params, {"alpn": "h2", "extra": [" x "]})
        self.assertEqual(parse_proxy_uri(vmess.to_uri(False)).params, {"alpn": "h2", "extra": [" x "]})
        invalid = ProxyNode("vless", "example.com", 443, "custom", params={"unknown": [{"required": "value"}]})
        with self.assertRaises(ValueError):
            invalid.to_uri(False)

    def test_deep_vmess_extras_fail_with_a_controlled_resource_error(self):
        for depth in (1100, 2200):
            payload = '{"add":"example.com","port":443,"id":"custom","extra":' + '[' * depth + '"required"' + ']' * depth + '}'
            uri = "vmess://" + base64.b64encode(payload.encode()).decode()
            with self.subTest(depth=depth), self.assertRaises(ConnectionLimitError):
                parse_proxy_uri(uri)
        node = ProxyNode("vmess", "example.com", 443, "custom")
        nested = "required"
        for _ in range(1100):
            nested = [nested]
        node.params = {"extra": nested}
        with self.assertRaises(ConnectionLimitError):
            deduplicate_nodes([node])

    def test_admitted_nesting_round_trips_and_dedup_keeps_an_independent_copy(self):
        with patch("src.models.MAX_PARAMETER_DEPTH", 2):
            payload = '{"add":"example.com","port":443,"id":"custom","extra":[["required"]]}'
            uri = "vmess://" + base64.b64encode(payload.encode()).decode()
            node = parse_proxy_uri(uri)
            self.assertIsNotNone(node)
            self.assertEqual(node.params, {"extra": [["required"]]})
            self.assertEqual(parse_proxy_uri(node.to_uri(False)).semantic_fingerprint(), node.semantic_fingerprint())
            merged = deduplicate_nodes([node])[0]
            merged.params["extra"][0][0] = "changed"
            self.assertEqual(node.params, {"extra": [["required"]]})
            excessive = '{"add":"example.com","port":443,"id":"custom","extra":[[["required"]]]}'
            with self.assertRaises(ConnectionLimitError):
                parse_proxy_uri("vmess://" + base64.b64encode(excessive.encode()).decode())

    def test_bracketed_country_heading_does_not_turn_plain_peers_into_json(self):
        first = "vless://first@example.com:443"
        second = base64.b64encode(b"trojan://second@example.com:443").decode()
        for heading in ("[DE]", "[IN]", "[North Korea]"):
            with self.subTest(heading=heading):
                nodes, executable = extract_nodes_from_content(heading + "\n" + first + "\n" + second, self.source)
                self.assertEqual([node.credential for node in nodes], ["first", "second"])
                self.assertEqual(executable, [])

    def test_rejected_or_malformed_json_is_never_rescanned_for_embedded_uris(self):
        uri = "vless://embedded@example.com:443"
        chained = {"outbounds": [{"protocol": "vless", "proxySettings": {"tag": "other"},
                                  "settings": {"vnext": [{"address": "example.com", "port": 443,
                                      "users": [{"id": "custom"}]}]}, "uri": uri}]}
        for content in [json.dumps(chained), json.dumps([chained]),
                        '{"outbounds":[{"uri":"' + uri + '"',
                        '[broken,{"outbounds":[{"uri":"' + uri + '"}]}]',
                        r'[broken,{"\u006futbounds":[{"uri":"' + uri + '"}]}]',
                        r'[broken,{"\u006futbounds":[{"\u0075ri":"' + uri + '"}]}]',
                        json.dumps({"unsupported": uri})]:
            with self.subTest(content=content):
                self.assertEqual(extract_nodes_from_content(content, self.source), ([], []))
        explicit_json = replace(self.source, format="xray_json")
        self.assertEqual(extract_nodes_from_content("[DE]\n" + uri, explicit_json), ([], []))

    def test_invalid_enclosing_json_records_still_consume_expanded_candidate_budget(self):
        bad_server = {"address": "example.com", "port": 0, "users": [{"id": "bad"}] * 4}
        good_server = {"address": "example.com", "port": 443, "users": [{"id": "good"}]}
        base = {"protocol": "vless", "settings": {"vnext": [bad_server, good_server]}}
        cases = [
            {"outbounds": [base]},
            {"outbounds": [base | {"streamSettings": {"network": "unsupported"}}]},
            {"outbounds": [base | {"proxySettings": {"tag": "dependency"}}]},
            {"outbounds": [base | {"protocol": "freedom"}]},
            {"outbounds": [base | {"settings": base["settings"] | {"unsupported": True}}]},
            {"outbounds": [base]},
        ]
        cases[-1]["transport"] = {"wsSettings": {"path": "/inherited"}}
        with patch("src.parsers.MAX_CONTENT_NODES", 2):
            for index, config in enumerate(cases):
                with self.subTest(case=index), self.assertRaises(ValueError):
                    extract_nodes_from_content(json.dumps(config), self.source)
            exact = {"protocol": "vless", "settings": {"vnext": [
                {"address": "example.com", "port": 443, "users": [{"id": "first"}, {"id": "second"}]}]}}
            nodes, _ = extract_nodes_from_content(json.dumps({"outbounds": [exact]}), self.source)
            self.assertEqual([node.credential for node in nodes], ["first", "second"])

    def test_approved_ocr_cache_survives_synthetic_discovery_registry_loading(self):
        from src.fetcher import load_sources

        ocr = {"id": "nemotron-vision-ocr", "name": "Nemotron 3.5 Super VL Vision OCR Feed",
               "url": "data/discovered_ocr_configs.txt", "verified_upstream": False,
               "format": "auto", "tags": ["auto-discovered", "vision-ocr", "nemotron"], "enabled": True}
        registries = {
            "fixture-sources.json": {"sources": [{"id": "public-http", "url": "https://example.com/feed"}]},
            "fixture-discovered.json": {"sources": [ocr]},
        }

        def registry_open(path, mode="r", *args, **kwargs):
            self.assertEqual(mode, "rb")
            return io.BytesIO(json.dumps(registries[path.name]).encode())

        with patch.object(Path, "open", autospec=True, side_effect=registry_open):
            sources = load_sources("fixture-sources.json", "fixture-discovered.json")
        self.assertEqual([source.id for source in sources], ["public-http", "nemotron-vision-ocr"])
        self.assertEqual(sources[1].url, "data/discovered_ocr_configs.txt")
        self.assertEqual(sources[1].tags, ["auto-discovered", "vision-ocr", "nemotron"])
        self.assertFalse(sources[1].verified_upstream)

    def test_ocr_exception_never_accepts_arbitrary_local_paths_or_variants(self):
        for url in ["data/private.txt", "data/../private.txt", "../private.txt", "C:/private.txt",
                    "file:///C:/private.txt", "./data/discovered_ocr_configs.txt", "data\\discovered_ocr_configs.txt",
                    "data/discovered_ocr_configs.txt#fragment", "data/discovered_ocr_configs.txt?query=1",
                    "data/discovered_ocr_configs.txt/", "DATA/discovered_ocr_configs.txt"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                SourceConfig.from_dict({"id": "fixture", "url": url})

    def test_ambiguous_single_alpn_is_not_flattened_into_a_different_protocol_list(self):
        bad = {"protocol": "trojan", "settings": {"servers": [
            {"address": "example.com", "port": 443, "password": "bad"}]},
            "streamSettings": {"security": "tls", "tlsSettings": {"alpn": ["h2,h3"]}}}
        good = {"protocol": "trojan", "settings": {"servers": [
            {"address": "example.com", "port": 443, "password": "good"}]},
            "streamSettings": {"security": "tls", "tlsSettings": {"alpn": ["h2"]}}}
        for ambiguous in ("h2,h3", ""):
            with self.subTest(alpn=ambiguous):
                bad["streamSettings"]["tlsSettings"]["alpn"] = [ambiguous]
                nodes, _ = extract_nodes_from_content(json.dumps({"outbounds": [bad, good]}), self.source)
                self.assertEqual([node.credential for node in nodes], ["good"])
                vmess = ProxyNode("vmess", "example.com", 443, "custom", params={"alpn": [ambiguous]})
                self.assertEqual(vmess.params, {"alpn": [ambiguous]})
                self.assertEqual(parse_proxy_uri(vmess.to_uri(False)).params, {"alpn": [ambiguous]})

    def test_decoder_recursion_is_a_resource_error_not_a_process_crash_or_empty_feed(self):
        uri = "vmess://" + base64.b64encode(b'{"add":"example.com","port":443,"id":"custom"}').decode()
        with patch("src.parsers.json.loads", side_effect=RecursionError("decoder limit")):
            for operation in [lambda: parse_proxy_uri(uri),
                              lambda: extract_nodes_from_content('{"outbounds":[]}', self.source)]:
                with self.subTest(operation=operation), self.assertRaises(ConnectionLimitError):
                    operation()
