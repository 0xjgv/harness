---
name: ratchet
description: >
  Improve a harness-adopting repo by one notch — raise test coverage and
  mutation score, lower CRAP, complexity, duplication, arch violations, and
  dead code floors — with zero risk of changing behavior. Use when asked to
  "ratchet the baseline", "raise the coverage floor", "improve CRAP", "kill
  surviving mutants", "pay down complexity debt", "turn the crank on tech
  debt", or "run the ratchet skill". Only applies to a repo that already has
  the harness-templates quality contract (`.harness-baseline`,
  `harness crap` / `harness coverage` / `harness mutation` /
  `harness complexity` / `harness arch` / `harness suppressions`).
---

# Ratchet

Turn a repo's `.harness-baseline` floors up by one notch — never down — by
adding tests, never by touching production code. The safety property comes
from a **file allowlist**, not from careful prompting. That distinction is
the whole design: an agent that writes only test files cannot change
behavior, no matter how it reasons about the target.

## Source of truth

Always read first. Do not restate commands from memory.

- The target repo's `.harness-baseline` — the floors you are about to raise.
- The language reference for the repo:
  - Python: [python.md](reference/python.md)
- `~/Code/harness-templates/CLAUDE.md` — the "## Role" section (human owns
  commits and pushes; this skill never does either).

## Baseline keys

The floors this skill moves, by name, as they appear in `.harness-baseline`.
The key names are identical in all four languages; only the suppression
kinds differ:

```
arch.max_violations         # boundary violations reported by the arch tool
complexity.max_violations   # lizard warnings over CCN 15 / args 8 / length 100
coverage.min                # coverage percent
crap.max_violations         # count of functions over the CRAP threshold
deadcode.max_findings       # python/bun deadcode findings
duplication.max_blocks      # lizard -Eduplicate duplicate blocks
mutation.min                # integer % of mutants killed on the last scoped run
suppressions.<kind>         # per-kind suppression counts (noqa, type_ignore, ...)
```

A key that is missing from the file is report-only for that gate; this
skill may add it, at the measured value, through the same writer it uses for
everything else.

`mutation.min` takes two commands, and only two. Read the score with
`<prefix> mutation --all` — the standalone command scoped to the whole tree,
which also prints the survivors. Record it with
`<prefix> suppressions --update-baseline --with-mutation`; the plain
`--update-baseline` never touches `mutation.min`, it carries the existing
value through untouched, because a mutation run costs minutes. The score is
`round(100 * killed / (killed + survived))`, so it moves up by killing
surviving mutants with tests — nothing else raises it.

CRAP = `ccn^2 * (1-cov)^3 + ccn`. It weights uncovered complexity cubically,
so covering a single high-CRAP function tends to move `coverage.min` and
`crap.max_violations` in the same run — that is the highest-leverage target,
and the language's `crap` command already prints offenders sorted
descending. Pick targets from that list, top first. Surviving mutants
(`mutation` prints them) are the second list: each one is a test that does
not exist yet, in a function that already has coverage.

## The loop

Run this once per invocation. One run raises each floor at most one notch.

1. **Measure.** Record the current value of every baseline key above before
   touching anything.
2. **Pick targets.** Read the `crap` offender list, highest CRAP first. Skip
   any offender you cannot cover from the test directory alone (see
   guardrails). Then the surviving mutants from `<prefix> mutation --all`,
   each named in its output. Then the
   **changed-but-untested modules**: every `⚠` line the scoped `test` gate
   printed for a changed source file with no mapped test. Those get a
   characterization test — a test that pins what the code does today, in
   the test file the mapping rule expects (see the language reference), so
   the next change to that module runs under a test. Assert current
   behavior, not intended behavior; a characterization test that fails on
   the unchanged code is a bug report for the human, not a reason to edit
   source.
3. **Write tests.** Add or extend tests under the test directory only.
   Cover the picked offenders. Use property-based tests when the behavior is
   law-like (a formula, parser, codec, or invariant) — see the language
   reference for which library.
