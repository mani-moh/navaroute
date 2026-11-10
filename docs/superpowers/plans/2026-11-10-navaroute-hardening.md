# NavaRoute Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Independent tasks may run in parallel only with disjoint file ownership and completed shared dependencies.

**Goal:** Deliver a safe, subscription-only NavaRoute repository, with reliable scheduled generation and owner-authorized standalone manual Iran results.

**Architecture:** Retain the standard-library batch pipeline and fix shared transport/connection contracts before their callers. Remove client machinery and destructive synthesis. Keep owner tester/workflow/imports and evidence on protected `iran-results`, separately from scheduled output on `master`.

**Tech Stack:** Python 3.10+, standard library, unittest; Git/GitHub CLI; supplied standalone Xray; GitHub Actions. No new runtime packages.

**Spec:** `docs/superpowers/specs/2026-11-10-navaroute-hardening-design.md`

**Audit:** `docs/audits/2026-11-10-repository-audit.md`

**Execution method:** User approved the multi-agent pipeline and implementation. Plan review precedes product edits.

## Global Constraints

- Product: **NavaRoute**; canonical official repository: **`mani-moh/navaroute`**; owner: **`mani-moh`**.
- Preserve **`master`**, daily discovery and six-hour aggregation; no history rewrite/force-push/local directory move.
- Remove **all app/client interaction**; do not read/migrate ignored local data or real client files.
- Iran testing runs only on the owner's machine, **only on explicit command**; never a schedule/refresh/discovery/retest.
- Supported tester, all imports and publication execution come from protected **`iran-results`**, never scheduled-writable code.
- Source inputs for publishable Iran evidence come from one immutable official public snapshot; no local fallback/private input.
- Official publication requires owner authorization/review; public-code execution and physical origin cannot be guaranteed.
- Preserve full connection semantics or reject the entry; unsupported fields never disappear into a successful probe.
- No new runtime dependency, backend, client manager, automatic core download or invented local relay.
- Existing private files, Git history and upstream copyrights/identities are not project rebranding targets.
- Do not run live Iran/Xray traffic from the agent session; owner acceptance invokes the documented command.

## Review Focus

- DNS/proxy differences: private/rebound destinations must not receive a request, and explicit proxy failure must not leak direct traffic (Task 2).
- Meaningful URI blanks/lists/custom identifiers: preserve valid connection semantics rather than silently normalizing (Task 3).
- Cancellation after dequeue or before checkpoint replacement: unfinished work and previous state must remain recoverable (Task 4).
- Publication races/approval invalidation: a changed snapshot/PR cannot retain stale owner authorization or report unconfirmed publication (Tasks 5, 8).
- Historical generated snapshots: rebranding cannot fabricate fresh probes or convert ambiguous legacy fingerprints into owner evidence (Tasks 6, 7).

---

## File/ownership map

