# Contributing

Thanks for your interest in harness-templates! Contributions are welcome — whether it's a bug fix, a new template, or an improvement to an existing one.

## Ways to contribute

- **Report bugs** — open an issue describing what's broken and how to reproduce it
- **Improve existing templates** — better defaults, clearer CLAUDE.md instructions, additional checks
- **Add a new language template** — see the checklist below
- **Improve documentation** — fix typos, clarify instructions, add examples

## Adding a new template

Every template must follow the same conventions. Use an existing template (e.g. `python/` or `go/`) as a reference.

### Checklist

- [ ] Create a directory named after the language/runtime (e.g. `ruby/`)
- [ ] Implement the **3-script contract** — `check`, `pre-commit`, and `ci` commands
- [ ] Include a **zero-dependency harness runner** (`harness.*`) using only stdlib/runtime APIs
- [ ] Include a **`CLAUDE.md`** with agent instructions for the template
- [ ] Include a **`README.md`** with getting-started instructions
- [ ] Include **security-focused lint rules** enabled in the linter config
- [ ] Include a **dependency audit** command (`audit`) wired into `ci`
- [ ] Include a **post-edit** command that formats changed source files (non-blocking)
- [ ] Include a **`.claude/settings.json`** with a Stop hook for post-edit
- [ ] Include at least one **smoke test**
- [ ] Add the template to the root `README.md` tables (Available Templates, Getting Started)

### Design principles to follow

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only

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
