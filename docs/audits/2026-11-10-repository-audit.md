# NavaRoute: pre-hardening professional repository audit

**Date:** 2026-11-10

**Audited revision:** `ffd5333d45c60f7352abcc5d5e5d271f184279db`

**Current repository:** `mani-moh/pattn`; selected target: `mani-moh/navaroute`

**Status:** Audit and proposed remediation only. Product code and GitHub settings have not been changed.

## What this repo does

The repository collects public proxy configurations, discovers additional sources, deduplicates connections, and publishes plain/Base64 subscriptions. It also manufactures bypass variants and integrates with a desktop client; those paths introduce substantial correctness and safety problems.

The approved direction is **NavaRoute**, a subscription producer with daily discovery, six-hour refreshes, and a separate owner-commanded Iran test. All client integration is to be removed. Iran testing must run on the owner's machine and publish official results only when explicitly commanded.

**Assumed load:** about 122 sources, 118,476 extracted configurations and 62,258 published nodes per refresh; a daily discovery budget of up to 500 API attempts; one owner performing bounded manual verification. This is a scheduled batch application, not a multi-user web service. A database, service backend, UI, or distributed worker framework is not justified.

## Executive grade

**36/100 — F for production readiness; a useful prototype, not a dependable production pipeline.**

This grade measures the current implementation against its own documented guarantees. New owner-publication requirements are identified separately as missing capabilities, not disguised as pre-existing bugs. A passing test suite does not offset a reproduced security or data-integrity failure.

| Dimension | Weight | Score /10 | Assessment |
|---|---:|---:|---|
| Correctness and data preservation | 20% | 3 | Distinct connections collide; valid peers are discarded; publication can destroy previous outputs. |
| Security and privacy | 20% | 2 | Authenticated requests use insecure TLS/redirect paths; public-input boundaries and artifact scans are incomplete. |
| Failure recovery and operational reliability | 15% | 3 | Errors become success, checkpoints can lose work, failed pushes can look green. |
| Protocol and verification fidelity | 10% | 4 | Basic URI formats work; JSON conversion and bypass testing lose required settings. |
| Tests and regression protection | 10% | 5 | 65 passing tests, but risky boundaries are mocked, untested, or tested for the wrong behavior. |
| Structure and maintainability | 8% | 6 | Useful small modules and standard-library operation; duplicated representations and embedded documentation cause drift. |
| Performance at expected batch scale | 7% | 6 | A 60,000-node offline run completes; unbounded responses and repeated work remain risks. |
| Documentation truthfulness | 5% | 3 | Comprehensive but materially overstates security, compatibility, freshness, and censorship bypass. |
| Git/GitHub delivery | 5% | 4 | Automation and shared writer concurrency exist; branch controls, push failure handling, and executable supply-chain controls need work. |

Scoring: 9–10 = dependable and evidenced; 7–8 = sound with limited gaps; 5–6 = functional but incomplete; 3–4 = substantial failures; 0–2 = absent or unsafe. Letter bands: A ≥90, B ≥80, C ≥70, D ≥60, F <60. Weighted score: 36.0.

**Target compliance, separately:** client separation fails; official owner-only Iran publication does not exist; NavaRoute branding has not yet been migrated. These are release blockers for the requested target.

### Strengths worth retaining

- A straightforward fetch → parse → deduplicate → publish structure.
- Standard-library-compatible Python, without a mandatory framework or dependency installation.
- Existing provenance tags, categorized outputs, and plain/Base64 pairs.
- Batched Xray process ownership, explicit routing to a blackhole default, and multi-round survivor intersection.
- Current daily and six-hour jobs share writer concurrency. Neither currently runs the Iran verifier.
- A clean initial working tree and native GitHub secret scanning/push protection.

## Method and evidence

Four independent read-only auditors covered parsing/identity/publishing, discovery, local scripts, and documentation/GitHub delivery. The coordinator traced the entry point, fetching and health checks, executed baseline tests, reproduced boundary failures, and cross-checked high-risk conclusions. No live proxy scan, NVIDIA inference, desktop interaction, private-feed migration, GitHub mutation, or history rewriting was performed.

Inventory: **21 Python files, 6,947 Python source/test lines, 194 functions, 80 exception handlers; 279 tracked files and 22 reachable revisions**. These counts are descriptive, not defects.