| Files | Responsibility / owner |
|---|---|
| `src/project.py` (new), `src/storage.py` (new) | Small shared identity constants/repo slug and atomic owned-file replacement; Task 1. |
| `tests/test_discovery.py` fixture setup | Credential/network-isolated temporary fixtures first; Task 1, then exclusively Task 4. |
| `src/network.py` (new), `src/fetcher.py`, `tests/test_network.py` (new) | Public/authenticated transport, cache containment, source loading/fetch failures; Task 2. |
| `src/models.py`, `src/parsers.py`, `src/dedup.py`, `tests/test_core.py` | Complete canonical connection and lossless ingestion/serialization; Task 3. |
| `src/main.py`, synthesis portions of `tests/test_pipeline.py` | Remove synthesizer/CLI/client-core switches and update shared callers; Task 3 then Task 6, never concurrently. |
| `src/transform_pattn.py` | Delete in Task 3, with all callers. |
| `src/discovery/*.py`, `tests/test_discovery.py` | Hard attempts/deadlines, durable queues/source state/retained yield; Task 4 after Tasks 1–3. |
| `src/healthcheck.py`, `scripts/test_iran_reachability.py`, `scripts/check_from_iran.ps1`, `tests/test_scripts.py`, health tests in `tests/test_pipeline.py` | Faithful owned-core probing and owner command; Task 5. |
| `scripts/sync_to_pattn.py`, `scripts/trigger_update.ps1` | Delete, with callers and obsolete tests, in Task 5. |
| `src/iran_results.py` (new), new manual publication/validation workflows, `tests/test_iran_results.py` (new) | Sanitized schema, public subset, manual PR and owner merge protocol; Task 5. |
| `src/categorizer.py`, publisher/main portions of `tests/test_pipeline.py` | Staged generation, evidence-qualified feeds, static-doc markers; Task 6. |
| Existing workflows, `.gitignore`, `.gitattributes`, `.env.example`, maintained docs, package docstrings and `subs/` | Security gates, identity/artifact migration; Task 7 after caller stabilization. |
| Protected branch setup, GitHub rename/metadata, remote release | Coordinator only, Task 8, after reviews and full verification. |

All public/private transport, atomic-file and identity interfaces below must be agreed before dispatch. Do not introduce overlapping helpers or let agents commit/reset/push each other's work.

### Task 1: Establish safe tests and shared small primitives

**Files:** create `src/project.py`, `src/storage.py`, `tests/test_project.py`, `tests/test_storage.py`; modify discovery fixture/environment setup in `tests/test_discovery.py` only.

**Interfaces:** `PROJECT_NAME="NavaRoute"`, `DEFAULT_REPOSITORY="mani-moh/navaroute"`, `OWNER_LOGIN="mani-moh"`, `DEFAULT_BRANCH="master"`; `detect_repo_slug() -> str` honors actual repo/environment for general generated links, not owner authorization. `atomic_write_text(path: Path, content: str) -> None` writes an owned sibling temporary file and replaces the target, propagating errors and cleaning its temporary file. It does not delete unrelated paths.

- [ ] Write `test_ambient_api_key_never_causes_network`, `test_fixtures_leave_existing_files_unchanged`, `test_slug_inputs_are_isolated`, `test_failed_replace_preserves_original`, and `test_atomic_writer_cleans_own_temp`. Assert zero transport calls with a synthetic ambient key; existing sentinel bytes unchanged; explicit env/remote cases; original bytes on replacement failure.
- [ ] Run `python -m unittest tests.test_project tests.test_storage tests.test_discovery -v`; record failing assertions before implementation.
- [ ] Move all discovery fixtures into `TemporaryDirectory`; inject unavailable/mock clients and prevent unexpected external requests. Implement only the shared constants/slug behavior and atomic writer above.
- [ ] Re-run the named tests and full offline suite; check fixture cleanup does not modify repo files.
- [ ] Coordinator commits verified Task 1 paths only: `test: isolate fixtures and establish safe shared primitives`.

### Task 2: Safe network/source boundary

**Files:** create `src/network.py`, `tests/test_network.py`; modify `src/fetcher.py`. Do not modify discovery callers yet.

**Consumes:** Task 1 constants/storage; SourceConfig remains its existing interface until Task 3.

**Produces:** `fetch_bytes(url: str, *, timeout: float=20.0, max_bytes: int, deadline: float|None=None, headers: dict[str,str]|None=None, data: bytes|None=None, method: str|None=None, authenticated: bool=False) -> tuple[int,bytes]`; `validate_public_url(url: str) -> None`; `connect_public(address: tuple[str,int], timeout: float|None=None, source_address=None) -> socket.socket`. Bad trust-boundary inputs raise `ValueError`; transport failures raise an explicit error and are not silently routed another way. Keep `_fetch_url(url, timeout) -> tuple[int,str]` as the fetcher wrapper and its public fetch APIs.

