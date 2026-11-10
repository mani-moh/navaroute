# NavaRoute staged-hardening design

**Status:** Approved by the user on 2026-11-10: “Approve and implement.” Implementation follows the reviewed file-level plan and verification gates.

**Date:** 2026-11-10

**Evidence:** [Professional repository audit](../../audits/2026-11-10-repository-audit.md)

## 1. Intent and agreed requirements

Make this repository a dependable, maintainable **public subscription producer**, independently named **NavaRoute**, with GitHub repository **`mani-moh/navaroute`**.

The user selected **staged hardening** rather than a breaking cleanup. They explicitly require:

- Remove **all app/client interaction**: no SQLite/client DB work, installation discovery, registry inspection, existing client-process/port probing, automatic registration, or client configuration mutation.
- Continue **daily discovery** and **six-hour aggregation** on GitHub.
- Allow **only the owner**, `mani-moh`, to invoke the supported standalone Iran configuration test.
- Run Iran probes on the owner's machine, **only on explicit command**, and upload official public results to the repo.
- Never run that test through a schedule, ordinary refresh, discovery cycle or automatic retest.
- Update all active project branding, including encoded/generated outputs, while retaining truthful external upstream attribution.

The audit shows a functioning small batch structure but serious transport, identity, verification, persistence, publication and documentation defects. The goal is to fix those defects, not turn the repo into a service or client manager.

## 2. Approaches considered

1. **Staged hardening and rebrand — selected.** Keep useful modules and public paths where safe; fix root causes, remove prohibited/invalid machinery, migrate identity and validate the complete result.
2. **Breaking cleanup — not selected.** Smaller initial target, but unnecessary subscriber/interface disruption.

A ground-up framework rewrite was rejected as unjustified. It adds risk without inherently fixing trust boundaries. Compatibility does not justify retaining client interactions, false verification, unsafe executable JSON or manufactured broken endpoints.

## 3. Goals, non-goals and anti-goals

### Goals

- Complete, conservative connection identity and parse/serialize fidelity.
- Safe public-input handling; authenticated TLS; bounded downloads and actual discovery budgets.
- Clear separation of upstream assertions, socket observations and owner-run end-to-end evidence.
- Validated complete output generations, honest counts/timestamps and failure exit codes.
- Owner-only official result publication with public snapshot provenance and no private diagnostics.
- One source of maintained documentation prose and exhaustive active branding migration.
- Small, targeted regressions and independent review for every high-risk change.

### Non-goals

- No UI, database, backend service, distributed queue, client manager or additional runtime dependency.
- No new local relay, SNI-spoof listener, arbitrary fronting template or fabricated bypass capability.
- No automatic core download/client discovery.
- No private source/config upload, raw logs, personal network-IP collection or environmental telemetry.
- No historical Git rewrite, force-push, default-branch rename or local directory move.
- No claim that code can prevent people running modified public copies, prove physical Iranian origin, or guarantee future proxy uptime.

### Anti-goals

- A successful-looking pipeline that published nothing, lost a previous snapshot or certified untested connections.
- A rebrand that restores old names on the next generation, changes upstream ownership claims, or mutates client data.
- Silently dropping required protocol settings to make an import or test pass.

## 4. Target architecture

```text
daily discovery ── validated public source/state checkpoint ──┐
curated registry ────────────────────────────────────────────┤
                                                          ▼
6-hour refresh → safe fetch → validated/lossless parse → canonical dedup
              → optional socket reachability observations
              → staged URI subscriptions + bounded generated docs → Git commit

owner explicit local command → GitHub owner authentication
                             → immutable official public subscription snapshot
                             → explicit standalone test core, owned child processes
                             → strict end-to-end probes, sanitized result evidence
                             → manual owner-gated GitHub publication workflow
                             → owner-approved merge to protected `iran-results`
                             → validated evidence + public-only tested subscriptions
```

No edge enters a desktop app. The manual publication workflow validates/renders already obtained evidence; it **does not run Iran probes**.

### 4.1 Remove invalid/prohibited surfaces

Delete the database synchronizer and all its callers, flags, tests and instructions. Remove installation/PATH discovery, registry/client-port probing, auto-registration and local client-management wrapper branches. Prefer deleting obsolete wrappers over keeping aliases that still imply client integration.

Remove destructive variant synthesis entirely. Keep actual upstream-supplied special parameters in the canonical model and label them as supplied, not generated or confirmed bypasses. Retire raw executable JSON export; an explicitly deprecated empty JSON artifact may retain a legacy URL, but never unvalidated inbounds/routing.

