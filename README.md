# NavaRoute

[![Tests & Security](https://github.com/mani-moh/navaroute/actions/workflows/ci.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/ci.yml)
[![Subscriptions](https://github.com/mani-moh/navaroute/actions/workflows/update-subs.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/update-subs.yml)
[![Discovery](https://github.com/mani-moh/navaroute/actions/workflows/daily-deep-discovery.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/daily-deep-discovery.yml)

NavaRoute aggregates public proxy connections, validates complete connection identities, deduplicates them, and publishes categorized URI subscriptions. It is not a client manager, routing service, or promise of censorship bypass. It never installs or discovers a client/core, synchronizes a database, or manufactures connection variants.

Canonical repository: **`mani-moh/navaroute`**, default branch: **`master`**. The repository rename, protected owner-results setup and remote acceptance are release checks owned by the coordinator; these links describe the intended canonical destination, not proof it is already available.

Prefer to explore interactively? **[NavaRoute Pipeline Explorer](https://mani-moh.github.io/navaroute/explorer/)** — a section-by-section walkthrough (pipeline, automation, repo anatomy, feed catalog, trust boundaries), with snapshot figures refreshed live from this README. Source under [`docs/explorer`](docs/explorer/) and [`docs/superpowers`](docs/superpowers/) in this repository.

Every GitHub Actions job ends with a shared privacy-safe run summary (checkout/final commit identities, job result, commits and per-file line deltas since checkout, tracked working-tree changes, new untracked paths and the publication outcome) written to the job's step-summary page and log; it never includes file contents, credentials, tokens or local paths.

## Public subscription formats

Plain `.txt` feeds contain comment headers followed by one proxy URI per line. Their `_base64.txt` pairs encode **only the URI rows**, joined by newlines, without the comment headers. The plain profile title is itself Base64-encoded. These are not native Xray, sing-box or Clash executable configurations; consumer protocol/parameter support must be checked separately. Retired `categories/serverless-fragment.json` is always `[]`.

| Feed on `master/subs/` | Meaning |
|---|---|
| [all.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all.txt) | All accepted, deduplicated public connections. Acceptance is not a working-credential claim. |
| [lite500.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/lite500.txt) | Up to 500 connections, sampled across protocol buckets. |
| [reachable.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/reachable.txt) | Successful dated socket observations from that generation, not end-to-end tests. Empty if checks did not run or none succeeded. |
| [top100.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/top100.txt) | Up to 100 from the reachable pool, measured connection latency first, with deterministic ties; not throughput or streaming performance. |
| [verified.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/verified.txt) | **Deprecated reachability mirror**, same URI rows as `reachable.txt`. The legacy filename does not mean credential or end-to-end verification. |

Protocol groups cover VLESS, VMess, Trojan, Shadowsocks, Hysteria2 and TUIC. Country labels come from upstream remarks/tags, not geolocation, optimized routing, ISP testing or guaranteed exit location. Categories select supplied parameters or upstream claims:

- Primary special path: [`categories/navaroute-special.txt`](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special.txt). No generated variants or universal bypass claim.
- `categories/pattn-special.txt` is the **only old-brand raw-path alias**, with NavaRoute headers and the same special URI rows.
- `categories/iran-optimized.txt` is a legacy filename for **Iran-related upstream claims**, not optimized routing or Iranian ISP evidence.
- Reality and web-transport groups describe supplied settings; ConfigsHUB and whitelist groups are source claims, not verified functionality.

See [STATUS.md](STATUS.md), [DISCOVERY.md](DISCOVERY.md), [architecture](docs/ARCHITECTURE.md) and [historical migration provenance](docs/MIGRATION.md). The initial migration is offline: its snapshot time belongs to the old public input, **not** a new crawl or healthcheck. No old verification boolean becomes current evidence.

<!-- NAVAROUTE:STATS:START -->
## NavaRoute Generated Snapshot

Snapshot time: 2026-11-13T05:33:33.384532+00:00
all.txt SHA-256: `2ca0e7fe95aafd4c197b5888d610699fcbb01f70ee07f32fd5544b73ab57b9c0`

Accepted public nodes: 64235; socket healthcheck ran: true; socket reachable: 974.
Reachability—not credential verified. Upstream labels and country/category labels are claims, not successful probes.
`verified.txt` is a deprecated reachable mirror. `categories/pattn-special.txt` is only a legacy raw-path mirror of supplied parameters.
Retired executable `categories/serverless-fragment.json` is `[]`.

| Feed | Nodes |
|---|---:|
| [all.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all.txt) | 64235 |
| [all_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all_base64.txt) | 64235 |
| [categories/cdn-ws-grpc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/cdn-ws-grpc.txt) | 39004 |
| [categories/cdn-ws-grpc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/cdn-ws-grpc_base64.txt) | 39004 |
| [categories/confighub.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/confighub.txt) | 9229 |
| [categories/confighub_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/confighub_base64.txt) | 9229 |
| [categories/iran-optimized.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/iran-optimized.txt) | 20895 |
| [categories/iran-optimized_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/iran-optimized_base64.txt) | 20895 |
| [categories/navaroute-special.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special.txt) | 1128 |
| [categories/navaroute-special_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special_base64.txt) | 1128 |
| [categories/pattn-special.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/pattn-special.txt) | 1128 |
| [categories/pattn-special_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/pattn-special_base64.txt) | 1128 |
| [categories/reality.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/reality.txt) | 10335 |
| [categories/reality_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/reality_base64.txt) | 10335 |
| [categories/serverless-fragment.json](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/serverless-fragment.json) | 0 |
| [categories/whitelist.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/whitelist.txt) | 754 |
| [categories/whitelist_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/whitelist_base64.txt) | 754 |
| [countries/ae.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ae.txt) | 104 |
| [countries/ae_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ae_base64.txt) | 104 |
| [countries/al.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/al.txt) | 25 |
| [countries/al_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/al_base64.txt) | 25 |
| [countries/am.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/am.txt) | 7 |
| [countries/am_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/am_base64.txt) | 7 |
| [countries/ar.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ar.txt) | 7 |
| [countries/ar_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ar_base64.txt) | 7 |
| [countries/at.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/at.txt) | 64 |
| [countries/at_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/at_base64.txt) | 64 |
| [countries/au.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/au.txt) | 103 |
| [countries/au_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/au_base64.txt) | 103 |
| [countries/az.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/az.txt) | 41 |
| [countries/az_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/az_base64.txt) | 41 |
| [countries/ba.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ba.txt) | 8 |
| [countries/ba_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ba_base64.txt) | 8 |
| [countries/be.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/be.txt) | 42 |
| [countries/be_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/be_base64.txt) | 42 |
| [countries/bg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bg.txt) | 81 |
| [countries/bg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bg_base64.txt) | 81 |
| [countries/br.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/br.txt) | 86 |
| [countries/br_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/br_base64.txt) | 86 |
| [countries/by.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/by.txt) | 16 |
| [countries/by_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/by_base64.txt) | 16 |
| [countries/ca.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ca.txt) | 1835 |
| [countries/ca_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ca_base64.txt) | 1835 |
| [countries/cf.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cf.txt) | 7 |
| [countries/cf_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cf_base64.txt) | 7 |
| [countries/ch.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ch.txt) | 181 |
| [countries/ch_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ch_base64.txt) | 181 |
| [countries/cl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cl.txt) | 11 |
| [countries/cl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cl_base64.txt) | 11 |
| [countries/cn.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cn.txt) | 284 |
| [countries/cn_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cn_base64.txt) | 284 |
| [countries/co.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/co.txt) | 7 |
| [countries/co_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/co_base64.txt) | 7 |
| [countries/cr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cr.txt) | 9 |
| [countries/cr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cr_base64.txt) | 9 |
| [countries/cy.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cy.txt) | 23 |
| [countries/cy_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cy_base64.txt) | 23 |
| [countries/cz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cz.txt) | 35 |
| [countries/cz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cz_base64.txt) | 35 |
| [countries/de.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/de.txt) | 2813 |
| [countries/de_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/de_base64.txt) | 2813 |
| [countries/dk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dk.txt) | 18 |
| [countries/dk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dk_base64.txt) | 18 |
| [countries/ee.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ee.txt) | 88 |
| [countries/ee_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ee_base64.txt) | 88 |
| [countries/eg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/eg.txt) | 8 |
| [countries/eg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/eg_base64.txt) | 8 |
| [countries/es.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/es.txt) | 132 |
| [countries/es_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/es_base64.txt) | 132 |
| [countries/fi.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fi.txt) | 539 |
| [countries/fi_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fi_base64.txt) | 539 |
| [countries/fr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fr.txt) | 791 |
| [countries/fr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fr_base64.txt) | 791 |
| [countries/gb.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gb.txt) | 895 |
| [countries/gb_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gb_base64.txt) | 895 |
| [countries/gr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gr.txt) | 26 |
| [countries/gr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gr_base64.txt) | 26 |
| [countries/hk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hk.txt) | 560 |
| [countries/hk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hk_base64.txt) | 560 |
| [countries/hu.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hu.txt) | 21 |
| [countries/hu_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hu_base64.txt) | 21 |
| [countries/id.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/id.txt) | 17 |
| [countries/id_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/id_base64.txt) | 17 |
| [countries/ie.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ie.txt) | 53 |
| [countries/ie_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ie_base64.txt) | 53 |
| [countries/il.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/il.txt) | 22 |
| [countries/il_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/il_base64.txt) | 22 |
| [countries/in.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/in.txt) | 83 |
| [countries/in_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/in_base64.txt) | 83 |
| [countries/ir.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ir.txt) | 924 |
| [countries/ir_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ir_base64.txt) | 924 |
| [countries/is.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/is.txt) | 7 |
| [countries/is_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/is_base64.txt) | 7 |
| [countries/it.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/it.txt) | 104 |
| [countries/it_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/it_base64.txt) | 104 |
| [countries/jo.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jo.txt) | 7 |
| [countries/jo_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jo_base64.txt) | 7 |
| [countries/jp.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jp.txt) | 553 |
| [countries/jp_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jp_base64.txt) | 553 |
| [countries/kh.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kh.txt) | 5 |
| [countries/kh_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kh_base64.txt) | 5 |
| [countries/kr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kr.txt) | 229 |
| [countries/kr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kr_base64.txt) | 229 |
| [countries/kw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kw.txt) | 7 |
| [countries/kw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kw_base64.txt) | 7 |
| [countries/kz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kz.txt) | 57 |
| [countries/kz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kz_base64.txt) | 57 |
| [countries/lt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lt.txt) | 123 |
| [countries/lt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lt_base64.txt) | 123 |
| [countries/lu.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lu.txt) | 18 |
| [countries/lu_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lu_base64.txt) | 18 |
| [countries/lv.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lv.txt) | 89 |
| [countries/lv_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lv_base64.txt) | 89 |
| [countries/md.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/md.txt) | 47 |
| [countries/md_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/md_base64.txt) | 47 |
| [countries/mk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mk.txt) | 5 |
| [countries/mk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mk_base64.txt) | 5 |
| [countries/mx.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mx.txt) | 29 |
| [countries/mx_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mx_base64.txt) | 29 |
| [countries/my.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/my.txt) | 28 |
| [countries/my_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/my_base64.txt) | 28 |
| [countries/ng.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ng.txt) | 49 |
| [countries/ng_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ng_base64.txt) | 49 |
| [countries/nl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/nl.txt) | 1429 |
| [countries/nl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/nl_base64.txt) | 1429 |
| [countries/no.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/no.txt) | 14 |
| [countries/no_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/no_base64.txt) | 14 |
| [countries/ph.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ph.txt) | 8 |
| [countries/ph_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ph_base64.txt) | 8 |
| [countries/pk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pk.txt) | 8 |
| [countries/pk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pk_base64.txt) | 8 |
| [countries/pl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pl.txt) | 385 |
| [countries/pl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pl_base64.txt) | 385 |
| [countries/pt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pt.txt) | 5 |
| [countries/pt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pt_base64.txt) | 5 |
| [countries/ro.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ro.txt) | 36 |
| [countries/ro_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ro_base64.txt) | 36 |
| [countries/rs.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/rs.txt) | 22 |
| [countries/rs_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/rs_base64.txt) | 22 |
| [countries/ru.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ru.txt) | 507 |
| [countries/ru_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ru_base64.txt) | 507 |
| [countries/sa.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sa.txt) | 15 |
| [countries/sa_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sa_base64.txt) | 15 |
| [countries/sc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sc.txt) | 61 |
| [countries/sc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sc_base64.txt) | 61 |
| [countries/se.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/se.txt) | 239 |
| [countries/se_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/se_base64.txt) | 239 |
| [countries/sg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sg.txt) | 611 |
| [countries/sg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sg_base64.txt) | 611 |
| [countries/si.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/si.txt) | 9 |
| [countries/si_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/si_base64.txt) | 9 |
| [countries/sk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sk.txt) | 5 |
| [countries/sk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sk_base64.txt) | 5 |
| [countries/th.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/th.txt) | 21 |
| [countries/th_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/th_base64.txt) | 21 |
| [countries/tr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tr.txt) | 414 |
| [countries/tr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tr_base64.txt) | 414 |
| [countries/tw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tw.txt) | 257 |
| [countries/tw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tw_base64.txt) | 257 |
| [countries/ua.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ua.txt) | 48 |
| [countries/ua_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ua_base64.txt) | 48 |
| [countries/um.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/um.txt) | 34 |
| [countries/um_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/um_base64.txt) | 34 |
| [countries/us.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/us.txt) | 7555 |
| [countries/us_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/us_base64.txt) | 7555 |
| [countries/vn.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vn.txt) | 377 |
| [countries/vn_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vn_base64.txt) | 377 |
| [countries/ws.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ws.txt) | 5 |
| [countries/ws_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ws_base64.txt) | 5 |
| [countries/za.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/za.txt) | 27 |
| [countries/za_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/za_base64.txt) | 27 |
| [lite500.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/lite500.txt) | 500 |
| [lite500_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/lite500_base64.txt) | 500 |
| [protocols/hysteria2.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/hysteria2.txt) | 800 |
| [protocols/hysteria2_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/hysteria2_base64.txt) | 800 |
| [protocols/ss.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/ss.txt) | 3663 |
| [protocols/ss_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/ss_base64.txt) | 3663 |
| [protocols/trojan.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/trojan.txt) | 5298 |
| [protocols/trojan_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/trojan_base64.txt) | 5298 |
| [protocols/tuic.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/tuic.txt) | 11 |
| [protocols/tuic_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/tuic_base64.txt) | 11 |
| [protocols/vless.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vless.txt) | 46009 |
| [protocols/vless_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vless_base64.txt) | 46009 |
| [protocols/vmess.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vmess.txt) | 8450 |
| [protocols/vmess_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vmess_base64.txt) | 8450 |
| [reachable.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/reachable.txt) | 974 |
| [reachable_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/reachable_base64.txt) | 974 |
| [top100.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/top100.txt) | 100 |
| [top100_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/top100_base64.txt) | 100 |
| [verified.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/verified.txt) | 974 |
| [verified_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/verified_base64.txt) | 974 |
<!-- NAVAROUTE:STATS:END -->

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

- Six-hour aggregation: **`0 */6 * * *`**. Daily discovery: **`0 2 * * *`**, with default ceilings of 500 actual API requests (including retries/fallbacks), 5400 discovery network seconds and 2000 channels, not promised actual yields.
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
