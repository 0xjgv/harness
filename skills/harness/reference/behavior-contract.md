# behavior-contract

Layer 2 of the harness: instruction text plus two mechanical integration
guards — one for architecture config changes, one for Gherkin-first.
Greenfield template copies include it by default. For an existing repo, port
it when the user wants the full behavior contract.

Source files:

- `~/Code/harness-templates/<lang>/AGENTS.md`
- `~/Code/harness-templates/<lang>/CLAUDE.md`
- `~/Code/harness-templates/<lang>/<runner>` (`harness.py`, `harness.ts`,
  `harness.go`, `harness.rs`, or `monorepo/Makefile`)

## What it enforces

The contract lives in three places that must agree:

- `AGENTS.md` and `CLAUDE.md` `## Behavior contract` — four `<important>`
  blocks the agent reads as instructions. Both files hold the same content
  byte-for-byte. The templates' `agents-md-drift` check enforces no drift,
  and `sync-agents-md` writes `AGENTS.md <- CLAUDE.md` after edits.
- The runner command `arch-config-guard` — a portable git-based guard that
  detects protected architecture config changes. It warns during `check` and
  `stop-hook`; it fails `pre-commit`, `pre-push`, and `ci` unless
  `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- The runner command `gherkin-guard` — a portable git-based guard that
  detects a changed production-source file with no accompanying changed
  `.feature` file. It warns during `check` and `stop-hook`; it fails
  `pre-commit`, `pre-push`, and `ci` unless `HARNESS_ALLOW_NO_FEATURE=1` is
  set after review. It skips silently, everywhere, when the repo has no
  `.feature` files anywhere yet.

| Rule | Contract says | Mechanical enforcement |
|---|---|---|
| Task sizing | <=5 sub-tasks, each <=1 non-test file + <=1 test | instruction only |
| Human owns commits | no `git commit`/`push` unless the prompt asked | instruction only |
| Gherkin-first | `.feature` -> approval -> step defs -> impl for behavior changes | `gherkin-guard` blocks integration |
| Arch config review | no silent arch-config changes | `arch-config-guard` blocks integration |

There are no Claude-only prompt classifiers or pre-tool edit/commit gates. The
agent can work without prompt-state approval machinery; integration commands
still catch protected architecture config changes and Gherkin-first
violations before commit, push, or CI.

## Arch config guard

Every template exposes `arch-config-guard` through its runner:

| Template | Command | Protected path |
|---|---|---|
| python | `uv run harness arch-config-guard` | `.importlinter` |
| bun | `bun harness.ts arch-config-guard` | `.dependency-cruiser.json` |
| go | `go run harness.go arch-config-guard` | `.go-arch-lint.yml` |
| rust | `cargo harness arch-config-guard` | `arch.toml` |
| monorepo | `make arch-config-guard` | all four names, basename-matched anywhere |

Modes:

- Default: fail if a protected arch config changed.
- `--warn`: print an advisory warning and exit 0.
- `--staged`: inspect staged paths only, for pre-commit.
- `HARNESS_ALLOW_ARCH_CONFIG=1`: explicit reviewed override for strict mode.

Stage wiring:

- `check`: warning mode.
- `stop-hook`: warning mode.
- `pre-commit`: strict staged mode.
- `pre-push`: strict mode, including git pre-push stdin refs when available.
- `ci`: strict mode. GitHub Actions checkout uses `fetch-depth: 0` so PR runs
  can compare `origin/$GITHUB_BASE_REF...HEAD`.

## Gherkin-first guard

Every language template exposes `gherkin-guard` through its runner. It
triggers when a changed "production source" file (language-specific: `src/`
for python/bun/rust, non-test `.go` files outside `features/` for go, always
excluding the harness runner file itself) has no changed path ending in
`.feature`, and it skips entirely, with no output, when the template has no
`.feature` files anywhere yet — this is deliberate and load-bearing for
adoption: retrofitting the harness into a repo with no acceptance suite must
never block.

| Template | Command | Production-source scope |
|---|---|---|
| python | `uv run harness gherkin-guard` | `src/` (excludes `harness.py`, `tests/`) |
| bun | `bun run gherkin-guard` | `src/` (excludes `harness.ts`, `tests/`) |
| go | `go run harness.go gherkin-guard` | non-test `.go` files outside `features/` (excludes `harness.go`) |
| rust | `cargo harness gherkin-guard` | `src/` (excludes `harness.rs`, `tests/`) |
| monorepo | none at the root — each subproject's own `check`/`ci`/`pre-commit`/`pre-push` runs its own `gherkin-guard` | per subproject, as above |

Modes: same shape as `arch-config-guard` — default fails, `--warn` is
advisory, `--staged` scopes to staged paths, `HARNESS_ALLOW_NO_FEATURE=1` is
the reviewed override for strict mode.

Stage wiring: identical to `arch-config-guard` above — warning mode in
`check`/`stop-hook`, strict mode in `pre-commit` (staged), `pre-push`
(including stdin refs), and `ci`.

## Existing repo port

When the user asks for the behavior contract in an existing repo:

1. Merge the `## Behavior contract` section into both `AGENTS.md` and
   `CLAUDE.md`. If only one exists, create the other with identical content.