- [ ] Write negative tests for private/IPv6/mapped/private-DNS/rebound targets, authenticated cross-origin and HTTPS→HTTP redirects, invalid certificates, explicit upper/lowercase proxy failure, oversized/trickling responses, `data/../` and symlink escapes, non-2xx status, and malformed middle registry rows. Assert **no request** to rejected destinations, no forwarded Authorization/direct retry, bounded work and retained valid peers.
- [ ] Run `python -m unittest tests.test_network -v` and relevant fetcher tests; record red evidence.
- [ ] Use standard-library verified TLS, connected-address validation and redirect policy. Pin validated DNS addresses for connections while retaining host/SNI verification. Keep trusted explicit proxy use separate from untrusted destination validation. Use byte/total-time caps; cache containment uses resolved paths; registry errors are visible and fetch-source success requires a successful HTTP status.
- [ ] Remove optional-httpx divergent behavior and duplicate insecure fallback paths. Run all Task 2 tests, including socket-free resolver/handler mocks.
- [ ] Coordinator commits verified Task 2 paths only: `fix: enforce public authenticated bounded transport`.

### Task 3: Complete connection identity and remove synthesis

**Files:** modify `src/models.py`, `src/parsers.py`, `src/dedup.py`, `src/main.py`, `tests/test_core.py`, synthesis/core parts of `tests/test_pipeline.py`; delete `src/transform_pattn.py`. Update mechanical predicate callers in the publisher only if needed; Task 6 owns its later logic.

**Consumes:** Task 2 fetcher boundary; existing `SourceConfig`, `ProxyNode`, `parse_proxy_uri` and extraction return shapes.

**Produces:** `FINGERPRINT_VERSION=1`; `ProxyNode.semantic_fingerprint() -> str` over the complete canonical connection; `to_uri(include_formatted_remark=True) -> str`; `has_special_parameters() -> bool`, `has_web_transport() -> bool`, `is_iran_related() -> bool`. Existing parse/dedup signatures remain. Add explicit observation kind/time fields without conflating upstream trust. Raw URI is never serialization authority.

- [ ] Write non-collision tests for fp/ALPN/plugin/alterId/headers/encryption/blank/password/path variations, and round trips for encoded delimiters, Unicode, IPv6, structured TUIC auth, repeated-list settings and supported custom identifiers. Assert semantic changes remain distinct; presentation changes do not.
- [ ] Add mixed valid/invalid JSON/source tests, all supported per-user connection settings, normalized JSON Shadowsocks, malformed finite ports, unsupported chained JSON rejection, href-only HTML, and mixed plain/encoded lines. Assert valid peers survive and no output URI silently loses required semantics.
- [ ] Run `python -m unittest tests.test_core -v`; capture the intended failures before fixes.
- [ ] Make canonical parsed fields authoritative; preserve meaningful values/unknown settings conservatively, encode user-info/fragments, validate per-entry types, reject unsupported dependencies, and merge metadata across equal full connections only.
- [ ] Delete synthesis, its imports/invocations and `--pattn-variants`/`--use-xray` main switches. Main scheduled mode remains optional **socket** reachability. Remove tests asserting manufactured endpoints; verify no fake localhost or authentication replacement remains.
- [ ] Run core/caller tests and compileall; coordinator commits this complete caller-reaching change: `fix: preserve canonical connections and remove invalid synthesis`.

### Task 4: Bounded, recoverable discovery

**Files:** `src/discovery/agent.py`, `nemotron_client.py`, `search_engine.py`, `state_manager.py`, package docstring; `tests/test_discovery.py`. No other agent edits these files.

**Consumes:** Tasks 1–3 shared storage/identity/transport/parser contracts.

**Produces:** Same discovery APIs; `run_cycle` enforces max API attempts, monotonic time and channels in every mode. Transport-attempt accounting is shared with the client; all callers use Task 2 safe bounded requests. Persisted schema remains migration-compatible where valid, with corrupt types rejected rather than reset.