Existing ignored private/local files are untouched. Keep their ignore/rejection rules as privacy defenses.

### 4.2 Connection contract

The validated canonical connection is authoritative for identity, serialization, categories and testing. Raw input must not override it. Dedup merges source/evidence metadata only across identical complete connections.

Version the fingerprint; retain all meaningful supported/unknown connection settings conservatively. Exclude remarks and provenance. Normalize aliases/defaults only when equivalence is established. Preserve passwords, paths, slashes, case-sensitive fields, blank values and ordered lists. Conflicting repeated parameters are rejected instead of silently taking the last value; supported list semantics remain representable.

Required invariant:

> Parse → serialize → parse preserves every supported connection field and the versioned fingerprint.

Do not impose a UUID-only global rule where supported cores permit custom identifiers. Reject empty/invalid required credentials according to a stated protocol-support contract. JSON outbounds must be independently representable without losing TLS, authentication, plugins, headers or routing/dialer dependencies; unsupported entries get bounded rejection counts, not misleading URIs.

### 4.3 Public/authenticated network boundaries

Use existing standard-library mechanisms consistently; remove optional-httpx behavior differences unless a genuine supported need is found.

- Certificate-verified TLS and explicit allowed API origins.
- Authenticated requests cannot redirect to another origin or HTTP.
- Public source destinations reject loopback/private/link-local/reserved targets, including IPv6/mapped addresses and private DNS results. Validate at connection/redirect, not merely before a second unguarded DNS lookup.
- Preserve server-name verification while dialing a validated address; do not replace authentication/SNI/Host meaning.
- Reject credential-bearing unapproved discovered URLs before probing/persistence. Do not strip required credentials and call the result public.
- Read caps and monotonic total deadlines cover network reads, retries, sleeps and expansion.
- Cache paths resolve within an owned repository cache. Explicit proxy configuration is honored, not silently bypassed; manual direct-ISP testing has no proxy/client attachment.
- Child test-core processes receive no GitHub/NVIDIA credentials and no unnecessary environment secrets.

Exact conservative byte caps and supported proxy details are builder choices. Document them and test excessive/slow responses. Any existing public feed requiring a secret URL is rejected/deactivated with a safe reason, not leaked or silently repaired.

### 4.4 Discovery and checkpoint contract

Daily cadence remains. The API cap counts actual attempts, including retries/fallbacks. One shared monotonic deadline/channel cap reaches every operation. A candidate is acknowledged only after processing; interrupted candidates remain queued. Temporary errors remain retryable without unbounded growth.

Validate existing state/registries, preserve corrupt prior files and return failure rather than resetting empty. Atomically replace owned checkpoint files; surface persistence failure. Reconcile confirmed dead sources, retain bounded deep-page configs/page provenance, remember successful unchanged images, and deduplicate by complete public URL. Leaderboard metrics distinguish scraped, retained, usable and active registry yields.

### 4.5 Scheduled publication and evidence semantics

Keep six-hour aggregation and optional socket checks, but socket success is **reachability**, not credential/end-to-end verification. Unsupported UDP protocols are not classified by an unrelated TCP socket. Pass execution state explicitly; zero successes never restore upstream trust as a successful test.

Maintain distinct upstream-claimed, reachable and owner-tested fields. Failed, untested and infrastructure-error outcomes are different. Ranking uses the relevant qualified pool, actual observed latency and deterministic ties. No streaming/throughput or universal bypass promises are inferred from setup latency.

Render and validate owned outputs before replacement, including plain/Base64 equivalence, row counts, schemas, categories and safe paths. Refuse an all-empty/no-usable-input refresh. A legitimate completed owner test with zero survivors may publish that result, but zero work/core-startup failure may not replace evidence.

Reconcile stale publisher-owned country pairs only after success. Use one emitted generation timestamp for STATUS/README; keep owner test dates separate. Custom/external output paths must not invent repository URLs or modify unrelated documentation.

### 4.6 Standalone manual Iran command

**Proposed interface:** a standalone Python script, optionally a thin PowerShell wrapper, requiring an explicit `--xray-bin` and GitHub CLI authentication. Final script name/argument spelling are builder choices; no legacy DB/sync/force-ownership-bypass flags remain.