| Verification | Result |
|---|---|
| `python -m compileall -q src scripts tests` | Passed on local Python 3.14.6. |
| `python -m unittest discover -s tests -v` | 65 passed. |
| `python -m pytest -q` | 65 passed. |
| `python -S -m unittest discover -s tests -q` | 65 passed without site packages. |
| Remote CI run `42821023251` | Existing Python 3.10/3.12 matrix passed, observed read-only. |
| Offline two-node real fetch/parse/publish smoke test | Two nodes emitted; Base64 equals URI lines; 33 files produced. |
| Offline 60,000-node synthetic batch | 60,000 emitted, 27.70 seconds **with allocation tracing**, 258.22 MiB peak Python allocations, 53.59 MiB output. |
| Current generated pairs | All 120 plain/Base64 pairs matched. |

The synthetic benchmark disables network health checks, synthesis, and docs; it is not a production latency or memory SLA. Python allocation tracing changes runtime and measures allocations, not process resident memory.

Coarse standard-library line tracing found approximately 47% of fetcher and 45% of healthcheck/discovery-agent lines exercised. It is not branch coverage; the large README literal also distorts categorizer line percentages. No coverage percentage is used as a release substitute.

The coordinator's 13 offline cases reproduced: empty-registry suppression; empty publication replacing good data; requested-Xray socket fallback; retained stale success; malformed VMess batch failure; stripped bypass settings; legacy SOCKS fallback success; HTTP 503 reported successful with optional httpx; cache path traversal; disabled TLS verification; discovery-only exit zero on failure; QUIC tested by TCP; and captive-page HTTP 200 accepted. The actual standard-library SOCKS handler also returned `unknown url type: socks5` without opening a socket.

## Ranked findings

Twenty root-cause groups follow. Related concrete cases are grouped to avoid counting the same defect several times. Dispositions are **repair**, **remove**, or **new target capability**. Severity denotes impact, not evidence of an existing compromise.

### Must fix

### 1. Authenticated transport can expose a write-capable token — Critical

**Locations:** `src/discovery/search_engine.py:16–36,244–251,294–300`; `src/discovery/nemotron_client.py:114–153`.

- **What this is:** GitHub searches and NVIDIA calls send bearer credentials through shared HTTP transport.
- **Problem:** Scraper HTTPS disables certificate verification. Python redirects retain Authorization across origins, including a downgrade to HTTP. An intercepted GitHub search can expose the workflow token; header retention and disabled verification were reproduced offline.
- **Fix:** Use certificate-verified HTTPS; reject cross-origin/downgrade redirects for authenticated requests; validate configured API origins and strip credentials from diagnostics. Never fall back to unauthenticated TLS.
- **If skipped:** Feed poisoning and token theft remain possible. No actual theft or compromise was observed.

### 2. Remote/local destinations and download sizes are not safe boundaries — High

**Locations:** `src/fetcher.py:69–128`; `src/discovery/agent.py:119–147,313–329`; `src/discovery/search_engine.py:268–277`; `src/healthcheck.py:44–55`.

- **What this is:** Untrusted feeds, model responses, and discovered URLs trigger requests or connection attempts.
- **Problem:** Metadata/private addresses reach the transport unchanged; redirects and DNS results are unrestricted. `data/../sentinel.txt` reads outside the permitted cache. Several bodies use unrestricted `read()`; a trickling response can evade a socket timeout. Explicit proxy failures can also cause a direct retry.
- **Fix:** Enforce public destinations at resolution, connection, and redirects; preserve HTTPS host verification; resolve cache paths inside an owned cache root; cap bytes and total deadlines. Honor explicit proxy choice instead of silently bypassing it.
- **If skipped:** Private services/files may be read or local services contacted; hostile responses can exhaust memory/time. Do not claim DNS rebinding is fixed by a URL-prefix check.

### 3. Credential URLs and incomplete secret gates undermine privacy — High

**Locations:** `src/discovery/agent.py:140–159,353–358`; `src/discovery/state_manager.py:237,285–289`; `.github/workflows/ci.yml:46–52`; publishing workflow scan/stage steps.

