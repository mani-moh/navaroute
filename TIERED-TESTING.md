# Tiered PattN testing

Two tiers of full-connection (Realping) testing, on top of the socket
probe the 6-hourly aggregation already does.

## Tier 1 — GitHub (every 2 hours)

`.github/workflows/tier1-pattn-test.yml` runs `scripts/tier1_pattn.py`
on a `windows-latest` runner. It downloads the pinned PattN WebAPI
(P40 — P41 ships no WebAPI assets), starts it with a per-run generated
key (loopback only, never a repo secret), imports the configs that are
due, and Realping-tests them: an end-to-end HTTPS GET through the proxy
to a `generate_204` URL, twice 100 ms apart, 5 s connect timeout, the
minimum recorded; both failing is a fail.

**Speed: page size + filling system.** The server tests in batches of
`SpeedTestPageSize` profiles (default 16 in the P40 binary), all of a
batch concurrently, and a batch waits for its slowest member — so with
8,000 due profiles a run took ~90 minutes. The driver writes page size
100 into each server's `guiNConfig.json` (`SpeedTestItem`, read at
startup; the `/api/settings/speedtest` endpoint cannot set it) and runs
4 WebAPI servers side by side as a filling system: due configs are
split into chunks of the page size, and each server is refilled with
the next chunk the moment its chunk finishes, so a slow config on one
server never idles the others. Each server gets its own copy of the
extracted WebAPI directory — the P40 server refuses a second instance
from one directory (exit code 73 on the instance lock) and needs its
own profile database. Both knobs are dispatch inputs
(`speedtest_page_size`, `speedtest_workers`); a worker that dies
mid-chunk has its chunk requeued once, then the chunk is dropped and
its configs stay untested. The per-test 5 s timeout is compiled into
the P40 binary (`Global.LocalFetch`) and cannot be lowered from the
driver; `SpeedTestTimeout` only bounds the download speed test.

**What gets tested.** All configs, not just the 1,000 the aggregation
samples — but not all at once. Each run tests up to 8,000 due configs
(`max_configs` dispatch input). A config is due again after an interval
that grows with its consecutive-failure streak: 0 → every run, 1 → 4 h,
2 → 8 h, 3 → 16 h, 4 → 32 h, 5+ → 48 h. Never-tested configs go first,
then the lowest streak, then the oldest last test. A full sweep of the
whole list takes roughly a day at the 2-hourly cadence.

**Outputs (committed each run).**
- `data/tier1_state.json` — fingerprint → {failures, last_test, last_delay}.
  Fingerprints only (SHA-256 of the canonical connection, the same
  values that key the published feeds and the health history).
- `data/tier1_results.json` — per-feed {tested, passed, github_blocked}
  plus totals. `passed` is what is currently passing tier 1 in the
  feed; the OS dashboard's Feeds sort reads this file.
- `subs/tested.txt` and `subs/tested_base64.txt` — the global tested
  feed — plus `online-tested.txt`, `online-week-tested.txt`,
  `fastest-100-tested.txt` and `balanced-500-tested.txt` (and their
  `_base64` twins): each feed's configs that passed tier 1, plus the
  GitHub-blocked set, sorted by delay. Every slice feed gets the same
  treatment: `protocols/<proto>-tested.txt`, `countries/<cc>-tested.txt`
  and `categories/<name>-tested.txt` (plain + base64), each the slice
  intersected with the tier-1 passing set. A slice with no passers
  still gets an empty file — the run never aborts over an empty slice.

**GitHub-blocked special treatment.** `data/github_blocked.json`
(written by tier 2) lists fingerprints that consistently fail tier 1
(streak ≥ 2) but pass from Iran. Tier 1 force-includes them in every
variant even when GitHub keeps failing them, and counts them
separately (`github_blocked`) so the dashboard can tell them apart.
A config leaves the list when it passes tier 1 again or fails tier 2.

**Honest limits.** Tier-1 delay is measured from GitHub's datacenter,
not Iran — that is tier 2's job. Some nodes block datacenter IPs;
backoff keeps them in rotation, and the blocked list is the escape
hatch for the ones that work from Iran.

## Tier 2 — owner PC (owner-triggered, no schedule)

`tools/tier2/tier2_pattn.py` is a stdlib-only Python app; the
`build-tier2` workflow builds it into a single Windows `.exe`
(`NavaRouteTier2.exe`) with PyInstaller and publishes it as a
workflow artifact (download it from the latest successful
`build-tier2` run — there are no releases in this repo). Run the exe
on your PC: it opens a dashboard at http://127.0.0.1:8765.

- It finds your PattN folder (`D:\PattN-windows-64-desktop\PattN-windows-64`)
  and reuses its cores, or downloads the pinned P40 WebAPI once and
  caches it under `%LOCALAPPDATA%\NavaRouteTier2`.
