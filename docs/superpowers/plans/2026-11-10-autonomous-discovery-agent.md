# Autonomous Subscription Discovery Agent (Enhanced) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a next-generation autonomous discovery system powered by NVIDIA's `nvidia/nemotron-3.5-super-vl-120b-a12b` that continuously harvests proxy configs and subscription links from the web, GitHub, and Telegram channels. Incorporates deep Telegram history pagination, channel bio/pinned link extraction, Nemotron vision-language OCR for configuration screenshots/QR images, an organic viral channel graph, and an automated source reputation leaderboard.

**Architecture:**
- **NVIDIA Nemotron Client (`src/discovery/nemotron_client.py`)**: Dual-mode client supporting both text reasoning (search query synthesis & candidate evaluation) and vision analysis (multimodal image decoding of Telegram QR codes and config screenshots) targeting `https://integrate.api.nvidia.com/v1/chat/completions`.
- **Deep Scraper Suite (`src/discovery/search_engine.py`)**:
  - *Telegram Deep Miner*: Scrapes channel bios, pinned posts, message history pagination (`?before=...`), and extracts forwarded links / `@mention` channels.
  - *Subscription Link Harvester*: Detects external subscription URLs (GitHub Gists, raw repos, dynamic sub endpoints) and tests their payloads.
  - *Web/GitHub Dork Engine*: Executes Nemotron-crafted targeted search queries on DuckDuckGo and GitHub.
- **Viral Channel Network & Reputation State (`src/discovery/state_manager.py`)**:
  - Maintains `data/discovery_state.json`: Tracks channel graph, visit history, queued candidates, and reliability ratings (yield count, healthcheck pass rate).
  - Generates `DISCOVERY.md`: A public, high-visibility dashboard with discovery statistics, top-performing channels, and node yield charts.
- **Orchestrator (`src/discovery/agent.py`)**: Runs bounded multi-round discovery loops, validates discovered sources, saves active verified feeds into `sources.discovered.json`, and seamlessly merges them into the PattN aggregation pipeline.

**Tech Stack:** Python 3.10+, NVIDIA NIM REST API (`nvidia/nemotron-3.5-super-vl-120b-a12b`), GitHub Actions, unittest.

---

## Global Constraints

- Zero heavy binary dependencies (pure Python standard library: `urllib`, `json`, `re`, `html`, `base64`).
- Strictly mask `NVIDIA_API_KEY` via `::add-mask::` in CI and audit against leakages.
- Safe execution timeout budget: discovery loop gracefully winds down within allocated time (e.g. 5–10 mins in CI) and saves partial progress without failing.
- Discovered sources isolated in `sources.discovered.json` without modifying hand-curated `sources.json`.
- 100% test coverage for newly added components.

---

## File Structure

```
pattn/
├── data/
│   └── discovery_state.json        # Persistent discovery state (graph, queue, ratings)
├── DISCOVERY.md                    # Auto-generated discovery leaderboard & report
├── sources.discovered.json         # Auto-generated verified sources list
├── src/
│   ├── discovery/
│   │   ├── __init__.py
│   │   ├── nemotron_client.py      # NVIDIA NIM API (Text + Vision for OCR)
│   │   ├── search_engine.py        # Web dorking, Telegram deep mining & bio/pinned scraper
│   │   ├── state_manager.py        # State persistence, channel graph & leaderboard generator
│   │   └── agent.py                # Autonomous discovery loop & probe orchestrator
│   ├── fetcher.py                  # Auto-merging sources.discovered.json
│   └── main.py                     # CLI args (--discover, --discover-only, --discovery-time)
├── tests/
│   └── test_discovery.py           # Comprehensive unit & integration tests
└── .github/workflows/
    └── update-subs.yml             # Discovery step & leaderboard publisher
```

---

## Tasks

### Task 1: Dual-Mode Nemotron Client (Text Reasoning & Vision OCR) & State Manager
**Files:**
- `src/discovery/__init__.py`
- `src/discovery/nemotron_client.py`
- `src/discovery/state_manager.py`
- `tests/test_discovery.py`

- [ ] **Step 1.1**: Write tests in `tests/test_discovery.py` for `NemotronClient`: text completion, structured JSON extraction, multimodal image payload handling (for QR / screenshot parsing), retry backoff, and missing-key fallback.
- [ ] **Step 1.2**: Write tests for `DiscoveryStateManager`: loading/saving `data/discovery_state.json`, queue prioritization based on cross-mentions, recording source performance scores, and generating `DISCOVERY.md`.
- [ ] **Step 1.3**: Implement `NemotronClient` in `src/discovery/nemotron_client.py`.
- [ ] **Step 1.4**: Implement `DiscoveryStateManager` in `src/discovery/state_manager.py`.
- [ ] **Step 1.5**: Run unit tests and confirm 100% pass.

---

### Task 2: Deep Telegram Mining & Web Search Engine
**Files:**
- `src/discovery/search_engine.py`
- `tests/test_discovery.py`

- [ ] **Step 2.1**: Write tests in `tests/test_discovery.py` for `TelegramChannelScraper`:
  - Bio and pinned message parsing.
  - History pagination (`?before=...`).
  - Extracting URI nodes, subscription links, forwarded channels, and image preview links.
- [ ] **Step 2.2**: Write tests for `WebSearchScraper`: DuckDuckGo HTML scraping and GitHub search query extraction.
- [ ] **Step 2.3**: Implement `TelegramChannelScraper` and `WebSearchScraper` in `src/discovery/search_engine.py`.
- [ ] **Step 2.4**: Run unit tests and verify extraction accuracy against real HTML fixtures.

---

### Task 3: Discovery Orchestrator, Live Prober & Pipeline Merging
**Files:**
- `src/discovery/agent.py`
- `src/fetcher.py`
- `src/main.py`
- `tests/test_discovery.py`

- [ ] **Step 3.1**: Write tests in `tests/test_discovery.py` for `DiscoveryAgent`:
  - Round execution and query synthesis.
  - Candidate live-probing (validating working URIs and sub-links).
  - Emitting valid sources into `sources.discovered.json`.
  - Updating `data/discovery_state.json` and rendering `DISCOVERY.md`.
- [ ] **Step 3.2**: Implement `DiscoveryAgent` in `src/discovery/agent.py` connecting Nemotron, search scrapers, state manager, and live node validation.
- [ ] **Step 3.3**: Update `src/fetcher.py` `load_sources()` to automatically load and merge `sources.discovered.json`.
- [ ] **Step 3.4**: Expose discovery flags in `src/main.py` (`--discover`, `--discover-only`, `--discovery-time`, `--discovery-model`).
- [ ] **Step 3.5**: Run full discovery test suite.

---

### Task 4: Workflow Integration, Security Audit & Documentation
**Files:**
- `.github/workflows/update-subs.yml`
- `README.md`
- `tests/test_scripts.py`

- [ ] **Step 4.1**: Update `.github/workflows/update-subs.yml` to run the autonomous discovery step before aggregation with `NVIDIA_API_KEY` masking and graceful fallbacks.
- [ ] **Step 4.2**: Add `sources.discovered.json`, `data/discovery_state.json`, and `DISCOVERY.md` to git commits.
- [ ] **Step 4.3**: Enhance dynamic privacy audit to assert `NVIDIA_API_KEY` is never exposed.
- [ ] **Step 4.4**: Document the discovery agent in `README.md` and create initial `data/discovery_state.json`.
- [ ] **Step 4.5**: Run complete test suite and commit all changes.
