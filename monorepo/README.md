# monorepo template

A Makefile that dispatches `check` / `pre-commit` / `pre-push` / `ci` into N subprojects — each subproject keeps its own zero-dep harness (`bun`, `python`, `go`, or `rust`).

## When to use

Use this template when a single repo holds multiple subprojects in different languages (e.g. `api/` in Python, `web/` in Bun) and you want one place to run quality checks. For a single-language repo, use the language-specific template directly instead.

## Getting started

Run these commands from the root of `harness-templates`:

```bash
cp -r monorepo/ my-project
cp -r python/ my-project/api
cp -r bun/ my-project/web
# Customize each copied subproject's package/module metadata before committing.
cd my-project
git init
git add . && git commit -m "Initial monorepo template"
make workspace

make check          # run check across every subproject
make check-api      # scope to one subproject
```

The [root workspace contract](../README.md#autonomous-workspace) supports macOS
and glibc Linux on `x86_64` and `arm64`. The VM supplies Make, Bash, Git, curl,
tar, Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`; include `cc` when
the monorepo contains Rust. No ambient language toolchains are prerequisites.

`make workspace` requires a clean tracked/index state and preserves untracked
files. It discovers and deduplicates top-level project markers once, provisions
the union of required language profiles once, restores locked dependencies,
deploys skills, owns the root Git and Stop hooks, verifies the result, and runs
`make check`. It never calls a child bootstrap target.

After an online run has populated every required tool and dependency cache, the
same workspace converges without network access:

```bash
make workspace OFFLINE=1
```

Offline mode makes no network requests and fails on a cold or incomplete
cache. `make bootstrap` and the retained `make setup` command are compatibility
aliases for `make workspace`.

`make` with no arguments prints help and does not mutate files.

## Targets

| Target | What it does |
|---|---|
| `make` / `make help` | Show help + detected subprojects |
| `make check` | Run `check` across all subprojects (auto-fix + suppression ratchet) |
| `make check-<name>` | Run `check` in one subproject (tab-complete via help) |
| `make check-dirty` | Run `check` only in subprojects with working-tree changes |
| `make pre-commit` | Run `pre-commit` only in subprojects with staged files |
| `make pre-push` | Read-only push gate across all subprojects (lint, format check, acceptance, arch) |
| `make ci` | Read-only gate across all subprojects (no fixes); each subproject runs its read-only gates in parallel |
| `make test` | Run tests only, all subprojects |
| `make list` | Show detected subprojects |
| `make deps` | Restore each subproject's committed native lock exactly once, in lexical order |
| `make workspace` | Provision the profile union, locked dependencies, skills, root hooks, verification, and `make check` |
| `make workspace OFFLINE=1` | Repeat convergence from warm caches without network access |
| `make bootstrap` / `make setup` | Compatibility aliases for `make workspace` |
| `make setup-hooks` | Reinstall collision-safe managed root hooks; normal setup uses `make workspace` |
| `make clean` | Delegate `clean` to each subproject |

Scoped variants exist for `ci-<name>`, `pre-push-<name>`, `test-<name>`, `pre-commit-<name>`.

Flags:

- `VERBOSE=1 make check` — forward `--verbose` to each subproject's harness.
- `PARALLEL=1 make check` — fan out subprojects with `xargs -P$(JOBS)`. Per-subproject output is buffered and dumped in-order on completion; exit status matches the sequential run. Off by default — CI logs, the Stop hook, and agent-visible runs stay sequential.

Parallelism applies only to quality-command dispatch. Workspace dependency
restoration always remains serial: it calls every detected child's `make deps`
once in lexical order, and each child preserves its native lock.

## Continuous integration

`.github/workflows/ci.yml` runs the same checked-in contract used locally on
every push to `main` and every pull request:

```bash
make workspace
make ci
```

There are no floating runtime setup steps to trim. The workspace provisioner
selects the exact profile union for the detected subprojects.

## How it works

Subproject discovery is filename-based. A top-level directory is a subproject when it contains one of:

| File | Profile | Root-managed runner invoked |
|---|---|---|
| `harness.ts` | bun | `.harness/workspace.sh exec bun -- bun harness.ts <cmd>` |
| `harness.py` | python | `.harness/workspace.sh exec python -- uv run --frozen --no-sync harness <cmd>` |
| `harness.go` | go | `.harness/workspace.sh exec go -- go run -mod=readonly harness.go <cmd>` |
| `Cargo.toml` | rust | `.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- <cmd>` |

The root classifies each top-level directory once in table order. If a directory
contains multiple recognized markers, the first match wins and that directory
appears only once. The resulting profile set is deduplicated before workspace
installs tools. Normal and scoped targets always enter the root provisioner's
managed environment, then run from the child directory. The Makefile continues
past failures and prints an aggregate summary.

`make pre-commit` is auto-scoped: it reads `git diff --cached --name-only`, maps each staged path to its top-level subproject directory, and runs `pre-commit` only in the affected ones. Staged files outside any subproject are ignored.

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract at the monorepo root. Agents that read either file receive the same instructions across every subproject:

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: do not `git commit` / `git push` unless the user's current prompt explicitly asked.
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Arch config guard**: edits to any subproject's arch config (`.importlinter`, `.dependency-cruiser.json`, `.go-arch-lint.yml`, `arch.toml`) warn during `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

Workspace installs the root Git hooks and verifies the checked-in Claude and
Codex Stop configuration. Every root Git and Stop hook enters the Git root and
calls `make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies
the exact managed environment. Unknown existing Git hooks cause setup to fail
without modification. Only exact legacy harness shims are migrated.

The root pre-push target captures its stdin once and replays the exact input to
the architecture guard and every child pre-push runner, including parallel
dispatch. Child hook files and Stop wiring remain dormant while the monorepo
root owns the workspace; they are available if a child is later used alone.

## Adding a subproject

```bash
cp -r ../harness-templates/<lang>/ <name>
make list   # confirm it's detected
```

No Makefile edit. Discovery is automatic.

## Adding a language (maintainers)

To support a new language:

1. Add its single-language template to `harness-templates/` (see `CONTRIBUTING.md`).
2. Edit three spots in the monorepo Makefile:
   - Add a `<LANG>_DIRS := $(patsubst ...)` line in the discovery section and append it to `SUBPROJECTS`.
   - Add a case to `lang_of()` and `runner_of()` inside the `SH_LANG_HELPERS` define. (`SH_FILTER_DIRS` is language-agnostic; no edit needed there.)
   - Add its managed profile to `WORKSPACE_PROFILES` and to the checked-in provisioner manifest.
3. Update the README "How it works" table.

## Design principles

- **Dispatch only** — fix, lint, typecheck, and test logic stays in each subproject's harness. Quality targets are pure routing; workspace orchestration delegates to the checked-in provisioner.
- **Quiet by default** — subprojects already print `✓`/`✗` summaries; the Makefile adds a one-line header per subproject and an aggregate footer.
- **Fail-slow** — `check` and `ci` continue past failure so you see every red subproject in one run.
- **`ci` never fixes** — the read-only gate stays read-only.
- **Checked-in provisioning** — `.harness/workspace.sh` owns exact tools, locks, skills, and hooks; the Makefile owns routing.
- **Subprojects remain standalone** — `cd <subproject> && make check` uses that template's managed workspace boundary.

## Pitfalls for Make newcomers

- Recipes must be indented with **tabs**, not spaces.
- Prefix recipe lines with `@` to suppress command echo.
- Declare non-file targets as `.PHONY` (already done for all targets here).
- Run `make` from the repo root — per-subproject harnesses expect CWD = their own dir, and the Makefile handles that.
