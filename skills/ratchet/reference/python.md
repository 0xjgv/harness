# python

Source: the target repo's own harness (`python/harness.py` if it was
bootstrapped from `~/Code/harness-templates/python/`, or an adapted
equivalent — see the repo's `CLAUDE.md`/`AGENTS.md` for its exact runner).

## Invocation prefix

Differs by repo; check for `uv.lock` / `pyproject.toml` / `.venv` first:

- uv project with a `[project.scripts]` console script: `uv run harness <cmd>`
- pip / `requirements.txt` project: `.venv/bin/python harness.py <cmd>`

Use whichever the target repo actually has, and read its `CLAUDE.md` —
the prefix is written there. Do not assume `uv` is installed, and do not
reach for bare `python3`: `harness.py` needs Python ≥3.10 at import, and on
macOS `python3` is the system 3.9, which dies with
`TypeError: unsupported operand type(s) for |`.

## Commands this skill uses

- `<prefix> crap` — prints CRAP offenders, sorted descending. Start here for
  target selection; the highest-CRAP function is the highest-leverage next
  test to write.
- `<prefix> coverage` — `coverage report --format=total` under the hood;
  re-run after adding tests to get the new `coverage.min` candidate.
- `<prefix> mutation --all` — mutmut over the whole `src/` tree; prints the
  integer % killed and names the surviving mutants. Bare `<prefix> mutation`
  is scoped to the changed files, which is what `ci` wants and not what a
  ratchet wants: always pass `--all` here, because `mutation.min` is a
  whole-tree number and a scoped run would not reproduce it. Each survivor
  is a missing test in a function that already has coverage — the second
  target list after CRAP. Advisory unless `--enforce`. Two python facts:
  mutmut needs the project's own venv (`.venv/bin/mutmut`; `uvx mutmut`
  cannot work), and the gate warns and skips when it is absent — run
  `uv sync` first. The template ships `mutation.min 94` with two survivors,
  `core.pricing.x_apply_discount__mutmut_6` and
  `adapters.formatting.x_render_receipt__mutmut_13`; those are the first
  ratchet targets in a fresh copy.
- `<prefix> complexity` — `lizard` (CCN, args, length) plus a second
  `-Eduplicate` run that counts duplicate blocks (`duplication.max_blocks`,
  over `src/` and `tests/` together, so consolidating duplicated test code
  lowers it); use the CCN output only to confirm a covered function's
  violation count, never to justify a refactor.
- `<prefix> arch` — import-linter over `.importlinter`; its broken-import
  count is `arch.max_violations`. Lowering it is a `src/` change — report,
  do not do. Never edit `.importlinter`.
- `<prefix> deadcode` — vulture findings over the app sources, floored by
  `deadcode.max_findings`; deleting genuinely dead code lowers it, but that
  is production-code work and therefore outside this skill's allowlist —
  report it, do not do it.
- `<prefix> suppressions --update-baseline` — the only command that may
  write `.harness-baseline`, and the writer of **all seven** floor families
  (`coverage.min`, `complexity.max_violations`, `duplication.max_blocks`,
  `crap.max_violations`, `arch.max_violations`, `deadcode.max_findings`,
  `suppressions.*`, plus `mutation.min` under `--with-mutation` only —
  `<prefix> suppressions --update-baseline` on its own leaves `mutation.min`
  exactly as it found it). Run
  it once, at the end of a successful loop, after every floor has been
  re-measured. It is all-or-nothing: if it aborts, it names the metric that
  errored — fix that, do not hand-edit `.harness-baseline`. A missing key
  means that gate is report-only; the writer adds it at the measured value.
- `<prefix> test` — in `check` this is scoped: changed `tests/test_*.py`
  plus the tests mapped from changed `src/` modules
  (`src/**/<mod>.py` → `tests/**/test_<mod>.py`), and one
  `⚠` line per changed source module with no mapped test — those lines are
  the characterization-test targets. `<prefix> test --all` is the full
  suite, defined by the `TEST_COMMAND` constant in the repo's `harness.py`
  (unittest or pytest, plus any deselections that environment needs). The
  full suite must be green before step 1 and after step 4; abort the whole
  run if it is red before you start.

## Test conventions

- Test discovery under `TEST_DIR` (`tests/` by default) — new test files
  follow `tests/test_*.py`, mirroring the module path of the code under test.
  The name is load-bearing: the scoped `test` gate maps
  `src/**/<mod>.py` to `tests/**/test_<mod>.py`, so a characterization test
  for `src/core/pricing.py` goes in `tests/test_pricing.py` or it will never
  run under `check` for that module. Whether tests run under `unittest` or
  `pytest` is set by `TEST_COMMAND` in `harness.py`; read it before writing
  a test that assumes fixtures.
- `behave` acceptance features under `tests/features/` — only touch these if
  the picked target already has feature coverage you are extending; do not
  invent new `.feature` scenarios as part of a ratchet run, that is a
  Gherkin-first behavior-contract decision the human makes, not this skill.
- `hypothesis` for property-based tests — reach for this instead of (or in
  addition to) example tests when the target function is law-like: a
  formula, parser, codec, normalization routine, or anything with a
  round-trip or invariant. A property test on a law-like function typically
  buys more coverage per line of test code than enumerating examples, which
  is why it is the preferred choice for CRAP-offender targets that fit the
  pattern.

## Allowlist reminder

Every file this skill writes or edits in a Python repo must live under
`tests/` (or the repo's equivalent test root) plus `.harness-baseline`
itself. `src/` (or the repo's app-source root) is off-limits for the
duration of a ratchet run, and so is `.importlinter`. Off-limits includes
`vulture_allowlist.py` and any `conftest.py` fixture that would require
touching a production module to create a test seam — if a target needs that,
refuse it and report it as human work per the SKILL.md guardrails.
