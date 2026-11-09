from __future__ import annotations

import base64
import json
import subprocess
import unittest
import urllib.parse
from pathlib import Path

from src.dedup import deduplicate_nodes
from src.models import (
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
        """Verify sources.json has 60 public sources and zero private leaks."""
        sources_path = BASE_DIR / "sources.json"
        assert sources_path.exists(), "sources.json must exist"

        content = sources_path.read_text(encoding="utf-8")

        # Dynamically verify none of the private endpoints appear in sources.json
        private_path = BASE_DIR / "sources.private.json"
        if private_path.exists():
            private_data = json.loads(private_path.read_text(encoding="utf-8"))
            for s in private_data.get("sources", []):
                u = s.get("url", "")
                parsed = urllib.parse.urlparse(u)
                if parsed.netloc:
                    host = parsed.netloc.split(":")[0]
                    assert host not in content, f"Private host {host} leaked in sources.json!"
                    for lbl in host.split("."):
                        if len(lbl) > 8:
                            assert lbl not in content, f"Private label {lbl} leaked in sources.json!"

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
            assert not config.is_private, f"Public source {config.id} marked as is_private"

    def test_sources_private_json_exists_and_ignored(self):
        """Verify sources.private.json is git-ignored and contains 6 BPB sources if present."""
        # Check git ignore status (always runs, even if file does not exist on disk in CI)
        res = subprocess.run(
            ["git", "check-ignore", "sources.private.json"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert res.returncode == 0, "sources.private.json MUST be ignored by git!"
        assert "sources.private.json" in res.stdout

        private_path = BASE_DIR / "sources.private.json"
        if private_path.exists():
            data = json.loads(private_path.read_text(encoding="utf-8"))
            assert "sources" in data
            assert len(data["sources"]) == 6, f"Expected 6 private BPB sources, got {len(data['sources'])}"


    def test_sources_private_example_safe(self):
        """Verify sources.private.json.example is a safe committed template."""
        example_path = BASE_DIR / "sources.private.json.example"
        assert example_path.exists(), "sources.private.json.example must exist"
        content = example_path.read_text(encoding="utf-8")

        # Dynamically verify none of the private endpoints appear in example
        private_path = BASE_DIR / "sources.private.json"
        if private_path.exists():
            private_data = json.loads(private_path.read_text(encoding="utf-8"))
            for s in private_data.get("sources", []):
                u = s.get("url", "")
                parsed = urllib.parse.urlparse(u)
                if parsed.netloc:
                    host = parsed.netloc.split(":")[0]
                    assert host not in content, f"Private host {host} leaked in example!"
                    for lbl in host.split("."):
                        if len(lbl) > 8:
                            assert lbl not in content, f"Private label {lbl} leaked in example!"


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
        assert node.is_cdn_fronted() is True
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
        assert node.is_cdn_fronted() is True

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

    def test_pattn_special_detection(self):
        node = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"fm": "1"},
        )
        assert node.is_pattn_special() is True

        node2 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"dialMode": "1"},
        )
        assert node2.is_pattn_special() is True

        node3 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"cs": "test"},
        )
        assert node3.is_pattn_special() is True

        node4 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            params={"fp": "unsafe"},
        )
        assert node4.is_pattn_special() is True

        node5 = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
            tags={"pattn"},
        )
        assert node5.is_pattn_special() is True

        normal_node = ProxyNode(
            scheme="vless",
            address="1.1.1.1",
            port=443,
            credential="uuid",
        )
        assert normal_node.is_pattn_special() is False


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
        assert len(jsons) == 1
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
        assert "[✅]" in formatted_uri
        assert "[🇩🇪 DE]" in formatted_uri
        assert "VLESS-Reality" in formatted_uri
        assert "45ms" in formatted_uri
