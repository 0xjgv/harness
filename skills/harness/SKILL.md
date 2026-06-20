---
name: harness
description: >
  Bootstrap or align a repo with the harness-templates contract: the
  5-script quality harness (check, pre-commit, ci, audit, post-edit) plus
  the hook-enforced behavior contract (task sizing, human-owned commits,
  Gherkin-first, arch-config write-protection). Use when starting a new
  python/bun/go/rust/monorepo project, adding a quality harness to an
  existing repo, or asked to match ~/Code/harness-templates conventions.
  Triggers: "add a harness", "set up check/ci/pre-commit", "align with
  harness-templates", "bootstrap quality gates", "add the behavior
  contract", "wire the engineering principles".
---

# Harness Template

Give a repo the same contract as `~/Code/harness-templates`. The contract
has **two layers**:

- **Layer 1 — quality harness** (always): the 5 scripts `check`,
  `pre-commit`, `ci`, `audit`, `post-edit`.
- **Layer 2 — behavior contract** (greenfield: automatic; existing repo:
  opt-in): `.claude/` hooks that enforce task sizing, human-owned commits,
  Gherkin-first, and arch-config write-protection. See
  [reference-behavior-contract.md](reference-behavior-contract.md).

## Source of truth

Always read first. Do not restate the contract from memory.

- `~/Code/harness-templates/README.md` — Layer-1 contract + getting started
- `~/Code/harness-templates/<lang>/AGENTS.md` and `…/CLAUDE.md` — same
  per-language commands + behavior contract, byte-identical. Claude Code
  reads `CLAUDE.md`; Codex (and other AGENTS.md-consuming tools) read
  `AGENTS.md`. Both files carry the full content; the harness
  `agents-md-drift` check enforces no drift, and `sync-agents-md` writes
  `AGENTS.md ← CLAUDE.md` after edits. Copy verbatim; do not paraphrase.
- `~/Code/harness-templates/<lang>/.claude/settings.json` — Claude hooks
- `~/Code/harness-templates/<lang>/.codex/hooks.json` — Codex Stop hook
- `~/Code/harness-templates/<lang>/.codex/hooks/codex-stop-hook.sh` —
  Codex stdout-to-JSON wrapper for Stop hooks
- `~/Code/harness-templates/<lang>/.claude/scripts/` — hook scripts and
  `role-block.md` (the contract text)
- `~/Code/harness-templates/python/harness.py` `run()` — canonical quiet output

Never edit anything under `~/Code/harness-templates/`.

## Decide first

1. **Empty dir** → copy a template verbatim (see per-language reference).
   Layer 2 comes inside `.claude/` — keep it.
2. **Existing repo with a runner** (just/make/npm scripts/cargo) → adapt
   Layer 1; reuse the existing runner. Do **not** add a second one.
   Layer 2 is **opt-in** — ask before wiring it; the hooks deny the
   agent's commits and arch-config edits, which surprises an unprepared
   user.
3. **Polyglot / multi-project** → use `monorepo/` Makefile dispatch.

## Language detection

| Signal | Template |
|---|---|
| `pyproject.toml` / `uv.lock` | `python/` → [reference-python.md](reference-python.md) |
| `package.json` + `bun.lock` | `bun/` → [reference-bun.md](reference-bun.md) |
| `Cargo.toml` | `rust/` → [reference-rust.md](reference-rust.md) |
| `go.mod` | `go/` → [reference-go.md](reference-go.md) |
| `Makefile` + multiple subprojects | `monorepo/` → [reference-monorepo.md](reference-monorepo.md) |

Claude/Codex hook + Stop-hook shape: [reference-settings-json.md](reference-settings-json.md).
The Stop hook runs `stop-hook`; `stop-hook` runs `post-edit`, then the
complexity gate and advisory CRAP.
Behavior contract: [reference-behavior-contract.md](reference-behavior-contract.md).

## Layer 1 — the 5-script contract

| Script | When | What | Fixes? |
|---|---|---|---|
| `check` | After edits | fix + format + typecheck + test + suppression report | yes |
| `pre-commit` | Git hook | same, staged files only | yes |
| `ci` | CI pipeline | read-only lint + typecheck + dep audit + complexity + acceptance + tests/coverage + crap (advisory) + arch | no |
| `audit` | CI pipeline | dependency vulnerability audit | no |
| `post-edit` | Stop hook helper | format if source files changed | yes |
| `stop-hook` | Agent Stop hook | post-edit + complexity + crap (advisory) | yes |