- [ ] Write real mocked retry-loop tests for one remaining attempt and for count 499/cap 500; zero/negative limits; expiry during expansion/read/backoff; proxy failure; and credential URL rejection before persistence. Assert attempts never exceed cap, no sleep/request exceeds remaining deadline, no secret string in output.
- [ ] Write checkpoint tests for failure after dequeue, corrupt registry/state, failed write/replace, timeout→success across cycles, active→confirmed-dead, pagination wrap `[100,90,...]`, deep retained config reaching aggregation, repeated unchanged OCR and duplicate README URLs. Assert unfinished suffix restored, old files preserved and recorded deliverable yield actually available.
- [ ] Run `python -m unittest tests.test_discovery -v`; record red cases.
- [ ] Implement exact per-attempt budget/deadline checks, acknowledgement/requeue, retryable outcomes, bounded successful image/source records, full handle/URL extraction, atomic persistence and source reconciliation. Treat model outputs as untrusted; propagate total/persistence failure to CLI/workflow instead of zero.
- [ ] Run discovery tests and full offline suite; coordinator commits: `fix: bound discovery and preserve resumable work`.

### Task 5: No-client, owner-only standalone testing and evidence

**Files:** `src/healthcheck.py`; replace retained Iran script/wrapper; create `src/iran_results.py`, `tests/test_iran_results.py`, manual publication/metadata validation workflows; update `tests/test_scripts.py` and health tests; delete `scripts/sync_to_pattn.py`, `scripts/trigger_update.ps1` and every remaining client caller/flag/fixture.

**Consumes:** Tasks 1–3 public transport/canonical fingerprints; Task 6 publisher must not be imported to mutate scheduled artifacts from this flow.

**Produces:** `build_xray_outbound(node) -> dict|None` is faithful or unsupported; batch/core probing distinguishes actual failure from infrastructure error and never socket-falls back in manual mode. Owner CLI requires `--xray-bin`, immutable official input and explicit publish/attestation intent. `validate_manifest(manifest: dict, nodes: list[ProxyNode], *, source_sha: str, source_digest: str) -> None`; `render_result_files(...) -> dict[str,str]` returns only `data/iran-verification.json`, `subs/iran-tested.txt`, and `subs/iran-tested_base64.txt` proposal content. No arbitrary URI field in submitted evidence.

**Manifest contract:** schema/fingerprint version 1; canonical repo; source commit and exact digest; UTC started/completed; owner-attested direct-Iran context; safe core version/hash; probe policy `https-204-v1`; positive rounds; bounded counts; unique fingerprint/status/finite latency observations. No raw credentials, environment/local path/IP/hostname/log fields. Unknown fields are rejected.

- [ ] Write failure-before-probe tests for anonymous/non-owner/expired auth, unofficial/mixed/failed input, missing supplied core, private/rebound destinations and invalid limits. Assert no SQLite/client path/PATH/registry/existing-service interaction; rejected legacy flags stay rejected.
- [ ] Write conversion/HTTPS tests for implicit Trojan TLS, VMess gRPC, supported options, unsupported fm/cs/dialMode/plugin/etc., malformed peer isolation, unsupported protocols before budget, reset of stale statuses, captive 200/invalid cert, core startup/cancellation cleanup and zero genuine work. Use owned local proxy mocks only, no external probes.
- [ ] Write manifest/publication tests for unknown fingerprints/fields, duplicates, negative/nonfinite latency, private sentinel values, oversized dispatch, failed/wrong-ref workflow, changed PR after local validation, repeated upload, and missing confirmed merge. Assert no false “published,” no stale approval reuse, and all emitted URI identities belong to the pinned snapshot.
- [ ] Run `python -m unittest tests.test_iran_results tests.test_scripts -v` and health tests; capture red evidence.
- [ ] Delete client machinery and implement standalone owned-core HTTPS probes with expected 204/certificate validation; mark unsupported configs untestable, not stripped. Require trusted protected tester checkout and `gh api user` owner before work; do not expose credentials to the child core.
- [ ] Implement `workflow_dispatch`-only protected-ref owner/triggering-actor checks. Trusted protected code validates data only, renders one proposal commit/PR to `iran-results`; local owner command matches exact PR files, approves and merges, then confirms official content. Never execute master/PR code or retest in workflow. Metadata validator may run automatically but does no traffic/core testing and uses protected trusted code only.
- [ ] Run all negative/privacy/cleanup/publication tests; coordinator commits: `feat: add owner-authorized standalone Iran evidence without client integration`.

