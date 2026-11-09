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
    Strictly excludes any node where is_private == True.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    # SAFETY GUARD: strictly filter out all private nodes from public output!
    public_nodes = [n for n in nodes if not n.is_private]

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
    pattn_special_nodes = [n for n in public_nodes if n.is_pattn_special()]
    _write_sub_pair(
        categories_dir / "pattn-special.txt",
        "PattN Special Bypass",
        pattn_special_nodes,
        counts,
    )

    # Iran Optimized
    iran_nodes = [n for n in public_nodes if n.is_iran_optimized()]
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
    cdn_nodes = [n for n in public_nodes if n.is_cdn_fronted()]
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

    # Serverless Fragment JSON (filtered to public-only)
    public_json_configs = [
        {k: v for k, v in j.items() if k != "_is_private"}
        for j in json_configs
        if not j.get("_is_private")
    ]
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
        public_stats = [s for s in stats if not getattr(s, "is_private", False)]
        _update_status_doc(base_dir / "STATUS.md", public_stats, public_nodes)
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


def _parse_repo_slug_from_url(url: str) -> Optional[str]:
    url = url.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url, re.IGNORECASE)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def detect_repo_slug(default: str = "mani-moh/pattn") -> str:
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
    total_nodes = len(public_nodes)
    healthy_nodes = sum(1 for n in public_nodes if n.passed_healthcheck)
    pattn_special = sum(1 for n in public_nodes if n.is_pattn_special())

    repo_slug = detect_repo_slug()
    branch = _detect_current_branch()
    repo_url = f"https://raw.githubusercontent.com/{repo_slug}/{branch}"

    lines = [
        "# 🌐 PattN - High-Speed Proxy Aggregator & Bypass Subscription Pipeline",
        "",
        "![Update Interval](https://img.shields.io/badge/Update%20Interval-6%20Hours-blue.svg) "
        "![Supported Protocols](https://img.shields.io/badge/Protocols-VLESS%20%7C%20VMess%20%7C%20Trojan%20%7C%20SS%20%7C%20Hy2%20%7C%20TUIC-brightgreen.svg) "
        "![Safety Guard](https://img.shields.io/badge/Private%20Gist-Zero%20Leaks-orange.svg)",
        "",
        "> Automated high-speed proxy subscription aggregator and Iranian ISP bypass engine. "
        "PattN aggregates 60+ verified upstream sources, performs deep semantic deduplication, "
        "synthesizes specialized PattN fragment and out-of-band bypass variants, runs live "
        "TCP/TLS health checks, and categorizes subscriptions for seamless multi-platform import.",
        "",
        f"**Last Sync**: `{now_utc}` | **Total Public Nodes**: `{total_nodes}` | **Verified Healthy**: `{healthy_nodes}` | **PattN Bypass Variants**: `{pattn_special}`",
        "",
        "---",
        "",
        "## 🚀 Core Subscriptions",
        "",
        "Import these primary subscriptions directly into PattN, v2rayN, v2rayNG, Clash Meta, sing-box, or Nekobox:",
        "",
        "| Feed | Description | Plain Subscription URL | Base64 Subscription URL | Nodes |",
        "|---|---|---|---|:---:|",
        f"| **⭐ Verified** | Tested & low-latency configs | [`verified.txt`]({repo_url}/subs/verified.txt) | [`verified_base64.txt`]({repo_url}/subs/verified_base64.txt) | `{counts.get('subs/verified.txt', 0)}` |",
        f"| **⚡ Top 100** | 100 lowest latency nodes | [`top100.txt`]({repo_url}/subs/top100.txt) | [`top100_base64.txt`]({repo_url}/subs/top100_base64.txt) | `{counts.get('subs/top100.txt', 0)}` |",
        f"| **⚖️ Lite 500** | Balanced mix across protocols | [`lite500.txt`]({repo_url}/subs/lite500.txt) | [`lite500_base64.txt`]({repo_url}/subs/lite500_base64.txt) | `{counts.get('subs/lite500.txt', 0)}` |",
        f"| **📦 All Configs** | Complete deduplicated public feed | [`all.txt`]({repo_url}/subs/all.txt) | [`all_base64.txt`]({repo_url}/subs/all_base64.txt) | `{counts.get('subs/all.txt', 0)}` |",
        "",
        "---",
        "",
        "## 🛡️ Specialized Bypass Channels",
        "",
        "Optimized for restrictive network environments, DPI evasion, and censorship circumvention:",
        "",
        "| Category | Features | Plain URL | Base64 URL | Nodes |",
        "|---|---|---|---|:---:|",
        f"| **🔥 PattN Special** | F&F fragment (`fm`), IPv6 dial (`dialMode=oob.0`), custom ciphers | [`pattn-special.txt`]({repo_url}/subs/categories/pattn-special.txt) | [`pattn-special_base64.txt`]({repo_url}/subs/categories/pattn-special_base64.txt) | `{counts.get('subs/categories/pattn-special.txt', 0)}` |",
        f"| **🇮🇷 Iran Optimized** | Domestic ISP route optimization | [`iran-optimized.txt`]({repo_url}/subs/categories/iran-optimized.txt) | [`iran-optimized_base64.txt`]({repo_url}/subs/categories/iran-optimized_base64.txt) | `{counts.get('subs/categories/iran-optimized.txt', 0)}` |",
        f"| **🕶️ Reality** | VLESS-Reality direct SNI camouflage | [`reality.txt`]({repo_url}/subs/categories/reality.txt) | [`reality_base64.txt`]({repo_url}/subs/categories/reality_base64.txt) | `{counts.get('subs/categories/reality.txt', 0)}` |",
        f"| **☁️ CDN WS/gRPC** | Cloudflare WebSocket & gRPC fronted | [`cdn-ws-grpc.txt`]({repo_url}/subs/categories/cdn-ws-grpc.txt) | [`cdn-ws-grpc_base64.txt`]({repo_url}/subs/categories/cdn-ws-grpc_base64.txt) | `{counts.get('subs/categories/cdn-ws-grpc.txt', 0)}` |",
        f"| **🎯 ConfigsHUB** | Curated daily channel configs | [`confighub.txt`]({repo_url}/subs/categories/confighub.txt) | [`confighub_base64.txt`]({repo_url}/subs/categories/confighub_base64.txt) | `{counts.get('subs/categories/confighub.txt', 0)}` |",
        f"| **⚡ Whitelist** | Clean IP / Whitelist bypass configs | [`whitelist.txt`]({repo_url}/subs/categories/whitelist.txt) | [`whitelist_base64.txt`]({repo_url}/subs/categories/whitelist_base64.txt) | `{counts.get('subs/categories/whitelist.txt', 0)}` |",
        f"| **🧩 Serverless JSON** | Xray JSON outbounds array | [`serverless-fragment.json`]({repo_url}/subs/categories/serverless-fragment.json) | - | `{counts.get('subs/categories/serverless-fragment.json', 0)}` |",
        "",
        "---",
        "",
        "## 📡 Protocols",
        "",
        "| Protocol | Plain Subscription URL | Base64 Subscription URL | Nodes |",
        "|---|---|---|:---:|",
        f"| **VLESS** | [`vless.txt`]({repo_url}/subs/protocols/vless.txt) | [`vless_base64.txt`]({repo_url}/subs/protocols/vless_base64.txt) | `{counts.get('subs/protocols/vless.txt', 0)}` |",
        f"| **VMess** | [`vmess.txt`]({repo_url}/subs/protocols/vmess.txt) | [`vmess_base64.txt`]({repo_url}/subs/protocols/vmess_base64.txt) | `{counts.get('subs/protocols/vmess.txt', 0)}` |",
        f"| **Trojan** | [`trojan.txt`]({repo_url}/subs/protocols/trojan.txt) | [`trojan_base64.txt`]({repo_url}/subs/protocols/trojan_base64.txt) | `{counts.get('subs/protocols/trojan.txt', 0)}` |",
        f"| **Shadowsocks** | [`ss.txt`]({repo_url}/subs/protocols/ss.txt) | [`ss_base64.txt`]({repo_url}/subs/protocols/ss_base64.txt) | `{counts.get('subs/protocols/ss.txt', 0)}` |",
        f"| **Hysteria2** | [`hysteria2.txt`]({repo_url}/subs/protocols/hysteria2.txt) | [`hysteria2_base64.txt`]({repo_url}/subs/protocols/hysteria2_base64.txt) | `{counts.get('subs/protocols/hysteria2.txt', 0)}` |",
        f"| **TUIC** | [`tuic.txt`]({repo_url}/subs/protocols/tuic.txt) | [`tuic_base64.txt`]({repo_url}/subs/protocols/tuic_base64.txt) | `{counts.get('subs/protocols/tuic.txt', 0)}` |",
        "",
        "---",
        "",
        "## 🔒 Private BPB Subscriptions (Zero-Leak Architecture)",
        "",
        "PattN features strict privacy isolation for private Cloudflare Workers / BPB endpoints:",
        "1. Store private subscription URLs locally in `sources.private.json` (git-ignored) or as a GitHub Actions secret `PRIVATE_SOURCES`.",
        "2. Private nodes are marked with `is_private=True` and **never written to any public subscription file**.",
        "3. Optional Secret Gist upload publishes private combined feeds (`bpb_only.txt`, `private_verified.txt`) directly to your private GitHub Gist, providing raw URLs accessible on all your devices.",
        "",
        "---",
        "",
        "## 🛠️ CLI Usage",
        "",
        "```bash",
        "# Run full pipeline with healthcheck and PattN variants",
        "python -m src.main",
        "",
        "# Fast run without healthcheck",
        "python -m src.main --no-healthcheck",
        "",
        "# Fast private sync to Secret Gist (uses existing subs/ without re-fetching or re-probing)",
        "python -m src.main --private-only",
        "",
        "# Include private BPB sources and publish to private Gist",
        "python -m src.main --include-private --gist-token $GIST_TOKEN --gist-id $GIST_ID",
        "```",
        "",
        "Detailed upstream source diagnostics and latency metrics are maintained in [STATUS.md](STATUS.md).",
    ]

    readme_path.write_text("\n".join(lines), encoding="utf-8")