- **What this is:** Registries, state, leaderboard, and subscriptions are automatically committed.
- **Problem:** A synthetic token-bearing subscription URL appeared in registry and leaderboard. A clean checkout with a committed private file escapes a working-tree-status-only guard; regex matches can print secrets. Broadly staging `data/` risks including unexpected files.
- **Fix:** Reject credential-bearing/unapproved discovered URLs before fetching/persistence, without stripping tokens into broken links. Check tracked and staged private paths and exact publication artifacts; report names/counts, never matching secret lines. Preserve legacy private-directory ignore/rejection rules.
- **If skipped:** Private configuration or credentials can become permanent public history; the advertised zero-leak gate is false assurance.

### 4. Missing schema validation discards valid data and accepts malformed nodes — High

**Locations:** `src/models.py:43–51`; `src/fetcher.py:40–64`; `src/parsers.py:192–197,438–444,490–591`; `src/discovery/state_manager.py:59–78`.

- **What this is:** Sources and outbounds cross JSON/type boundaries into the pipeline.
- **Problem:** `enabled:"false"` is truthy; string tags split into characters; `tags:null` truncates later registry entries. A bad `outbounds` root, list-valued network, infinite VMess port, scalar JSON, and out-of-range JSON ports either lose valid peers, crash later, or are accepted. Incorrect persisted-state types are merged unchecked.
- **Fix:** Validate container/field types, bounded ports and required protocol fields. Reject bad entries individually with safe counts; treat unreadable existing registries/state as fatal rather than empty successful input.
- **If skipped:** One malformed upstream entry can discard useful source data or interrupt publication.

### 5. Publication can destroy a good snapshot and still report success — High

**Locations:** `src/main.py:159–224`; `src/categorizer.py:24–46,207–264`; `src/discovery/agent.py:339–364,395–404`.

- **What this is:** Rendering writes directly over currently published files.
- **Problem:** Empty loading/fetching replaced a previous feed and returned zero. Scalar JSON fails after 20 files were written; a failed second write leaves new plain beside old Base64. Corrupt discovered-source JSON can be rewritten empty. Country pairs disappear from counts but remain stale on disk.
- **Fix:** Validate/render into owned staging storage before replacing outputs; refuse a refresh with no usable input; propagate critical write failures. Reconcile only publisher-owned obsolete paths. Publish complete output sets in one Git commit and preserve previous local data on failure.
- **If skipped:** An outage, malformed source, or disk failure can publish empty/mixed generations with a green result.

### 6. Fingerprints merge different authenticated connections — High

**Locations:** `src/models.py:96–122`; `src/parsers.py:346–348`; `src/dedup.py:12–17`.

- **What this is:** A fingerprint identifies connections that may be deduplicated.
- **Problem:** Changes to `fp`, ALPN, verification flags, headers, encryption, plugins, or VMess alterId can hash identically. Password whitespace and trailing path slashes are stripped. These are omitted-field collisions, not cryptographic hash collisions.
- **Fix:** Version fingerprints over the complete supported connection, retaining unknown meaningful parameters conservatively. Normalize proven aliases only; presentation/provenance/evidence are excluded. Preserve passwords, paths, case-sensitive tokens, ordering and meaningful blanks.
- **If skipped:** Working alternatives and protocol credentials can be merged into the wrong connection. Old fingerprints cannot safely certify future manual results.

### 7. The model and serialized URI can disagree or inject extra lines — High

**Locations:** `src/dedup.py:30–65`; `src/models.py:213–273`; `src/parsers.py:85,463`.

- **What this is:** Raw URI and parsed fields are competing representations of the same connection.
- **Problem:** A deduplicated model can contain `fp=chrome` while its serialized raw URI uses `fp=unsafe`. Unescaped `#`/`?` passwords corrupt parsing; a JSON password containing a newline can emit a second URI line. Unescaped formatted remarks are truncated at the first space on re-ingestion.
- **Fix:** Select one complete canonical representative and merge metadata only. Serialize supported fields, percent-encode user-info/fragments correctly, preserve structured TUIC authentication, and reject dangerous host/control-character boundaries.
- **If skipped:** Categorization/probes test one configuration while subscribers receive another.

### 8. JSON conversion is lossy and executable upstream JSON is unsanitized — High

**Locations:** `src/parsers.py:512–589`; `src/categorizer.py:207–213`; `README.md:62`.

