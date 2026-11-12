# NavaRoute Pipeline Explorer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `D:\Projects\navaroute-site`, a standalone React+Vite static site with 7 sections (Overview, Pipeline explorer, Automation, Repo anatomy, Published artifacts, Trust boundaries, Local development) that explains the NavaRoute pipeline and every claim cites its repo source.

**Architecture:** Single-page app, hash navigation, no router. A typed static content layer (`src/data/siteContent.ts`) holds all facts; sections are presentational components over it; the pipeline diagram is an inline SVG with a click-to-expand stage card; automation section computes "next run" chips from cron expressions. `vite build` emits `dist/` for any static host.

**Tech Stack:** React 18, Vite 5, TypeScript, Vitest (pure-logic and content tests only — no jsdom/component-testing deps), hand-rolled CSS over a token file. Dev dependencies: react, react-dom, vite, vitest, @vitejs/plugin-react, typescript.

**Spec:** `docs/superpowers/specs/2026-11-12-navaroute-pipeline-explainer-design.md` (in the nava repo at `D:\Projects\nava`; site is external to that repo).

## Global Constraints

- Static export only: `vite.config.ts` must set `base: './'`; zero runtime network fetches; `dist/` opens offline from `file://`.
- Dependencies limited to react, react-dom, vite, vitest, @vitejs/plugin-react, typescript, @types/react(+dom). No UI libraries, no charting libraries.
- Every content card carries a `source` field (repo file path, permalink pinned to commit `c2249e1`); figures stamped "as of 2026-11-12".
- CSS is hand-rolled on a token layer (`theme.css`); dark by default, light toggle persisted to localStorage, `prefers-reduced-motion` honored everywhere (animation must be reducible to instant state changes).
- Git history: the site is its own repo (`git init` in task 1); one commit per task; no committing into the nava repo (the spec doc there stays as-is, uncommitted until the owner commits it).

## Review Focus

The five input classes/failure modes most likely to bite, each pinned to a task's test:

1. **Stale figures** — a reader must not see feed counts or schedules that contradict the repo. Pinned by the content-snapshot test (Task 2) and the numbers audit (Task 7).
2. **Cron boundary math** — `nextRun("0 */6 * * *")` must be the next 6-hour mark in UTC and `nextRun("0 2 * * *")` the next 02:00 UTC, including when called exactly on the hour. Pinned by `src/lib/cron.test.ts` (Task 4).
3. **Theme/reduced-motion behavior** — toggling must persist and reduced-motion must disable reveals. Pinned by theme-logic tests (Task 1) plus manual verification steps.
4. **Absolute asset paths** — a misconfigured base would break offline `file://` use. Pinned by the post-build assertion in Task 7.
5. **Long unbroken strings** (URI examples in artifact cards) overflowing narrow viewports. Verified manually in Task 6's visual-check step; mitigated by CSS `overflow-wrap` rules in Task 1's layout pass.

---

## File Map

```
D:\Projects\navaroute-site/
  package.json            react/react-dom/vite/vitest scripts: dev, build, preview, test
  vite.config.ts          base './', plugin-react, test: node environment
  tsconfig.json (+node)   Vite defaults
  index.html              root div + module script (relative)
  public/favicon.svg
  src/main.tsx            mounts <App/>, imports styles once
  src/App.tsx             hash-nav section router (anchor mapping), ThemeProvider-less class + data-theme on <html>
  src/styles/theme.css    design tokens (colors, type scale, radii, motions) for data-theme dark + light
  src/styles/globals.css  reset, layout, typography, overflow-wrap rules
  src/lib/theme.ts        resolveInitialMode({ prefersDark, stored }) -> "dark"|"light"; setMode(mode)
  src/lib/section.ts      scroll-into-view with reduced-motion guard: smoothScrollTo(hash)
  src/lib/cron.ts         nextRun(cron6h | cronDaily, now: Date) -> Date (UTC math only)
  src/lib/cron.test.ts
  src/lib/theme.test.ts
  src/types/site.ts       SiteSection, PipelineStage, Workflow, WorkflowTrigger, FeedEntry, TrustItem, DevCommand
  src/data/siteContent.ts overrides of all seven section contents; every card has source: "path@commit"
  src/data/content.test.ts snapshot + invariant validation
  src/components/{Navbar.tsx, Section.tsx (scroll-margin wrapper), Footer.tsx,
                  PipelineSection.tsx, StageCard.tsx, AutomationSection.tsx, ScheduleChip.tsx,
                  AnatomySection.tsx, ArtifactsSection.tsx, TrustSection.tsx, DevSection.tsx, OverviewSection.tsx}
```

