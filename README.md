# NavaRoute

[![Tests & Security](https://github.com/mani-moh/navaroute/actions/workflows/ci.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/ci.yml)
[![Subscriptions](https://github.com/mani-moh/navaroute/actions/workflows/update-subs.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/update-subs.yml)
[![Discovery](https://github.com/mani-moh/navaroute/actions/workflows/daily-deep-discovery.yml/badge.svg)](https://github.com/mani-moh/navaroute/actions/workflows/daily-deep-discovery.yml)

NavaRoute aggregates public proxy connections, validates complete connection identities, deduplicates them, and publishes categorized URI subscriptions. It is not a client manager, routing service, or promise of censorship bypass. It never installs or discovers a client/core, synchronizes a database, or manufactures connection variants.

Canonical repository: **`mani-moh/navaroute`**, default branch: **`master`**. The repository rename, protected owner-results setup and remote acceptance are release checks owned by the coordinator; these links describe the intended canonical destination, not proof it is already available.

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

Snapshot time: 2026-11-10T06:56:15+00:00
all.txt SHA-256: `87be40b583cddf2bedb663c2a53839500243ce76df240684f6d4a84f438af8d8`

Accepted public nodes: 54189; socket healthcheck ran: false; socket reachable: 0.
Reachability—not credential verified. Upstream labels and country/category labels are claims, not successful probes.
`verified.txt` is a deprecated reachable mirror. `categories/pattn-special.txt` is only a legacy raw-path mirror of supplied parameters.
Retired executable `categories/serverless-fragment.json` is `[]`.

| Feed | Nodes |
|---|---:|
| [all.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all.txt) | 54189 |
| [all_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/all_base64.txt) | 54189 |
| [categories/cdn-ws-grpc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/cdn-ws-grpc.txt) | 32853 |
| [categories/cdn-ws-grpc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/cdn-ws-grpc_base64.txt) | 32853 |
| [categories/confighub.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/confighub.txt) | 6016 |
| [categories/confighub_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/confighub_base64.txt) | 6016 |
| [categories/iran-optimized.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/iran-optimized.txt) | 13724 |
| [categories/iran-optimized_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/iran-optimized_base64.txt) | 13724 |
| [categories/navaroute-special.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special.txt) | 1000 |
| [categories/navaroute-special_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/navaroute-special_base64.txt) | 1000 |
| [categories/pattn-special.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/pattn-special.txt) | 1000 |
| [categories/pattn-special_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/pattn-special_base64.txt) | 1000 |
| [categories/reality.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/reality.txt) | 7458 |
| [categories/reality_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/reality_base64.txt) | 7458 |
| [categories/serverless-fragment.json](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/serverless-fragment.json) | 0 |
| [categories/whitelist.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/whitelist.txt) | 504 |
| [categories/whitelist_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/categories/whitelist_base64.txt) | 504 |
| [countries/ae.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ae.txt) | 92 |
| [countries/ae_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ae_base64.txt) | 92 |
| [countries/ai.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ai.txt) | 7 |
| [countries/ai_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ai_base64.txt) | 7 |
| [countries/al.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/al.txt) | 20 |
| [countries/al_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/al_base64.txt) | 20 |
| [countries/am.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/am.txt) | 9 |
| [countries/am_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/am_base64.txt) | 9 |
| [countries/ar.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ar.txt) | 9 |
| [countries/ar_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ar_base64.txt) | 9 |
| [countries/at.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/at.txt) | 34 |
| [countries/at_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/at_base64.txt) | 34 |
| [countries/au.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/au.txt) | 101 |
| [countries/au_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/au_base64.txt) | 101 |
| [countries/az.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/az.txt) | 44 |
| [countries/az_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/az_base64.txt) | 44 |
| [countries/ba.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ba.txt) | 8 |
| [countries/ba_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ba_base64.txt) | 8 |
| [countries/be.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/be.txt) | 30 |
| [countries/be_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/be_base64.txt) | 30 |
| [countries/bg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bg.txt) | 44 |
| [countries/bg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/bg_base64.txt) | 44 |
| [countries/br.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/br.txt) | 67 |
| [countries/br_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/br_base64.txt) | 67 |
| [countries/by.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/by.txt) | 13 |
| [countries/by_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/by_base64.txt) | 13 |
| [countries/ca.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ca.txt) | 1735 |
| [countries/ca_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ca_base64.txt) | 1735 |
| [countries/cc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cc.txt) | 771 |
| [countries/cc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cc_base64.txt) | 771 |
| [countries/cd.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cd.txt) | 673 |
| [countries/cd_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cd_base64.txt) | 673 |
| [countries/ch.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ch.txt) | 168 |
| [countries/ch_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ch_base64.txt) | 168 |
| [countries/ci.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ci.txt) | 71 |
| [countries/ci_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ci_base64.txt) | 71 |
| [countries/cl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cl.txt) | 32 |
| [countries/cl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cl_base64.txt) | 32 |
| [countries/cn.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cn.txt) | 416 |
| [countries/cn_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cn_base64.txt) | 416 |
| [countries/co.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/co.txt) | 70 |
| [countries/co_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/co_base64.txt) | 70 |
| [countries/cr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cr.txt) | 5 |
| [countries/cr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cr_base64.txt) | 5 |
| [countries/cv.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cv.txt) | 8 |
| [countries/cv_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cv_base64.txt) | 8 |
| [countries/cy.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cy.txt) | 25 |
| [countries/cy_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cy_base64.txt) | 25 |
| [countries/cz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cz.txt) | 35 |
| [countries/cz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/cz_base64.txt) | 35 |
| [countries/de.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/de.txt) | 2554 |
| [countries/de_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/de_base64.txt) | 2554 |
| [countries/dk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dk.txt) | 26 |
| [countries/dk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dk_base64.txt) | 26 |
| [countries/dz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dz.txt) | 6 |
| [countries/dz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/dz_base64.txt) | 6 |
| [countries/ee.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ee.txt) | 71 |
| [countries/ee_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ee_base64.txt) | 71 |
| [countries/eg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/eg.txt) | 14 |
| [countries/eg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/eg_base64.txt) | 14 |
| [countries/es.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/es.txt) | 124 |
| [countries/es_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/es_base64.txt) | 124 |
| [countries/fi.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fi.txt) | 488 |
| [countries/fi_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fi_base64.txt) | 488 |
| [countries/fr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fr.txt) | 1264 |
| [countries/fr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/fr_base64.txt) | 1264 |
| [countries/ga.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ga.txt) | 10 |
| [countries/ga_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ga_base64.txt) | 10 |
| [countries/gb.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gb.txt) | 878 |
| [countries/gb_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gb_base64.txt) | 878 |
| [countries/gg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gg.txt) | 5 |
| [countries/gg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gg_base64.txt) | 5 |
| [countries/gl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gl.txt) | 10 |
| [countries/gl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gl_base64.txt) | 10 |
| [countries/gq.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gq.txt) | 6 |
| [countries/gq_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gq_base64.txt) | 6 |
| [countries/gr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gr.txt) | 27 |
| [countries/gr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/gr_base64.txt) | 27 |
| [countries/hk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hk.txt) | 467 |
| [countries/hk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hk_base64.txt) | 467 |
| [countries/hu.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hu.txt) | 25 |
| [countries/hu_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/hu_base64.txt) | 25 |
| [countries/id.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/id.txt) | 15 |
| [countries/id_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/id_base64.txt) | 15 |
| [countries/ie.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ie.txt) | 41 |
| [countries/ie_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ie_base64.txt) | 41 |
| [countries/il.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/il.txt) | 23 |
| [countries/il_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/il_base64.txt) | 23 |
| [countries/in.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/in.txt) | 70 |
| [countries/in_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/in_base64.txt) | 70 |
| [countries/io.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/io.txt) | 169 |
| [countries/io_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/io_base64.txt) | 169 |
| [countries/ir.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ir.txt) | 2852 |
| [countries/ir_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ir_base64.txt) | 2852 |
| [countries/is.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/is.txt) | 10 |
| [countries/is_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/is_base64.txt) | 10 |
| [countries/it.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/it.txt) | 87 |
| [countries/it_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/it_base64.txt) | 87 |
| [countries/jo.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jo.txt) | 7 |
| [countries/jo_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jo_base64.txt) | 7 |
| [countries/jp.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jp.txt) | 433 |
| [countries/jp_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/jp_base64.txt) | 433 |
| [countries/kg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kg.txt) | 56 |
| [countries/kg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kg_base64.txt) | 56 |
| [countries/kh.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kh.txt) | 6 |
| [countries/kh_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kh_base64.txt) | 6 |
| [countries/kr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kr.txt) | 234 |
| [countries/kr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kr_base64.txt) | 234 |
| [countries/kw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kw.txt) | 7 |
| [countries/kw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kw_base64.txt) | 7 |
| [countries/kz.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kz.txt) | 42 |
| [countries/kz_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/kz_base64.txt) | 42 |
| [countries/lt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lt.txt) | 79 |
| [countries/lt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lt_base64.txt) | 79 |
| [countries/lu.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lu.txt) | 10 |
| [countries/lu_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lu_base64.txt) | 10 |
| [countries/lv.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lv.txt) | 84 |
| [countries/lv_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/lv_base64.txt) | 84 |
| [countries/md.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/md.txt) | 37 |
| [countries/md_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/md_base64.txt) | 37 |
| [countries/mk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mk.txt) | 7 |
| [countries/mk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mk_base64.txt) | 7 |
| [countries/ml.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ml.txt) | 13 |
| [countries/ml_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ml_base64.txt) | 13 |
| [countries/mx.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mx.txt) | 27 |
| [countries/mx_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/mx_base64.txt) | 27 |
| [countries/my.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/my.txt) | 25 |
| [countries/my_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/my_base64.txt) | 25 |
| [countries/ng.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ng.txt) | 70 |
| [countries/ng_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ng_base64.txt) | 70 |
| [countries/nl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/nl.txt) | 1255 |
| [countries/nl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/nl_base64.txt) | 1255 |
| [countries/no.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/no.txt) | 15 |
| [countries/no_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/no_base64.txt) | 15 |
| [countries/pe.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pe.txt) | 8 |
| [countries/pe_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pe_base64.txt) | 8 |
| [countries/ph.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ph.txt) | 9 |
| [countries/ph_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ph_base64.txt) | 9 |
| [countries/pk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pk.txt) | 9 |
| [countries/pk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pk_base64.txt) | 9 |
| [countries/pl.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pl.txt) | 356 |
| [countries/pl_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pl_base64.txt) | 356 |
| [countries/pt.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pt.txt) | 10 |
| [countries/pt_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pt_base64.txt) | 10 |
| [countries/pw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pw.txt) | 9 |
| [countries/pw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/pw_base64.txt) | 9 |
| [countries/ro.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ro.txt) | 29 |
| [countries/ro_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ro_base64.txt) | 29 |
| [countries/rs.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/rs.txt) | 22 |
| [countries/rs_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/rs_base64.txt) | 22 |
| [countries/ru.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ru.txt) | 1155 |
| [countries/ru_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ru_base64.txt) | 1155 |
| [countries/sa.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sa.txt) | 15 |
| [countries/sa_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sa_base64.txt) | 15 |
| [countries/sb.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sb.txt) | 9 |
| [countries/sb_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sb_base64.txt) | 9 |
| [countries/sc.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sc.txt) | 55 |
| [countries/sc_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sc_base64.txt) | 55 |
| [countries/se.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/se.txt) | 226 |
| [countries/se_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/se_base64.txt) | 226 |
| [countries/sg.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sg.txt) | 594 |
| [countries/sg_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sg_base64.txt) | 594 |
| [countries/si.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/si.txt) | 7 |
| [countries/si_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/si_base64.txt) | 7 |
| [countries/sk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sk.txt) | 5 |
| [countries/sk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/sk_base64.txt) | 5 |
| [countries/th.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/th.txt) | 21 |
| [countries/th_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/th_base64.txt) | 21 |
| [countries/tk.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tk.txt) | 9 |
| [countries/tk_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tk_base64.txt) | 9 |
| [countries/tr.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tr.txt) | 435 |
| [countries/tr_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tr_base64.txt) | 435 |
| [countries/tv.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tv.txt) | 6 |
| [countries/tv_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tv_base64.txt) | 6 |
| [countries/tw.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tw.txt) | 208 |
| [countries/tw_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/tw_base64.txt) | 208 |
| [countries/ua.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ua.txt) | 242 |
| [countries/ua_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ua_base64.txt) | 242 |
| [countries/um.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/um.txt) | 41 |
| [countries/um_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/um_base64.txt) | 41 |
| [countries/us.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/us.txt) | 6427 |
| [countries/us_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/us_base64.txt) | 6427 |
| [countries/uy.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/uy.txt) | 6 |
| [countries/uy_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/uy_base64.txt) | 6 |
| [countries/vn.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vn.txt) | 371 |
| [countries/vn_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/vn_base64.txt) | 371 |
| [countries/ws.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ws.txt) | 5 |
| [countries/ws_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/ws_base64.txt) | 5 |
| [countries/za.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/za.txt) | 24 |
| [countries/za_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/countries/za_base64.txt) | 24 |
| [lite500.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/lite500.txt) | 500 |
| [lite500_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/lite500_base64.txt) | 500 |
| [protocols/hysteria2.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/hysteria2.txt) | 567 |
| [protocols/hysteria2_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/hysteria2_base64.txt) | 567 |
| [protocols/ss.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/ss.txt) | 3568 |
| [protocols/ss_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/ss_base64.txt) | 3568 |
| [protocols/trojan.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/trojan.txt) | 5448 |
| [protocols/trojan_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/trojan_base64.txt) | 5448 |
| [protocols/tuic.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/tuic.txt) | 8 |
| [protocols/tuic_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/tuic_base64.txt) | 8 |
| [protocols/vless.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vless.txt) | 37820 |
| [protocols/vless_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vless_base64.txt) | 37820 |
| [protocols/vmess.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vmess.txt) | 6774 |
| [protocols/vmess_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/protocols/vmess_base64.txt) | 6774 |
| [reachable.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/reachable.txt) | 0 |
| [reachable_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/reachable_base64.txt) | 0 |
| [top100.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/top100.txt) | 0 |
| [top100_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/top100_base64.txt) | 0 |
| [verified.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/verified.txt) | 0 |
| [verified_base64.txt](https://raw.githubusercontent.com/mani-moh/navaroute/master/subs/verified_base64.txt) | 0 |
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

Python 3.12 and 3.14 are the tested CI versions; the pipeline has no mandatory third-party runtime dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md). Explicit local public aggregation (fetches public sources; socket checks disabled here):

```powershell
python -m src.main --no-healthcheck
```

## Attribution and limits

Public upstream URLs, source identities and labels are not NavaRoute-owned branding. Patterniha/Xray-core and other upstream projects retain their own identities and licenses; this project does not ship or automatically install their binaries. [MIT license](LICENSE) copyright remains unchanged. Historical audit/spec/plan references and the finite legacy paths are documented in [MIGRATION.md](docs/MIGRATION.md).

No client import, live core interoperability, OCR accuracy, remote release enforcement, long-term uptime or Iranian carrier behavior is established by the offline tests. Public feed credentials are already public upstream; users still need to evaluate source trust and applicable law.