- **What this is:** Full Xray configurations are converted into independent subscription URIs and republished.
- **Problem:** Trojan SNI/path/Host and VMess user encryption/alterId are lost; only the first user is extracted. Shadowsocks uses an unsupported internal scheme. TLS/header/chaining fields are dropped. Arbitrary full configurations—including a public unauthenticated inbound—are exported unchanged as recommended serverless fragment JSON.
- **Fix:** Extract losslessly representable independent outbounds consistently; isolate unsupported/dependent entries with rejection counts. Normalize `shadowsocks` to `ss`. Stop distributing arbitrary executable configs; retire the executable feed safely.
- **If skipped:** Nodes are unusable or misleading; running a recommended full config can expose a listener or unwanted direct route.

### 9. Manufactured bypass variants break endpoint authentication — High; remove

**Locations:** `src/transform_pattn.py:68–73,103–190`; `src/main.py:180–189`.

- **What this is:** The generator replaces arbitrary servers with fixed Cloudflare or localhost endpoints.
- **Problem:** Reality keys/short IDs/flow are removed, TLS is invented, ports and distinct SNI/Host values are replaced. The localhost `127.0.0.1:40443` variant has no provisioned service. Existing tests assert these field changes rather than a usable connection.
- **Fix:** Remove destructive synthesis and its calls/flags/tests. Preserve externally supplied special parameters and source attribution; do not invent endpoint conversions or local client dependencies.
- **If skipped:** Large special feeds contain manufactured configurations that cannot represent their authenticated upstreams. This removal is a deliberate loss of invalid output, not a request for a new relay service.

### 10. Verification fails open and can test the wrong configuration — High

**Locations:** `src/categorizer.py:65–87`; `src/models.py:155–156`; `src/healthcheck.py:57–64,104–214,217–284,375–434,511–589`.

- **What this is:** A single boolean is used for source assertions, TCP/TLS reachability, and proxied traffic.
- **Problem:** Zero successful probes is treated as disabled testing and restores upstream-labelled nodes. Missing requested Xray silently falls back to sockets. Legacy SOCKS urllib cannot handle SOCKS and falls back anyway. QUIC is tested with unrelated TCP; captive-page 200 passes; bypass parameters are stripped; ordinary Trojan can lose implicit TLS and VMess gRPC its service. Stale passes remain outside a later budget.
- **Fix:** Separate evidence kinds and timestamps. Socket reachability is not authenticated proxy verification. Manual testing must fail closed on infrastructure problems, reject unsupported conversion, validate HTTPS/expected responses, clear untested statuses, and filter unsupported nodes before selecting its budget.
- **If skipped:** Official-looking results may certify wrong credentials, a different connection, a captive portal, or no test at all.

### 11. Top/verified ranking excludes faster healthy nodes — Medium

**Locations:** `src/categorizer.py:82–113`.

- **What this is:** Verified and Top 100 feeds promise latency-based selection.
- **Problem:** Source provenance sorts before observed latency. One 10 ms independently passing node is excluded by 100 upstream-labelled 300 ms peers; failed upstream nodes can remain candidates.
- **Fix:** Select the appropriate evidence-qualified pool first and sort by observed latency with deterministic tie-breaking. Describe the metric as connection/probe latency, not sustained throughput.
- **If skipped:** The fastest-feed promise remains false and failed nodes retain preferential treatment.

### 12. Discovery request, time, and channel caps are not hard caps — High

**Locations:** `src/discovery/nemotron_client.py:127–178`; `src/discovery/agent.py:407–460,539–553`.

- **What this is:** The daily engine advertises bounded API use and elapsed time.
- **Problem:** One granted logical call can make 12 retry/fallback attempts; a run at 499 advances to 511. A direct retry is undercounted. Expansion, reads and sleeps outlive deadlines; extended mode ignores channel limits.
- **Fix:** Share an attempt budget and monotonic deadline through every transport/retry operation; charge before each actual attempt and clamp timeouts/sleeps. Enforce all advertised bounds in both modes; validate invalid arguments before work.
- **If skipped:** Spend, job duration and candidate work cannot be predicted from supplied limits.

### 13. Checkpoint and queue handling loses unfinished work — High

**Locations:** `src/discovery/agent.py:395–404,460–518`; `src/discovery/state_manager.py:59–78,158–166`; `src/main.py:147–151`.

