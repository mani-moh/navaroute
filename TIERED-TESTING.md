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
- `subs/tier1_<group>.txt` and `subs/tier1_<group>_base64.txt` for
  `all, reachable, active, top100, lite500` — the feed's configs that
  passed tier 1, plus the GitHub-blocked set, sorted by delay.
  `tier1_all` is the global tier-1 feed.

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
(`NavaRouteTier2.exe`) with PyInstaller and publishes it as a release
asset. Run the exe on your PC: it opens a dashboard at
http://127.0.0.1:8765.

- It finds your PattN folder (`D:\PattN-windows-64-desktop\PattN-windows-64`)
  and reuses its cores, or downloads the pinned P40 WebAPI once and
  caches it under `%LOCALAPPDATA%\NavaRouteTier2`.
- It applies the Iran regional preset, then tests either the tier-1
  passers (`subs/tier1_all.txt`, default) or **all configs**
  (`subs/all.txt`, the full sweep — chunked and resumable, progress
  saved per chunk under the same cache dir).
- **Upload to repo** (needs a fine-grained PAT for `mani-moh/navaroute`
  with contents + pull requests, entered once in the UI and stored in
  Windows Credential Manager — never in the repo or a file) commits to
  a branch + PR: `subs/iran_<group>.txt` and `subs/iran_<group>_base64.txt`
  (per-feed Iran-verified variants, sorted by Iran delay),
  `data/iran_results.json` (per-feed {tested, passed}), and an updated
  `data/github_blocked.json`.

Your `PattN.exe` stays for everyday use; the tester drives the headless
engine behind it.

## Files

| File | What it is |
|---|---|
| `src/pattn_client.py` | WebAPI client + launcher (shared by both tiers) |
| `src/tier1.py` | Backoff, state, variants, results (pure, tested) |
| `scripts/tier1_pattn.py` | Tier-1 driver (GitHub runner) |
| `tools/tier2/tier2_pattn.py` | Tier-2 owner-PC app + browser UI |
| `tools/tier2/tier2_pattn.spec` | PyInstaller spec for the .exe |
| `.github/workflows/tier1-pattn-test.yml` | Tier-1 schedule (every 2 h) |
| `.github/workflows/build-tier2.yml` | Builds the tier-2 .exe |
