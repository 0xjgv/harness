# behavior-contract

Layer 2 of the harness: instruction text plus two integration guards, one for
architecture config changes and one for pushes to protected branches.
Greenfield template copies include it by default.
For an existing repo, port it when the user wants the full behavior contract.

Source files:

- `~/Code/harness-templates/<lang>/AGENTS.md`
- `~/Code/harness-templates/<lang>/CLAUDE.md`
- `~/Code/harness-templates/<lang>/<runner>` (`harness.py`, `harness.ts`,
  `harness.go`, `harness.rs`, or `monorepo/Makefile`)

## What it enforces

The contract lives in two places that must agree:

- `AGENTS.md` and `CLAUDE.md` `## Behavior contract` — four `<important>`
  blocks the agent reads as instructions. Both files hold the same content
  byte-for-byte. The templates' `agents-md-drift` check enforces no drift,
  and `sync-agents-md` writes `AGENTS.md <- CLAUDE.md` after edits.
- The runner command `arch-config-guard` — a portable git-based guard that
  detects protected architecture config changes. It warns during `check`,
  `pre-commit`, and `stop-hook`; it fails `pre-push` and `ci` unless
  `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- The runner command `branch-guard` — refuses direct pushes to, or deletions
  of, `main`/`master`. It runs first in `pre-push`, reads
  `HARNESS_PRE_PUSH_REFS`, else the git pre-push stdin refs, else the current
  branch, and passes with `HARNESS_ALLOW_PROTECTED_PUSH=1`. It stops
  accidents, not intent: `git push --no-verify`, local merges, and hosted
  merges bypass it, so merge ownership stays a rule the agent follows unless
  server-side branch protection enforces it.

| Rule | Contract says | Mechanical enforcement |
|---|---|---|
| Plan first | open with sub-tasks + files, then execute in the same turn | instruction only |
| Human owns merge | commit/push on a feature branch; never `main`/`master`, force-push, or merge | `branch-guard` blocks direct pushes in `pre-push`; merge itself is instruction (or server branch protection) |
| Specify what is worth specifying | `.feature` + step defs + impl in one turn for user-visible flows, law-like rules, and cross-component contracts; unit tests suffice for the rest; review judges | instruction only |
| Arch config review | no silent arch-config changes; own commit with rationale | `arch-config-guard` blocks push/CI |

The rules are outcome checks, not process gates: nothing stops the agent
mid-turn to wait for approval. The bet is that review of a branch catches a
misread of intent at least as well as a mid-turn question did, and costs a
capable agent far fewer turns; the contract therefore no longer caps files
per sub-task or asks before editing when a classification is unclear. The
agent states its plan and classification and proceeds, and the human reviews
the branch. The one remaining pre-publication approval is the arch config
guard. There are no Claude-only prompt classifiers or pre-tool edit/commit
gates.

## Arch config guard

Deleting a protected config counts as a change: the scans do not filter out
deletions, because a missing config makes the arch gate skip and would
otherwise slip past review.

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
- `pre-commit`: warning mode over staged paths.
- `pre-push`: strict mode, including git pre-push stdin refs when available.
- `ci`: strict mode. GitHub Actions checkout uses `fetch-depth: 0` so PR runs
  can compare `origin/$GITHUB_BASE_REF...HEAD`.

## Branch guard

Every template exposes `branch-guard` through its runner (`uv run harness
branch-guard`, `bun harness.ts branch-guard`, `go run harness.go
branch-guard`, `cargo harness branch-guard`, `make branch-guard`).

- Protected: `main`, `master`.
- Ref source, in order: `HARNESS_PRE_PUSH_REFS` (set by dispatchers such as
  the monorepo Makefile so child harnesses see the real destinations), else
  git pre-push stdin lines (`<local ref> <local sha> <remote ref> <remote
  sha>`), else the current branch.
- A push is protected when any remote ref is `refs/heads/main` or
  `refs/heads/master`, including deletions (all-zero local sha). Tags and
  other non-branch refs never match.
- Stdin is read at most once per `pre-push` and shared with the arch guard.
  The read has a one-second deadline: empty EOF or an idle pipe (agent
  tools, CI) means no refs and falls back to the current branch; data that
  arrives without EOF inside the deadline is incomplete and fails both
  guards (`✗ Pre-push refs incomplete after 1s`) rather than guessing.
- `HARNESS_ALLOW_PROTECTED_PUSH=1`: explicit human override (solo repos that
  push straight to `main` export it once). Tests that spawn the harness strip
  it so an ambient override cannot flip a refusal scenario.
- Wired first in `pre-push` only; a refusal (or incomplete refs) exits before any
  other gate runs, including drift checks and the parallel batch.

## Existing repo port

When the user asks for the behavior contract in an existing repo:

1. Merge the `## Behavior contract` section into both `AGENTS.md` and
   `CLAUDE.md`. If only one exists, create the other with identical content.
2. Add `agents-md-drift` and `sync-agents-md` so the two files stay identical.
3. Add `arch-config-guard` for the repo's real architecture config path, or
   skip it explicitly if the repo has no architecture config.
4. Wire the guard into `check`/`pre-commit`/`stop-hook` as warning mode and
   into `pre-push`/`ci` as strict mode.
5. Add `branch-guard` and run it first in `pre-push`.
6. Keep Claude and Codex Stop hook wiring from [settings-json.md](settings-json.md).

Do not add `.claude/scripts/` behavior hooks; the templates no longer use
SessionStart, UserPromptSubmit, or PreToolUse gates.

## Onboarding

Tell the user:

- "The agent commits and pushes on feature branches and opens PRs. Merge is
  yours. `pre-push` refuses `main`/`master`; if you push straight to `main`
  yourself, export `HARNESS_ALLOW_PROTECTED_PUSH=1`."
- "Architecture config changes warn during `check`/`pre-commit`/`stop-hook`
  and fail `pre-push`/`ci` unless reviewed with `HARNESS_ALLOW_ARCH_CONFIG=1`.
  The agent is told to isolate such a change in its own commit and report
  the refused push to you."
- "A guard failure means the arch config changed and needs review; either undo
  it or rerun the integration command with the override after review."

## Verify

1. `AGENTS.md` and `CLAUDE.md` are byte-identical.
2. `agents-md-drift` fails when they differ.
3. `arch-config-guard --warn` reports changed protected config paths without
   failing.
4. `arch-config-guard` fails on changed protected config paths without
   `HARNESS_ALLOW_ARCH_CONFIG=1`.
5. `HARNESS_ALLOW_ARCH_CONFIG=1 <runner> arch-config-guard` passes and prints
   the override line.
6. `check`, `pre-commit`, and `stop-hook` warn on protected config changes.
7. `pre-push` and `ci` fail on protected config changes unless the override
   is set.
8. `branch-guard` fails on `main`, passes with
   `HARNESS_ALLOW_PROTECTED_PUSH=1`, passes with a feature-branch ref on
   stdin, refuses deletion of `main`, honours `HARNESS_PRE_PUSH_REFS`,
   returns within ~1s on an idle pipe (`sleep 5 | <runner> branch-guard`),
   and fails on partial input (`(printf 'a b refs/heads/x d'; sleep 5) |
   <runner> branch-guard`).
9. On a new-branch push (all-zero remote sha) the arch guard diffs the whole
   branch against its merge-base with `origin/main`/`origin/master`, not the
   tip commit: two commits, arch change in the first, still reported.