- **What this is:** Persistent discovery state resumes future batches.
- **Problem:** Candidates are dequeued before completion; budget exhaustion left four candidates neither queued nor visited. Save errors are swallowed, registries are non-atomic, and discovery-only failure returns zero. URLs marked visited before successful fetch may never be retried.
- **Fix:** Acknowledge completed candidates only or restore the unprocessed suffix on every exit. Validate/preserve checkpoints, atomically replace owned files, and propagate persistence/total-run failures. Keep retryable failures distinct from completed or permanently rejected sources.
- **If skipped:** Discovery silently loses work and healthy temporary failures become permanent exclusions.

### 14. Revisit/deep-mining logic reports yield it does not retain — Medium

**Locations:** `src/discovery/agent.py:283–310,487–617`; `src/discovery/search_engine.py:53–56,137–147`; `src/discovery/state_manager.py:31–38`.

- **What this is:** Discovery revisits Telegram pages, images and linked feeds.
- **Problem:** Dead sources remain enabled; deep-page configs are counted but later unavailable to shallow aggregation. Pagination wrap refetches the latest page; unchanged OCR images repeat API use. Handles are insufficiently validated, URL queries truncate, and repeated README links can be probed under different source IDs.
- **Fix:** Reconcile confirmed source state; retain bounded deep-mined public configs/page sources; persist successful image/content identity; normalize complete handles/URLs; use one source-URL set. Preserve retries for temporary failures.
- **If skipped:** Leaderboard counts overstate delivered configurations and cycles spend effort on repeated or unusable work.

### 15. Tests can spend ambient credentials and modify existing fixture files — High

**Locations:** `tests/test_discovery.py:84–122,225–255,343–366`; `tests/test_pipeline.py:247–277`; workflow substring tests in `tests/test_scripts.py`.

- **What this is:** The regression suite is supposed to establish safe behavior offline.
- **Problem:** A default discovery client can call NVIDIA when a real ambient key exists. Fixed repository fixture paths may overwrite/remove existing files. Mocked retry/core tests omit real budget and schema behavior; substring checks do not prove workflows run safely.
- **Fix:** Isolate all fixtures in temporary storage, clear/inject credentials, and reject unexpected external networking/subprocesses in tests. Add focused boundary regressions, not a coverage-number target. Remove client-specific tests rather than preserving obsolete features.
- **If skipped:** Running tests can cost money or damage user work, and green CI continues to approve defects it never exercises.

### 16. GitHub writers can fail or execute mutable inputs while looking successful — High

**Locations:** `.github/workflows/update-subs.yml:21–73,104–140`; `.github/workflows/daily-deep-discovery.yml:21–72,109–142`; `.github/workflows/ci.yml:4–13,30`.

- **What this is:** Scheduled jobs fetch, render, stage and push public artifacts.
- **Problem:** Rebase failures are ignored and exhausted push loops can exit successfully. Broad stages and `[skip ci]` bypass claimed gates. Mutable latest-core downloads are unused; action tags are movable. Full history is fetched even for test-only CI.
- **Fix:** Preserve shared writer concurrency, fail on unresolved rebase/exhausted pushes, validate exact final staged paths, remove unused core downloads, pin remaining actions, minimize write-token exposure and use shallow CI checkout. Never silently discard human commits.
- **If skipped:** A green refresh may not be published, or an executable supply-chain change may run with unnecessary write authority.

### 17. Documentation regeneration restores errors and fabricates freshness — High

**Locations:** `src/categorizer.py:261–264,337–399,379–805`; `README.md:7`; `STATUS.md:3`.

- **What this is:** A complete README is duplicated as a Python string and overwritten.
- **Problem:** Direct corrections/rebranding disappear on regeneration. README reports 13:01 freshness for a 06:56 snapshot. Custom output directories produce zero core counts and wrong `/subs/` links.
- **Fix:** Maintain prose in Markdown; update uniquely marked generated sections only. Derive counts, paths and timestamps from the same emitted snapshot; refuse malformed markers and avoid inventing public links for external output directories.
- **If skipped:** Every scheduled run can reintroduce misleading claims and old branding.

### 18. Geography, CDN, compatibility and performance claims exceed evidence — Medium

**Locations:** `src/parsers.py:88–120`; `src/models.py:138–150`; `src/parsers.py:447–472`; `README.md:5,40–62,150–154,216–248,299–305,339–393`.