1. Run the supported tester and **all its imports** from a pinned, reviewed checkout of protected `iran-results`, not scheduled-writable `master`. First-time setup uses a separate trusted checkout/worktree; never execute a bootstrap/helper fetched from unprotected `master`. Query the authenticated GitHub user; require `mani-moh` and the canonical official repository before testing. Anonymous, non-owner, expired credentials and wrong repository fail before any probes.
2. Require a supplied trusted standalone binary; no PATH/client search, downloads or existing-process attachment. Inspect only that binary's version/hash; start/stop only owned children.
3. Fetch one immutable official public `subs/all.txt` snapshot and record commit plus exact SHA-256. Do not read `local_subs`, arbitrary local configs, mutable mixed feeds or an unofficial repo. Failed download has no local fallback.
4. Select supported, faithfully convertible public connections before applying a bounded test budget. VLESS/VMess/Trojan/Shadowsocks are the initial Xray subset; Hy2/TUIC remain aggregation protocols but unsupported for this core test. Unknown/unsupported required settings are recorded untestable, never stripped.
5. Use HTTPS through the test proxy, validate destination certificates and the expected endpoint response, and apply real requested positive limits/rounds. The user attests a direct Iranian ISP connection; no client/VPN inspection is introduced.
6. Produce only allowlisted public evidence: schema/fingerprint versions, canonical repo, input commit/digest, UTC start/end, sanitized core version/hash, probe-policy identity, rounds, outcome counts and complete public connection fingerprints/status/latency. No raw URI credentials, IP/hostname, environment value, local path or raw process log enters the upload.
7. Require explicit publication intent in the command. Dispatch the owner-only workflow, wait for its result PR, verify the PR's exact paths/content against the local sanitized evidence, then approve and merge using the authenticated owner's GitHub identity. Report **published** only after the merge and raw output are confirmed. Distinguish a local test, accepted dispatch, open PR and official publication; none is silently treated as another.

The command may retain a sanitized recovery manifest in owned temporary/ignored storage after upload failure; it must not commit arbitrary local files. A retry publishes the same dated evidence, not a fictional retest. The final implementation must specify and test idempotency.

### 4.7 Official owner-results publication

Add a **`workflow_dispatch`-only** workflow, dispatched on protected `iran-results`. Its YAML, validation/render code and all executed dependencies are taken from that protected revision; never checkout/execute source code from unprotected `master` or a submitted PR. `master` may need the workflow registered for GitHub dispatch, but the local command pins and verifies the protected execution ref/run revision. No cron, push, pull-request, workflow-run, daily or refresh invocation. Job authorization checks `github.actor`, `github.triggering_actor`, repository and protected ref against the actual owner/canonical repository—not user-supplied manifest ownership. The supported local command authenticates the same owner separately.

Validate immutable official input and manifest schema, versions, times, counts, unique fingerprints, bounded finite latencies and public-subset membership. Confirm all distributed tested URIs come from the validated public snapshot; never accept supplied arbitrary URIs or execute submitted code. Render deterministic result subscriptions and evidence in one commit on a unique proposal branch and open a PR to protected `iran-results`. Use explicit non-forced push/conflict errors, preserving unrelated concurrent changes. The workflow cannot approve or merge its own official result.

**Protected publication and executable-code boundary:** create `iran-results` separately from scheduled output on `master`. It contains the reviewed standalone tester, every imported module, publication workflow/validator and result artifacts. Protect it with a required owner/code-owner PR review, dismissal of stale approvals, approval of the most recent push, and no scheduled-bot/app bypass. Use a CODEOWNERS rule naming `mani-moh`; keep Actions PR approval disabled. Tester/validator updates require the same owner-reviewed boundary and cannot be copied automatically from `master`. Schedule tokens must be unable to push directly, approve, or merge **unreviewed** official evidence or executable tester code. Verify those effective permissions; if the available repository plan/API cannot enforce them, stop as a release blocker rather than fall back to an unprotected branch.

**Authorization guarantee:** only the owner's explicit command authorizes a test/result publication and approves a matching result PR. The command normally merges and confirms it. GitHub may permit another writer to finish merging an already owner-approved PR; that does not grant authority to certify another test or alter the reviewed result, and the design does not claim exclusive merge-actor enforcement. Most-recent-push approval/dismissal prevents post-approval mutation from retaining authorization. Unapproved evidence is never official. If exclusive merge identity is later requested, assess additional platform controls separately instead of asserting that review protection supplies it.

The authenticated owner is currently the only enumerated human writer/admin; no deploy keys were returned. GitHub App installation access was not fully enumerable. Audit that residual automation access before promising strict isolation. A script owner check, manifest owner field, or same-repo write token alone is not enforcement: scheduled and manual GITHUB_TOKENs are not path-scoped.

