# PattN Architecture Overview

PattN combines automated multi-source aggregation, AI-driven feed discovery, semantic deduplication, DPI bypass variant synthesis, strict zero-leak privacy isolation, and real-traffic local ISP verification.

## High-Level Data Flow

```mermaid
flowchart LR
    subgraph Discovery["1. Autonomous Discovery (src/discovery/)"]
        NIM["NVIDIA NIM Frontier AI\n(Kimi K2.6 + Fallback Chain)"]
        SCRAPE["Telegram / DuckDuckGo /\nGitHub Repo & README Scrapers"]
        STATE["data/discovery_state.json\n(Queue, Visited URLs, Deepest Post IDs)"]
        NIM <--> SCRAPE
        SCRAPE --> STATE
        SCRAPE --> DISC["sources.discovered.json\n+ DISCOVERY.md"]
    end

    subgraph Pipeline["2. Core Aggregation Pipeline (src/)"]
        PUB["sources.json\n(60 Curated Public Sources)"]
        PRIV["sources.private.json / PRIVATE_SOURCES\n(Git-Ignored Private BPB Links)"]
        FETCH["fetcher.py\n(Concurrent Base64/URI/HTML/JSON)"]
        DEDUP["dedup.py\n(Semantic Fingerprint Merging)"]
        VAR["transform_pattn.py\n(F&F / IPv6-OOB / SNI-Spoof)"]
        HC["healthcheck.py\n(TCP+TLS & Batched Xray HTTP Probing)"]
    end

    subgraph Outputs["3. Outputs & Local Verification"]
        SUBS["subs/\n(Public Plain & Base64 Feeds + Docs)"]
        PSUBS["private_subs/ & Secret Gist\n(Strict is_private Isolation)"]
        IRAN["scripts/test_iran_reachability.py\n(Local In-Iran Batched Xray Check)"]
    end

    PUB --> FETCH
    DISC --> FETCH
    PRIV -. "--include-private / --private-only" .-> FETCH
    FETCH --> DEDUP --> VAR --> HC
    HC -->|"is_private == False"| SUBS
    HC -->|"is_private == True + Verified"| PSUBS
    SUBS -->|"Disconnect VPN & Test Locally"| IRAN
```

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `src/models.py` | Data structures (`ProxyNode`, `SourceConfig`, `SourceStats`), semantic fingerprinting, and formatted remarks. |
| `src/parsers.py` | Multi-format parsers (`vless`, `vmess`, `trojan`, `ss`, `hy2`, `tuic`, Base64, Telegram `/s/` HTML, Xray JSON). |
| `src/fetcher.py` | Concurrent fetching merging `sources.json`, `sources.discovered.json`, and optional `is_private` feeds. |
| `src/dedup.py` | Merges duplicate endpoints across providers while unioning tags, `source_ids`, and privacy flags. |
| `src/transform_pattn.py` | Generates PattN bypass variants (`F&F`, `IPv6-OOB`, `SNI-Spoof`). |
| `src/healthcheck.py` | Prioritized socket verification plus batched multi-round HTTP-proxied Xray probing (`blackhole` fallback guard). |
| `src/categorizer.py` | Strictly isolates `not n.is_private` nodes, emits categorized `subs/` feeds, and updates `README.md` / `STATUS.md`. |
| `src/private_gist.py` | Generates `private_subs/` (`bpb_only`, `private_verified`, `private_all`) and uploads to Secret Gist (`::add-mask::`). |
| `src/discovery/` | Autonomous search & Telegram/GitHub/OCR discovery loop backed by `data/discovery_state.json`. |
| `scripts/test_iran_reachability.py` | Local Windows verifier using PattN's `xray.exe` to probe configs directly through your Iranian ISP. |
| `scripts/sync_to_pattn.py` | Safely backs up and upserts PattN Hub subscriptions into `guiNDB.db` while protecting private BPB rows. |

## CI Automation Cadence

- **`.github/workflows/ci.yml`**: Runs Python compilation checks (`compileall`), unit/integration tests across Python 3.10 & 3.12, and secret scans on every push & PR.
- **`.github/workflows/update-subs.yml`**: Runs every 6 hours (`0 */6 * * *`) to discover new feeds, aggregate public nodes, update `subs/` & docs, audit privacy, and sync Secret Gist.
- **`.github/workflows/daily-deep-discovery.yml`**: Runs daily (`0 2 * * *`) for an extended 500-request frontier AI crawl, serialized via `concurrency: pattn-git-writer`.
