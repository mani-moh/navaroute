# NavaRoute Run Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Repair shipped discovery inputs, request high Step reasoning and record privacy-safe changes for every workflow run; diagnose aggregation without guessing.
**Architecture:** Existing production validators remain strict. Add the verified reasoning parameter at the shared NVIDIA wire boundary and one shared standard-library workflow-summary script called by all jobs. Coordinator performs migration/diagnosis and integration; disjoint model/reporting workers have no Git/GitHub authority.
**Tech Stack:** Python standard library, existing Git/GitHub Actions; no dependencies or new actions.
**Spec:** `docs/superpowers/specs/2026-11-12-navaroute-run-repair-design.md`

## Global Constraints

- Only `mani-moh/navaroute` (repo ID 1455263931) is the target; `pattn` and local origins remain unchanged.
- Preserve `master`, cron schedules, blocked Actions PR capability, no-client behavior and manual-only Iran testing.
- Q5 and protected `iran-results` setup are separate pending work.
- No agent-session live NVIDIA/core/Iran calls. Coordinator owns Git and GitHub writes; workers have none.
- No file contents, URI credentials, private paths, tokens or environment dumps in summaries or diagnostics.
- No new runtime dependency, secret, logging action, repository log commits or validation bypass.

## Review Focus

- Real shipped state, not only synthetic fixtures, must initialize while valid memory/order/timestamps survive repair.
- Text and image Step requests use high reasoning; fallback payloads do not inherit an unsupported parameter.
- Failed checkout/setup/persistence/push still gets a truthful summary and cannot be mislabeled published.
- A rebased/missing baseline or unusual filename cannot leak private data, inject Markdown or become a Git option.
- Summary/reporting work must not enable scheduled Iran probes, PR authority or unbounded fetch/retry operations.

### Task 1: Repair exact shipped inputs (coordinator)
**Files:** `data/discovery_state.json`, `sources.discovered.json`, new `tests/test_repository_inputs.py`; one-off migration stays ignored.
**Interfaces:** Existing `DiscoveryStateManager`, `serialize_state`, `SourceConfig.from_dict` and `load_sources` unchanged.
- [x] Test actual shipped loaders and round-trip; RED: checkpoint load and two invalid registry rows rejected.
- [x] Exclude only diagnosed invalid entries with original fields/timestamps preserved; GREEN: 2 tests pass, no external attempts.
- [ ] Include exact semantic-preservation evidence and original-parent identity in scoped report/review.

### Task 2: Step high reasoning (model worker)
**Files:** `src/discovery/nemotron_client.py`, `.env.example`, `tests/test_nemotron_default.py`.
**Interfaces:** `_call_api(payload, max_retries=3, fallback_models=None)` unchanged; attach `reasoning_effort=high` only to the actual Step wire payload.
- [ ] Add text/image real-payload and fallback-compatibility assertions; observe RED for absent high parameter.
- [ ] Smallest shared-boundary change and one example comment; preserve all routing/retry/budget behavior.
- [ ] Focused model/discovery tests GREEN; worker never stages, commits or runs live inference.

### Task 3: Per-job summaries (reporting worker)
**Files:** new `scripts/workflow_summary.py`, new `tests/test_workflow_summary.py`, all 3 `.github/workflows/*.yml`, workflow-contract test and maintained README sentence.
**Interfaces:** Existing `scripts.public_git` Git/privacy primitives; script writes `GITHUB_STEP_SUMMARY` and stdout, with `RUN_START_SHA` and `RUN_STATUS` supplied by workflows.
- [ ] RED tests: real Git-diff/summary outputs through an isolated fixture; incoming/generated/working/untracked/no-change/failure/missing-repo/private/token/Markdown cases.
- [ ] Shared bounded script; all jobs capture checkout SHA and call it in `always()` with checkout-unavailable fallback. CI depth 2 suffices for the triggering commit, otherwise report missing history honestly.
- [ ] GREEN focused tests; no remote calls, secrets, staging, scheduling, core/NVIDIA calls or new dependencies.

### Task 4: Diagnosis, review and public integration (coordinator)
**Files:** `src/main.py`, affected CLI privacy tests; ignored reports/ledger.
- [ ] RED test safe function/line diagnostic with secret exception message omitted; minimal change; GREEN affected CLI tests.
- [ ] Full guarded offline suite and compile/diff/index privacy gates on stable integrated tree.
- [ ] Fresh task-scoped review of the complete additive patch, fix only concrete blockers, preserve worktrees.
- [ ] Non-forced push to new repo master; verify exact code and CI/report steps.
- [ ] At most one bounded aggregation-only diagnosis run with socket checks disabled; inspect safe location to establish actual cause. No speculative cap increase or fabricated source success; unresolved causes stay reported.

## Self-review

Task 1 consumes existing loaders without changing schemas; Task 2 owns only NVIDIA payloads; Task 3 owns reporting/CI history and its contract fixture; Task 4 owns CLI diagnostics and final publication. Tasks 2/3 share no mutable files. Full verification waits for both workers. Every spec requirement maps to a task and a concrete privacy/behavior test; no original audit, Q5 or protected-release task is reimplemented.