Keep `master`'s existing scheduled-publication model separate. The main README links explicitly to the `iran-results` branch's tested subscription and evidence. Scheduled jobs neither stage those result paths nor have bypass authority over that reference. Owner approval of the design includes creating/configuring this new protected branch, not rewriting or restricting existing `master` history.

Use a bounded compact dispatch manifest. GitHub input-size limits must be checked before upload; never silently truncate evidence. The builder may use a compact fingerprint/result representation within a documented cap rather than introduce a service or storage backend.

**Initial freshness decision (proposal):** display explicit test dates and snapshot membership; do not claim current health or invent an expiry SLA. Scheduled jobs never modify the owner-certified evidence/tested feed. An owner test replaces current evidence with the latest genuine outcomes, including failures. Older evidence remains in Git history. Consumers can see exactly what was tested and when.

If aggregation later displays intersections with current nodes, it must preserve original dates, exact fingerprint versions and latest failures; that optional projection is not needed for the first release. An edited credential/transport/security/endpoint/special parameter invalidates identity. No automatic test or stale-pass promotion is allowed.

## 5. NavaRoute migration and compatibility

Rename GitHub repo only after candidate code/docs/tests pass and the manual workflow is ready. Keep `master`. Update repo description/topics, origin URL, badges, raw/clone links, fallbacks, headers, reports, retained symbols/tests and encoded artifacts. Do not move the local directory or rewrite old commits.

Maintained prose remains static Markdown. Generated sections have unique markers and fail safely when missing/duplicated. Actual snapshot facts replace hardcoded promises. Explain output formats without application integration or unsupported native-core import claims.

Preserve factual Patterniha/client/core/source attribution. All active project labels are NavaRoute. Historical/deprecation references to old names are explicit, finite exceptions—not active branding.

**Feed contract:** `master/subs/all.txt` remains all accepted public connections; `lite500.txt` remains an up-to-500 protocol-balanced sample. Add `master/subs/reachable.txt` for actual current-run socket observations and select `top100.txt` from that pool by measured connection latency only. Keep `verified.txt` solely as an explicitly deprecated same-format mirror of reachable, with a profile title and prominent docs stating that its legacy filename does **not** imply credential or end-to-end verification. There is no upstream-claim fallback when checks are disabled or all fail. Official end-to-end owner evidence lives only at `iran-results/subs/iran-tested.txt` and its Base64 pair, with dated provenance.

Compatibility mirrors may keep the old special raw path with NavaRoute headers and supplied-parameter selection; the primary path is `categories/navaroute-special.txt`. Both explicitly lack universal bypass claims. Retired executable JSON remains `[]` with deprecation documentation where preserving its URL is useful. Each mirror's format/semantics and active-header identity are tested; a migration ledger enumerates the finite old-path exceptions.

Do not regenerate by blindly editing credentials/Base64. Use the corrected producer and validated public input, preserving legitimate upstream attributes. Old manufactured endpoints may be removed deliberately; do not relabel legacy ambiguous fingerprint/verification booleans as owner evidence.

## 6. Professional multi-agent delivery pipeline

This is the design-level pipeline. After design approval, `writing-plans` supplies file-level tasks, tests and acceptance commands before implementation starts.

### Roles and ownership

| Role | Owned work | Skills | Required evidence |
|---|---|---|---|
| Coordinator/release owner | Scope, shared contracts, dependency ordering, integration, Git/GitHub mutation | `brainstorming`, `graphify`, `writing-plans`, `using-git-worktrees`, `verification-before-completion`, `finishing-a-development-branch` | Approved spec; findings-to-task ledger; clean integration; honest final limits. |
| Transport/security implementer | Shared public/authenticated transport, path/read boundaries, privacy scans | `systematic-debugging`, `test-driven-development`, `ponytail` | No credential forwarding, private-target access, unbounded reads, proxy bypass or sensitive log leak in regressions. |
| Connection/publishing implementer | Parser/model/dedup contract, lossless exports, evidence selection, staged output reconciliation | `systematic-debugging`, `test-driven-development`, `ponytail` | Fingerprint non-collisions, round trips, malformed-peer isolation and zero partial-write cases. |
| Discovery implementer | Attempt/time/channel limits, queue/checkpoint recovery, retained yield/revisits | `systematic-debugging`, `test-driven-development`, `ponytail` | Exact attempt caps, restored unfinished work, preserved corrupt state and delivered deep-page yield. |
| Standalone/manual-publication implementer | Client deletion, owner command, trusted input/results, manual-only workflow | `systematic-debugging`, `test-driven-development`, `ponytail` | Owner/failure-before-probe, public subset, no client operations, sanitized evidence, confirmed publication and schedule exclusion. |
| Documentation/identity implementer | Static prose, generated markers, NavaRoute metadata/encoded outputs/compatibility inventory | `ponytail`, `verification-before-completion` | Audit discrepancy ledger closed; regeneration stable; old-brand matches classified. |
| Independent reviewers | Requirements/security review and connected-code quality review, not their own implementations | `requesting-code-review`, `ponytail-review`, `receiving-code-review` | Concrete findings, verified fixes, no unresolved critical/high release issues. |

