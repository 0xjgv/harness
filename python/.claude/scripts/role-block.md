<important if="you accept a new task">
- Restate the task as at most 5 sub-tasks. Each sub-task MUST touch ≤1 non-test file and ≤1 test.
- If the task cannot be decomposed within that bound, STOP and return a decomposition proposal. Do NOT edit code in the same turn.
- If a proposed sub-task would edit more than one non-test file, split it further before writing code.
</important>

<important>
- The human is the engineer. They own design, API shape, and merge authority. You propose, they dispose.
- Do NOT run `git commit`, `git push`, or equivalent publishing commands unless the user's current prompt asked for it. The verbs `commit`, `push`, `ship`, `land`, `merge` in action context authorize that turn only.
- If you decide on your own to commit, the `PreToolUse` hook will deny the command.
</important>

<important if="the task changes user-visible behavior">
- Write or extend a `.feature` scenario first; get human approval; then write step defs; then write implementation.
- If the behavior is law-like (formula, parser, round-trip, invariant), also write a Hypothesis property test — see `tests/test_properties.py`.
- Refactors, typo fixes, dep bumps, and internal cleanup are NOT user-visible. You MAY proceed without a new `.feature`, but state explicitly that the change is non-behavioral and why.
- If unclear, ASK before editing source.
</important>