4. **Re-measure.** Run the full suite (`test --all`). It must be green.
   Recompute every baseline key, and re-run `<prefix> mutation --all` if you
   killed a mutant — that run is the only source of the new `mutation.min`.
5. **Raise the floors one notch**, and only the floors that actually moved:
   `coverage.min` and `mutation.min` up; `crap.max_violations`,
   `complexity.max_violations`, `duplication.max_blocks`,
   `arch.max_violations`, and `deadcode.max_findings` down when the
   re-measured count is below the floor — which happens when a covered
   function also dropped in violation count, when duplicate blocks lived in
   test files you consolidated, or when a human's cleanup was never
   re-baselined. Write the new values into `.harness-baseline` via the
   language's `suppressions --update-baseline` (add `--with-mutation` when
   `mutation.min` moved; that is the only way it gets written) — never
   hand-edit the numbers past what was actually measured.
6. **Stop.** Report what moved and what the next highest-leverage target is.
   Do not start a second pass in the same run.

## Guardrails

These are load-bearing. Follow them exactly; do not "improve" on them.

- **File allowlist: the test directory and `.harness-baseline`. Nothing
  else.** If a target function cannot be tested without touching production
  source (missing seam, needs a constructor change, needs a new export),
  refuse that target and report it as human work. Do not make the
  production edit "just this once."
- **Never lower a floor.** If a floor cannot be raised this run, say why and
  stop — do not weaken `.harness-baseline` to make the run look like it
  succeeded.
- **Never add a suppression to make a test pass.** The suppression baseline
  would eventually catch a growing count, but do not rely on that backstop —
  do not reach for `# noqa` / `# type: ignore` / `# pyright: ignore` at all
  during this skill's run.
- **Complexity, duplication, arch, and deadcode offenders in production
  source are reported, never refactored.** A falling
  `complexity.max_violations` or `arch.max_violations` in `src/` only
  happens when a human extracts, restructures, or moves the offending code.
  Extracting a helper is a behavior-change risk this skill does not take,
  even when it would also improve CRAP. Duplicate blocks inside the test
  directory are the one exception: they are inside the allowlist, and
  consolidating them is test work.
- **Never touch the arch config** to move `arch.max_violations`. The
  `arch-config-guard` blocks that at integration anyway; do not make it
  block on your run.
- **Abort if the suite was red before the run.** This skill improves a green
  tree; it does not repair a broken one. If step 1's measurement can't
  complete because tests are already failing, stop immediately and report
  that as a separate, human-owned problem.
- **Never commit or push.** Leave the working tree dirty for the human to
  review and commit. The repo's behavior contract reserves commit/push
  ownership for the human — see the "## Role" section in the root
  `CLAUDE.md` of `~/Code/harness-templates`.

## Report format

```markdown
# Ratchet Run

Repo: <path>
Language: <language> (reference: <reference file>)

## Before

| Key | Value |
|---|---|
| coverage.min | ... |
| mutation.min | ... (or "missing — report-only") |
| crap.max_violations | ... |
| complexity.max_violations | ... |
| duplication.max_blocks | ... |
| arch.max_violations | ... |
| deadcode.max_findings | ... |
| suppressions.* | ... |

## Targets picked

- <function>, CRAP <n> — covered / refused (reason)
- <function>, surviving mutant <description> — killed / refused (reason)
- <module>, changed but untested — characterization test added / refused (reason)

## After

| Key | Old | New | Moved |
|---|---|---|---|
| coverage.min | ... | ... | yes/no |
| mutation.min | ... | ... | yes/no |
| crap.max_violations | ... | ... | yes/no |
| complexity.max_violations | ... | ... | yes/no |
| duplication.max_blocks | ... | ... | yes/no |
| arch.max_violations | ... | ... | yes/no |
| deadcode.max_findings | ... | ... | yes/no |

## Refused targets (human work)

- <function> — <why it needs a production-source change>

## Next highest-leverage target

<function>, CRAP <n>, <one-line reason it's next>
```