- **What this is:** Selection heuristics are presented as measured engineering guarantees.
- **Problem:** North Korea becomes KR; explicit `[IN]` is missed; SNI TLD becomes endpoint location. Any WS transport can be labelled CDN; tags become domestic optimization/blackout capability. Href-only and mixed encoded configs are skipped. URI feeds are confused with native Clash/sing-box config formats, and latency is sold as throughput.
- **Fix:** Correct extraction/classification cases and describe source-claimed geography/transport tags honestly. Document actual output formats and conversion needs; remove universal DPI/blackout, no-loss streaming and 100%-reliability claims.
- **If skipped:** Consumers select unsuitable feeds or fail imports using instructions that cannot be substantiated.

### 19. Client interaction violates the requested product boundary — Blocker; remove

**Locations:** `scripts/sync_to_pattn.py:1–390`; `scripts/check_from_iran.ps1:27,47`; `scripts/test_iran_reachability.py:55–123,281–287`; `scripts/trigger_update.ps1:39–40,76–83`; connected script tests/docs.

- **What this is:** Supported commands locate desktop installations and mutate subscription databases.
- **Problem:** The Iran wrapper enables sync by default. Synthetic DB checks also reproduced WAL-incomplete backups, same-second backup overwrite, private-feed disabling, transaction retention, concurrent duplication, dry-run journal mutation and malformed file URLs.
- **Fix:** Delete the synchronizer and all client discovery/sync/registration flags, callers, fixtures, wrappers and documentation. Do not repair these DB bugs or add migration code touching actual client data. Use an explicitly supplied standalone test core only.
- **If skipped:** NavaRoute remains a client manager and can touch private desktop data despite the explicit prohibition.

### 20. Official owner-only manual Iran publication is absent — Blocker; new capability

**Locations:** `scripts/test_iran_reachability.py:133–289`; no current owner-results publication workflow.

- **What this is:** Local results currently stay in ignored `local_subs/` without an official upload/provenance contract.
- **Problem:** No authentication, immutable approved input, owner publication gate or tested timestamp exists. Local/private and unofficial feeds are accepted; failed downloads fall back locally. Diagnostics can print proxy credentials; zero work/infrastructure failure can replace results and return success.
- **Fix:** Replace this flow with an owner-authenticated manual standalone command using only one pinned official public snapshot. Publish an allowlisted, versioned, non-secret result manifest and public-only subscriptions through an owner-gated **manual-only** workflow. Never run Iran testing in daily/six-hour jobs; distinguish upload acceptance from confirmed publication.
- **If skipped:** Uploading existing local outputs introduces a new privacy risk and cannot substantiate owner-certified Iran results.

**Enforcement limits:** Public source cannot prevent modified local copies being executed. GitHub access controls and the trusted publication workflow govern official acceptance; an editable owner field does not. Physical Iranian origin is owner-attested, not cryptographically proven. Network peers can see the connection's source IP even when that IP is excluded from published evidence.

## Documentation versus implementation ledger

Correcting README alone is insufficient: its Python generator restores these claims.