2. Add `agents-md-drift` and `sync-agents-md` so the two files stay identical.
3. Add `arch-config-guard` for the repo's real architecture config path, or
   skip it explicitly if the repo has no architecture config.
4. Add `gherkin-guard` for the repo's production-source scope, or skip it
   explicitly if the repo has no acceptance suite and the user does not want
   one yet — it self-skips anyway until the first `.feature` file lands.
5. Wire both guards into `check`/`stop-hook` as warning mode and into
   `pre-commit`/`pre-push`/`ci` as strict mode.
6. Keep Claude and Codex Stop hook wiring from [settings-json.md](settings-json.md).

Do not add `.claude/scripts/` behavior hooks; the templates no longer use
SessionStart, UserPromptSubmit, or PreToolUse gates.

## Onboarding

Tell the user:

- "The agent is instructed not to `git commit`/`push` unless your prompt asks
  for it; this is instruction, not a pre-tool denial."
- "Architecture config changes warn during `check`/`stop-hook` and fail
  `pre-commit`/`pre-push`/`ci` unless reviewed with
  `HARNESS_ALLOW_ARCH_CONFIG=1`."
- "A changed production-source file with no matching `.feature` change warns
  during `check`/`stop-hook` and fails `pre-commit`/`pre-push`/`ci` unless
  reviewed with `HARNESS_ALLOW_NO_FEATURE=1` — this only starts applying once
  the repo has at least one `.feature` file."
- "A guard failure means the arch config changed, or a behavior change shipped
  with no `.feature`, and needs review; either undo it or rerun the
  integration command with the override after review."

## Verify

1. `AGENTS.md` and `CLAUDE.md` are byte-identical.
2. `agents-md-drift` fails when they differ.
3. `arch-config-guard --warn` reports changed protected config paths without
   failing.
4. `arch-config-guard` fails on changed protected config paths without
   `HARNESS_ALLOW_ARCH_CONFIG=1`.
5. `HARNESS_ALLOW_ARCH_CONFIG=1 <runner> arch-config-guard` passes and prints
   the override line.
6. `gherkin-guard --warn` reports a changed production-source file with no
   `.feature` change without failing.
7. `gherkin-guard` fails on that same change without
   `HARNESS_ALLOW_NO_FEATURE=1`, once the repo has at least one `.feature` file.
8. `gherkin-guard` passes silently in a repo with no `.feature` files anywhere.
9. `HARNESS_ALLOW_NO_FEATURE=1 <runner> gherkin-guard` passes and prints the
   override line.
10. `check` and `stop-hook` warn on protected config changes and on
    production-source changes with no `.feature`.
11. `pre-commit`, `pre-push`, and `ci` fail on protected config changes and on
    production-source changes with no `.feature`, unless the matching override
    is set.
