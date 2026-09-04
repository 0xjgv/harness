---
name: ratchet
description: >
  Improve a harness-adopting repo by one notch — raise test coverage, lower
  CRAP, lower complexity — with zero risk of changing behavior. Use when
  asked to "ratchet the baseline", "raise the coverage floor", "improve
  CRAP", "pay down complexity debt", "turn the crank on tech debt", or
  "run the ratchet skill". Only applies to a repo that already has the
  harness-templates quality contract (`.harness-baseline`, `harness crap` /
  `harness coverage` / `harness complexity` / `harness suppressions`).
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

The floors this skill moves, by name, as they appear in `.harness-baseline`:

```
complexity.max_violations   # lizard -i N: exit normally if warnings <= N
coverage.min                # coverage report --format=total
crap.max_violations         # count of functions over the CRAP threshold
suppressions.noqa           # per-kind suppression counts
suppressions.type_ignore
suppressions.pyright_ignore
```

CRAP = `ccn^2 * (1-cov)^3 + ccn`. It weights uncovered complexity cubically,
so covering a single high-CRAP function tends to move `coverage.min` and
`crap.max_violations` in the same run — that is the highest-leverage target,
and the language's `crap` command already prints offenders sorted
descending. Pick targets from that list, top first.

## The loop

Run this once per invocation. One run raises each floor at most one notch.

1. **Measure.** Record the current value of every baseline key above before
   touching anything.
2. **Pick targets.** Read the `crap` offender list, highest CRAP first. Skip
   any offender you cannot cover from the test directory alone (see
   guardrails).
3. **Write tests.** Add or extend tests under the test directory only.
   Cover the picked offenders. Use property-based tests when the behavior is
   law-like (a formula, parser, codec, or invariant) — see the language
   reference for which library.
4. **Re-measure.** Run the full suite. It must be green. Recompute every
   baseline key.
5. **Raise the floors one notch**, and only the floors that actually moved:
   `coverage.min` up, `crap.max_violations` down, `complexity.max_violations`
   down if a covered function also dropped in violation count. Write the new
   values into `.harness-baseline` via the language's
   `suppressions --update-baseline` (or equivalent baseline-writing command)
   — never hand-edit the numbers past what was actually measured.
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
- **Complexity offenders are reported, never refactored.** A falling
  `complexity.max_violations` only happens when a human extracts or
  restructures the offending function. Extracting a helper is a
  behavior-change risk this skill does not take, even when it would also
  improve CRAP.
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
| crap.max_violations | ... |
| complexity.max_violations | ... |
| suppressions.* | ... |

## Targets picked

- <function>, CRAP <n> — covered / refused (reason)

## After

| Key | Old | New | Moved |
|---|---|---|---|
| coverage.min | ... | ... | yes/no |
| crap.max_violations | ... | ... | yes/no |
| complexity.max_violations | ... | ... | yes/no |

## Refused targets (human work)

- <function> — <why it needs a production-source change>

## Next highest-leverage target

<function>, CRAP <n>, <one-line reason it's next>
```
