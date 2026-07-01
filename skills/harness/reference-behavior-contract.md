# reference-behavior-contract

Layer 2 of the harness: a hook-enforced behavior contract. Greenfield
template copies include it automatically inside `.claude/`. For an
existing repo, wire it **only when the user opts in** — the hooks deny the
agent's commits and arch-config edits, which is surprising if unexpected.

Source files: `~/Code/harness-templates/<lang>/.claude/`.

## What it enforces

The contract lives in two places that must agree:

- `AGENTS.md` and `CLAUDE.md` `## Behavior contract` — four `<important>`
  blocks the agent reads as instructions. Both files hold the same
  content byte-for-byte. The templates' `agents-md-drift` check enforces
  no drift, and `sync-agents-md` writes `AGENTS.md ← CLAUDE.md` after
  edits.
- `.claude/scripts/` + `.claude/settings.json` — hooks that enforce them
  mechanically, so the contract survives `/clear`, `/compact`, and resume.
  SessionStart extracts the contract from `CLAUDE.md`; there is no separate
  role block copy.

| Rule | Contract says | Hook enforcement |
|---|---|---|
| Task sizing | ≤5 sub-tasks, each ≤1 non-test file + ≤1 test | instruction only |
| Human owns commits | no `git commit`/`push` unless the prompt asked | `pre-bash-gate.sh` denies it |
| Gherkin-first | `.feature` → approval → step defs → impl for behavior changes | instruction only |
| Config write-protection | no silent arch-config edits | `pre-edit-gate.sh` denies it |

## The hook scripts (`.claude/scripts/`)

| File | Hook event | Role |
|---|---|---|
| `session-start.sh` | SessionStart | extracts every `<important>…</important>` block from `CLAUDE.md` so the contract survives `/clear`, `/compact`, resume |
| `ups-classify.sh` | UserPromptSubmit | scans the prompt; writes short-TTL state files to `.claude/state/` |
| `pre-bash-gate.sh` | PreToolUse(Bash) | denies `git commit`/`push` unless unexpired `commit-intent` state exists |
| `pre-edit-gate.sh` | PreToolUse(Write\|Edit\|MultiEdit) | denies edits to the arch config unless `edit-auth` state names that path |

The hooks are wired via Claude Code's `.claude/settings.json`. The
contract text in `AGENTS.md`/`CLAUDE.md` applies as instruction to any
agent reading the file; only the hook-enforced denials require Claude
Code's hook runtime.

## Turn-bounded authorization (`.claude/state/`)

`.claude/state/` holds `commit-intent` and `edit-auth` — JSON files each
carrying an `expires_at` (default TTL 300s, override via
`COMMIT_INTENT_TTL`). `ups-classify.sh` **wipes both on every prompt**, so
authorization is turn-bounded: a commit verb in one prompt does not
authorize a commit two prompts later.

How intent is captured:

- **commit-intent** — written when the prompt contains
  `commit|push|ship|land|merge` in action context.
- **edit-auth** — written only when an edit verb (`edit|update|change|
  modify|write|fix|…`) and the protected path co-occur within ~80 chars
  (e.g. "edit `.importlinter` to add a layer"). Incidental mentions of the
  path do not authorize.

`.claude/state/` is git-ignored — per-session scratch, never tracked.

## Per-language protected arch config

`ups-classify.sh` and `pre-edit-gate.sh` each carry a `PROTECTED` list.
It differs per language:

| Template | Arch config | Arch tool |
|---|---|---|
| python | `.importlinter` | import-linter |
| bun | `.dependency-cruiser.json` | dependency-cruiser |
| go | `.go-arch-lint.yml` | go-arch-lint |
| rust | `arch.toml` | cargo-modules |
| monorepo | all four, basename + suffix-matched | per-subproject |

When copying the scripts to a new language or an existing repo, update
`PROTECTED` in **both** scripts — and the example path in
`pre-edit-gate.sh`'s deny message — to the repo's real arch config.

## settings.json

All five hooks wire through `.claude/settings.json`. Full shape:
[reference-settings-json.md](reference-settings-json.md).

## Greenfield — nothing to do

`cp -r ~/Code/harness-templates/<lang>/` brings `.claude/` intact. Verify
the hooks fire (see below), then onboard the user.

## Existing repo — opt-in port

Only when the user explicitly asks for the behavior contract:

1. Copy `.claude/scripts/` verbatim. Adjust `PROTECTED` in
   `ups-classify.sh` and `pre-edit-gate.sh` to the repo's arch config — or
   drop config write-protection entirely if the repo has no arch tool.
2. Merge the four contract hooks into `.claude/settings.json` alongside
   the existing `Stop` hook — preserve every other key.
3. Append the `## Behavior contract` section to **both** `AGENTS.md` and
   `CLAUDE.md` (or wire the harness `agents-md-drift` check to keep them
   in sync — `sync-agents-md` writes AGENTS.md ← CLAUDE.md). SessionStart
   extracts `<important>` blocks from `CLAUDE.md`; no separate role block is
   copied. If only one file exists, copy the contract into the other; never
   reduce either to a stub.
4. Git-ignore `.claude/state/`.
5. Verify (below), then onboard the user.

## Onboarding — what to tell the user after wiring

State plainly, so a denied action reads as designed, not broken:

- "The agent will not `git commit`/`push` unless your prompt asks for it
  (verbs: commit, push, ship, land, merge). Authorization lasts one turn."
- "Edits to `<arch config>` are denied unless you name the file in your
  prompt."
- "Every new or resumed session reprints the role block — that is the
  contract reasserting itself, not noise."
- "A denied hook is working as intended. Re-prompt with explicit intent."

## Verify (Layer 2)

1. Start a fresh session — the role block prints (SessionStart).
2. With no commit verb in the prompt, have the agent try `git commit` —
   `pre-bash-gate.sh` denies it.
3. Prompt with a commit verb — the commit passes within the TTL window.
4. Have the agent edit the arch config without the prompt naming it —
   `pre-edit-gate.sh` denies it.
5. Prompt naming the arch config path — the edit passes.
6. `.claude/state/` is git-ignored and absent from tracked files.
