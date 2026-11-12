# Design: NavaRoute Pipeline Explorer (interactive explainer site)

Date: 2026-11-12
Status: awaiting human approval

## Intent and why

NavaRoute (github.com/mani-moh/navaroute) is a dependency-free Python pipeline that
aggregates public proxy URIs, validates their identities, deduplicates, and
publishes categorized subscription feeds on `master` via scheduled GitHub Actions.
Its design carries unusual trust boundaries (privacy gates, non-forced pushes,
protected `iran-results` branch, explicit "socket reachability is not credential
verification" disclaimers) that are documented but hard to grasp from prose.

This site explains the workflow and pipeline to a technical reader by walking the
actual data flow, the automation, the repo parts, and the published artifacts — so
someone can understand NavaRoute in minutes instead of reading every doc.

## Goals

- A standalone, professional, interactive static website explaining NavaRoute.
- Interactive animated pipeline diagram (sources → fetch → parse/identity →
  dedupe → healthcheck → categorize → publish) with click-to-expand stage cards.
- CI/CD section covering the three workflows (ci.yml, update-subs.yml,
  daily-deep-discovery.yml): triggers, schedules with countdown chips, key steps,
  permissions and ceilings.
- Repo anatomy section: part-by-part analysis of every top-level path and each
  `src/` module, drawn from `docs/ARCHITECTURE.md` and the code.
- Published-artifacts section: the `subs/` feed catalog with counts, what each
  feed means, and its trust level (accepted / socket-observed / deprecated mirror).
- Trust-boundaries section: privacy gate, non-forced rebased pushes, protected
  `iran-results` branch, "not credential verification" disclaimers.
- Local development section: `python -m src.main --no-healthcheck`, unittest
  setup, Python 3.12/3.14 CI matrix.
- Every content claim traceable to a repo file (card cites its source).

## Non-goals

- Not marketing copy; the audience is technical evaluators, not end users.
- Not a replacement for README.md / ARCHITECTURE.md — it is an explainer that links to them.
- Not a live dashboard: no runtime fetching of the nava repo or GitHub API; content is authored static data (avoids CORS, keys, and stale-data drift).
- No backend, no analytics, no CMS, no i18n.

## Anti-goals

- No server-side rendering, no database, no dynamic hosting requirements: the site must build to static files that open offline.
- No extra dependencies beyond React/Vite; no UI component library.

## Constraints

- Standalone project outside the nava repo: `D:\Projects\navaroute-site`.
- Static export (`vite build` → `dist/`); serves anywhere (GitHub Pages, Netlify, `vite preview`).
- Content accuracy is the top constraint; the site documents trust-sensitive behavior, so numbers and claims must match the repo as of 2026-11-12 (post-pull, HEAD `c2249e1`).
- Match the repo's minimalism: few dependencies, fast, readable.

## Approaches

| Approach | What it trades off |
|---|---|
| A. Single self-contained HTML page, committed into the repo | Zero deps, simplest, but limited "app" feel; lives inside a repo that has no frontend tooling |
| B. Framework static site (React/Vite), standalone project | **Picked.** Richer interactivity, conventional structure for future edits, lives outside the repo; costs one small toolchain (node + vite) at build time |

Picked B. Its build cost only affects whoever edits the site, not visitors, and the
user wants a "professional interactive webapp," which a component structure serves
better. Next.js was rejected: SSR adds hosting constraints for zero benefit on a
static explainer.

## How it gets built (decisions so far)

- **Stack:** React 18 + Vite + TypeScript + plain hand-rolled CSS (no UI lib).
  Rationale: conventional, typed content model, matches the pairwise simplicity of the repo.
- **Structure (sections):** 1 Overview · 2 Pipeline explorer · 3 Automation · 4 Repo anatomy · 5 Published artifacts · 6 Trust boundaries · 7 Local development.
- **Content model:** a typed content module (`src/data/siteContent.ts`) holding all facts
  from the repo analysis; each card carries `source` (repo file) for traceability.
- **Interactivity:** scroll-revealed pipeline steps; stage click expands an info card;
  cron "next run" countdown chips; filterable feed catalog; keyboard/ARIA basics;
  dark theme with light toggle; responsive down to mobile.
- **Numbers:** from the repo as of the build date — 54,187 accepted public nodes; ~500-channel
  sampling; ceilings (500 API requests / 5400 s / 2000 channels); schedules
  (`0 */6 * * *`, `0 2 * * *`).

## Settled review calls (builder's judgment, per reviewer pass 2026-11-12)

- **Visual identity: independent, restrained neutral "technical documentation" theme.** The repo has no visual identity to match (no logo, plain-markdown README), so the site gets its own token set, hero treatment, favicon, and a dark default with a light toggle. If the maintainer later adopts a brand, retheme is isolated to the CSS token layer.
- **Source naming: mirror the repo's own posture.** The site describes sources generically ("curated public URI registries") and points to `sources.json` / `sources.discovered.json` for the list — it does not enumerate upstream third-party feeds. It names only what the README already names publicly (e.g. Patterniha/Xray-core attribution) and cites repo files per claim (path + permalink pinned to `c2249e1`).

## What is left to the builder

- Exact file layout inside the site, section copy (guided by the content model),
  diagram styling, animation details, and commit/publish mechanics for the site repo itself.
- Verify every number against the nava repo before shipping; flag anything stale.

## Size

A focused build: one static Vue-free React app, ~6-10 content-bearing components,
no backend. Roughly a day of elapsed work including content verification.