Quality subcommands also callable standalone: `complexity`, `crap`,
`acceptance`, `coverage` (Go: `test-cov`), `mutation`, `arch`. Python
`test` runs `unittest`, or `py_compile` over quality targets when no
`tests/test*.py` files exist. Bun `test`, `coverage`, `mutation`, and
`crap` warn and skip when no Bun test files exist. `complexity` runs
`uvx lizard@1.22.2` (CCN≤15, args≤8, length≤100) — all 4 templates, so
`uvx` must be on PATH. `crap` is **advisory by default** (warns; pass
`--enforce` to hard-fail) and runs in `ci` and `stop-hook`.

Suppression report (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`) is
**report-only** — never affects exit code.

Property-based tests run inside the normal `test` step — no extra script.
Each template carries the language's PBT dev-dep (hypothesis / fast-check /
rapid / proptest) and seeds a property suite over its own CRAP and parser
helpers as the worked example. The behavior contract's law-like rule
points agents at that suite.

## Layer 2 — the behavior contract

Greenfield copies inherit it via `.claude/`. For an existing repo, wire it
**only when the user opts in**. Full porting + onboarding steps:
[reference-behavior-contract.md](reference-behavior-contract.md). In short:

- `.claude/scripts/` + `.claude/settings.json` add four hooks around
  `stop-hook`: reinject a role block each session, capture commit/edit
  intent from the user's prompt, deny unauthorized `git commit`/`push`,
  deny unauthorized arch-config edits.
- `AGENTS.md` and `CLAUDE.md` both carry the `## Behavior contract`
  section. The two files hold the same content (the templates'
  `agents-md-drift` check enforces no drift); `role-block.md` must stay
  in sync with that text.

Hook denials require Claude Code's hook runtime; the contract text in
`AGENTS.md`/`CLAUDE.md` applies as instruction to any agent reading the
file.

After wiring Layer 2, **onboard the user** — state plainly what now gets
denied and why (see the reference), so a denied commit reads as designed,
not broken.

## Adapt rules (existing repos)

- Merge into existing `AGENTS.md` / `CLAUDE.md`, configs, and lockfiles.
  Never overwrite. Never reduce an existing contract file to a stub —
  AGENTS.md-consuming tools read it literally, so a stub delivers no
  contract.
- Both `AGENTS.md` and `CLAUDE.md` should hold the same full content:
  - Both files exist → merge the contract into both; wire the drift check.
  - Only `AGENTS.md` exists → add `CLAUDE.md` with identical content.
  - Only `CLAUDE.md` exists → add `AGENTS.md` with identical content.
  - Neither exists → create both.
- Keep the repo's task runner; reuse its invocation prefix in the
  command list.
- Template runner files (`harness.py`, `harness.ts`, `harness.go`,
  `cargo harness`) are for greenfield/copy flow — not the default for
  repos that already have a runner.
- Layer 2 is opt-in: never wire the behavior-contract hooks into a repo
  that did not ask for them.
- The contract's law-like rule (property tests) needs the language's PBT
  dev-dep: hypothesis (python), fast-check (bun), rapid (go), proptest
  (rust). Wire it when porting the contract — or on the first law-like
  change — and model the suite on the template's seeded example.

## Runner output contract

Quiet by default. Mirror `~/Code/harness-templates/python/harness.py` `run()`:

- One short line per step on success.
- Capture stdout/stderr; print only on failure.
- On failure: print failing command + full captured output, then exit.
- `--verbose` escape hatch streams raw command output.
- Zero external deps — stdlib/runtime only.

## Verify (end-to-end)

Layer 1:

1. `check` passes from a fresh tree.
2. `ci` does not mutate tracked files (`git status` clean after).
3. Pre-commit hook fires on a staged change (`.git/hooks/pre-commit` exists).
4. `audit` passes.
5. `stop-hook` runs via Stop hook and includes post-edit formatting (Claude/Codex hooks wired per
   [reference-settings-json.md](reference-settings-json.md)).
6. Suppression report exits 0 regardless of count.
7. Runner imports nothing outside stdlib/runtime.

Layer 2 (only if wired):

8. A new/resumed session prints the role block (SessionStart hook).
9. An unprompted `git commit` is denied; a user-requested one passes.
10. An unauthorized arch-config edit is denied; a path-named one passes.
11. `.claude/state/` is git-ignored.
