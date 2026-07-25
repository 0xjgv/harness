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
- [ ] Implement the full **seven-stage contract** — `check`, `pre-commit`, `pre-push`, `ci`, `audit`, `post-edit`, and `stop-hook` commands. `check` must run every gate that is offline, fast, and takes no build lock (fix, format, typecheck, test, complexity, acceptance, suppression ratchet, plus a lockfile check or deadcode gate if the language has one, and the architecture boundary check itself if that tool is also offline and lock-free — e.g. import-linter and dependency-cruiser are, but a tool that fetches a module or takes an exclusive build lock stays `ci`/`pre-push`-only instead); `ci` adds the network dependency audit, coverage, advisory CRAP, and `arch` if it wasn't already in `check`
- [ ] Include a **zero-dependency harness runner** (`harness.*`) using only stdlib/runtime APIs
- [ ] Include a **`gherkin-guard`** command: warns in `check`/`stop-hook`, blocks a changed production-source file with no accompanying `.feature` change in `pre-commit`/`pre-push`/`ci` (`HARNESS_ALLOW_NO_FEATURE=1` overrides after review), and skips silently when the repo has no `.feature` files anywhere
- [ ] Include byte-identical **`AGENTS.md`** and **`CLAUDE.md`** agent instructions for the template
- [ ] Include a **`README.md`** with getting-started instructions
- [ ] Include **security-focused lint rules** enabled in the linter config
- [ ] Include a **dependency audit** command (`audit`) wired into `ci`
- [ ] Include a **post-edit** command that formats changed source files (non-blocking), using repo-root-relative paths so it also works from a subdirectory (e.g. a `monorepo/` subproject)
- [ ] Include a **stop-hook** command that runs post-edit, then complexity (+ deadcode where shipped), and **exits 2 with a failure summary on stderr** on failure — Claude Code only treats a Stop hook's exit code 2 as blocking and only reads the failure back from stderr; any other exit code is silently swallowed. Every Claude Stop-hook command must end in `|| exit 2` as a belt-and-suspenders re-assertion of that exit code
- [ ] Include **`.claude/settings.json`** and **`.codex/hooks.json`** with Stop hooks for stop-hook
- [ ] Include at least one **smoke test**
- [ ] Add the template's command surface to `scripts/parity-gate.sh`'s expectations at the root of this repo — either by matching the existing core-command set, or by adding a documented allowlist entry for a deliberate divergence
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
