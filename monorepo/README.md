# monorepo template

A Makefile that dispatches `check` / `pre-commit` / `ci` into N subprojects — each subproject keeps its own zero-dep harness (`bun`, `python`, `go`, or `rust`).

## When to use

Use this template when a single repo holds multiple subprojects in different languages (e.g. `api/` in Python, `web/` in Bun) and you want one place to run quality checks. For a single-language repo, use the language-specific template directly instead.

## Getting started

```bash
cp -r monorepo/ my-project && cd my-project
git init

# Add subprojects (use any of the single-language templates):
cp -r ../harness-templates/python/ api
cp -r ../harness-templates/bun/    web

# One-time install + git hook:
make bootstrap

# Daily loop:
make check          # run check across every subproject
make check-api      # scope to one subproject
```

`make` with no arguments prints help. It never mutates files.

## Targets

| Target | What it does |
|---|---|
| `make` / `make help` | Show help + detected subprojects |
| `make check` | Run `check` across all subprojects (auto-fix) |
| `make check-<name>` | Run `check` in one subproject (tab-complete via help) |
| `make check-dirty` | Run `check` only in subprojects with working-tree changes |
| `make pre-commit` | Run `pre-commit` only in subprojects with staged files |
| `make ci` | Read-only gate across all subprojects (no fixes) |
| `make test` | Run tests only, all subprojects |
| `make list` | Show detected subprojects |
| `make setup` | Per-language deps: `bun install`, `uv sync`, `go mod download`, `cargo build` |
| `make setup-hooks` | Install `.git/hooks/pre-commit` → `make pre-commit`. Re-run with `FORCE=1` to overwrite an existing hook |
| `make bootstrap` | `setup` + `setup-hooks` |
| `make clean` | Delegate `clean` to each subproject |

Scoped variants exist for `ci-<name>`, `test-<name>`, `pre-commit-<name>`.

Flags:

- `VERBOSE=1 make check` — forward `--verbose` to each subproject's harness.
- `PARALLEL=1 make check` — fan out subprojects with `xargs -P$(JOBS)`. Per-subproject output is buffered and dumped in-order on completion; exit status matches the sequential run. Off by default — CI logs, the Stop hook, and agent-visible runs stay sequential.

## How it works

Subproject discovery is filename-based. A top-level directory is a subproject when it contains one of:

| File | Language | Runner invoked |
|---|---|---|
| `harness.ts` | bun | `bun harness.ts <cmd>` |
| `harness.py` | python | `uv run harness <cmd>` |
| `harness.go` | go | `go run harness.go <cmd>` |
| `Cargo.toml` | rust | `cargo harness <cmd>` |

The Makefile fans out `<cmd>` to each matching subproject, continues past failures, and prints an aggregate summary.

`make pre-commit` is auto-scoped: it reads `git diff --cached --name-only`, maps each staged path to its top-level subproject directory, and runs `pre-commit` only in the affected ones. Staged files outside any subproject are ignored.

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
   - Add the install command to the `setup` target.
3. Update the README "How it works" table.

## Design principles

- **Dispatch only** — fix, lint, typecheck, test logic stays in each subproject's harness. The Makefile is pure routing.
- **Quiet by default** — subprojects already print `✓`/`✗` summaries; the Makefile adds a one-line header per subproject and an aggregate footer.
- **Fail-slow** — `check` and `ci` continue past failure so you see every red subproject in one run.
- **`ci` never fixes** — the read-only gate stays read-only.
- **No external helper script** — self-contained in the Makefile.
- **Subprojects remain standalone** — `cd <subproject> && <its runner> check` still works.

## Pitfalls for Make newcomers

- Recipes must be indented with **tabs**, not spaces.
- Prefix recipe lines with `@` to suppress command echo.
- Declare non-file targets as `.PHONY` (already done for all targets here).
- Run `make` from the repo root — per-subproject harnesses expect CWD = their own dir, and the Makefile handles that.
