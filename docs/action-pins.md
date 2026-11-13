# Verified action pins

GitHub Actions used by the workflows in `.github/workflows/`, pinned by
commit SHA (not by a mutable tag). Verify a pin against the upstream
release before changing it.

| Action | Pin (SHA) | Version |
| --- | --- | --- |
| actions/checkout | `11d5960a326750d5838078e36cf38b85af677262` | v4 |
| actions/setup-python | `a26af69be951a213d495a4c3e4e4022e16d87065` | v5 |

Used in: `ci.yml`, `update-subs.yml`, `daily-deep-discovery.yml`.
