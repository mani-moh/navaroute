# NavaRoute run repair and reporting

User approved repairing invalid saved state while retaining valid data/history and diagnosing aggregation. Additional explicit requests: Step 5 Preview uses high reasoning; every workflow run records a summary of all changes.

## Scope and approach

- Repair the shipped public state, not the validators: exclude exactly the diagnosed 3 invalid queued handles, 4 invalid score keys, 2 unapproved metadata records and 2 rejected discovered-source rows. Keep every valid record, order, score, other state field and timestamp; original bytes remain in Git history. Add tests that exercise the actual shipped files through the production loaders.
- Set NVIDIA wire `reasoning_effort=high` whenever the selected model is `stepfun-ai/step-5-preview`, for both text and image calls. Keep explicit model overrides, fallbacks, budgets, timeouts and retries. Do not send Step-specific parameters to other models without verified support.
- Use GitHub's existing per-job `GITHUB_STEP_SUMMARY` and job log, including failed runs. No new repository log commits, log database, runtime dependency, secret or action is required. Summarize the checkout/finished commit identities, job result, incoming commit changes, committed changes since checkout, tracked working-tree changes and new untracked paths. Log safe paths and added/deleted line counts, never file contents, URI credentials, private paths, tokens or environment dumps.
- A final `always()` step in all three workflows writes the summary. Checkout/setup failure gets a minimal fallback summary, not a false success. The native Actions page retains the per-job summaries with the run. CI needs only enough history for the incoming commit's diff; do not fetch full history.
- Diagnose aggregation from safe failure-location metadata and existing logs. Add only function name and source line number to the already sanitized CLI error, not exception messages, source paths, locals or full tracebacks. Do not weaken caps or claim an unconfirmed cause.

## Alternatives and calls

- GitHub-native summaries were chosen over committing logs: the latter creates publication races, unnecessary commits and potential credential exposure. A summary is a log of changes, not a raw configuration dump.
- Step's verified high-reasoning parameter was chosen over blindly sending it to all fallback models, which can reject provider-specific arguments.
- Repairing the exact shipped state was chosen over resetting discovery memory or accepting invalid URLs at runtime.

## Boundaries

Only `mani-moh/navaroute` (repo ID 1455263931) is the target; `pattn` and local origins remain unchanged. Preserve `master`, cron schedules, blocked Actions PR capability, no-client behavior and manual-only Iran testing. Q5 and protected `iran-results` setup are separate pending work. No agent-session live NVIDIA/core/Iran calls. Coordinator owns Git and GitHub writes; workers have none. One bounded aggregation-only diagnostic workflow may run with socket checks disabled; do not automatically launch daily inference or unlimited retry jobs.

## Acceptance

Shipped state/registry tests fail before repair and pass afterward; payload tests confirm high reasoning for text/image Step requests and compatible fallbacks; summary tests cover successful/failed/no-change/missing-checkout/private-path cases. All affected callers/workflows and the full offline suite pass before public push. A fresh scoped review gates the patch. Actual workflow outcomes, unavailable baselines and live-provider limitations remain explicit rather than fabricated.
