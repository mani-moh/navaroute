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
        PUB["sources.json\n(Curated Public Sources)"]
        FETCH["fetcher.py\n(Concurrent Base64/URI/HTML/JSON)"]
        DEDUP["dedup.py\n(Semantic Fingerprint Merging)"]
        VAR["transform_pattn.py\n(F&F / IPv6-OOB / SNI-Spoof)"]
        HC["healthcheck.py\n(TCP+TLS & Batched Xray HTTP Probing)"]
    end

    subgraph Outputs["3. Outputs & Local Verification"]
        SUBS["subs/\n(Public Plain & Base64 Feeds + Docs)"]
        IRAN["scripts/test_iran_reachability.py\n(Local In-Iran Batched Xray Check -> local_subs/)"]
    end

    PUB --> FETCH
    DISC --> FETCH
    FETCH --> DEDUP --> VAR --> HC
    HC --> SUBS
    SUBS -->|"Disconnect VPN & Test Locally"| IRAN
```

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `src/models.py` | Data structures (`ProxyNode`, `SourceConfig`, `SourceStats`), semantic fingerprinting, and formatted remarks. |
| `src/parsers.py` | Multi-format parsers (`vless`, `vmess`, `trojan`, `ss`, `hy2`, `tuic`, Base64, Telegram `/s/` HTML, Xray JSON). |
| `src/fetcher.py` | Concurrent fetching merging `sources.json` and `sources.discovered.json`. |
| `src/dedup.py` | Merges duplicate endpoints across providers while unioning tags and `source_ids`. |
| `src/transform_pattn.py` | Generates PattN bypass variants (`F&F`, `IPv6-OOB`, `SNI-Spoof`). |
| `src/healthcheck.py` | Prioritized socket verification plus batched multi-round HTTP-proxied Xray probing (`blackhole` fallback guard). |
| `src/categorizer.py` | Emits categorized `subs/` feeds and updates `README.md` / `STATUS.md`. |
| `src/discovery/` | Autonomous search & Telegram/GitHub/OCR discovery loop backed by `data/discovery_state.json`. |
| `scripts/test_iran_reachability.py` | Local Windows verifier using PattN's `xray.exe` to probe configs directly through your Iranian ISP (`local_subs/`). |
| `scripts/sync_to_pattn.py` | Safely backs up and upserts PattN Hub subscriptions into `guiNDB.db`. |

## CI Automation Cadence

- **`.github/workflows/ci.yml`**: Runs Python compilation checks (`compileall`), unit/integration tests across Python 3.10 & 3.12, and secret scans on every push & PR.
- **`.github/workflows/update-subs.yml`**: Runs every 6 hours (`0 */6 * * *`) for fast scheduled source aggregation, multi-platform categorization, docs update, and security checks (without AI discovery overhead).
- **`.github/workflows/daily-deep-discovery.yml`**: Runs daily (`0 2 * * *`) for an autonomous 500-request frontier AI deep crawl exploring new channels and configs, serialized via `concurrency: pattn-git-writer`.
