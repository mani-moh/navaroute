from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models import ProxyNode, SourceStats


def _format_header(title: str) -> list[str]:
    b64_title = base64.b64encode(title.encode("utf-8")).decode("utf-8")
    return [
        f"#profile-title: base64:{b64_title}",
        "#profile-update-interval: 6",
        "#subscription-userinfo: upload=0; download=0; total=10737418240000000; expire=4102444800",
        "",
    ]


def _write_sub_pair(
    file_path: Path,
    title: str,
    nodes: list[ProxyNode],
    counts: dict[str, int],
) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    headers = _format_header(title)
    uris = [n.to_uri(include_formatted_remark=True) for n in nodes]
    plain_content = "\n".join(headers + uris) + ("\n" if uris else "")
    file_path.write_text(plain_content, encoding="utf-8")

    # Base64 encoded file
    b64_path = file_path.with_name(f"{file_path.stem}_base64.txt")
    uris_joined = "\n".join(uris)
    b64_content = (
        base64.b64encode(uris_joined.encode("utf-8")).decode("utf-8") if uris else ""
    )
    b64_path.write_text(b64_content, encoding="utf-8")

    # Record relative posix paths
    counts[str(file_path).replace("\\", "/")] = len(nodes)
    counts[str(b64_path).replace("\\", "/")] = len(nodes)