### Execution gates

1. **Design approval.** No product edits before approval. Adopt this scope, with any user corrections recorded.
2. **Planning and isolation.** Invoke `writing-plans`; create an isolated worktree/branch; record baseline SHA, all affected callers/config/tests/exports and deletion scope. Do not touch ignored local/client files.
3. **Test safety and shared contracts first.** Establish network-isolated tests and fingerprint/evidence/transport interfaces. Coordinate shared files; no concurrent edits to the same model, main, publisher or workflow.
4. **Parallel bounded implementation.** Use `subagent-driven-development` and `dispatching-parallel-agents` only for independent file ownership. Each agent receives the approved spec, explicit files, acceptance regressions and no GitHub mutation authority. Shared dependencies land before callers.
5. **Review each task.** Fresh requirements review, then connected-code quality/security review. Use `receiving-code-review` to verify feedback; reproduce and fix root causes, not blindly accept taste suggestions. Agents do not self-certify completion.
6. **Integrate and revalidate.** Run compilation/offline tests, large fixture smoke, artifact subset/count/Base64 checks, doc regeneration and branding scan. Check all workflow triggers/permissions/staging/conflict behavior. Run real-core schema validation only with an explicit supplied standalone binary; no Iran traffic without the owner's command.
7. **Git/GitHub release.** Review final diff and private-path inventory; make logical non-destructive commits; recheck target availability/authorization. Rename repo/update remote/metadata after gates pass, publish via the agreed branch/PR route, observe remote CI, and verify raw paths. Do not change branch/history/access scope silently.
8. **Owner acceptance.** Provide one documented manual command. The owner runs the actual Iran test; verify only the sanitized official results and metadata are published. This is the only live-Iran acceptance step, and never a scheduled task.

### Definition of done

- Every audit root cause is fixed, removed, or explicitly evidenced as a supported limit; no known critical/high defect is merely hidden by wording.
- No supported command imports/accesses/manages a client DB/installation/process/registry; no local endpoint is manufactured.
- Owner authentication/publication gates and immutable public-input/result subset are tested negatively.
- Daily/six-hour workflows cannot invoke Iran testing or overwrite owner evidence.
- Full connection round trips and versioned identity are correct for the stated support subset; unsupported inputs are not stripped into false positives.
- No silent success for empty refresh, corrupted checkpoint, missing core, zero work, exhausted push or upload failure.
- All retained outputs, callers, fixtures, docs, workflow metadata and encoded project titles use NavaRoute; explicit upstream/history/legacy exceptions are enumerated.
- Offline compilation/tests and independent reviews pass, with remote CI observed after publication. Real Iran functionality is reported only after the owner's actual command.

## 7. Explicit design calls and remaining limits

**Coordinator proposals, not prior user statements:** delete destructive synthesis instead of repairing speculative fronting; use GitHub CLI plus a manual result PR and protected `iran-results` branch rather than a backend; owner-command approval/merge; initial Xray-supported subset; retain narrow honest raw-path mirrors; show dated owner evidence without a guessed expiry period; never automatically promote a refreshed snapshot to newly Iran-tested.

**Builder review resolutions:** protect the entire tester/workflow/import tree along with evidence, rather than execute scheduled-writable `master` code with owner credentials. The enforceable result guarantee is owner-authorized publication, not exclusive identity of the final GitHub merge actor. Both are explicit design calls for user approval.

The builder decides small helper/file boundaries, conservative caps and exact commands. They may not change ownership, manual-only testing, no-client policy, public-input privacy, truthful evidence or stage/publication guarantees.

Live carrier behavior, core extension schemas, OCR quality and consumer redirects remain unverified until appropriate explicit tests. Repo rename availability/access must be rechecked at the release step.
