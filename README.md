# 🌐 PattN - High-Speed Proxy Aggregator & Bypass Subscription Pipeline

[![CI Tests](https://github.com/mani-moh/pattn/actions/workflows/ci.yml/badge.svg)](https://github.com/mani-moh/pattn/actions/workflows/ci.yml) [![Update Subs](https://github.com/mani-moh/pattn/actions/workflows/update-subs.yml/badge.svg)](https://github.com/mani-moh/pattn/actions/workflows/update-subs.yml) [![Daily Discovery](https://github.com/mani-moh/pattn/actions/workflows/daily-deep-discovery.yml/badge.svg)](https://github.com/mani-moh/pattn/actions/workflows/daily-deep-discovery.yml) ![Supported Protocols](https://img.shields.io/badge/Protocols-VLESS%20%7C%20VMess%20%7C%20Trojan%20%7C%20SS%20%7C%20Hy2%20%7C%20TUIC-brightgreen.svg) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Automated high-speed proxy subscription aggregator and Iranian ISP bypass engine. PattN aggregates 60+ verified upstream sources, performs deep semantic deduplication, synthesizes specialized PattN fragment and out-of-band bypass variants, runs live TCP/TLS health checks, and categorizes subscriptions for seamless multi-platform import.

**Last Sync**: `2026-11-10 06:56:15 UTC` | **Total Public Nodes**: `62258` | **Verified Healthy**: `918` | **PattN Bypass Variants**: `1361`

---

## 🚀 Core Subscriptions

Import these primary subscriptions directly into PattN, v2rayN, v2rayNG, Clash Meta, sing-box, or Nekobox:

| Feed | Description | Plain Subscription URL | Base64 Subscription URL | Nodes |
|---|---|---|---|:---:|
| **⭐ Verified** | Tested & low-latency configs | [`verified.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/verified.txt) | [`verified_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/verified_base64.txt) | `918` |
| **⚡ Top 100** | 100 lowest latency nodes | [`top100.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/top100.txt) | [`top100_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/top100_base64.txt) | `100` |
| **⚖️ Lite 500** | Balanced mix across protocols | [`lite500.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/lite500.txt) | [`lite500_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/lite500_base64.txt) | `500` |
| **📦 All Configs** | Complete deduplicated public feed | [`all.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/all.txt) | [`all_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/all_base64.txt) | `62258` |

---

## 🛡️ Specialized Bypass Channels

Optimized for restrictive network environments, DPI evasion, and censorship circumvention:

| Category | Features | Plain URL | Base64 URL | Nodes |
|---|---|---|---|:---:|
| **🔥 PattN Special** | F&F fragment (`fm`), IPv6 dial (`dialMode=oob.0`), custom ciphers | [`pattn-special.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/pattn-special.txt) | [`pattn-special_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/pattn-special_base64.txt) | `1361` |
| **🇮🇷 Iran Optimized** | Domestic ISP route optimization | [`iran-optimized.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/iran-optimized.txt) | [`iran-optimized_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/iran-optimized_base64.txt) | `21077` |
| **🕶️ Reality** | VLESS-Reality direct SNI camouflage | [`reality.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/reality.txt) | [`reality_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/reality_base64.txt) | `7652` |
| **☁️ CDN WS/gRPC** | Cloudflare WebSocket & gRPC fronted | [`cdn-ws-grpc.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/cdn-ws-grpc.txt) | [`cdn-ws-grpc_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/cdn-ws-grpc_base64.txt) | `40085` |
| **🎯 ConfigsHUB** | Curated daily channel configs | [`confighub.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/confighub.txt) | [`confighub_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/confighub_base64.txt) | `8372` |
| **⚡ Whitelist** | Clean IP / Whitelist bypass configs | [`whitelist.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/whitelist.txt) | [`whitelist_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/whitelist_base64.txt) | `616` |
| **🧩 Serverless JSON** | Xray JSON outbounds array | [`serverless-fragment.json`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/categories/serverless-fragment.json) | - | `3497` |

---

## 📡 Protocols

| Protocol | Plain Subscription URL | Base64 Subscription URL | Nodes |
|---|---|---|:---:|
| **VLESS** | [`vless.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/vless.txt) | [`vless_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/vless_base64.txt) | `41900` |
| **VMess** | [`vmess.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/vmess.txt) | [`vmess_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/vmess_base64.txt) | `8706` |
| **Trojan** | [`trojan.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/trojan.txt) | [`trojan_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/trojan_base64.txt) | `6444` |
| **Shadowsocks** | [`ss.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/ss.txt) | [`ss_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/ss_base64.txt) | `4273` |
| **Hysteria2** | [`hysteria2.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/hysteria2.txt) | [`hysteria2_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/hysteria2_base64.txt) | `568` |
| **TUIC** | [`tuic.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/tuic.txt) | [`tuic_base64.txt`](https://raw.githubusercontent.com/mani-moh/pattn/master/subs/protocols/tuic_base64.txt) | `8` |

---

## 🛰️ Autonomous Discovery Agent (NVIDIA NIM Frontier AI)

Before every 6-hour refresh (plus a daily 500-request extended deep scan), PattN runs an autonomous discovery agent powered by `moonshotai/kimi-k2.6` (with automatic failover to `mistralai/mistral-large-4-1t-a52b`, `nvidia/nemotron-3-ultra-550b-a55b`, and `nvidia/nemotron-3.5-super-vl-120b-a12b`):
- **Web, GitHub & Telegram Exploration**: Synthesizes search dorks via Nemotron reasoning, mines public Telegram previews (`t.me/s/...`), follows cross-promoted sister channels, and harvests GitHub subscription repositories.
- **Multimodal Vision OCR**: Extracts proxy URIs directly from configuration screenshots and QR code images posted in Telegram channels.
- **Persistent Memory**: Builds upon previous runs via [`data/discovery_state.json`](data/discovery_state.json) and outputs validated feeds into [`sources.discovered.json`](sources.discovered.json), automatically merged into every aggregation.
- **Secure API Key Setup**: Set `NVIDIA_API_KEY` in GitHub Repository Secrets (for Actions) or in your local git-ignored `.env` file.
- Track live discovery metrics and top channels in [**DISCOVERY.md**](DISCOVERY.md).

---

## 🛠️ CLI Usage

```bash
# Run autonomous discovery agent before full aggregation
python -m src.main --discover

# Run ONLY discovery cycle and update sources.discovered.json
python -m src.main --discover-only --discovery-time 360

# Run full pipeline with healthcheck and PattN variants
python -m src.main

# Fast run without healthcheck
python -m src.main --no-healthcheck

```

See [STATUS.md](STATUS.md) for upstream source diagnostics, [DISCOVERY.md](DISCOVERY.md) for live autonomous discovery stats, [ARCHITECTURE.md](docs/ARCHITECTURE.md) for system design, and [CONTRIBUTING.md](CONTRIBUTING.md) for contributor guidelines.