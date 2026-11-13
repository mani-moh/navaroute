# Contributing to NavaRoute

The public aggregation pipeline uses the Python standard library. CI tests Python 3.12 and 3.14; no runtime dependency installation is needed.

```powershell
git clone https://github.com/mani-moh/navaroute.git
# Use an operator-selected checkout; do not move an existing client/user directory.
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
python scripts/public_git.py check
```

The canonical repository rename and protected owner-results setup are coordinator release checks, not already-established availability. Optional `.env.example` documents discovery configuration; actual `.env*` files and credentials must stay ignored. Do not expose ambient keys in test output. Tests must use synthetic transport/core/Git boundaries, never real shared pushes, downloads, live proxy traffic or client files.

## Public sources and complete identity

Edit `sources.json` with unique source IDs and unauthenticated public URLs. Do not add personal tokens, local subscriptions or private destinations. Preserve full credential, transport, TLS and special-parameter semantics or reject the entry; never strip unsupported settings into a successful test. Add a regression test before nontrivial changes. Upstream names, URLs and copyrights remain upstream identities.

## Publication and privacy

CI has unrestricted push/PR triggers for compilation, full unittest and the quiet tracked-role/secret gate. GitHub's `GITHUB_TOKEN` push recursion protection still applies: scheduled writers run their own tests and final privacy gate, not a promised downstream CI run. The gate inspects index blobs, not untracked private files. Publication stages only `subs/`, README/STATUS/DISCOVERY, `sources.json`, `data/discovery_state.json` and optional `data/discovered_ocr_configs.txt`; final staged paths must match exact known public roles. Never stage broad `data/`, ignored historical private inputs, `.env` variants, DB/cache artifacts or local recovery files. The pattern scanner is not a complete secret detector or history audit.

Preserve the two README stats markers exactly once and in order. Only the reviewed producer updates their interior and STATUS. Do not hand-edit encoded artifacts. Validate plain/Base64 row equivalence, decoded VMess remarks, connection identities, counts and stale countries after regeneration. Use the actual input time for historical offline migration, not a new test date.

One publisher per output directory (including custom/local outputs) and one checkpoint publisher per discovery state directory. Shared Actions concurrency does not serialize local commands. Non-forced rebase/push failures must stay nonzero; never erase conflicts or human commits.

## Owner boundary

No client/DB integration, core download, manufactured variant, automatic Iran retest or Iran workflow is permitted. The supported manual tester and every import execute only from a separately trusted pinned protected `iran-results` checkout. See [README](README.md#manual-owner-procedure). Actions PR creation/approval stays blocked; only the authenticated owner administrator uses the explicit `enforce_admins=false` exception. Scheduled tokens are not path-scoped and must not receive administration privileges. Coordinator/owner release and live acceptance remain separate from offline development.