| Claim | Evidence/current behavior | Required wording or action |
|---|---|---|
| Enterprise-grade, seamless all-client import | No corresponding operational/import evidence | Describe a public subscription generator and explicit limits. |
| Verified healthy | Upstream assertion, socket success and zero-pass fallback | Separate claimed, reachable and owner-tested evidence. |
| Top 100 lowest latency | Provenance sorted first; failures eligible | Fix ranking and define observed latency. |
| Exactly 500 balanced nodes | Round-robin implemented, smaller inputs possible | Say up to 500. |
| Native Clash Meta/sing-box support | Emits URI feeds, not corresponding core configs | Distinguish GUI imports from core formats and conversion. |
| `fm=1` / randomized ciphers | JSON-valued `fm`; fixed cipher constant | Remove synthesis claims; document supplied parameters only. |
| Iranian routing/low hops optimized | Tags and predicates, no hop/routing measurement | Source-claimed Iran-related category. |
| CDN WS/gRPC | Any qualifying transport | Transport category, CDN only when independently evidenced. |
| Whitelist works during national blackout | Tag selection, no blackout tests | No guaranteed blackout reachability. |
| Safe ready-to-import fragment outbounds | Arbitrary list of full configs; current artifact empty | Retire unvalidated executable feed. |
| Async/non-blocking ingestion | Thread pool with blocking sockets | Concurrent threaded fetching/probing. |
| >90% deduplication | 118,476 raw / 62,258 output ≈47.5% reduction including synthesis | Report actual snapshot counts, not a universal percentage. |
| SNI inspection completely bypassed | No carrier evidence; required settings stripped | Conditional external-core behavior, not guarantee. |
| Maintained IPv4 tunnel with IPv6 OOB | Fixed endpoint replacement; no tunnel management | Delete manufactured-tunnel claim. |
| Multiple SNI alternatives | Same extracted domain used | Remove claim. |
| CI socket and Xray tests | Core downloaded but `--use-xray` omitted | Socket-only refresh; owner manual core testing separately. |
| 500 requests / 90 minutes | Attempts/deadlines can overrun; snapshot used 23 | Hard budgets after fix; achieved counts separately. |
| Dead-channel filtering | Existing dead entries remain enabled | Reconcile source state and report retryable outcomes. |
| Deep-mined yield reaches subscriptions | Older-page configs counted but not retained | Retain bounded public evidence before counting delivered yield. |
| Nemotron-only OCR attribution | Default Kimi model; Nemotron fallback | Record actual models/attempts, with unverified live OCR accuracy. |
| 100% reliability | Swallowed failure and best-effort schedules | Remove guarantee. |
| Every push/PR scanned | Path/branch filters and skip-ci commits | Broaden security checks or state actual coverage. |
| 65 tests on 3.10/3.12 | Count/matrix observed correct | Keep fact; do not equate it with live interoperability. |
| Verified no VPN masking | Proxy heuristics cannot prove TUN/router/physical origin | No client inspection; owner-attested direct test context. |
| Native DB integration / auto-registration | Real desktop mutation | Remove completely. |
| Automatically locates client Xray | Hardcoded client paths | Require explicit standalone binary. |
| Deterministic reproducibility | Live feeds, optional httpx, completion order, latest binaries/time | Standard-library compatible; deterministic offline rendering after fix. |
| Zero leaks | Current inventory clean; controls incomplete | Describe measured controls and residual privacy limits. |
| Fresh README snapshot | 13:01 vs 06:56 artifacts | Derive one generation timestamp from emitted data. |
| Realtime discovery leaderboard | Checkpoint-generated snapshot | Say snapshot and name metric precisely. |
| 49 active discovered feeds | Active metadata entries vs 62 enabled registry sources | Distinguish metrics. |
| Active source = green table row | HTTP success may yield zero nodes | Separate fetch success from usable source yield. |
| Contribution source schema validation | Source-count test fixed at 60; weak runtime types | Validate schema/unique IDs, not permanent count. |
| Current owner-certified Iran results | No official upload/provenance yet | Add explicit manual origin, source digest and test date. |

### Snapshot checks that do match

- 60 curated plus 62 discovered enabled sources.
- 62,258 aggregate nodes, 918 verified-labelled nodes, Top 100 =100, Lite =500, special =1,361; current README counts match current files.
- 120 plain/Base64 pairs agree. Agreement proves encoding correspondence, not functional connections.
- Discovery snapshot: 243 visited, 231 queued, 49 active metadata entries, 9,198 recorded yield, 23 last/cumulative API attempts. Those numbers do not demonstrate successful 500-request operation.
- Country staleness is real, not merely hypothetical: Georgia has six retained entries, four absent from the current aggregate, with only two current Georgian nodes.

## Git/GitHub assessment

Read-only observations at audit time:

| Item | Observation |
|---|---|
| Authenticated user | `mani-moh` |
| Repository access | Admin, maintain, push and pull available |
| Target `mani-moh/navaroute` | HTTP 404 to the authenticated owner; appears available, not reserved |
| Default branch | `master`; preserve unless separately requested |
| Protection/rulesets | `master` unprotected; no rulesets returned |
| Other write access | Owner is the only enumerated collaborator; no deploy keys. GitHub App installation access not fully enumerable. |
| Native scanning/push protection | Enabled; zero alerts returned |
| Actions default token | Read; publication workflows explicitly request contents-write |
| Allowed actions | All; pin retained executable actions |
| Current tracked size | 157.63 MiB; subscriptions account for 157.15 MiB |
| Packed history | About 76.6 MiB after only 22 commits |

Recent updater run `42771031028` completed 06:56:37 after creation at 06:48:53, almost 49 minutes after the nominal schedule. Daily discovery run `42766683157` was manually dispatched, not evidence of a sustained daily schedule. One day of repository history is not a reliability record.