### Task 6: Safe truthful subscription and documentation generation

**Files:** `src/categorizer.py`, `src/main.py`, publisher/main tests in `tests/test_pipeline.py`.

**Consumes:** Tasks 1–3 and 5 observation semantics. Uses no owner-evidence update path.

**Produces:** `publish_subscriptions(nodes, json_configs, stats, output_dir="subs", update_docs=True, *, healthcheck_ran: bool=False, snapshot_time: str|None=None) -> dict[str,int]`; counts are relative to `output_dir`. `snapshot_time` is the actual source/generation context, not a new test time. Main passes explicit execution state. Static README dynamic markers: `<!-- NAVAROUTE:STATS:START -->` / `<!-- NAVAROUTE:STATS:END -->`, each exactly once.

- [ ] Write tests for total fetch failure/malformed input, all probes failed vs disabled, latency-first ranking, no upstream fallback, unsupported UDP reachability, plain/Base64 identity, failed render/second write, stale owned country removal/unrelated sentinel retention, correct custom-output counts, external-output doc safety and missing/duplicate markers. Assert previous snapshot survives failure and unrelated prose/files unchanged.
- [ ] Write historical migration test: supplied old timestamp remains explicit; no owner test/evidence timestamp is invented. Assert no owner result paths are rendered/staged by normal publication.
- [ ] Run publisher/main tests and capture failures.
- [ ] Validate/render to owned staging first, then controlled replacement with failure recovery. Make reachable actual socket-pass only; Top 100 latency-first from it; verified is a clearly deprecated reachable mirror. Keep up-to-500 balance and six protocol feeds. Rename special category to supplied-parameter NavaRoute feed with explicit legacy mirror; retired executable JSON is `[]`.
- [ ] Remove the full embedded README prose. Update only unique marked generated facts; keep STATUS semantics truthful and one generation context. Remove invented subscription quotas/expiry headers instead of fabricating provider data. Never update protected owner evidence.
- [ ] Run publication/round-trip/failure tests and full suite; coordinator commits: `fix: publish complete truthful subscription generations`.

### Task 7: GitHub security, full rebrand and artifact migration

**Files:** existing workflows, `.gitignore`, `.gitattributes`, `.env.example`, README, architecture/contributing/status/discovery docs, package/service text, generated `subs/` and relevant workflow/rebrand tests.

**Consumes:** All stable prior contracts; no edits to owner tester logic without its owner.

- [ ] Write tests for tracked/staged private paths, safe scanner output, exact staging allowlist, push exhaustion/rebase failure, retained shared writer group, schedule-only intended jobs, no scheduled Iran/core download, repo environment rename, marked-doc regeneration and decoded profile/VMess branding. Assert generated/secret changes cannot skip the relevant gate and no matching credential is printed.
- [ ] Run these tests and capture red cases before fixes.
- [ ] Remove unused scheduled core downloads; pin retained Actions by verified commit, shallow-checkout test CI, narrow tokens/staging and fail on exhausted conflicts/push. Keep cron values unchanged. Security checks cover tracked/private/new staged files without displaying values; retain private legacy ignores.
- [ ] Rewrite maintained docs to the actual NavaRoute product, feed formats and standalone owner procedure; remove client setup/DB integration and unsupported guarantees. Update every active slug/name/user-agent/report/workflow symbol, templates and metadata. Preserve truthful Patterniha/source attribution and explicit old-path exceptions.
- [ ] Regenerate from a validated existing **public** snapshot or normal scheduled source generation; exclude identified old manufactured variants, do not touch private files, and never invent fresh probe evidence. Record migration input revision/time. Check every plain/Base64/profile title/VMess record, counts, stale countries and legacy mirrors. Remaining old-name matches must be classified as external attribution, historic audit or deliberate compatibility—not active identity.
- [ ] Run full offline suite, compileall, doc regeneration twice, artifact invariants and tracked-private-path scan; coordinator commits coherent security/docs/artifact changes separately where useful.

