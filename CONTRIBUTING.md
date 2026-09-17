# Contributing

Thanks for your interest in harness-templates! Contributions are welcome — whether it's a bug fix, a new template, or an improvement to an existing one.

## Ways to contribute

- **Report bugs** — open an issue describing what's broken and how to reproduce it
- **Improve existing templates** — better defaults, clearer AGENTS.md / CLAUDE.md instructions, additional checks
- **Add a new language template** — see the checklist below
- **Improve documentation** — fix typos, clarify instructions, add examples

## Adding a new template

Every template must follow the same conventions. Use an existing template (e.g. `python/` or `go/`) as a reference.

### Checklist

- [ ] Create a directory named after the language/runtime (e.g. `ruby/`)
- [ ] Implement the **5-script contract** — `check`, `pre-commit`, `ci`, `audit`, and `post-edit` commands
- [ ] Include a **zero-dependency harness runner** (`harness.*`) using only stdlib/runtime APIs
- [ ] Include byte-identical **`AGENTS.md`** and **`CLAUDE.md`** agent instructions for the template
- [ ] Include a **`README.md`** with getting-started instructions
- [ ] Include **security-focused lint rules** enabled in the linter config
- [ ] Include a **dependency audit** command (`audit`) wired into `ci`
- [ ] Include a **post-edit** command that formats changed source files (non-blocking), and `post-edit --hook` for Claude PostToolUse (one file, always exit 0, `additionalContext` when the file changed)
- [ ] Include a **stop-hook** command that runs post-edit, then blocks only on the change: lint on changed lines, over-limit functions the change touched (+ deadcode on changed lines where shipped). Silent on success, exit 2 with findings on stderr, exit 1 on tool failure, exit 1 instead of 2 when `stop_hook_active` (see `skills/harness/reference/settings-json.md`)
- [ ] Include **`.claude/settings.json`** (Stop → stop-hook, PostToolUse → post-edit --hook) and **`.codex/hooks.json`** (Stop → the Codex wrapper around stop-hook)
- [ ] Include at least one **smoke test**
- [ ] Add the template to the root `README.md` tables (Available Templates, Getting Started)

### Design principles to follow

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only
- **Tools own everything checkable** — formatting, lint, types, dead code, drift, and complexity are decided by deterministic tools and auto-fixed where the tool can. The agent reads the output and fixes the code, never the gate
- **Quality gates are hard, permission gates are two** — lint, types, arch boundaries, complexity, suppression ratchet, dead code, dependency audit, and drift block. Only `arch-config-guard` (pre-push/ci) and `branch-guard` (pre-push) need a human to unblock; everything else an agent can clear by doing the work
- **Metrics that can be gamed are advisory** — CRAP and mutation point at the next test or split; they are never gates, because a coverage-shaped target gets satisfied with assertion-free tests. The coverage floor is a ratchet from `.harness-baseline`, raised by a human, never a target number

## Running checks

Each template has its own harness. From inside a template directory, run the `check` command to lint, format, typecheck, and test:

```bash
# Python
cd python && uv run harness check

# Bun
cd bun && bun harness.ts check

# Go
cd go && go run harness.go check

# Rust
cd rust && cargo harness check
```

## Code style

Follow the conventions already established in each template. There is no global linter — each template enforces its own standards through its harness.
