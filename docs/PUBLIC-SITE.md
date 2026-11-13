# Public site (user-facing dashboard)

NavaRoute has two different public pages — do not confuse them:

| Page | Audience | URL | Source |
|---|---|---|---|
| **Public site** (this doc) | Proxy users | `https://mani-moh.github.io/navaroute/` (mirror) + Cloudflare Pages (primary) | `scripts/build_public_site.py` |
| **Pipeline Explorer** | Repo readers | `https://mani-moh.github.io/navaroute/explorer/` | `docs/explorer/` (Vite app) |

The public site is a single self-contained `index.html`: every feed link,
count and tier-1 figure is baked in at build time. It makes **no runtime
request to GitHub or anywhere else**, so it loads from any mirror and in
places where `github.com` is unreachable. It carries **no credentials, no
owner controls and no tokens** — just feeds, clients and how-to guidance,
in Persian (default) and English.

## How it updates

`scripts/build_public_site.py` reads only public repo data:

- `subs/*.txt` — feed inventory + config counts (plain feeds; `_base64`
  twins ride along as copy buttons)
- `data/tier1_results.json` — per-feed tier-1 tested/passed
- `STATUS.md` (README fallback) — accepted / socket-reachable snapshot
- `sources.json` — source count

`.github/workflows/public-site.yml` runs the generator and deploys to
GitHub Pages on every push to `master` that touches the data, on a 6-hour
schedule, and on manual dispatch. The workflow also copies
`docs/explorer/` into the Pages artifact, so the Pipeline Explorer keeps
working at `/explorer/`.

Build it locally:

```sh
python3 scripts/build_public_site.py --repo-root . --out site
```

## Hosting: why Cloudflare Pages is primary

`github.io` is filtered in Iran, so it is only a mirror. The primary host
is **Cloudflare Pages** (free: unlimited bandwidth, 500 builds/month,
GitHub-connected auto-deploy). Honest caveat: `*.pages.dev` is
intermittently throttled by some Iranian ISPs (Cloudflare Community,
Nov 2026), so the robust setup is a **custom domain on Cloudflare** —
Cloudflare's main edge is far harder to blanket-block than `pages.dev`.
Ryan already needs a Cloudflare account + domain for the WhiteDNS Wizard,
so the same account can host this site.

### Connecting Cloudflare Pages (owner, ~5 minutes, one time)

1. Sign in at <https://dash.cloudflare.com> (free account).
2. Workers & Pages → Create → Pages → **Connect to Git**.
3. Pick the `mani-moh/navaroute` repo.
4. Build settings:
   - Build command: `python3 scripts/build_public_site.py --repo-root . --out site`
   - Build output directory: `site`
   - (No environment variables, no secrets needed.)
5. Save and deploy. Every push to `master` redeploys automatically.
6. Optional but recommended: add a custom domain (Pages → Custom domains)
   so users get a stable address that is not `*.pages.dev`.

No repo secret or token is involved: Cloudflare pulls the public repo
itself. If the Cloudflare project is ever deleted, the GitHub Pages
mirror keeps serving the same page.

## Content rules for this page

- Only official client links (verified in-repo: v2rayNG, v2rayN, Hiddify,
  Streisand, NekoBox). Never link a third-party APK mirror.
- Never promise a config works. "Accepted" ≠ working; "reachable" is a
  socket check, not an end-to-end test; only `tier1_*` passed a real
  connection test.
- The page must stay a single file with no external CSS/JS/fonts.