**Changes needed:** safe writer failure handling, explicit staged-path validation, shallow test checkout, quieter generated/Base64 diffs, authoritative owner-result publication controls, and final metadata/remote/link updates. Do not reset/rebase away human changes or force-push. Do not rewrite generated history or move the local directory merely as part of a rename.

Strict owner-result isolation needs a separate protected official-evidence reference. The same repository-write Actions token is not path-scoped; merely keeping Iran code out of cron does not prevent a compromised scheduled writer from changing evidence on an unprotected branch. The proposed design uses protected `iran-results`, a manual result PR, and an owner-authenticated approval/merge, while keeping ordinary schedules on `master`.

The protected boundary must include the standalone tester, all imported modules and executed publication workflow—not just JSON/text results. Otherwise a scheduled writer could alter code that later receives owner credentials. Review protection enforces owner authorization of evidence/code; it does not inherently forbid another writer merging a result that the owner has already approved.

GitHub repo redirects do not repair renamed file paths and not every subscription consumer follows raw redirects. Keep `master`, update active links, and use narrowly documented legacy file mirrors where compatible. Never update client databases to migrate URLs.

## Complete rebrand/removal surface

**Rename active project identity to NavaRoute / `navaroute`:** maintained and generated docs; repo description/topics/badges/clone/raw links; repo-slug fallback; CLI/service/user-agent/report text; package docstrings; workflow names/shared concurrency groups; retained symbols/tags/tests/fixtures; category filenames and every plain/Base64 profile title; VMess JSON remarks and Base64 subscriptions.

Current artifacts include 241 files: 120 plain, 120 Base64 and one JSON. Plain files contain encoded profile titles. Repeated generated remarks include 729 F&F, 834 IPv6-OOB and 744 SNI-Spoof occurrences across feeds; these are occurrences, not unique connections. Do not search-and-replace encoded credentials or globally rename legitimate upstream nodes.

**Delete rather than rename:** `scripts/sync_to_pattn.py`; client auto-detection and sync calls; sync/DB/registration flags and fixtures; desktop-path and registry/proxy/client-port inspection; local client-management branches in wrappers; destructive synthesis and its tests; arbitrary executable-config publication. Preserve private-file ignores without reading or migrating existing private files.

**Preserve accurate external identities:** Patterniha's PattN GUI fork, `patterniha/Xray-core`, upstream source URLs/names and protocol-specific extension keys. They are not NavaRoute-owned software. Rename historical `local_pattn` provenance only to an honest source description, not a client integration claim.

**Historical exceptions:** previous Git commits and ignored local handoff files are not current product branding. No hidden handoff/plan files are currently tracked. Historical private-module/example files are reachable in older commits; no history rewrite is authorized. MIT copyright remains `mani-moh`.

## Repair priority and exit criteria

1. Isolate work and freeze the evidence baseline. Prevent unsafe tests/API spending.
2. Remove client interaction and destructive synthesis. Fix authenticated/public-input transport.
3. Establish complete connection identity, lossless serialization/conversion and validation.
4. Make publication/checkpoints fail-safe and discovery budgets/retries real.
5. Add owner-authenticated standalone testing and manual-only, public-subset-validated result publication.
6. Consolidate docs/metadata/encoded branding and improve GitHub writer/security gates.
7. Run independent requirements/security and connected-code reviews, offline full verification, an artifact/branding diff, then staged release/rename.

Every concrete case above requires a failing regression, a smallest root-cause fix or explicit deletion, and fresh verification. No finding is closed by changing a README adjective, raising a timeout, or adding a wrapper around the same unsafe code.

**Verdict:** Keep the small batch architecture; fix trust/identity/publication first and delete prohibited client machinery.

**Lean:** at least 583 production lines removable from the synchronizer (390) and invalid synthesizer (193), before related wrappers/tests and duplicated README prose; zero new runtime dependencies justified. These are potential removals, not a measured final diff.

**Not checked:** live NVIDIA/GitHub model responses, OCR/QR quality, actual standalone-core schema/runtime interoperability, real Iranian carrier/DPI behavior, client imports/raw redirect behavior, private/local data, legal provenance of every upstream feed, or long-term service reliability. Security cases are demonstrated failure paths, not evidence of an existing breach.
