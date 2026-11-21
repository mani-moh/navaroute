# NavaRoute

[![Tests & Security](https://github.com/mani-moh/navaroute/actions/workflows/ci.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/ci.yml)
[![Subscriptions](https://github.com/mani-moh/navaroute/actions/workflows/update-subs.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/update-subs.yml)
[![Discovery](https://github.com/mani-moh/navaroute/actions/workflows/daily-deep-discovery.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/daily-deep-discovery.yml)
[![Tier 1 PattN Test](https://github.com/mani-moh/navaroute/actions/workflows/tier1-pattn-test.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/tier1-pattn-test.yml)
[![Nightly Snapshot](https://github.com/mani-moh/navaroute/actions/workflows/nightly-snapshot.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/nightly-snapshot.yml)

NavaRoute aggregates public proxy connections, validates complete connection identities, deduplicates them, and publishes categorized URI subscriptions. It is not a client manager, routing service, or promise of censorship bypass. It never installs or discovers a client/core, synchronizes a database, or manufactures connection variants.

Canonical repository: **`mani-moh/navaroute`**, default branch: **`master`**. Feeds publish to `master` on a schedule; the separately owner-tested results live on the protected `iran-results` branch (see below).

Prefer to explore interactively? **[NavaRoute Pipeline Explorer](https://mani-moh.github.io/navaroute/explorer/)** — a section-by-section walkthrough (pipeline, automation, repo anatomy, feed catalog, trust boundaries), with snapshot figures refreshed live from this README. Source under [`docs/explorer`](docs/explorer/) and [`docs/superpowers`](docs/superpowers/) in this repository.

Every GitHub Actions job ends with a shared privacy-safe run summary (checkout/final commit identities, job result, commits and per-file line deltas since checkout, tracked working-tree changes, new untracked paths and the publication outcome) written to the job's step-summary page and log; it never includes file contents, credentials, tokens or local paths.

## Public subscription formats

Plain `.txt` feeds contain comment headers followed by one proxy URI per line. Their `_base64.txt` pairs encode **only the URI rows**, joined by newlines, without the comment headers. The plain profile title is itself Base64-encoded. These are not native Xray, sing-box or Clash executable configurations; consumer protocol/parameter support must be checked separately. Retired `categories/serverless-fragment.json` is always `[]`.

| Feed on `master/subs/` | Meaning |
|---|---|
| [all.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all.txt) | All accepted, deduplicated public connections. Acceptance is not a working-credential claim. |
| [online.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online.txt) | Successful dated socket observations from that generation, not end-to-end tests. Empty if checks did not run or none succeeded. |
| [online-week.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-week.txt) | Configs with a successful socket observation in the last 7 days, from the committed reachability history. Socket reachability only, not end-to-end or Iran-tested working. |
| [fastest-100.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/fastest-100.txt) | Up to 100 from the online pool, measured connection latency first, with deterministic ties; not throughput or streaming performance. |
| [balanced-500.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/balanced-500.txt) | Up to 500 connections, sampled across protocol buckets. |
| [tested.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/tested.txt) | Configs that passed a full-connection Realping test on GitHub runners. The global tested feed. |
| [tested-iran.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/tested-iran.txt) | Configs that passed the same Realping test from inside Iran (owner PC). Appears once the owner runs the tier-2 test. |

### Which feed should I use?

- **I am in Iran and I just want configs that work** → `tested-iran`
- **I just want configs that work (anywhere)** → `tested`
- **I want the fastest, and a short list** → `fastest-100` (or `fastest-100-tested-iran` in Iran)
- **I want a small list that covers every protocol** → `balanced-500`
- **I want configs that are up right now** → `online`
- **I want configs that worked at least once this week** → `online-week`
- **I want everything** → `all`
- **I want only one country / one protocol** → the country or protocol slice

Plain or Base64? If your client accepts a normal link, use plain. If it only takes a Base64 subscription, use the `_base64` link. Same configs either way. Every feed has a `_base64.txt` twin.

Protocol groups cover VLESS, VMess, Trojan, Shadowsocks, Hysteria2 and TUIC. Country labels come from upstream remarks/tags, not geolocation, optimized routing, ISP testing or guaranteed exit location. Categories select supplied parameters or upstream claims:

- Primary special path: [`categories/navaroute-special.txt`](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special.txt). No generated variants or universal bypass claim.
- `categories/pattn-special.txt` is the **only old-brand raw-path alias**, with NavaRoute headers and the same special URI rows.
- `categories/iran-optimized.txt` is a legacy filename for **Iran-related upstream claims**, not optimized routing or Iranian ISP evidence.
- Reality and web-transport groups describe supplied settings; ConfigsHUB and whitelist groups are source claims, not verified functionality.

### Tested feeds

These are the same groups as above, restricted to configs that passed a full-connection Realping test (see [Tiered testing](#tiered-testing)). Each has a `_base64.txt` mirror. The global feeds `tested.txt` and `tested-iran.txt` are in the table above; the per-group variants are:

| Feed on `master/subs/` | Meaning |
|---|---|
| [online-tested.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-tested.txt) | Tested passers from the online pool. |
| [online-week-tested.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-week-tested.txt) | Tested passers from the online-week (last-7-days socket) pool. |
| [fastest-100-tested.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/fastest-100-tested.txt) | Tested passers from the fastest-100 pool. |
| [balanced-500-tested.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/balanced-500-tested.txt) | Tested passers from the balanced-500 pool. |
| [online-tested-iran.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-tested-iran.txt) | Iran-tested passers from the online pool. |
| [online-week-tested-iran.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-week-tested-iran.txt) | Iran-tested passers from the online-week pool. |
| [fastest-100-tested-iran.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/fastest-100-tested-iran.txt) | Iran-tested passers from the fastest-100 pool. |
| [balanced-500-tested-iran.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/balanced-500-tested-iran.txt) | Iran-tested passers from the balanced-500 pool. |

Counts change every run; the current numbers live in [`data/tier1_results.json`](data/tier1_results.json) (GitHub) and [`data/iran_results.json`](data/iran_results.json) (Iran).

Every slice feed has the same two variants: `protocols/<proto>-tested.txt` and `protocols/<proto>-tested-iran.txt`, `countries/<cc>-tested.txt` and `countries/<cc>-tested-iran.txt`, `categories/<name>-tested.txt` and `categories/<name>-tested-iran.txt` (each with a `_base64` twin). An empty tested variant means no config in that slice passed yet — it is still written, so the link never 404s.

See [STATUS.md](STATUS.md), the discovery leaderboard [DISCOVERY.md](DISCOVERY.md), [tiered testing](TIERED-TESTING.md), [architecture](docs/ARCHITECTURE.md) and [historical migration provenance](docs/MIGRATION.md). The initial migration is offline: its snapshot time belongs to the old public input, **not** a new crawl or healthcheck. No old verification boolean becomes current evidence.

<!-- NAVAROUTE:STATS:START -->
## NavaRoute Generated Snapshot

Snapshot time: 2026-11-21T10:47:41.233936+00:00
all.txt SHA-256: `51cb6252e4bc522f8c2af146ad9aef9fd828e159406683c9babc4cae5a413768`

Accepted public nodes: 150460; socket healthcheck ran: true; socket reachable: 981; socket reachable in the last 7 days: 2764.
Reachability—not credential verified. Upstream labels and country/category labels are claims, not successful probes.
`categories/pattn-special.txt` is only a legacy raw-path mirror of supplied parameters.
`online-week.txt` lists configs with a successful socket observation in the last 7 days; it is reachability history, not end-to-end verification.
Retired executable `categories/serverless-fragment.json` is `[]`.

| Feed | Nodes |
|---|---:|
| [all.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all.txt) | 150460 |
| [all_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all_base64.txt) | 150460 |
| [balanced-500.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/balanced-500.txt) | 500 |
| [balanced-500_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/balanced-500_base64.txt) | 500 |
| [categories/cdn-ws-grpc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/cdn-ws-grpc.txt) | 76130 |
| [categories/cdn-ws-grpc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/cdn-ws-grpc_base64.txt) | 76130 |
| [categories/confighub.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/confighub.txt) | 9016 |
| [categories/confighub_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/confighub_base64.txt) | 9016 |
| [categories/iran-optimized.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/iran-optimized.txt) | 26057 |
| [categories/iran-optimized_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/iran-optimized_base64.txt) | 26057 |
| [categories/navaroute-special.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special.txt) | 1272 |
| [categories/navaroute-special_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special_base64.txt) | 1272 |
| [categories/pattn-special.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/pattn-special.txt) | 1272 |
| [categories/pattn-special_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/pattn-special_base64.txt) | 1272 |
| [categories/reality.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/reality.txt) | 20609 |
| [categories/reality_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/reality_base64.txt) | 20609 |
| [categories/serverless-fragment.json](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/serverless-fragment.json) | 0 |
| [categories/whitelist.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/whitelist.txt) | 752 |
| [categories/whitelist_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/whitelist_base64.txt) | 752 |
| [countries/ae.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ae.txt) | 298 |
| [countries/ae_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ae_base64.txt) | 298 |
| [countries/al.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/al.txt) | 38 |
| [countries/al_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/al_base64.txt) | 38 |
| [countries/am.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/am.txt) | 41 |
| [countries/am_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/am_base64.txt) | 41 |
| [countries/ar.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ar.txt) | 15 |
| [countries/ar_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ar_base64.txt) | 15 |
| [countries/at.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/at.txt) | 196 |
| [countries/at_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/at_base64.txt) | 196 |
| [countries/au.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/au.txt) | 374 |
| [countries/au_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/au_base64.txt) | 374 |
| [countries/az.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/az.txt) | 90 |
| [countries/az_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/az_base64.txt) | 90 |
| [countries/ba.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ba.txt) | 13 |
| [countries/ba_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ba_base64.txt) | 13 |
| [countries/bd.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bd.txt) | 6 |
| [countries/bd_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bd_base64.txt) | 6 |
| [countries/be.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/be.txt) | 101 |
| [countries/be_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/be_base64.txt) | 101 |
| [countries/bg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bg.txt) | 138 |
| [countries/bg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bg_base64.txt) | 138 |
| [countries/bh.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bh.txt) | 24 |
| [countries/bh_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bh_base64.txt) | 24 |
| [countries/bl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bl.txt) | 6 |
| [countries/bl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bl_base64.txt) | 6 |
| [countries/br.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/br.txt) | 171 |
| [countries/br_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/br_base64.txt) | 171 |
| [countries/by.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/by.txt) | 49 |
| [countries/by_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/by_base64.txt) | 49 |
| [countries/bz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bz.txt) | 9 |
| [countries/bz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bz_base64.txt) | 9 |
| [countries/ca.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ca.txt) | 2781 |
| [countries/ca_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ca_base64.txt) | 2781 |
| [countries/cc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cc.txt) | 17 |
| [countries/cc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cc_base64.txt) | 17 |
| [countries/cf.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cf.txt) | 21 |
| [countries/cf_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cf_base64.txt) | 21 |
| [countries/ch.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ch.txt) | 424 |
| [countries/ch_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ch_base64.txt) | 424 |
| [countries/cl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cl.txt) | 36 |
| [countries/cl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cl_base64.txt) | 36 |
| [countries/cn.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cn.txt) | 1144 |
| [countries/cn_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cn_base64.txt) | 1144 |
| [countries/co.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/co.txt) | 18 |
| [countries/co_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/co_base64.txt) | 18 |
| [countries/cr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cr.txt) | 43 |
| [countries/cr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cr_base64.txt) | 43 |
| [countries/cy.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cy.txt) | 225 |
| [countries/cy_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cy_base64.txt) | 225 |
| [countries/cz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cz.txt) | 240 |
| [countries/cz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cz_base64.txt) | 240 |
| [countries/de.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/de.txt) | 6370 |
| [countries/de_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/de_base64.txt) | 6370 |
| [countries/dk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dk.txt) | 146 |
| [countries/dk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dk_base64.txt) | 146 |
| [countries/ee.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ee.txt) | 235 |
| [countries/ee_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ee_base64.txt) | 235 |
| [countries/eg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/eg.txt) | 12 |
| [countries/eg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/eg_base64.txt) | 12 |
| [countries/es.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/es.txt) | 350 |
| [countries/es_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/es_base64.txt) | 350 |
| [countries/fi.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fi.txt) | 1075 |
| [countries/fi_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fi_base64.txt) | 1075 |
| [countries/fr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fr.txt) | 2528 |
| [countries/fr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fr_base64.txt) | 2528 |
| [countries/gb.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gb.txt) | 1963 |
| [countries/gb_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gb_base64.txt) | 1963 |
| [countries/ge.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ge.txt) | 13 |
| [countries/ge_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ge_base64.txt) | 13 |
| [countries/gr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gr.txt) | 53 |
| [countries/gr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gr_base64.txt) | 53 |
| [countries/hk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hk.txt) | 1660 |
| [countries/hk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hk_base64.txt) | 1660 |
| [countries/hr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hr.txt) | 8 |
| [countries/hr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hr_base64.txt) | 8 |
| [countries/hu.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hu.txt) | 38 |
| [countries/hu_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hu_base64.txt) | 38 |
| [countries/id.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/id.txt) | 57 |
| [countries/id_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/id_base64.txt) | 57 |
| [countries/ie.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ie.txt) | 310 |
| [countries/ie_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ie_base64.txt) | 310 |
| [countries/il.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/il.txt) | 52 |
| [countries/il_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/il_base64.txt) | 52 |
| [countries/im.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/im.txt) | 27 |
| [countries/im_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/im_base64.txt) | 27 |
| [countries/in.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/in.txt) | 290 |
| [countries/in_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/in_base64.txt) | 290 |
| [countries/iq.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/iq.txt) | 7 |
| [countries/iq_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/iq_base64.txt) | 7 |
| [countries/ir.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ir.txt) | 1727 |
| [countries/ir_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ir_base64.txt) | 1727 |
| [countries/is.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/is.txt) | 17 |
| [countries/is_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/is_base64.txt) | 17 |
| [countries/it.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/it.txt) | 220 |
| [countries/it_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/it_base64.txt) | 220 |
| [countries/jo.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jo.txt) | 13 |
| [countries/jo_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jo_base64.txt) | 13 |
| [countries/jp.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jp.txt) | 1999 |
| [countries/jp_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jp_base64.txt) | 1999 |
| [countries/ke.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ke.txt) | 5 |
| [countries/ke_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ke_base64.txt) | 5 |
| [countries/kh.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kh.txt) | 7 |
| [countries/kh_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kh_base64.txt) | 7 |
| [countries/kr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kr.txt) | 814 |
| [countries/kr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kr_base64.txt) | 814 |
| [countries/kw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kw.txt) | 7 |
| [countries/kw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kw_base64.txt) | 7 |
| [countries/kz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kz.txt) | 242 |
| [countries/kz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kz_base64.txt) | 242 |
| [countries/lt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lt.txt) | 236 |
| [countries/lt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lt_base64.txt) | 236 |
| [countries/lu.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lu.txt) | 231 |
| [countries/lu_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lu_base64.txt) | 231 |
| [countries/lv.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lv.txt) | 206 |
| [countries/lv_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lv_base64.txt) | 206 |
| [countries/ma.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ma.txt) | 9 |
| [countries/ma_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ma_base64.txt) | 9 |
| [countries/md.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/md.txt) | 116 |
| [countries/md_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/md_base64.txt) | 116 |
| [countries/mk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mk.txt) | 9 |
| [countries/mk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mk_base64.txt) | 9 |
| [countries/mo.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mo.txt) | 7 |
| [countries/mo_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mo_base64.txt) | 7 |
| [countries/mt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mt.txt) | 133 |
| [countries/mt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mt_base64.txt) | 133 |
| [countries/mx.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mx.txt) | 58 |
| [countries/mx_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mx_base64.txt) | 58 |
| [countries/my.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/my.txt) | 64 |
| [countries/my_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/my_base64.txt) | 64 |
| [countries/ng.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ng.txt) | 63 |
| [countries/ng_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ng_base64.txt) | 63 |
| [countries/nl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/nl.txt) | 3257 |
| [countries/nl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/nl_base64.txt) | 3257 |
| [countries/no.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/no.txt) | 60 |
| [countries/no_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/no_base64.txt) | 60 |
| [countries/om.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/om.txt) | 5 |
| [countries/om_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/om_base64.txt) | 5 |
| [countries/pe.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pe.txt) | 8 |
| [countries/pe_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pe_base64.txt) | 8 |
| [countries/ph.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ph.txt) | 29 |
| [countries/ph_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ph_base64.txt) | 29 |
| [countries/pk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pk.txt) | 16 |
| [countries/pk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pk_base64.txt) | 16 |
| [countries/pl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pl.txt) | 905 |
| [countries/pl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pl_base64.txt) | 905 |
| [countries/pt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pt.txt) | 12 |
| [countries/pt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pt_base64.txt) | 12 |
| [countries/ro.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ro.txt) | 211 |
| [countries/ro_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ro_base64.txt) | 211 |
| [countries/rs.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/rs.txt) | 40 |
| [countries/rs_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/rs_base64.txt) | 40 |
| [countries/ru.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ru.txt) | 2291 |
| [countries/ru_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ru_base64.txt) | 2291 |
| [countries/sa.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sa.txt) | 21 |
| [countries/sa_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sa_base64.txt) | 21 |
| [countries/sc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sc.txt) | 213 |
| [countries/sc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sc_base64.txt) | 213 |
| [countries/se.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/se.txt) | 862 |
| [countries/se_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/se_base64.txt) | 862 |
| [countries/sg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sg.txt) | 1607 |
| [countries/sg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sg_base64.txt) | 1607 |
| [countries/si.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/si.txt) | 102 |
| [countries/si_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/si_base64.txt) | 102 |
| [countries/sk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sk.txt) | 101 |
| [countries/sk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sk_base64.txt) | 101 |
| [countries/ss.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ss.txt) | 352 |
| [countries/ss_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ss_base64.txt) | 352 |
| [countries/tg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tg.txt) | 78 |
| [countries/tg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tg_base64.txt) | 78 |
| [countries/th.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/th.txt) | 136 |
| [countries/th_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/th_base64.txt) | 136 |
| [countries/tr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tr.txt) | 818 |
| [countries/tr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tr_base64.txt) | 818 |
| [countries/tw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tw.txt) | 518 |
| [countries/tw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tw_base64.txt) | 518 |
| [countries/ua.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ua.txt) | 158 |
| [countries/ua_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ua_base64.txt) | 158 |
| [countries/um.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/um.txt) | 44 |
| [countries/um_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/um_base64.txt) | 44 |
| [countries/us.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/us.txt) | 15974 |
| [countries/us_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/us_base64.txt) | 15974 |
| [countries/uz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/uz.txt) | 190 |
| [countries/uz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/uz_base64.txt) | 190 |
| [countries/vg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vg.txt) | 80 |
| [countries/vg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vg_base64.txt) | 80 |
| [countries/vn.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vn.txt) | 420 |
| [countries/vn_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vn_base64.txt) | 420 |
| [countries/ws.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ws.txt) | 204 |
| [countries/ws_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ws_base64.txt) | 204 |
| [countries/za.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/za.txt) | 84 |
| [countries/za_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/za_base64.txt) | 84 |
| [fastest-100.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/fastest-100.txt) | 100 |
| [fastest-100_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/fastest-100_base64.txt) | 100 |
| [online-week.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-week.txt) | 2764 |
| [online-week_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online-week_base64.txt) | 2764 |
| [online.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online.txt) | 981 |
| [online_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/online_base64.txt) | 981 |
| [protocols/hysteria2.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/hysteria2.txt) | 4512 |
| [protocols/hysteria2_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/hysteria2_base64.txt) | 4512 |
| [protocols/ss.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/ss.txt) | 15893 |
| [protocols/ss_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/ss_base64.txt) | 15893 |
| [protocols/trojan.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/trojan.txt) | 36133 |
| [protocols/trojan_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/trojan_base64.txt) | 36133 |
| [protocols/tuic.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/tuic.txt) | 109 |
| [protocols/tuic_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/tuic_base64.txt) | 109 |
| [protocols/vless.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vless.txt) | 76717 |
| [protocols/vless_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vless_base64.txt) | 76717 |
| [protocols/vmess.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vmess.txt) | 16993 |
| [protocols/vmess_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vmess_base64.txt) | 16993 |
<!-- NAVAROUTE:STATS:END -->

## How the feeds are built

### Aggregation — every 6 hours

The `update-subs` workflow (`0 */6 * * *`) fetches the registry of public subscription sources (curated + discovered, listed in [`sources.json`](sources.json)), validates complete connection identities, deduplicates them, and publishes the feeds above plus the generated snapshot in this README. A 1,000-config sample gets a socket reachability probe each run; the rest is untested at this stage. The aggregate raw-node cap scales with the enabled source count (500,000 floor, 2,500 per source); hitting it publishes a partial result with the skipped sources marked instead of failing the run, and a single source is truncated at 100,000 nodes.

### Discovery — daily 02:00 UTC

The `daily-deep-discovery` workflow finds new subscription sources. An AI scout (default model `nvidia/nemotron-3.5-super-vl:free` via OpenRouter) extracts subscription URLs and Telegram handles from page snippets, judges ambiguous handles, and suggests fresh search queries when the candidate queue runs dry (at most 3 extra passes), steered by a compact list of already-known sources. Text fallbacks run `openrouter/free` → Nemotron Ultra 550B → Nano Omni → Lightning; vision falls back to Nano Omni. If OpenRouter itself is unreachable, the same models are retried on NVIDIA's own API with their IDs mapped (the free router has no direct twin). Ceilings are 500 actual API requests including retries/fallbacks, 5400 discovery network seconds and 2000 channels — not promised yields. The 6-hourly aggregation itself uses no AI.

### Tiered testing

Socket reachability is not proof a config works end to end, so the feeds above are also Realping-tested in two tiers; see [TIERED-TESTING.md](TIERED-TESTING.md).

- **Tier 1 — GitHub, every 2 hours** (`0 */2 * * *`): the `tier1-pattn-test` workflow runs the PattN WebAPI (pinned P40 release) on `windows-latest` and Realping-tests up to 8,000 due configs per run — an end-to-end HTTPS GET through the proxy, twice 100 ms apart, 5 s connect timeout, minimum recorded. It tests in batches of 100 across 4 servers side by side as a filling system (about 19 minutes for 8,000 configs, down from ~90). Results publish `subs/tested.txt` (plus `online-tested.txt`, `online-week-tested.txt`, `fastest-100-tested.txt`, `balanced-500-tested.txt` and a `-tested` variant for every slice feed) and `data/tier1_results.json`. Configs that consistently fail on GitHub but pass from Iran are force-included via `data/github_blocked.json` and counted separately.
- **Tier 2 — owner PC, owner-triggered**: a single Windows executable (`NavaRouteTier2.exe`, built by the `build-tier2` workflow) drives PattN on the owner's PC with the Iran regional preset, tests the tier-1 passers or all configs, and publishes `subs/tested-iran.txt` variants (plus `online-tested-iran.txt`, `online-week-tested-iran.txt`, `fastest-100-tested-iran.txt`, `balanced-500-tested-iran.txt` and a `-tested-iran` variant for every slice feed) plus an updated `data/github_blocked.json` through a branch and PR. No scheduled tier-2 job exists; Iran testing is owner-only.

### Nightly snapshot check — 03:00 UTC

The `nightly-snapshot` workflow runs the full snapshot verification the PR checks skip: every generated profile and decoded URI canonical, and the current tracked snapshot loadable within its limits. It guards the published snapshot; a red nightly run means the snapshot (not the PR gate) needs attention.

## Public site and mirrors

- **Public site:** [mani-moh.github.io/navaroute](https://mani-moh.github.io/navaroute/) — bilingual (Persian default, English toggle), all feeds with copy buttons and mirrors, no runtime requests. Built by `scripts/build_public_site.py`; see [docs/PUBLIC-SITE.md](docs/PUBLIC-SITE.md). Cloudflare Pages is the primary host; GitHub Pages stays as a mirror.
- **ArvanCloud mirror:** [repoget.s3.ir-central1.arvanstorage.ir](https://repoget.s3.ir-central1.arvanstorage.ir/index.html) — the base64 feeds behind neutral labels, synced every 6 hours from a private mirror repo.
- **Telegram bot:** [@awsmanibot](https://t.me/awsmanibot) — `/status` and `/feeds` for anyone; discovery and aggregation triggers are owner-only.

## Separate owner-tested results

Only the owner's explicit standalone command may publish dated end-to-end evidence to protected **`iran-results`**:

- [Tested URI feed](https://raw.githubusercontent.com/mani-moh/navaroute/iran-results/subs/iran-tested.txt)
- [Base64 tested feed](https://raw.githubusercontent.com/mani-moh/navaroute/iran-results/subs/iran-tested_base64.txt)
- [Dated provenance manifest](https://raw.githubusercontent.com/mani-moh/navaroute/iran-results/data/iran-verification.json)

These links are separate from scheduled `master` feeds. Availability and the first live owner test are pending release/owner acceptance, not claimed here. Evidence records the immutable public snapshot commit/digest, protected tester revision, core version/hash, UTC dates, policy and outcomes. It does not prove physical origin, current health, every carrier, every consumer, or unrestricted connectivity.

### Manual owner procedure

1. Obtain a reviewed immutable tester commit on protected `iran-results` through a trusted review channel. The coordinator must first establish the branch and protection. All tester imports and publication code must live there. **Never execute a bootstrap, wrapper or imported module from scheduled-writable `master`.** Use a separate trusted checkout; no existing user/client directory is moved or migrated.
2. Authenticate GitHub CLI as actual **`mani-moh`**. The command checks login, canonical repository ownership/admin access and protection before probes. Keep Actions PR creation/approval blocked. Normal non-admin writers require review; the explicit owner-admin exception is `enforce_admins=false`, with no bot/App bypass. Owner access-review attestation covers the known incomplete App/secret inventory; it is not independent enumeration.
3. Supply an **absolute path** to a trusted standalone Xray binary. There is no download, PATH/client search, existing-process attachment or proxy/VPN inspection. The owner must actually use a direct Iranian ISP connection and explicitly attest it.
4. Supply a full immutable official `master` snapshot SHA and independently checked SHA-256 of its exact public `subs/all.txt` bytes. Failed retrieval has no local/private fallback. Only faithfully convertible supported public nodes are tested (initial core subset: VLESS/VMess/Trojan/SS; unsupported parameters and Hy2/TUIC are not silently stripped).

Example PowerShell commands, with **operator-supplied reviewed values**, not executable release pins:

```powershell
# Use a NEW operator-selected trusted checkout, not a client directory.
git clone --branch iran-results https://github.com/mani-moh/navaroute.git "$TrustedCheckout"
git -C "$TrustedCheckout" checkout --detach "$ReviewedIranResultsRevision"
# Verify the reviewed revision/tree and protection before executing any code.
python -I "$TrustedCheckout/scripts/test_iran_reachability.py" `
  --xray-bin "$AbsoluteTrustedStandaloneXray" `
  --source-sha "$ImmutableMasterSnapshotSha" --source-digest "$PublicAllSha256" `
  --tester-revision "$ReviewedIranResultsRevision" `
  --attest-direct-iran --attest-access-reviewed --publish
```

Omitting `--publish` means a local test only, not an uploaded result. Defaults are up to 500 selected nodes, 2 rounds, batches of 48, concurrency 24 and a 5-second probe timeout; all limits are positive and bounded. HTTPS policy checks require a certificate-valid expected 204 response. Only owned child processes are stopped. A fully executed zero-survivor test may publish empty results; zero work or core/infrastructure failure cannot replace evidence.

Publication validates the manifest and public subset, creates one complete result commit through owner-authenticated GitHub APIs and advances the captured protected head with `force=false`. Concurrent changes fail rather than overwrite them. Success is reported only after reference/content confirmation. Upload failure can retain a sanitized dated recovery manifest in ignored `local_subs/iran-results`; add `--retry-manifest "$AbsoluteOwnedRecoveryManifest"` to the same explicit command to retry identical evidence without a fictional retest. Identical dated publication is idempotent. Do not commit recovery files or arbitrary local inputs.

## Automation and local development

- Both retained writers run only on `master`, share **`navaroute-git-writer`**, and stop on discovery/rebase/push exhaustion. Pushes are non-forced and retain human commits. No scheduled Iran job or core download exists.
- One writer per output directory **including local/custom publishers**, and one checkpoint/discovery publisher per state directory. Actions concurrency does not lock local commands. Do not run overlapping local publishers; journal recovery is not a multi-writer lock.
- CI declares unrestricted push/PR triggers without path exclusions or skip-CI commit markers. GitHub normally suppresses downstream workflow triggers for `GITHUB_TOKEN` pushes; scheduled writers therefore run their own tests and final staged privacy gate, not a claimed second CI run. Actions are pinned to verified immutable checkout/setup-python references. Tests check logic offline, not live core schemas or carrier behavior.
- Tokens are repository-scoped, **not path-scoped**. `persist-credentials: false`, step environment omission and staging allowlists are not protection for executable owner code; the protected branch and reviewed trusted checkout are the separate boundary. Scheduled jobs need only explicit `contents: write` for public master publication, not administration/PR authority.
- The quiet privacy gate checks all tracked index roles on clean checkouts and the final staged public role set before commit. It rejects historical private directories, `.env` variants, DB/cache artifacts and unexpected `data/` paths, without reading untracked private files or printing matching secrets. It is a bounded pattern gate, not proof that no possible secret exists.
- AI debugging trace (optional, local-only): set `NAVAROUTE_AI_TRACE_PATH=<file.jsonl>` to append a machine-local JSONL record of every NVIDIA attempt — model, status, bytes, SHA-256 of request/response, and content truncated to 4 KiB strings / 16 KiB response text. The `NVIDIA_API_KEY` and request headers are never recorded. Never set it in public CI and never commit the trace file; it may contain unvetted model output. Public workflows leave it unset, so no AI exchange is ever published.

Python 3.12 and 3.14 are the tested CI versions; the pipeline has no mandatory third-party runtime dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md). Explicit local public aggregation (fetches public sources; socket checks disabled here):

```powershell
python -m src.main --no-healthcheck
```

## Attribution and limits

Public upstream URLs, source identities and labels are not NavaRoute-owned branding. Patterniha/Xray-core and other upstream projects retain their own identities and licenses; this project does not ship or automatically install their binaries. [MIT license](LICENSE) copyright remains unchanged. Historical audit/spec/plan references and the finite legacy paths are documented in [MIGRATION.md](docs/MIGRATION.md).

No client import, live core interoperability, OCR accuracy, remote release enforcement, long-term uptime or Iranian carrier behavior is established by the offline tests. Public feed credentials are already public upstream; users still need to evaluate source trust and applicable law.