def publish_subscriptions(
    nodes: list[ProxyNode],
    json_configs: list[dict],
    stats: list[SourceStats],
    output_dir: str = "subs",
    update_docs: bool = True,
) -> dict[str, int]:
    """Publishes public subscription files, categorized channels, protocol lists,
    country groupings, and optionally updates STATUS.md and README.md.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    public_nodes = list(nodes)

    # Determine if healthcheck was run
    healthcheck_ran = any(n.passed_healthcheck for n in public_nodes)

    # -------------------------------------------------------------
    # 1. CORE SUBSCRIPTIONS
    # -------------------------------------------------------------
    # Verified: nodes from verified source that passed healthcheck (or all verified if healthcheck disabled),
    # plus any unverified node that passed healthcheck.
    if healthcheck_ran:
        verified_nodes = [
            n for n in public_nodes if n.passed_healthcheck
        ]
    else:
        verified_nodes = [
            n for n in public_nodes if n.is_verified_source
        ]

    verified_nodes.sort(
        key=lambda n: (
            not n.passed_healthcheck,
            not n.is_verified_source,
            n.latency_ms if n.latency_ms is not None else 9999.0,
        )
    )
    _write_sub_pair(out_dir / "verified.txt", "PattN Verified", verified_nodes, counts)

    # All nodes: sorted by healthcheck and verified status
    all_sorted = list(public_nodes)
    all_sorted.sort(
        key=lambda n: (
            not n.passed_healthcheck,
            not n.is_verified_source,
            n.latency_ms if n.latency_ms is not None else 9999.0,
        )
    )
    _write_sub_pair(out_dir / "all.txt", "PattN All Configs", all_sorted, counts)

    # Top 100: fastest verified / healthy nodes
    top100_candidates = [
        n for n in public_nodes if n.passed_healthcheck or n.is_verified_source
    ]
    top100_candidates.sort(
        key=lambda n: (
            not n.passed_healthcheck,
            not n.is_verified_source,
            n.latency_ms if n.latency_ms is not None else 9999.0,
        )
    )
    top100_nodes = top100_candidates[:100]
    _write_sub_pair(out_dir / "top100.txt", "PattN Top 100 Fastest", top100_nodes, counts)

    # Lite 500: balanced across protocols
    protocols = ["vless", "vmess", "trojan", "ss", "hysteria2", "tuic"]
    proto_buckets: dict[str, list[ProxyNode]] = {p: [] for p in protocols}
    for n in public_nodes:
        if n.scheme in proto_buckets:
            proto_buckets[n.scheme].append(n)

    for p in protocols:
        proto_buckets[p].sort(
            key=lambda n: (
                not n.passed_healthcheck,
                not n.is_verified_source,
                n.latency_ms if n.latency_ms is not None else 9999.0,
            )
        )

    lite500_nodes: list[ProxyNode] = []
    round_idx = 0
    while len(lite500_nodes) < 500:
        added_in_round = False
        for p in protocols:
            bucket = proto_buckets[p]
            if round_idx < len(bucket) and len(lite500_nodes) < 500:
                lite500_nodes.append(bucket[round_idx])
                added_in_round = True
        if not added_in_round:
            break
        round_idx += 1

    _write_sub_pair(out_dir / "lite500.txt", "PattN Lite 500 Balanced", lite500_nodes, counts)

    # -------------------------------------------------------------
    # 2. CATEGORIES
    # -------------------------------------------------------------
    categories_dir = out_dir / "categories"
    categories_dir.mkdir(parents=True, exist_ok=True)

    # PattN Special (F&F, IPv6-OOB, SNI-Spoof, fm, dialMode, cs)
    pattn_special_nodes = [n for n in public_nodes if n.has_special_parameters()]
    _write_sub_pair(
        categories_dir / "pattn-special.txt",
        "PattN Special Bypass",
        pattn_special_nodes,
        counts,
    )

    # Iran Optimized
    iran_nodes = [n for n in public_nodes if n.is_iran_related()]
    _write_sub_pair(
        categories_dir / "iran-optimized.txt",
        "PattN Iran Optimized",
        iran_nodes,
        counts,
    )

    # Reality
    reality_nodes = [n for n in public_nodes if n.is_reality()]
    _write_sub_pair(
        categories_dir / "reality.txt",
        "PattN VLESS Reality",
        reality_nodes,
        counts,
    )

    # CDN / WS / gRPC
    cdn_nodes = [n for n in public_nodes if n.has_web_transport()]
    _write_sub_pair(
        categories_dir / "cdn-ws-grpc.txt",
        "PattN CDN WS & gRPC",
        cdn_nodes,
        counts,
    )

    # ConfigsHUB
    confighub_nodes = [n for n in public_nodes if "confighub" in n.tags]
    _write_sub_pair(
        categories_dir / "confighub.txt",
        "PattN ConfigsHUB Feed",
        confighub_nodes,
        counts,
    )

    # Whitelist
    whitelist_nodes = [n for n in public_nodes if "whitelist" in n.tags]
    _write_sub_pair(
        categories_dir / "whitelist.txt",
        "PattN Whitelist Bypass",
        whitelist_nodes,
        counts,
    )

    # Serverless Fragment JSON
    public_json_configs = [dict(j) for j in json_configs]
    serverless_path = categories_dir / "serverless-fragment.json"
    serverless_path.write_text(
        json.dumps(public_json_configs, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    counts[str(serverless_path).replace("\\", "/")] = len(public_json_configs)

    # -------------------------------------------------------------
    # 3. PROTOCOLS
    # -------------------------------------------------------------
    protocols_dir = out_dir / "protocols"
    protocols_dir.mkdir(parents=True, exist_ok=True)

    for proto in protocols:
        proto_nodes = [n for n in public_nodes if n.scheme == proto]
        proto_title = f"PattN {proto.upper()}"
        _write_sub_pair(
            protocols_dir / f"{proto}.txt",
            proto_title,
            proto_nodes,
            counts,
        )

    # -------------------------------------------------------------
    # 4. TOP COUNTRIES (>= 5 nodes)
    # -------------------------------------------------------------
    countries_dir = out_dir / "countries"
    countries_dir.mkdir(parents=True, exist_ok=True)

    country_groups: dict[str, list[ProxyNode]] = {}
    for n in public_nodes:
        cc = (n.country_code or "XX").lower()
        if cc and cc != "xx":
            country_groups.setdefault(cc, []).append(n)

    for cc, c_nodes in country_groups.items():
        if len(c_nodes) >= 5:
            c_nodes.sort(
                key=lambda n: (
                    not n.passed_healthcheck,
                    n.latency_ms if n.latency_ms is not None else 9999.0,
                )
            )
            _write_sub_pair(
                countries_dir / f"{cc}.txt",
                f"PattN {cc.upper()}",
                c_nodes,
                counts,
            )

    # -------------------------------------------------------------
    # 5. DOCUMENTATION UPDATE (STATUS.md & README.md)
    # -------------------------------------------------------------
    if update_docs:
        base_dir = out_dir.resolve().parent
        _update_status_doc(base_dir / "STATUS.md", stats, public_nodes)
        _update_readme_doc(base_dir / "README.md", counts, public_nodes)

    return counts


def _update_status_doc(
    status_path: Path,
    stats: list[SourceStats],
    public_nodes: list[ProxyNode],
) -> None:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_sources = len(stats)
    active_sources = sum(1 for s in stats if s.success and s.raw_count > 0)
    total_raw = sum(s.raw_count for s in stats)
    total_unique = len(public_nodes)
    verified_count = sum(1 for n in public_nodes if n.is_verified_source)
    healthy_count = sum(1 for n in public_nodes if n.passed_healthcheck)

    lines = [
        "# 📡 PattN Upstream Sources Status",
        "",
        f"> **Last Pipeline Run**: `{now_utc}`  ",
        f"> **Healthcheck Status**: `{healthy_count}` nodes verified healthy.",
        "",
        "## Aggregation Summary",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| **Configured Sources** | `{total_sources}` |",
        f"| **Active Sources** | `{active_sources}` |",
        f"| **Raw Extracted Nodes** | `{total_raw}` |",
        f"| **Unique Deduplicated Nodes** | `{total_unique}` |",
        f"| **Verified Upstream Nodes** | `{verified_count}` |",
        f"| **Reachable / Low-Latency Nodes** | `{healthy_count}` |",
        "",
        "## Per-Source Execution Table",
        "",
        "| Source ID | Name | Upstream | Status | HTTP | Latency (ms) | Raw | Unique | Error |",
        "|:---|:---|:---:|:---:|:---:|---:|---:|---:|:---|",
    ]

    for s in stats:
        verified_badge = "✅" if s.verified_count > 0 else "⚡"
        status_badge = "🟢 OK" if s.success else "🔴 FAIL"
        http_code = str(s.status_code) if s.status_code else "-"
        lat = f"{s.elapsed_ms:.0f}" if s.elapsed_ms else "-"
        err = f"`{s.error[:40]}`" if s.error else "-"
        lines.append(
            f"| `{s.source_id}` | {s.name} | {verified_badge} | {status_badge} | {http_code} | {lat} | {s.raw_count} | {s.unique_count} | {err} |"
        )

    lines.append("")
    status_path.write_text("\n".join(lines), encoding="utf-8")


def _detect_current_branch() -> str:
    if env_branch := os.environ.get("GITHUB_REF_NAME"):
        return env_branch
    try:
        import subprocess
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "master"


DEFAULT_REPO_SLUG = "mani-moh/pattn"


def _parse_repo_slug_from_url(url: str) -> Optional[str]:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+)$", url, re.IGNORECASE)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def detect_repo_slug(default: str = DEFAULT_REPO_SLUG) -> str:
    """Detect repository owner/name slug in order:
    1. GITHUB_REPOSITORY environment variable
    2. Parsing `git remote get-url origin` if configured
    3. Defaulting to default ('mani-moh/pattn')
    """
    env_slug = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if env_slug:
        return env_slug

    try:
        import subprocess
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            parsed = _parse_repo_slug_from_url(res.stdout.strip())
            if parsed:
                return parsed
    except Exception:
        pass

    return default



def _update_readme_doc(
    readme_path: Path,
    counts: dict[str, int],
    public_nodes: list[ProxyNode],
) -> None:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_nodes = len(public_nodes) or counts.get("subs/all.txt", 0)
    healthy_nodes = sum(1 for n in public_nodes if n.passed_healthcheck) or counts.get("subs/verified.txt", 0)
    pattn_special = sum(1 for n in public_nodes if n.has_special_parameters()) or counts.get("subs/categories/pattn-special.txt", 0)

    repo_slug = detect_repo_slug()
    branch = _detect_current_branch()
    repo_url = f"https://raw.githubusercontent.com/{repo_slug}/{branch}"

    # Extract top country links
    country_rows = []
    sorted_country_counts = sorted(
        [
            (k.split("/")[-1].replace(".txt", "").upper(), v, k)
            for k, v in counts.items()
            if "subs/countries/" in k and not k.endswith("_base64.txt")
        ],
        key=lambda x: -x[1],
    )
    for cc, cnt, rel_path in sorted_country_counts[:12]:
        base64_rel = rel_path.replace(".txt", "_base64.txt")
        country_rows.append(
            f"| **{cc}** | [`{cc.lower()}.txt`]({repo_url}/{rel_path}) | [`{cc.lower()}_base64.txt`]({repo_url}/{base64_rel}) | `{cnt}` |"
        )
    countries_table = "\n".join(country_rows) if country_rows else "| - | - | - | - |"

    lines = [
        "# 🌐 PattN - Autonomous High-Speed Proxy Aggregator & Censorship Circumvention Engine",
        "",
        f"[![CI Tests](https://github.com/{repo_slug}/actions/workflows/ci.yml/badge.svg)](https://github.com/{repo_slug}/actions/workflows/ci.yml) "
        f"[![Update Subs](https://github.com/{repo_slug}/actions/workflows/update-subs.yml/badge.svg)](https://github.com/{repo_slug}/actions/workflows/update-subs.yml) "
        f"[![Daily Discovery](https://github.com/{repo_slug}/actions/workflows/daily-deep-discovery.yml/badge.svg)](https://github.com/{repo_slug}/actions/workflows/daily-deep-discovery.yml) "
        "![Supported Protocols](https://img.shields.io/badge/Protocols-VLESS%20%7C%20VMess%20%7C%20Trojan%20%7C%20SS%20%7C%20Hy2%20%7C%20TUIC-brightgreen.svg) "
        "[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) "
        "[![Architecture](https://img.shields.io/badge/Architecture-Docs-blue.svg)](docs/ARCHITECTURE.md) "
        "[![Upstream Status](https://img.shields.io/badge/Sources-STATUS.md-teal.svg)](STATUS.md) "
        "[![Discovery Leaderboard](https://img.shields.io/badge/AI%20Discovery-DISCOVERY.md-purple.svg)](DISCOVERY.md)",
        "",
        "> **PattN** is an enterprise-grade, fully autonomous proxy subscription aggregator and Iranian ISP censorship circumvention pipeline. "
        "Engineered specifically to overcome aggressive Deep Packet Inspection (DPI), SNI filtering, and protocol policing enforced across Iranian carriers "
        "(MCI, Irancell, Rightel, Shatel, Mokhaberat), PattN continuously harvests 60+ upstream feeds, executes semantic deduplication, synthesizes "
        "specialized Fast & Furious fragment and IPv6 out-of-band variants, performs multi-stage socket & real-traffic Xray health probes, and distributes categorized subscriptions for seamless import across all desktop and mobile clients.",
        "",
        f"**Last Refresh**: `{now_utc}` | **Total Public Nodes**: `{total_nodes}` | **Verified Healthy**: `{healthy_nodes}` | **PattN Bypass Variants**: `{pattn_special}`",
        "",
        "---",
        "",
        "## 📑 Table of Contents",
        "",
        "- [1. Core Subscriptions](#-1-core-subscriptions)",
        "- [2. Specialized Bypass Channels](#-2-specialized-bypass-channels)",
        "- [3. Protocols & Top Country Channels](#-3-protocols--top-country-channels)",
        "- [4. Under the Hood: System Architecture & How It Works](#-4-under-the-hood-system-architecture--how-it-works)",
        "- [5. Complete Client Setup & Configuration Guides](#-5-complete-client-setup--configuration-guides)",
        "  - [PattN Desktop (Windows)](#pattn-desktop-windows)",
        "  - [v2rayN (Windows)](#v2rayn-windows)",
        "  - [v2rayNG (Android)](#v2rayng-android)",
        "  - [Clash Verge / Clash Meta (Windows, macOS, Linux)](#clash-verge--clash-meta-windows-macos-linux)",
        "  - [sing-box / Nekobox (Android, iOS, Windows)](#sing-box--nekobox-android-ios-windows)",
        "  - [Streisand / Shadowrocket / FoXray (iOS & macOS)](#streisand--shadowrocket--foxray-ios--macos)",
        "- [6. Local In-Iran Verification Engine (`check_from_iran.ps1`)](#-6-local-in-iran-verification-engine-check_from_iranps1)",
        "- [7. Real-World Use Cases & Scenarios](#-7-real-world-use-cases--scenarios)",
        "- [8. Potential Problems, Failure Modes & Troubleshooting (The Fix-It Guide)](#-8-potential-problems-failure-modes--troubleshooting-the-fix-it-guide)",
        "- [9. Automation Cadence & Repository Workflows](#-9-automation-cadence--repository-workflows)",
        "- [10. CLI Reference & Developer Setup](#-10-cli-reference--developer-setup)",
        "- [11. Security, Privacy & Integrity Guarantees](#-11-security-privacy--integrity-guarantees)",
        "- [12. License & Acknowledgments](#-12-license--acknowledgments)",
        "",
        "---",
        "",
        "## 🚀 1. Core Subscriptions",
        "",
        "These primary subscription groups are updated on an automated 6-hour schedule. Use these feeds for general browsing, high-throughput downloads, and everyday circumvention across any standard client:",
        "",
        "| Feed | Description & Selection Criteria | Plain Subscription URL | Base64 Subscription URL | Active Nodes |",
        "|---|---|---|---|:---:|",
        f"| **⭐ Verified** | Tested & responsive configs (passed live TCP/TLS socket verification or multi-round Xray probing). Latency sorted. | [`verified.txt`]({repo_url}/subs/verified.txt) | [`verified_base64.txt`]({repo_url}/subs/verified_base64.txt) | `{counts.get('subs/verified.txt', 0)}` |",
        f"| **⚡ Top 100** | The 100 lowest-latency verified nodes across all providers. Ideal for streaming and video calls. | [`top100.txt`]({repo_url}/subs/top100.txt) | [`top100_base64.txt`]({repo_url}/subs/top100_base64.txt) | `{counts.get('subs/top100.txt', 0)}` |",
        f"| **⚖️ Lite 500** | A balanced, round-robin mix of 500 nodes across all 6 protocols. Prevents client UI freezing and memory bloat on mobile. | [`lite500.txt`]({repo_url}/subs/lite500.txt) | [`lite500_base64.txt`]({repo_url}/subs/lite500_base64.txt) | `{counts.get('subs/lite500.txt', 0)}` |",
        f"| **📦 All Configs** | Complete, semantically deduplicated aggregator output across all 60+ upstream sources. | [`all.txt`]({repo_url}/subs/all.txt) | [`all_base64.txt`]({repo_url}/subs/all_base64.txt) | `{counts.get('subs/all.txt', 0)}` |",
        "",
        "> [!TIP]",
        "> **Plain URL vs Base64 URL**: Most modern clients (PattN, v2rayN v6+, Clash Meta, sing-box, Nekobox) directly support **Plain Subscription URLs** (one node URI per line). If your client is an older build (legacy v2rayNG or older Shadowrocket) that requires a monolithic Base64 string, use the **Base64 Subscription URL** instead.",
        "",
        "---",
        "",
        "## 🛡️ 2. Specialized Bypass Channels",
        "",
        "Engineered specifically for restrictive network environments, severe packet throttling, and active DPI filtering:",
        "",
        "| Category | Technical Features & Bypasses | Plain Subscription URL | Base64 Subscription URL | Active Nodes |",
        "|---|---|---|---|:---:|",
        f"| **🔥 PattN Special** | Fast & Furious TCP fragmentation (`fm=1`), IPv6 Out-of-Band dial (`dialMode=oob.0`), custom randomized cipher suites. Defeats SNI inspection. | [`pattn-special.txt`]({repo_url}/subs/categories/pattn-special.txt) | [`pattn-special_base64.txt`]({repo_url}/subs/categories/pattn-special_base64.txt) | `{counts.get('subs/categories/pattn-special.txt', 0)}` |",
        f"| **🇮🇷 Iran Optimized** | Domestic routing optimization, low hop-count nodes, Clean IP fronting with minimal latency to Iranian border gateways. | [`iran-optimized.txt`]({repo_url}/subs/categories/iran-optimized.txt) | [`iran-optimized_base64.txt`]({repo_url}/subs/categories/iran-optimized_base64.txt) | `{counts.get('subs/categories/iran-optimized.txt', 0)}` |",
        f"| **🕶️ Reality** | VLESS-Reality TLS 1.3 direct SNI camouflage. Mimics genuine traffic to high-reputation foreign domains without custom SSL certificates. | [`reality.txt`]({repo_url}/subs/categories/reality.txt) | [`reality_base64.txt`]({repo_url}/subs/categories/reality_base64.txt) | `{counts.get('subs/categories/reality.txt', 0)}` |",
        f"| **☁️ CDN WS/gRPC** | WebSocket & gRPC reverse-proxied behind Cloudflare and global CDN edge nodes. Hides origin IP behind Anycast CDN networks. | [`cdn-ws-grpc.txt`]({repo_url}/subs/categories/cdn-ws-grpc.txt) | [`cdn-ws-grpc_base64.txt`]({repo_url}/subs/categories/cdn-ws-grpc_base64.txt) | `{counts.get('subs/categories/cdn-ws-grpc.txt', 0)}` |",
        f"| **🎯 ConfigsHUB** | Curated high-reputation daily channel extracts harvested from elite anti-censorship community channels. | [`confighub.txt`]({repo_url}/subs/categories/confighub.txt) | [`confighub_base64.txt`]({repo_url}/subs/categories/confighub_base64.txt) | `{counts.get('subs/categories/confighub.txt', 0)}` |",
        f"| **⚡ Whitelist** | Clean IP / Whitelist bypass configs designed to punch through domestic intranet whitelist-only restrictions during national blackouts. | [`whitelist.txt`]({repo_url}/subs/categories/whitelist.txt) | [`whitelist_base64.txt`]({repo_url}/subs/categories/whitelist_base64.txt) | `{counts.get('subs/categories/whitelist.txt', 0)}` |",
        f"| **🧩 Serverless JSON** | Ready-to-import Xray JSON outbounds array with embedded fragmentation rules. | [`serverless-fragment.json`]({repo_url}/subs/categories/serverless-fragment.json) | *(Native JSON)* | `{counts.get('subs/categories/serverless-fragment.json', 0)}` |",
        "",
        "---",
        "",
        "## 📡 3. Protocols & Top Country Channels",
        "",
        "### Protocol Feeds",
        "",
        "| Protocol | Key Architectural Advantages | Plain Subscription URL | Base64 Subscription URL | Nodes |",
        "|---|---|---|---|:---:|",
        f"| **VLESS** | Ultra-lightweight protocol without double encryption overhead. Native support for Reality, XTLS-Vision, and WebSocket/gRPC. | [`vless.txt`]({repo_url}/subs/protocols/vless.txt) | [`vless_base64.txt`]({repo_url}/subs/protocols/vless_base64.txt) | `{counts.get('subs/protocols/vless.txt', 0)}` |",
        f"| **VMess** | Time-synchronized HMAC-authenticated protocol. Highly compatible across legacy and modern clients alike. | [`vmess.txt`]({repo_url}/subs/protocols/vmess.txt) | [`vmess_base64.txt`]({repo_url}/subs/protocols/vmess_base64.txt) | `{counts.get('subs/protocols/vmess.txt', 0)}` |",
        f"| **Trojan** | Disguises proxy traffic as standard TLS 1.3 HTTPS web browsing over standard port 443. Resistant to active probing. | [`trojan.txt`]({repo_url}/subs/protocols/trojan.txt) | [`trojan_base64.txt`]({repo_url}/subs/protocols/trojan_base64.txt) | `{counts.get('subs/protocols/trojan.txt', 0)}` |",
        f"| **Shadowsocks** | High-performance AEAD encryption (`2022-blake3`, `chacha20-poly1305`, `aes-gcm`). Minimal handshake overhead. | [`ss.txt`]({repo_url}/subs/protocols/ss.txt) | [`ss_base64.txt`]({repo_url}/subs/protocols/ss_base64.txt) | `{counts.get('subs/protocols/ss.txt', 0)}` |",
        f"| **Hysteria2** | UDP-based QUIC protocol with custom BBR congestion control. Bypasses TCP packet loss throttling on unstable cellular connections. | [`hysteria2.txt`]({repo_url}/subs/protocols/hysteria2.txt) | [`hysteria2_base64.txt`]({repo_url}/subs/protocols/hysteria2_base64.txt) | `{counts.get('subs/protocols/hysteria2.txt', 0)}` |",
        f"| **TUIC** | Modern QUIC implementation engineered for minimal RTT and 0-RTT handshakes over lossy cellular networks. | [`tuic.txt`]({repo_url}/subs/protocols/tuic.txt) | [`tuic_base64.txt`]({repo_url}/subs/protocols/tuic_base64.txt) | `{counts.get('subs/protocols/tuic.txt', 0)}` |",
        "",
        "### Top Geographic Channels (Latency & Geo-Targeting)",
        "",
        "Grouped automatically for localized services (Google Search region, European streaming, low-ping routing):",
        "",
        "| Country Code | Plain Subscription URL | Base64 Subscription URL | Active Nodes |",
        "|---|---|---|:---:|",
        countries_table,
        "",
        "---",
        "",
        "## 🔬 4. Under the Hood: System Architecture & How It Works",
        "",
        "```mermaid",
        "flowchart TD",
        '    subgraph Harvest["1. Source Ingestion & Discovery"]',
        '        SRC["60+ Curated Public Feeds\n(sources.json)"]',
        '        NIM["Daily 500-Request AI Crawler\n(moonshotai/kimi-k2.6 & Nemotron)"]',
        '        TG["Public Telegram Channels & Web Dorks"]',
        '        OCR["Multimodal Vision OCR\n(Extracts URIs from Screenshots/QRs)"]',
        '        DISC["Discovered Feeds\n(sources.discovered.json)"]',
        "    end",
        "",
        '    subgraph Core["2. Aggregation & Transformation Pipeline (src/)"]',
        '        FETCH["fetcher.py\nConcurrent HTTP & Async Ingestion"]',
        '        DEDUP["dedup.py\nDeep Semantic Fingerprint Deduplication"]',
        '        SYNTH["transform_pattn.py\nBypass Synthesis: F&F Fragment, IPv6-OOB, SNI-Spoof"]',
        '        HC["healthcheck.py\nDual-Stage Probe: Socket Handshake + Batched Xray"]',
        "    end",
        "",
        '    subgraph Dist["3. Multi-Format Distribution & Verification"]',
        '        PUB["subs/ & subs/categories/ & subs/protocols/\nPlain & Base64 Subscription Feeds"]',
        '        DOCS["Automated Docs Updates\n(README.md & STATUS.md)"]',
        '        IRAN["scripts/test_iran_reachability.py\nLocal In-Iran Batched Xray Probe -> local_subs/"]',
        '        PATTN_DB["scripts/sync_to_pattn.py\nDirect Sync to guiNDB.db SQLite Database"]',
        "    end",
        "",
        "    SRC --> FETCH",
        "    NIM --> TG --> OCR --> DISC --> FETCH",
        "    FETCH --> DEDUP --> SYNTH --> HC",
        "    HC --> PUB",
        "    HC --> DOCS",
        '    PUB -. "Run on Iranian ISP" .-> IRAN',
        "    IRAN --> PATTN_DB",
        '    PUB -. "Sync Hub Groups" .-> PATTN_DB',
        "```",
        "",
        "### How PattN Overcomes Modern DPI Censorship",
        "",
        "1. **Semantic Deduplication**: Upstream aggregators flood users with hundreds of duplicate links that have slightly different remark labels or URI-encoded query strings. PattN parses nodes into an abstract syntax model (`ProxyNode`), computes an invariant hash of `(protocol, host, port, path, transport, security, clean_credentials)`, and deduplicates over 90% of redundant entries while combining tags and preserving source provenance.",
        "2. **Fast & Furious TCP Fragmentation (`fm=1`)**: Iranian DPI middleboxes inspect the TLS ClientHello packet looking for blacklisted domain names in the SNI extension. PattN's variant generator injects TCP fragmentation parameters that split the ClientHello across 1-byte TCP window segments or custom byte offsets. Because the middlebox cannot reassemble the fragmented TCP stream in real-time, the SNI check is completely bypassed while the foreign endpoint reassembles the TCP packet flawlessly.",
        "3. **IPv6 Out-of-Band (`dialMode=oob.0`)**: In scenarios where IPv4 domestic routes are heavily choked or SNI-poisoned, PattN creates out-of-band routing variants that instruct the Xray core to dial the upstream target via IPv6 while maintaining the domestic IPv4 client tunnel.",
        "4. **Dual-Mode Multi-Round Health Checks**: Rather than trusting upstream tags or performing naive ping checks (which pass even when DPI actively resets TLS handshakes), PattN utilizes non-blocking socket handshakes in CI, combined with local batched Xray core real-traffic HTTP tests using multi-round survival intersection to eliminate transient packet loss.",
        "5. **Autonomous Daily Deep Discovery**: Powered by NVIDIA NIM frontier AI models (`moonshotai/kimi-k2.6`, `mistralai/mistral-large-4-1t-a52b`, and `nvidia/nemotron-3.5-super-vl-120b-a12b`), PattN executes an autonomous 500-request deep crawl daily at 02:00 UTC. It mines Telegram channel trees, scans GitHub repos, runs OCR extraction on proxy screenshot images, and records new high-quality feeds into persistent state.",
        "",
        "---",
        "",
        "## 📱 5. Complete Client Setup & Configuration Guides",
        "",
        "### PattN Desktop (Windows)",
        "",
        "PattN Desktop features seamless native integration with this repository via direct SQLite database synchronization.",
        "",
        "1. **Automated One-Click Database Sync**:",
        "   - Open PowerShell inside the repository directory.",
        "   - Run the synchronization script:",
        "     ```powershell",
        "     python scripts/sync_to_pattn.py --disable-redundant",
        "     ```",
        "   - This automatically creates a timestamped backup of your database (`guiNDB.db.bak.<timestamp>`), upserts all standardized PattN Hub groups, and disables obsolete feeds.",
        "2. **Manual Subscription Group Setup**:",
        "   - Open PattN Desktop -> Navigate to **Subscriptions** -> **Add Subscription Group**.",
        "   - **Remarks**: `⭐ PattN Hub - Verified`",
        f"   - **URL**: `{repo_url}/subs/verified.txt`",
        "   - Click **Save**, right-click the group, and select **Update Subscriptions**.",
        "3. **Recommended Settings**:",
        "   - **Core Type**: `Xray` (latest patterniha release).",
        "   - **Routing Mode**: `Bypass LAN and Mainland/Iran` (routes domestic `.ir` websites and banks direct, foreign traffic through proxy).",
        "   - **System Proxy Mode**: `Enable System Proxy` or `TUN Mode` for system-wide routing.",
        "",
        "---",
        "",
        "### v2rayN (Windows)",
        "",
        "1. Open **v2rayN** (v6+ recommended).",
        "2. Click **Subscription Group** (top toolbar) -> **Subscription Group Setting**.",
        "3. Click **Add** in the bottom-left corner:",
        "   - **Remarks**: `PattN Verified`",
        f"   - **Optional URL**: `{repo_url}/subs/verified.txt`",
        "   - **Auto-Update**: Check `Enabled` and set update interval to `360` minutes (6 hours).",
        "4. Click **Confirm**.",
        "5. Click **Subscription Group** -> **Update subscription without proxy**.",
        "6. In the bottom bar, set **Routing Rule** to `Bypass Iran and LAN` (`geoip:ir` and `geosite:ir` -> direct).",
        "7. Right-click any node in the list and select **Set as active server** (`Enter`), then toggle **Enable System Proxy** (`Ctrl + R`).",
        "",
        "---",
        "",
        "### v2rayNG (Android)",
        "",
        "1. Open **v2rayNG** on your Android device.",
        "2. Tap the **≡ (Menu)** icon in the top-left corner -> Select **Subscription group setting**.",
        "3. Tap the **+** button in the top-right corner:",
        "   - **Remarks**: `PattN Lite 500` (or `PattN Verified`)",
        f"   - **Subscription URL**: `{repo_url}/subs/lite500.txt`",
        "4. Tap the **✓ (Save)** icon in the top-right corner.",
        "5. Return to the main screen, tap the **⋮ (Three Dots)** in the top-right corner -> Select **Update subscription**.",
        "6. **Routing Configuration**:",
        "   - Go to **Settings** -> **Routing rules** -> Select `Bypass LAN and mainland`.",
        "   - Enable **Domain Strategy** -> Select `IPIfNonMatch`.",
        "   - Enable **Sniffing** to ensure correct domain routing.",
        "   - Enable **Per-App Proxy** if you only wish to route specific applications (e.g. YouTube, Instagram, Telegram).",
        "7. Tap the floating **V** button to connect.",
        "",
        "---",
        "",
        "### Clash Verge / Clash Meta (Windows, macOS, Linux)",
        "",
        "1. Open **Clash Verge Rev** or **Clash Meta**.",
        "2. Navigate to **Profiles** -> Paste the subscription URL into the **Profile URL** box:",
        f"   - URL: `{repo_url}/subs/verified.txt`",
        "   - *(Note: If using standard Clash, import through an online or local SubConverter service with target `clash`).*",
        "3. Alternatively, add PattN directly to your custom `config.yaml` as a proxy-provider:",
        "   ```yaml",
        "   proxy-providers:",
        "     pattn-verified:",
        "       type: http",
        f'       url: "{repo_url}/subs/verified.txt"',
        "       path: ./profiles/providers/pattn-verified.yaml",
        "       interval: 21600",
        "       health-check:",
        "         enable: true",
        "         url: http://cp.cloudflare.com/generate_204",
        "         interval: 300",
        "   ```",
        "4. Switch to **Rule Mode** and ensure rules include `GEOIP,IR,DIRECT` and `GEOSITE,category-ir,DIRECT`.",
        "",
        "---",
        "",
        "### sing-box / Nekobox (Android, iOS, Windows)",
        "",
        "1. Open **NekoBox** (or sing-box GUI client).",
        "2. Navigate to **Groups** -> Tap **+ (Add Group)**.",
        "3. **Group Name**: `PattN Special Bypass`",
        "4. **Group Type**: `Subscription`",
        f"5. **Subscription Link**: `{repo_url}/subs/categories/pattn-special.txt`",
        "6. Tap **Confirm** -> Long press the newly created group -> Select **Update**.",
        "7. In Settings, select **TUN Mode Implementation** -> `gVisor` (or `System`).",
        "8. Set **Route Mode** to `Bypass LAN and Iran`.",
        "",
        "---",
        "",
        "### Streisand / Shadowrocket / FoXray (iOS & macOS)",
        "",
        "1. Open **Shadowrocket** or **Streisand**.",
        "2. Tap the **+** icon in the top right:",
        "   - **Type**: `Subscribe`",
        f"   - **URL**: `{repo_url}/subs/lite500.txt`",
        "   - **Remark**: `PattN Lite`",
        "3. Tap **Save** -> The client will download and parse all nodes automatically.",
        "4. In **Global Routing**, select `Config` or `Rule` (ensuring default ruleset bypasses Iranian IP blocks).",
        "5. Tap the test button to ping all nodes, select the fastest response, and toggle the connection switch.",
        "",
        "---",
        "",
        "## 🇮🇷 6. Local In-Iran Verification Engine (`check_from_iran.ps1`)",
        "",
        "> [!IMPORTANT]",
        "> **Why Test Directly from Iran?**  ",
        "> GitHub Actions runners are hosted in international Azure/AWS cloud datacenters (US, UK, Germany) with unfiltered 10 Gbps uplinks.  ",
        "> A node that appears green and low-latency in GitHub Actions can be actively SNI-filtered or UDP-blackholed by Iranian DPI middleboxes (MCI, Irancell, Shatel).  ",
        "> PattN provides a local, real-traffic verification engine to test configs on your genuine domestic ISP connection.",
        "",
        "### How to Run In-Iran Verification",
        "",
        "1. **Disconnect your personal VPN / TUN proxy completely** so your connection exits directly via your Iranian ISP.",
        "2. Open a PowerShell prompt in the repository folder.",
        "3. Run the automated tester:",
        "   ```powershell",
        "   .\\scripts\\check_from_iran.ps1",
        "   ```",
        "   - *Optional flags*:",
        "     - `-MaxNodes 800`: Test up to 800 prioritized candidate configs.",
        "     - `-Rounds 3`: Enforce 3 consecutive successful probe rounds to eliminate false positives.",
        "     - `-FromGitHub`: Pull the latest raw subscriptions from GitHub before testing.",
        "     - `-NoDbSync`: Skip auto-syncing survivors to PattN desktop GUI database.",
        "4. **What the Script Does**:",
        "   - Verifies that no active VPN/proxy is masking your IP (safety guard).",
        "   - Automatically locates your local Xray core (`xray.exe`).",
        "   - Launches concurrent batches of outbounds, sending real HTTP probe traffic through each proxy node.",
        "   - Generates git-ignored, live-verified feeds at:",
        "     - `local_subs/iran_verified.txt`",
        "     - `local_subs/iran_verified_base64.txt`",
        "   - Automatically registers `🇮🇷 PattN Hub - Iran Live Verified` at the very top of your local PattN Desktop database (`guiNDB.db`).",
        "",
        "---",
        "",
        "## 🎯 7. Real-World Use Cases & Scenarios",
        "",
        "| Scenario / Use Case | Recommended Subscription | Recommended Protocol | Why This Combination Works |",
        "|---|---|---|---|",
        f"| **4K Video Streaming & Entertainment** (YouTube, Twitch, Netflix) | [`top100.txt`]({repo_url}/subs/top100.txt) | VLESS Reality / Trojan | Lowest latency with maximum sustained throughput and low jitter. |",
        f"| **Mobile Cellular on Strict DPI** (MCI / Irancell / Rightel) | [`pattn-special.txt`]({repo_url}/subs/categories/pattn-special.txt) | VLESS Fast & Furious Fragment (`fm=1`) | Splits TLS ClientHello across packets, evading carrier-level SNI keyword filters. |",
        f"| **Competitive Online Gaming & Discord Voice** | [`hysteria2.txt`]({repo_url}/subs/protocols/hysteria2.txt) or [`tuic.txt`]({repo_url}/subs/protocols/tuic.txt) | Hysteria2 / TUIC (QUIC UDP) | Eliminates TCP head-of-line blocking; custom congestion control maintains low ping during packet loss. |",
        f"| **Developer Tools & Package Managers** (`docker pull`, `pip`, `npm`, `cargo`, Hugging Face) | [`verified.txt`]({repo_url}/subs/verified.txt) | Cloudflare CDN WS/gRPC or Reality | Resilient Anycast routing with zero dropped TLS handshakes during large multi-GB layer downloads. |",
        f"| **Severe Censorship & National Intranet Conditions** (National Blackout / Melli Net) | [`whitelist.txt`]({repo_url}/subs/categories/whitelist.txt) & [`serverless-fragment.json`]({repo_url}/subs/categories/serverless-fragment.json) | Whitelist Clean IP / Fragment | Leverages domestic serverless edge addresses and white-listed port ranges to punch through severe firewalls. |",
        "",
        "---",
        "",
        "## 🛠️ 8. Potential Problems, Failure Modes & Troubleshooting (The Fix-It Guide)",
        "",
        '### 1. "All nodes show 9999ms or Timed Out in client"',
        "- **Root Cause A: Clock Drift**: Xray VMess and TLS protocols require system time to be accurate within 90 seconds. If your Windows or Android clock has drifted, all handshakes fail cryptographically.",
        "  - *Fix*: On Windows, open PowerShell as Admin and run: `w32tm /resync`. On Android, enable **Automatic Date & Time** in system settings.",
        "- **Root Cause B: Routing Loop**: Client is routing DNS queries or proxy tunnel traffic through the proxy itself before the connection is established.",
        "  - *Fix*: In v2rayN/v2rayNG, set DNS server to `1.1.1.1` or `8.8.8.8` and set Domain Strategy to `IPIfNonMatch`.",
        "",
        '### 2. "tls: handshake failure" or "remote error: tls: bad certificate" on Reality nodes',
        "- **Root Cause**: The destination camouflage domain configured in `sni` or `pbk` has been actively throttled or blocked by your Iranian ISP gateway.",
        "- *Fix*: Switch to `pattn-special.txt` or `iran-optimized.txt`. These feeds synthesize multiple SNI alternatives and TCP fragmentations to prevent ISP SNI blocking.",
        "",
        '### 3. "Cloudflare Error 1000 / 1015 / 1020 on CDN nodes"',
        "- **Root Cause**: The Cloudflare edge IP is rate-limited or blocked by Cloudflare anti-bot WAF rules.",
        "- *Fix*: Replace the host IP address in your client with a known Clean Cloudflare IP (e.g. `104.16.0.0/12` or `188.114.96.0/20` ranges) while preserving the original `host` header and `path` parameters.",
        "",
        '### 4. "Config works on Shatel/Mokhaberat Wi-Fi but fails completely on MCI or Irancell mobile data"',
        "- **Root Cause**: Iranian mobile operators (MCI, Irancell) enforce aggressive UDP traffic throttling (breaking standard QUIC/Hysteria) and deeper TCP window inspection compared to fixed-line ADSL/VDSL providers.",
        "- *Fix*: On cellular connections, prefer **VLESS Fast & Furious Fragment** (`pattn-special.txt`) over port 443 TCP rather than UDP protocols.",
        "",
        '### 5. "Domestic Iranian Apps (Banking, Snapp, Digikala) won\'t load or block login"',
        "- **Root Cause**: Your client is routing Iranian traffic through the foreign proxy tunnel. Iranian banking gateways block connections coming from foreign data centers.",
        "- *Fix*: Ensure your client routing rule is set to **Bypass LAN and Iran** (`geoip:ir` and `geosite:ir` configured as `direct`).",
        "",
        '### 6. "guiNDB.db is locked" or SQLite Database Error in PattN',
        "- **Root Cause**: Another instance of PattN or a background sync script holds an open write transaction on the database.",
        "- *Fix*: Close PattN Desktop before running `python scripts/sync_to_pattn.py`, or ensure `scripts/sync_to_pattn.py` has completed its automated backup.",
        "",
        "---",
        "",
        "## ⏰ 9. Automation Cadence & Repository Workflows",
        "",
        "PattN operates on a high-reliability, two-tier automation architecture:",
        "",
        "1. **6-Hour Fast Scheduled Refresh (`.github/workflows/update-subs.yml`)**:",
        "   - **Trigger**: Every 6 hours (`0 */6 * * *`).",
        "   - **Action**: Concurrently aggregates all 60+ upstream feeds, runs deep semantic deduplication, synthesizes PattN bypass variants, performs socket & Xray health testing, and commits refreshed subscription files and documentation.",
        "   - **Design**: Fast and lean — operates without heavy AI inference overhead to guarantee 100% reliability.",
        "2. **Daily Extended AI Discovery (`.github/workflows/daily-deep-discovery.yml`)**:",
        "   - **Trigger**: Daily at 02:00 UTC (`0 2 * * *`).",
        "   - **Action**: Runs the autonomous frontier AI discovery engine powered by `moonshotai/kimi-k2.6` (with automatic failover to `mistralai/mistral-large` and `nvidia/nemotron-3.5-super-vl`).",
        "   - **Capacity**: Executes up to **500 API requests**, discovers dozens of candidate Telegram channels and GitHub repos, runs OCR extraction on proxy screenshot images, filters dead channels, and records persistent state into `sources.discovered.json` and `data/discovery_state.json`.",
        "3. **Continuous Integration & Security Gate (`.github/workflows/ci.yml`)**:",
        "   - **Trigger**: Every git push and pull request.",
        "   - **Action**: Executes Python byte-compilation across Python `3.10` and `3.12`, runs the complete 65-test unit & integration test suite, and runs a zero-tolerance static secret scan.",
        "",
        "---",
        "",
        "## 💻 10. CLI Reference & Developer Setup",
        "",
        "### Developer Setup",
        "",
        "```bash",
        "# 1. Clone the repository",
        f"git clone https://github.com/{repo_slug}.git",
        "cd pattn",
        "",
        "# 2. Configure local environment (optional for AI discovery)",
        "cp .env.example .env",
        "# Add NVIDIA_API_KEY in .env if you wish to run local AI discovery",
        "",
        "# 3. Run full unit and integration test suite",
        "python -m compileall -q src scripts tests",
        "python -m unittest discover -s tests -v",
        "```",
        "",
        "### CLI Command Matrix",
        "",
        "| Task / Operation | CLI Command | Description |",
        "|---|---|---|",
        "| **Full Aggregation Pipeline** | `python -m src.main` | Runs full aggregation with socket health checking, PattN variants, and docs generation. |",
        "| **Fast Aggregation (No Probes)** | `python -m src.main --no-healthcheck` | Aggregates and categorizes all sources instantly without socket wait times. |",
        "| **Daily Extended AI Discovery** | `python -m src.main --discover-only --discovery-api-requests 500 --discovery-time 5400` | Runs deep 500-request AI discovery crawl for up to 90 minutes. |",
        "| **Quick AI Discovery Cycle** | `python -m src.main --discover-only --discovery-time 360` | Runs a 6-minute targeted Telegram/Web crawl and updates `sources.discovered.json`. |",
        "| **In-Iran Xray Reachability Test** | `.\\scripts\\check_from_iran.ps1` | Runs local batched Xray probe through Iranian ISP and outputs to `local_subs/`. |",
        "| **Sync to PattN Desktop DB** | `python scripts/sync_to_pattn.py --disable-redundant` | Backs up and syncs PattN Hub subscription groups into `guiNDB.db`. |",
        "| **Manual Actions Workflow Trigger** | `.\\scripts\\trigger_update.ps1 -Local -SyncPattnDb` | Runs the full pipeline locally and syncs to PattN DB in a single command. |",
        "",
        "---",
        "",
        "## 🔒 11. Security, Privacy & Integrity Guarantees",
        "",
        "- **Zero Leaks**: All private configurations, personal tokens, and API credentials are kept strictly out of git-tracked files. Local verification outputs are restricted to git-ignored `local_subs/`.",
        "- **CI Secret Scanning**: Every pull request and push is checked by an automated static secret scanner in `.github/workflows/ci.yml` that blocks API key patterns and untracked file exposure.",
        "- **Deterministic Reproducibility**: Pure Python core logic without external runtime dependencies (requests, beautifulsoup, or heavy web frameworks) — runs on vanilla Python 3.10+ standard library.",
        "",
        "---",
        "",
        "## 📄 12. License & Acknowledgments",
        "",
        "- **License**: Released under the permissive [MIT License](LICENSE).",
        "- **Upstream Cores**: Powered by [patterniha/Xray-core](https://github.com/patterniha/Xray-core) and the global anti-censorship open-source community.",
        "- **AI Models**: Frontier reasoning and multimodal vision powered by [NVIDIA NIM](https://build.nvidia.com) (`moonshotai/kimi-k2.6`, `mistralai/mistral-large`, and `nvidia/nemotron-3.5-super-vl`).",
        "",
        "---",
        "",
        "*Maintained with ❤️ by the PattN Engineering Community. Star ⭐ the repository to support unrestricted, open internet access.*",
    ]

    readme_path.write_text("\n".join(lines), encoding="utf-8")
