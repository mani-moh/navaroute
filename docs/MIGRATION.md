# NavaRoute public migration ledger

This is an **offline historical migration**, not a new source crawl, socket test or owner ISP result. The reviewed producer regenerated URI feeds, Base64 pairs, profile titles and marked README/STATUS facts. No private files, user/client directories, upstream URLs, copyright, origin remote or old commits were migrated/rebranded.

## Immutable input and timestamp

- Integration base: `2ef74d6e4de8633e9e50804680e256e9614ef827` (Tasks 1–6 reviewed).
- Actual artifact source commit: `cfbb8793f81ea6fcefc90c511ebd786c47082766`.
- Public `subs/all.txt`: **17,154,773 bytes**, **62,258 URI rows**; SHA-256 `53f3e18546dca31645b25bb9f726696f651acab24f76b6f2924c91511cbfeb13`.
- Actual prior generation time: **`2026-11-10T06:56:15+00:00`**, from tracked `STATUS.md` at the artifact source commit, corroborated by that commit's `06:56:17Z` time. A later README refresh time is not a new feed generation.
- The input was extracted and hashed before any artifact writes. Regeneration passed this historical `snapshot_time` explicitly with `healthcheck_ran=false` and fixed `GITHUB_REPOSITORY=mani-moh/navaroute`; the real origin remains unchanged pending coordinator release.

## Validation and deliberate exclusions

The corrected parser accepted **54,741** input rows and losslessly round-tripped their complete canonical connection identity. **7,517** rows were rejected by the reviewed strict parser; they were **not repaired**, decoded with permissive fallback into accepted nodes, or rewritten into different credentials/connection settings. Rejection counts are explicit in the machine manifest: VLESS 3,788; VMess 1,922; Trojan 958; SS 493; legacy unrecognized `shadowsocks://` 355; Hysteria2 1. This is an acceptance/compatibility limit requiring coordinator review, not a claim that every historical input was valid.

Among accepted rows, **330 positively identified old manufactured factory variants** were excluded: F&F 105, IPv6-OOB 116, SNI-Spoof 109. Identification required the specific historical `[PattN ...]` generation label **and** matching factory endpoint/port plus the corresponding TLS/fingerprint/fragment/cipher/dial semantics from the generator at the public source commit. Supplied special parameters or a generic Patterniha/source label alone were not exclusion criteria. Seven IPv6-OOB-labelled accepted records did **not** match the factory endpoint/port/fingerprint and were preserved rather than guessed away. Rejected rows may also carry historical labels; they remain parser rejections, not falsely validated factory removals.

The reviewed deduplicator collapsed **222** complete-equal canonical identities. The final pool contains **54,189** connections. Exact public auth/credential and connection semantics are preserved for every accepted identity; no parameter is stripped to make a row pass. Already-public generated `src:` IDs and corresponding public registry tags were retained from immutable public `sources.json` at the integration base (digest in the manifest). Historical generated country brackets were restored explicitly, rather than inferring geography from transport tokens such as `ws`: **30,338** accepted metadata labels differed from the parser's free-form country inference. Unknown/non-ISO labels become `XX`; no endpoint geolocation was invented. This is migration metadata handling, not a parser behavior change. The producer emits its standard connection/source labels (including decoded VMess `ps`); arbitrary original upstream remarks were already absent from the historical producer's formatted input. Ambiguous old success checkmarks were not promoted to either source certification or probe evidence.

The generation contains **227 public files**, including **95 country pairs**. Eighteen stale historical country files were removed; the full finite list is in the manifest. The reviewed producer reconciled recognized stale owned countries. Historical non-ISO `cp`, `eu` and `un` pairs were explicitly retired as obsolete generated artifacts; the runtime producer's ownership policy was not changed.

- All observation status is **untested**; no fresh socket date or owner evidence exists.
- `reachable`, `top100` and deprecated `verified` URI bodies are empty, honestly.
- Primary supplied-parameter `navaroute-special` and legacy `pattn-special` have identical URI bodies (1,000 connections), distinct NavaRoute profile titles.
- Retired executable JSON is `[]`.
- STATUS reports zero configured/fetched source statistics for this migration because the producer received **no source-fetch statistics**. The input row counts and exclusions are recorded here, not fabricated as a new HTTP fetch/crawl.
- DISCOVERY retains its historical `2026-11-10 06:55:22 UTC` records/counts/links; no fresh model yield or current-health claim is inferred.

See [migration-manifest.json](migration-manifest.json) for per-file SHA-256, feed counts, conservation equations, input/output digests and `tested=false`. Output `all.txt` SHA-256: `87be40b583cddf2bedb663c2a53839500243ce76df240684f6d4a84f438af8d8`. Sorted inventory digest (UTF-8 `path + NUL + hex-digest + LF` per file): `50ec260873c6190a9dee73f04328f63d335407069c66a730364deba4636381fe`. The manifest is historical and must not be updated to pretend later scheduled data belongs to this migration.

## Remaining old-name classification

| Retained reference | Classification |
|---|---|
| `subs/categories/pattn-special.txt` and `_base64.txt`; related producer constants/docs/tests | Deliberate **raw-path compatibility only**, NavaRoute titles; same supplied-parameter rows as primary `navaroute-special`. |
| `verified.txt`, `iran-optimized.txt`, `serverless-fragment.json` | Legacy semantic filenames, explicitly deprecated/claims-only/retired; no verified socket, routing or native-import guarantee. |
| `sources.json` `patterniha-*`, `Patterniha`, `patterniha/...`, external `pattn` tags/URLs and other source identities | Truthful external attribution/feature vocabulary, **not** NavaRoute-owned project branding. No global upstream URL replacement. |
| `docs/audits/2026-11-10-repository-audit.md`, approved `docs/superpowers/specs/` and `plans/` | Historical audit/design records describing the old product and migration; not maintained active identity. |
| Legacy deletion/flag regression names in tests, historical labels in this ledger and ignored Task7 scratch | Finite migration/security regression and provenance references, not retained runtime functionality. |
| Old Git commits, local worktree name/origin until release, ignored local/private files | Explicitly outside rebranding scope; history and real user data remain untouched. |

Active workflow names, shared writer group, canonical badges/links, environment example and package/service report text are NavaRoute. Mechanical retained-code changes outside workflow/docs: `src/__init__.py` docstring and two exact service branding strings in `src/discovery/state_manager.py`. No owner/health/parser/publisher behavior or protection policy changed.

## Release and trust limits

Repository rename/availability, protected `iran-results` setup/enforcement, remote CI and actual owner supplied-core/direct-ISP testing belong to coordinator/owner acceptance. They are **not completed or proven** by this offline migration. Actions PR creation/approval stays blocked, and the owner-selected `enforce_admins=false` exception is unchanged. Patterniha/Xray-core attribution and MIT copyright remain intact. Single-writer rules include local/custom output and checkpoint publishers; Actions concurrency is not a local lock. Pattern scanning is bounded and does not prove absence of every possible secret or remove historical secrets.