### Task 8: Independent review, protected boundary and release

**Files/settings:** final branch diff, finding-resolution ledger, GitHub repo metadata/remote and new protected `iran-results` branch only. No product edits without appropriate implementer/review ownership.

- [ ] Dispatch a fresh requirements/security reviewer with approved spec and complete diff; then a fresh connected-code `ponytail-review`. Verify each finding, fix root causes with red/green evidence and rerun impacted/full tests. Do not waive critical/high defects as cosmetic limits.
- [ ] Run fresh `python -m compileall -q src scripts tests` and offline `python -m unittest discover -s tests -v`; run pytest if installed, without making it a runtime dependency. Check `git diff --check`, public artifact schemas/round trips/counts, no-client imports/calls, secret/private inventory, marker stability, workflow triggers and both actor/ref checks. Record any unavailable real-core/schema checks honestly.
- [ ] Review logical commits and source baseline; no resets of user work. Recheck authenticated owner, target availability, collaborators/apps and branch-protection capabilities. Verify a create/protect/PR-review policy is possible before promising isolation.
- [ ] Rename repo to `navaroute`, update origin/description/topics and active links, retain master. Observe remote CI and raw URLs; no old-name reuse/history rewrite.
- [ ] Initialize `iran-results` with the reviewed tester, all imports/workflow/validator, CODEOWNERS and empty **untested** evidence paths; protect with owner code-owner review, stale-review dismissal/latest-push approval, enforced no automation bypass. Keep Actions approval disabled. Verify effective settings and scheduled-token lack of direct/unreviewed update authority; inability to enforce is a blocker, not a silent fallback.
- [ ] Ensure protected manual workflow registration/ref and local owner's separate trusted checkout procedure work without executing master code. Tests validate failures without real Iran traffic. Leave real input/probe evidence unclaimed until owner command actually runs.
- [ ] Publish resolution ledger/post-hardening assessment with evidence, final diff and residual live-test limits. Provide the one explicit owner command and explain accepted dispatch vs PR vs official merged result. Run `finishing-a-development-branch` for integration decisions and leave original ignored local data untouched.

## Dependency/dispatch order

Task 1 first. Tasks 2 and 3 can run concurrently with disjoint tests/files after interface freeze. Task 4 and Task 5 can run concurrently after Tasks 1–3; no shared health/script/discovery files. Task 6 owns publisher/main integration after their dependencies. Task 7 follows; Task 8 is coordinator/review/release only. Fresh review gates occur for each coherent deliverable, not only at the end.

## Self-review

- Spec sections 1–3 → constraints/removals in Tasks 3/5/7; 4.1–4.4 → Tasks 1–4; 4.5 → Task 6; 4.6–4.7 → Tasks 5/8; identity/migration → Task 7/8; pipeline/definition of done → Task 8.
- All five Review Focus inputs have explicit negative tests in their owning tasks.
- Interface names/observation/version/publication paths above are the integration contract. Any justified adjustment is recorded before callers are dispatched.
- No live Iranian traffic, client-data migration, dependency install, historical rewrite or false successful publication is hidden in a task.
- Plan is a task/contract checklist, not a transcript of implementation bodies. Each deliverable requires failing regression, minimal change, fresh passing check, review and coordinator-owned commit.