- It applies the Iran regional preset, then tests either the tier-1
  passers (`subs/tested.txt`, default) or **all configs**
  (`subs/all.txt`, the full sweep — chunked and resumable, progress
  saved per chunk under the same cache dir). The scope picker narrows
  it further: everything remaining (untested + last run's failures),
  only last run's failures, only untested, or everything again.
- **Test modes.** Realping (default) is an end-to-end HTTPS GET and
  stays the authority for published Iran-verified feeds. **UDP**
  runs the WebAPI's `Udptest` action instead (UDP over SOCKS5,
  NTP-based): it proves UDP forwarding works and tracks speed, but
  does NOT prove the proxy can do HTTPS/web traffic — only Realping
  does, so a UDP-only run is labelled `mode: udp` in
  `data/iran_results.json` and in the PR body, and should not be read
  as Iran-verified for web traffic. **UDP-then-Realping** screens
  with UDP first and Realping-confirms only the UDP passers, so the
  published set stays Realping-proven while the slow Realping step
  skips configs that already fail UDP. The P40 WebAPI accepts
  `Udptest` (verified live against the P40 Linux build, 2026-11-16:
  HTTP 202, `speedtest.started`); if a build ever refuses it, the app
  falls back to Realping for that run and says so in the log, and the
  UDP options are disabled in the UI afterwards.
- **Customization.** Before starting: test mode (above), parallelism
  (how many configs the server tests at once — `SpeedTestPageSize`,
  default 100, written into the WebAPI's `guiNConfig.json` before the
  server starts), and a max-delay cutoff in ms (0 = none; a config
  slower than the cutoff counts as a failure even if it answers).
  A run can be **stopped** cleanly at any time with the Stop button:
  the in-flight speed test is cancelled, progress stays saved per
  chunk, and the run resumes where it left off.
- **ETA.** While a run is going the dashboard shows tested/total,
  elapsed time, configs/min and time remaining, updated on every
  server poll. Before starting it shows an estimate from the last
  run's rate (`%LOCALAPPDATA%\NavaRouteTier2\last-rate.json`,
  written at the end of every run); with no previous run there is no
  estimate yet. For UDP-then-Realping the confirm phase counts in the
  progress total, so the ETA stays honest through phase 2.
- **Upload to repo** (needs a fine-grained PAT for `mani-moh/navaroute`
  with contents + pull requests, entered once in the UI and stored in
  Windows Credential Manager — never in the repo or a file) commits to
  a branch + PR: `subs/tested-iran.txt` and `subs/tested-iran_base64.txt`
  (the global Iran-verified feed) plus `online-tested-iran.txt`,
  `online-week-tested-iran.txt`, `fastest-100-tested-iran.txt`,
  `balanced-500-tested-iran.txt` and a `-tested-iran` variant for every
  slice feed (per-feed Iran-verified variants, sorted by Iran delay),
  `data/iran_results.json` (per-feed {tested, passed} plus the run's
  `mode`: `realping`, `udp` or `udp-realping`), and an updated
  `data/github_blocked.json`. The upload checkboxes pick which groups
  go up instead of all-or-nothing (core groups and/or all slice
  variants); `iran_results.json` then covers only the uploaded groups.

Your `PattN.exe` stays for everyday use; the tester drives the headless
engine behind it.

## Download speed ranking — GitHub (every 4 hours)

`.github/workflows/speedtest-feeds.yml` runs `scripts/speedtest_feeds.py` on `ubuntu-latest` with a checksum-verified Xray-core binary. It selects up to 400 candidates from `subs/tested.txt`, `subs/fastest-100.txt`, and `subs/online.txt`, measures fixed-size HTTPS download throughput (Mbps) through each proxy with bounded concurrency and failure backoff, persists pruned state in `data/speed_state.json`, and publishes `subs/speed-100.txt` (+ `_base64.txt`) ranked deterministically by `(-speed_mbps, latency_ms, fingerprint)` plus `data/speed_results.json`. Measurements are from GitHub runner locations, not Iran.

## Files

| File | What it is |
|---|---|
| `src/pattn_client.py` | WebAPI client + launcher (shared by both tiers) |
| `src/tier1.py` | Backoff, state, variants, results (pure, tested) |
| `src/speedtest.py` | Download throughput testing, state pruning, top-100 ranking |
| `scripts/tier1_pattn.py` | Tier-1 driver (GitHub runner) |
| `scripts/speedtest_feeds.py` | Download speedtest driver (`subs/speed-100.txt`) |
| `tools/tier2/tier2_pattn.py` | Tier-2 owner-PC app + browser UI |
| `tools/tier2/tier2_pattn.spec` | PyInstaller spec for the .exe |
| `.github/workflows/tier1-pattn-test.yml` | Tier-1 schedule (every 2 h) |
| `.github/workflows/speedtest-feeds.yml` | Download speedtest schedule (every 4 h) |
| `.github/workflows/build-tier2.yml` | Builds the tier-2 .exe |