---

### Task 1: Scaffold, theme, and layout shell

**Files:**
- Create: the whole scaffold listed under File Map (package.json, vite.config.ts, tsconfigs, index.html, public/favicon.svg, src/main.tsx, src/App.tsx, src/styles/*.css, src/lib/theme.ts, src/lib/theme.test.ts)

**Interfaces:**
- Consumes: nothing.
- Produces: `resolveInitialMode({prefersDark: boolean, stored: string | null}) -> "dark" | "light"`; `setMode(mode: "dark" | "light")` (writes document.documentElement.dataset.theme + localStorage); `smoothScrollTo(hash: string, prefersReducedMotion: boolean)`; default-export-free `App` rendering Navbar + a placeholder main with anchor targets + Footer.

Steps:

- [ ] **Step 1: Verify toolchain and scaffold.** `node --version && npm --version` must be ≥20 (if not, stop and report). Write the scaffold files from the File Map directly (no interactive `npm create vite`).
- [ ] **Step 2: `npm install`** (test/build green status is established by later steps, not here).
- [ ] **Step 3: Write `theme.test.ts` first.** Assert: `resolveInitialMode({prefersDark: true, stored: null}) === "dark"`; `resolveInitialMode({prefersDark: false, stored: null}) === "light"` (null stored → follow system); `resolveInitialMode({prefersDark: false, stored: "dark"}) === "dark"` (stored wins over system).
- [ ] **Step 4: Implement `src/lib/theme.ts`** to the signatures above (logic testable via injected `storage` and `doc`; `setMode` touches `documentElement.dataset.theme` and `localStorage`).
- [ ] **Step 5: Run `npx vitest run`** — all theme tests pass.
- [ ] **Step 6: Implement theme.css tokens + App shell** (Navbar with anchor links, data-theme on <html>, Footer). Manual verification: toggling persists on reload; with DevTools `prefers-reduced-motion: reduce`, header reveal animations are instant.
- [ ] **Step 7: Commit** `git init` (default branch `main`) then `git add -A && git commit -m "chore: scaffold navaroute pipeline explorer shell"`.

---

### Task 2: Typed content layer

**Files:**
- Create: `src/types/site.ts`, `src/data/siteContent.ts`, `src/data/content.test.ts`

**Interfaces:**
- Consumes: nothing (standalone data layer; UI tasks consume this).
- Produces: `export const SECTIONS` and per-section constants used by Tasks 3–6:
  - `pipelineStages: PipelineStage[]` — `{ id, title, subtitle, detail, modules: string[], source }` in execution order: sources-registry → fetch → parse-and-identity → dedupe → healthcheck → categorize → publish.
  - `workflows: Workflow[]` — ci, update-subs, daily-deep-discovery with `{ id, name, triggers: {schedule: string|null, dispatch: boolean}, steps: string[], permissions: string, ceilings?: string, source }`.
  - `anatomy: AnatomyPart[]` — one entry per top-level path (`src/`, `scripts/`, `tests/`, `docs/`, `subs/`, `.github/`, root files) each with per-module one-liners from docs/ARCHITECTURE.md.
  - `feeds: FeedEntry[]` — the ~30 published artifacts with counts from the 2026-11-10 snapshot (all 54187; lite500 500; protocols vless 37818 / vmess 6774 / trojan 5448 / ss 3568 / hysteria2 567 / tuic 8; reachable/top100/verified 0; categories + selected countries as of snapshot), each with `kind: "all"|"summary"|"protocol"|"category"|"country"|"deprecated-mirror"`, `rows`, `meaning`, `trust`, `source`.
  - `trustItems: TrustItem[]`, `devCommands: DevCommand[]` (from README "Automation and local development").

- [ ] **Step 1: Write `content.test.ts` first** with the invariants: every entry in every exported array has non-empty `source` starting with a path that exists in the checked-in repo-manifest list (`repoFiles` string[] exported from the same file, transcribed from `git ls-files | grep -v subs/`); `pipelineStages.length === 7` and ids unique; `feeds` counts are non-negative integers and `feeds.filter(f => f.kind === "all").rows >= 50000`; every `Workflow.schedule` matches `/^[0-9 *\/]+$/` when present.
- [ ] **Step 2: Run vitest** — fails (files don't exist).
- [ ] **Step 3: Implement `src/types/site.ts`** then `siteContent.ts` with the snapshot figures and the repo-manifest list.
- [ ] **Step 4: Run `npx vitest run`** — content tests pass.
- [ ] **Step 5: Manual spot-check** — open `siteContent.ts`, verify three numbers against the repo (`wc -l subs/all.txt` ≈ 54187 URI rows + headers; README stats table; schedules in .github/workflows). Note: don't enumerate upstream feed URLs; describe registries generically.
- [ ] **Step 6: Commit** `git add src/{types,lib,data} src/data/content.test.ts && git commit -m "feat: typed content layer with snapshot validation"`.

---

### Task 3: Pipeline explorer section

**Files:**
- Create: `src/components/PipelineSection.tsx`, `src/components/StageCard.tsx`

**Interfaces:**
- Consumes: `pipelineStages` (Task 2), `smoothScrollTo`.
- Produces: rendered section `id="pipeline"` with inline-SVG stage chain; clicked stage opens `StageCard` (title, subtitle, detail, module chips, source permalink); selected stage tracked in component state (unselected default: first stage).

- [ ] **Step 1: Implement `PipelineSection`** — SVG chain (7 nodes, arrows, animated reveal on intersection-observer entry; reveal = instant if `prefers-reduced-motion`). Clicking a node selects it; arrow flow direction left→right desktop, wraps vertically ≤900px.
- [ ] **Step 2: Implement `StageCard`** — presents the selected stage's full detail + module chips + `source` link (external permalink to `mani-moh/navaroute/blob/c2249e1/<path>`).
- [ ] **Step 3: Visual verification** — `npm run dev`, walk each stage, confirm card updates, keyboard focus outline visible, mobile wrap ok, reduced-motion makes reveal instant.
- [ ] **Step 4: Commit** `git add src/components/{Pipeline,StageCard}* src/App.tsx && git commit -m "feat: interactive pipeline explorer"`.

---

### Task 4: Automation section (workflows + cron chips)

**Files:**
- Create: `src/lib/cron.ts`, `src/lib/cron.test.ts`, `src/components/AutomationSection.tsx`, `src/components/ScheduleChip.tsx`

**Interfaces:**
- Consumes: `workflows` from Task 2.
- Produces: `nextRun(pattern: "6h" | "daily", now: Date): Date` computing next 6-hour mark / next 02:00 UTC; the section renders one card per workflow (triggers, inputs, steps list, permissions, ceilings) with a live-updating countdown chip.

- [ ] **Step 1: Write `cron.test.ts` first.** With `now = new Date("2026-11-12T05:59:00Z")`: 6h → 06:00 same day; daily → 02:00 next day. With `now = new Date("2026-11-12T06:00:00Z")` exactly on the hour: 6h → 12:00 (next interval, not current); daily at `2026-11-12T02:00:00Z` → 02:00 next day. Times always UTC regardless of local tz.
- [ ] **Step 2: Run vitest — fails** (no cron.ts).
- [ ] **Step 3: Implement `src/lib/cron.ts`** (pure Date math, UTC getters).
- [ ] **Step 4: Run vitest — passes.** Then implement AutomationSection + ScheduleChip (`setInterval` 1s tick only when tab visible; chip re-renders mm:ss countdown to `nextRun`).
- [ ] **Step 5: Visual verification** — cards match the three workflow files: ci (push/PR, py3.12+3.14 matrix, privacy gate), update-subs (q6h, dispatch inputs run_healthcheck/max_test_nodes), discovery (daily 02:00 UTC, 500 req / 5400s / 2000 channel ceilings, NVIDIA_API_KEY). Countdown ticks; reduced-motion alters nothing here.
- [ ] **Step 6: Commit** `git add src/lib/cron.* src/components/{Automation,Schedule}* src/App.tsx && git commit -m "feat: workflow cards with live cron countdowns"`.

---

### Task 5: Repo anatomy + published artifacts sections

**Files:**
- Create: `src/components/AnatomySection.tsx`, `src/components/ArtifactsSection.tsx`

**Interfaces:**
- Consumes: `anatomy`, `feeds` from Task 2; Navbar anchor list extended (`#anatomy`, `#artifacts`).
- Produces: AnatomySection as an expandable per-directory tree with module one-liners and role tags (fetch/parse/dedupe/health/categorize/discovery/storage/git-security/iran-results); ArtifactsSection as a filterable grid/table with kind badges, row counts, meaning, trust pill (accepted / socket-observed / deprecated mirror / claim-only), search input filtering by name/meaning.

- [ ] **Step 1: Implement AnatomySection** (accordion, each part's modules + `source`).
- [ ] **Step 2: Implement ArtifactsSection** (filter input + kind pill + count per row; note block explaining base64 pairs and retired `serverless-fragment.json []`).
- [ ] **Step 3: Visual verification** — filters work with >100 rows expectation (only ~30 feed rows exist); touch targets ≥44px; URI example strings wrap on 360px viewport.
- [ ] **Step 4: Commit** `git add src/components/{Anatomy,Artifacts}.tsx src/App.tsx && git commit -m "feat: repo anatomy explorer and filterable feed catalog"`.

---

### Task 6: Overview, trust boundaries, local dev + final composition

**Files:**
- Modify: `src/components/OverviewSection.tsx`, `TrustSection.tsx`, `DevSection.tsx`, `Navbar.tsx`, `Footer.tsx` (all created here)

**Interfaces:**
- Consumes: remaining Task 2 exports (`trustItems`, `devCommands`), hash-nav anchors from Task 1's App.

- [ ] **Step 1: Implement OverviewSection** — what NavaRoute is / is not (from README intro), headline snapshot figure (54,187 accepted nodes, 2026-11-12), link-out row to README/ARCHITECTURE/CONTRIBUTING, figures-as-of stamp.
- [ ] **Step 2: Implement TrustSection** — trustItems rendered as guardrail cards (privacy gate, non-forced rebased pushes, protected `iran-results` boundary, no-credential claims, source-claim labeling), each with `source`.
- [ ] **Step 3: Implement DevSection** — devCommands rendered as copyable command blocks with expected-effect lines; note on serializing local publishers (one-writer rule).
- [ ] **Step 4: Compose** — wire all seven sections into App in order, footer with commit pin + license note, update Navbar.
- [ ] **Step 5: Visual verification sweep** — every section reachable from nav, focus order sane, dark/light both read well, no horizontal scroll on 360×640, reduced-motion everywhere instant.
- [ ] **Step 6: Commit** `git add src/App.tsx src/components/{Overview,Trust,Dev,Navbar,Footer}* && git commit -m "feat: overview, trust boundaries, local dev; full section composition"`.

---

### Task 7: Build verification, numbers audit, handoff

**Files:**
- Modify: none (verification only)

- [ ] **Step 1: `npm run build`** must exit 0 and produce `dist/` with no type errors.
- [ ] **Step 2: Asset-path assertion** — run a check that `dist/index.html` and emitted chunks reference relative asset URLs (no `src="/`, no `href="/"`); a quick grep/strip in PowerShell or a tiny node -e script. If any absolute root path appears, fix `base` and rebuild.
- [ ] **Step 3: Offline smoke** — `npm run preview -- --port 4173`, fetch `http://localhost:4173/` and two asset URLs with HTTPS? no — plain HTTP, expect 200. Open locally and click through all seven sections once.
- [ ] **Step 4: Figures audit** — re-derive every number in the site against the nava repo at `c2249e1`: `git -C D:\Projects\nava ls-files | grep -v -c '^subs/'` vs repo-manifest list; feed counts vs `README.md` stats table and `git -C D:\Projects\nava log -1 --format=%H` == c2249e1…; schedules vs the two workflow files. Record any mismatch as a task-2 data fix + recommit before claiming done.
- [ ] **Step 5: Test sweep** — `npx vitest run` green; `npm run build` green. Final `git add -A && git commit -m "ci: final verification sweep"` only if something changed; otherwise close the repo.
- [ ] **Step 6: Handoff** — report the site path, `npm run preview` instructions, commit list, and the three things deliberately not built (marketing copy, live data, Next.js SSR).
