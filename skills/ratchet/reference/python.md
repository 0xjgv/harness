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
- `<prefix> complexity` — `lizard` (CCN, args, length); use only to confirm
  a covered function's violation count, never to justify a refactor.
- `<prefix> deadcode` — vulture findings over the app sources. Its floor
  (`deadcode.max_findings`) is the fifth ratcheted metric; deleting genuinely
  dead code lowers it, but that is production-code work and therefore outside
  this skill's allowlist — report it, do not do it.
- `<prefix> suppressions --update-baseline` — the only command that may
  write `.harness-baseline`, and the writer of **all five** floors
  (`coverage.min`, `complexity.max_violations`, `crap.max_violations`,
  `deadcode.max_findings`, `suppressions.*`). Run it once, at the end of a
  successful loop, after every floor has been re-measured. It is
  all-or-nothing: if it aborts, it names the metric it could not measure —
  fix that, do not hand-edit `.harness-baseline`.
- `<prefix> test` — the full suite, defined by the `TEST_COMMAND` constant in
  the repo's `harness.py` (unittest or pytest, plus any deselections that
  environment needs). Must be green before step 1 and after step 4; abort the
  whole run if it is red before you start.

## Test conventions

- Test discovery under `TEST_DIR` (`tests/` by default) — new test files
  follow `tests/test_*.py`, mirroring the module path of the code under test.
  Whether they run under `unittest` or `pytest` is set by `TEST_COMMAND` in
  `harness.py`; read it before writing a test that assumes fixtures.
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
duration of a ratchet run, including `vulture_allowlist.py` and any
`conftest.py` fixture that would require touching a production module to
create a test seam — if a target needs that, refuse it and report it as
human work per the SKILL.md guardrails.
