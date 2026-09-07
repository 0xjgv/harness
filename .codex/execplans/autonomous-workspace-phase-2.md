# Route every project through the managed workspace

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept current while the work proceeds. This document follows `/Users/juan/.codex/PLANS.md`.

## Purpose / Big Picture

Phase 2 turns the canonical provisioner from Phase 1 into the execution boundary for the meta-repository and the five copyable templates. After this phase, every existing Make target runs with the exact managed tool profile, dependency restoration respects committed locks, `make workspace` converges tools, dependencies, hooks, and self-contained skills, and CI uses the same entry point instead of floating setup actions. The E2E audit proved that Stop migration and embedded-skill packaging are prerequisites for a working standalone command, so they are terminal work in this plan rather than deferred hardening.

The user explicitly requested a durable task list and serial execution. Every numbered task below owns at most one non-test file and at most one test file. Tasks run in order; a checked item has implementation and focused verification evidence. This serial checklist replaces phase-by-phase permission prompts while preserving the repository's bounded-change guardrail.

## Progress

- [x] (2026-08-24 14:20Z) Task 0: create this Phase 2 execution plan and serial task list.
- [x] (2026-08-24 14:31Z) Task 1: extended `.harness/workspace.sh` and `tests/workspace_lock_test.sh` with a backward-compatible format-2 parser for hashed ecosystem inputs and keyed composite artifacts; 29 lock cases and all 46 Phase 1 black-box cases pass.
- [x] (2026-08-24 14:39Z) Task 2: added `.harness/python-downloads.json` and `tests/python_downloads_test.sh`; all four approved CPython targets, URLs, digests, versions, and uv schema normalization pass 9 focused checks.
- [x] (2026-08-24 14:45Z) Task 3: added the uv-generated `.harness/python-tools.lock` and `tests/python_tools_lock_test.sh`; 31 exact packages, universal distribution hashes, approved roots, and generator provenance pass 6 focused checks.
- [x] (2026-08-24 14:48Z) Task 4: added the private `.harness/bun-tools/package.json` and `tests/bun_tools_package_test.sh`; the exact Bun/Knip pins and absence of mutable dependency sources pass 5 checks.
- [x] (2026-08-24 14:54Z) Task 5: generated `.harness/bun-tools/bun.lock` with Bun 1.3.14 and added `tests/bun_tools_lock_test.sh`; 60 integrity-bound package records and a frozen warm-offline no-rewrite probe pass 6 checks.
- [x] (2026-08-24 14:58Z) Task 6: added `.harness/go-tools/go.mod` and `tests/go_tools_mod_test.sh`; the local module identity, Go 1.27.0 directive, three exact analyzer modules, and absence of graph overrides pass 5 checks.
- [x] (2026-08-24 15:03Z) Task 7: generated `.harness/go-tools/go.sum` with managed Go 1.27.0 and added `tests/go_tools_sum_test.sh`; 142 unique h1 checksums and a readonly warm-offline no-rewrite probe pass 5 checks.
- [x] (2026-08-24 15:14Z) Task 8: added `.harness/rust-dist.lock` and `tests/rust_dist_lock_test.sh`; the verified channel manifest, sidecar, and six-component closure for four hosts pass 7 checks including an upstream-manifest byte comparison.
- [x] (2026-08-24 15:46Z) Task 8a: retained the authenticated `cargo-modules` 0.26.0 package lock as `.harness/cargo-modules.lock` and added `tests/cargo_modules_lock_test.sh`; all 308 package identities, 307 registry checksums, and byte equality with the approved `.crate` pass 7 checks.
- [x] (2026-08-24 15:51Z) Task 9: upgraded `.harness/workspace.lock` to format 2, bound all eight ecosystem-native inputs by SHA-256, moved the cargo-modules URL to the verified official static artifact, and extended `tests/workspace_lock_test.sh`; 31 manifest cases, all native lock suites, and all 46 Phase 1 cases pass.
- [x] (2026-08-24 15:57Z) Task 10: taught `.harness/workspace.sh` to install and verify normalized CPython through the absolute managed uv with receipt-bound download metadata and atomic publication; `tests/workspace_test.sh` now passes 56 cases covering fresh install, reuse, warm offline, corrupt repair, and ambiguous payload refusal, and a real CPython 3.13.15 relocation/offline smoke passed.
- [x] (2026-08-24 16:02Z) Task 10a: normalized Lizard's expected output to exact `1.22.2` and extended `tests/workspace_lock_test.sh` to guard all three real Python CLI probes; 32 manifest cases pass.
- [x] (2026-08-24 16:12Z) Task 11: taught `.harness/workspace.sh` to install isolated Lizard, Vulture, and pip-audit environments from one constraint/hash lock with stable relocatable wrappers and receipt-bound builder inputs; all 67 black-box cases pass, and a real `OFFLINE=1` install from the populated cache produced the three exact CLI probes.
- [x] (2026-08-24 16:18Z) Task 12: taught `.harness/workspace.sh` to install Knip from its exact Bun inputs with frozen/offline resolution, ignored scripts, copied module storage, a managed-Bun wrapper, and atomic receipts; all 77 black-box cases and a real warm-cache `OFFLINE=1` Knip 5.88.1 install pass.
- [x] (2026-08-24 16:22Z) Task 12a: aligned the three Go analyzer probe arguments with real binaries and guarded their exact source module identities in `tests/workspace_lock_test.sh`; all 33 manifest cases pass.
- [x] (2026-09-01) Task 13: taught `.harness/workspace.sh` to build and verify the three pinned Go analyzers from the readonly tools module with exact embedded module sums, atomic publication, and warm-offline repair; all 87 provisioner black-box cases pass, and a managed Go 1.27.0 offline build produced all three binaries with the expected module metadata.
- [x] (2026-09-01) Task 14: taught `.harness/workspace.sh` to install Rust 1.97.1 through managed rustup 1.28.2 from an exact file-only mirror and six SHA-keyed component caches, then build cargo-modules 0.26.0 from its authenticated crate and 308-package lock; all 104 provisioner cases pass, as do a real cold online install, download-free rerun, warm-offline reuse, and offline reconstruction after removing both managed installations.
- [x] (2026-09-01) Task 15: sealed managed profile execution in `.harness/workspace.sh` against ambient language tools while preserving legitimate Go workspace discovery; all 109 provisioner cases pass with poisoned PATH/environment coverage, and a real redirected warm-offline Rust execution resolved the exact Python 3.13.15, Lizard 1.22.2, Rust/Cargo 1.97.1, and cargo-modules 0.26.0 binaries.
- [x] (2026-09-01) Task 16: added the Python workspace Gherkin contract to `python/tests/features/smoke.feature`, covering fresh locked convergence, repeat/warm-offline reuse, cold-offline failure before mutation, and unmanaged-hook refusal before downloads or mutation; Behave parsed all scenarios and reported only the intentionally pending Task 17 steps.
- [x] (2026-09-01) Task 17: implemented the Python workspace Gherkin steps in `python/tests/features/steps/smoke_steps.py` with an isolated fake provisioner, poisoned ambient uv, exact offline/argv traces, managed-state snapshots, and modeled cache/collision failures; syntax, Ruff lint, and formatting pass, all steps are defined, and the four workspace scenarios fail at the intended TDD boundary because the current Makefile has no `workspace` target.
- [x] (2026-09-01) Task 18: routed `python/Makefile` through the managed Python profile, made `deps` use `uv sync --locked` plus `--offline` only under `OFFLINE=1`, added the preflight/install/deps/skills/hooks/verify/check/final-verify `workspace` sequence, and retained `bootstrap` as its alias; all 5 targeted scenarios and 22 steps pass, including warm byte stability and pre-mutation failure behavior.
- [x] (2026-09-01) Task 19: added the Bun workspace Gherkin contract to `bun/tests/features/smoke.feature`, covering fresh frozen convergence, repeat/warm-offline reuse, cold-offline failure before mutation, and unmanaged-hook refusal before downloads or mutation; an independent Gherkin parser accepted all five scenarios and tables, with only the intentionally pending Task 20 steps undefined.
- [x] (2026-09-01) Task 20: implemented the Bun workspace Gherkin steps in `bun/tests/features/steps/smoke.steps.ts` with a native isolated fixture, absolute Make invocation, poisoned ambient Bun, frozen dependency/cache modeling, exact offline traces, managed-state snapshots, and modeled cold-cache/collision failures; Node 22 TypeScript syntax, extracted shell syntax, and formatting-width checks pass, while the current Makefile remains red at the intended missing-`workspace` TDD boundary.
- [x] (2026-09-01) Task 21: routed `bun/Makefile` through the managed Bun profile, made `deps` use `bun install --frozen-lockfile` plus `--offline` only under `OFFLINE=1`, added the preflight/install/deps/skills/hooks/verify/check/final-verify `workspace` sequence, and retained `bootstrap` as its alias; online/offline Make expansion exactly matches the Gherkin trace and the step module passes TypeScript syntax validation. Full Cucumber execution remains a terminal convergence check because this checkout has no Bun runtime or restored `node_modules` yet.
- [x] (2026-09-01) Task 21a: audited the real standalone and brownfield path, distinguished fake Make acceptance from terminal E2E, and expanded the serial plan for all native input copies, embedded skills, Stop migration, historical hook migration, direct managed runner commands, safe `setup-hooks`, changed-path reporting, public documentation, and real matrix convergence.
- [x] (2026-09-01) Task 22: added the Go workspace Gherkin contract to `go/features/smoke.feature`, covering readonly module restoration, managed readonly harness execution, warm/offline reuse with proxy shutdown, cold-offline failure before mutation, and unmanaged-hook refusal before downloads or mutation; an independent Gherkin parser accepted all five scenarios and tables with only the intentionally pending Task 23 steps undefined.
- [x] (2026-09-01) Task 23: implemented the Go workspace Gherkin steps in `go/features/steps/smoke_steps.go` with an isolated stdlib fixture, absolute Make invocation, poisoned ambient Go, exact offline/GOPROXY/argv traces, readonly cache/state modeling, byte/mode snapshots, and cold-cache/collision failures; pinned Go 1.27.0 formatting and compilation pass, and the focused suite reaches the intended missing-`workspace` TDD boundary (4 existing scenarios pass, 4 workspace scenarios fail).
- [x] (2026-09-01) Task 24: routed `go/Makefile` through the managed Go profile with readonly harness builds, made `deps` run `GOFLAGS=-mod=readonly go mod download` under the provisioner's online/offline proxy policy, added the full workspace/final-verify sequence, retained `bootstrap`, and gave `setup-hooks` explicit collision-safe provisioner ownership; all 8 Godog scenarios pass with pinned Go 1.27.0.
- [x] (2026-09-01) Task 25: added the Rust workspace Gherkin contract to `rust/tests/features/smoke.feature`, covering locked Cargo fetch/build, managed locked harness execution, warm/offline reuse with Cargo networking disabled, cold-offline failure before mutation, and unmanaged-hook refusal before downloads or mutation; the Gherkin parser accepted all five scenarios and tables.
- [x] (2026-09-01) Task 26: implemented the Rust workspace Gherkin steps in `rust/tests/acceptance.rs` with an isolated stdlib fixture, absolute Make invocation, poisoned ambient Cargo, exact locked command/order traces, command-line offline assignments, cache/state snapshots including executable modes, and pre-mutation cold-cache/collision failures; Rustfmt, shell syntax, `git diff --check`, offline compilation, and Clippy pass, while the focused workspace scenario reaches the intended missing-`workspace` Task 28 boundary.
- [x] (2026-09-01) Task 27: added `rust/rust-toolchain.toml` with Rust 1.97.1, the minimal profile, and the exact clippy/llvm-tools-preview/rustfmt component set; executable byte-contract coverage in `tests/rust_toolchain_test.sh`, Bash syntax, and `git diff --check` pass.
- [x] (2026-09-01) Task 28: routed `rust/Makefile` targets through managed locked Cargo execution, made `deps` fetch and build the exact lock, added the nine-step `workspace` sequence, retained `bootstrap`, and gave `setup-hooks` collision-safe provisioner ownership; Rustfmt and offline compilation pass, and the focused Rust workspace feature passes all 5 scenarios and 22 steps with poisoned ambient Cargo. The unrelated CRAP feature still depends on ambient `uvx` cache access until Task 28f replaces it with the provisioned Lizard.
- [x] (2026-09-01) Task 28a: extended `.harness/workspace.sh` to recognize and atomically migrate only the exact historical no-`exec` Python, Bun, Go, and Rust harness shims, including the two older pre-commit forms; near matches and unknown bytes still refuse before downloads or mutation, while migrated hooks are exact 0755 root-entering Make shims and pre-push stdin survives. Bash syntax, `git diff --check`, and all 122 provisioner black-box cases pass.
- [x] (2026-09-01) Task 28b: made final verification print stable Git porcelain statuses and concrete paths for staged and unstaged modifications, renames, and deletions before failing, without reverting state or including untracked files; black-box coverage preserves changed bytes and unrelated untracked content, and all 127 provisioner cases pass.
- [x] (2026-09-01) Task 28c: made `python/harness.py` invoke provisioned Lizard, Vulture, and pip-audit directly, audit the running locked project's site-packages, centralize project commands on `uv run --frozen --no-sync`, and delegate `setup-hooks` exclusively to the collision-safe provisioner; removed runner-owned hook mutation and added downloader-poisoning/exact-vector coverage. All 17 focused tests and Ruff checks pass from a redirected uv cache, with acceptance and CRAP also passing when the managed Lizard path is supplied.
- [x] (2026-09-01) Task 28d: made `bun/harness.ts` use project-local Biome/TypeScript plus provisioned Lizard/Knip directly, removed check-time dependency installation, delegated `setup-hooks` exclusively to the provisioner, and removed runner-owned hook mutation; exact-vector and poisoned-launcher coverage was added. TypeScript 5.9 transpilation/runtime assertions, forbidden-launcher scans, Node module parsing, and `git diff --check` pass; native Bun execution remains an explicit terminal E2E gate because Bun is not yet installed in this checkout.
- [x] (2026-09-01) Task 28e: made `go/harness.go` invoke provisioned Lizard, govulncheck, go-arch-lint, and Gremlins directly and delegate hook setup solely to the provisioner, removing runner-owned Git/Stop mutation; the executable black-box test builds with managed Go 1.27.0, poisons `go`/`uvx`, and proves exact analyzer, mutation, CRAP, and OFFLINE hook vectors. The focused test and cached Go packages pass; full offline `./...` remains blocked only by the intentionally cold Rapid module cache pending real dependency convergence.
- [x] (2026-09-01) Task 28f: made `rust/harness.rs` invoke provisioned Lizard directly, remove ambient/system LLVM selection from every cargo-llvm-cov probe/run, deterministically skip unpinned cargo-mutants, and delegate hook setup only to the provisioner; focused black-box coverage poisons uvx/cargo-mutants and LLVM overrides while asserting exact command vectors. Managed Rust 1.97.1 formatting and offline locked tests pass all 29 unit tests plus all 8 acceptance scenarios/34 steps.
- [x] (2026-09-01) Task 28g: made `python/Makefile` dispatch the harness through `uv run --frozen --no-sync`, gave `setup-hooks` explicit provisioner ownership, and extended the isolated acceptance fixture with exact online/offline dry-run assertions; the Gherkin trace now states the sealed command directly, and all 5 scenarios/22 steps plus Ruff and Make expansion checks pass.
- [x] (2026-09-01) Task 28h: gave `bun/Makefile` an explicit provisioner-only `setup-hooks` target and extended its isolated acceptance fixture to assert exact OFFLINE hook and managed-check dry-run expansions; Make expansion, Node TypeScript parsing/transformation, embedded shell syntax, static dispatch assertions, and `git diff --check` pass, with native Bun execution reserved for terminal E2E.
- [x] (2026-09-01) Task 28i: updated Python's Claude Stop handler to `cd $CLAUDE_PROJECT_DIR && make stop-hook` and added executable JSON-structural coverage requiring one co-located command/type handler and no duplicate or misplaced managed command text; JSON parsing, Bash syntax, and the focused test pass.
- [x] (2026-09-01) Task 28j: updated Python's Codex Stop handler to enter the Git root and invoke the existing wrapper with `make stop-hook`; strengthened structural coverage to require the precise one-item Stop nesting, timeout/status fields, unique command placement, executable wrapper, and runtime forwarding of exactly `make stop-hook`. All 3 focused checks pass.
- [x] (2026-09-01) Task 28k: updated Bun's Claude Stop handler to call `make stop-hook` and added it to the exact structural matrix; all 4 shared Stop checks pass.
- [x] (2026-09-01) Task 28l: updated Bun's Codex Stop handler to invoke the root-entering wrapper with `make stop-hook`; structural and isolated runtime forwarding coverage now spans Bun and Python, and all 6 shared checks pass.
- [x] (2026-09-01) Task 28m: updated Go's Claude Stop handler to call `make stop-hook`, added it to the structural matrix, and all 7 shared checks pass.
- [x] (2026-09-01) Task 28n: updated Go's Codex Stop handler to invoke the root-entering wrapper with `make stop-hook`; structural and isolated runtime forwarding coverage spans Bun, Go, and Python, with all 9 shared checks passing.
- [x] (2026-09-01) Task 28o: updated Rust's Claude Stop handler to call `make stop-hook`, added it to the structural matrix, and all 10 shared checks pass.
- [x] (2026-09-01) Task 28p: updated Rust's Codex Stop handler to invoke the root-entering wrapper with `make stop-hook`; the four-template structural and runtime forwarding matrix now passes all 12 checks.
- [x] (2026-09-01) Task 29: copied the canonical provisioner byte-identically into `python/.harness/workspace.sh` at mode 0755 and added executable destination path/byte/mode drift coverage; both focused TAP checks and Bash syntax pass.
- [x] (2026-09-01) Task 30: copied the canonical lock byte-identically into `python/.harness/workspace.lock` at mode 0644 and generalized drift coverage to exact per-artifact destination sets plus per-entry bytes/modes; all 4 checks pass.
- [x] (2026-09-01) Task 31: copied the canonical provisioner byte-identically into `bun/.harness/workspace.sh` at mode 0755 and extended exact drift coverage; all 5 checks pass.
- [x] (2026-09-01) Task 32: copied the canonical lock byte-identically into `bun/.harness/workspace.lock` at mode 0644 and extended drift coverage; all 6 checks pass.
- [x] (2026-09-01) Task 33: copied the canonical provisioner byte-identically into `go/.harness/workspace.sh` at mode 0755 and extended drift coverage; all 7 checks pass.
- [x] (2026-09-01) Task 34: copied the canonical lock byte-identically into `go/.harness/workspace.lock` at mode 0644 and extended drift coverage; all 8 checks pass.
- [x] (2026-09-01) Task 35: copied the canonical provisioner byte-identically into `rust/.harness/workspace.sh` at mode 0755 and extended exact drift coverage; all 9 checks and Bash syntax pass.
- [x] (2026-09-01) Task 36: copied the canonical lock byte-identically into `rust/.harness/workspace.lock` at mode 0644 and extended exact drift coverage; all 10 checks pass.
- [x] (2026-09-01) Task 37: copied the canonical provisioner byte-identically into `monorepo/.harness/workspace.sh` at mode 0755 and extended exact drift coverage; all 11 checks and Bash syntax pass.
- [x] (2026-09-01) Task 38: copied the canonical lock byte-identically into `monorepo/.harness/workspace.lock` at mode 0644 and extended exact drift coverage; all 12 checks pass.
- [x] (2026-09-01) Task 38a1: copied `.harness/python-downloads.json` byte-identically into `python/.harness/` at mode 0644 and generalized exact-depth destination-set drift coverage for nested native inputs; all 14 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a2: copied `.harness/python-tools.lock` byte-identically into `python/.harness/` at mode 0644 and extended exact drift coverage; all 16 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a3: copied `.harness/bun-tools/package.json` byte-identically into `python/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 18 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a4: copied `.harness/bun-tools/bun.lock` byte-identically into `python/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 20 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a5: copied `.harness/go-tools/go.mod` byte-identically into `python/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 22 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a6: copied `.harness/go-tools/go.sum` byte-identically into `python/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 24 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a7: copied `.harness/rust-dist.lock` byte-identically into `python/.harness/` at mode 0644 and extended exact drift coverage; all 26 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38a8: copied `.harness/cargo-modules.lock` byte-identically into `python/.harness/` at mode 0644 and extended exact drift coverage; the complete Python native-input bundle now passes all 28 checks under Bash 3.2.
- [x] (2026-09-01) Task 38b1: copied `.harness/python-downloads.json` byte-identically into `bun/.harness/` at mode 0644 and extended the exact destination set; all 29 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b2: copied `.harness/python-tools.lock` byte-identically into `bun/.harness/` at mode 0644 and extended exact drift coverage; all 30 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b3: copied `.harness/bun-tools/package.json` byte-identically into `bun/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 31 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b4: copied `.harness/bun-tools/bun.lock` byte-identically into `bun/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 32 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b5: copied `.harness/go-tools/go.mod` byte-identically into `bun/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 33 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b6: copied `.harness/go-tools/go.sum` byte-identically into `bun/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 34 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b7: copied `.harness/rust-dist.lock` byte-identically into `bun/.harness/` at mode 0644 and extended exact drift coverage; all 35 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38b8: copied `.harness/cargo-modules.lock` byte-identically into `bun/.harness/` at mode 0644 and extended exact drift coverage; the complete Bun native-input bundle now passes all 36 checks under Bash 3.2.
- [x] (2026-09-01) Task 38c1: copied `.harness/python-downloads.json` byte-identically into `go/.harness/` at mode 0644 and extended exact drift coverage; all 37 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c2: copied `.harness/python-tools.lock` byte-identically into `go/.harness/` at mode 0644 and extended exact drift coverage; all 38 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c3: copied `.harness/bun-tools/package.json` byte-identically into `go/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 39 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c4: copied `.harness/bun-tools/bun.lock` byte-identically into `go/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 40 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c5: copied `.harness/go-tools/go.mod` byte-identically into `go/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 41 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c6: copied `.harness/go-tools/go.sum` byte-identically into `go/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 42 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c7: copied `.harness/rust-dist.lock` byte-identically into `go/.harness/` at mode 0644 and extended exact drift coverage; all 43 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38c8: copied `.harness/cargo-modules.lock` byte-identically into `go/.harness/` at mode 0644 and extended exact drift coverage; the complete Go native-input bundle now passes all 44 checks under Bash 3.2.
- [x] (2026-09-01) Task 38d1: copied `.harness/python-downloads.json` byte-identically into `rust/.harness/` at mode 0644 and extended exact drift coverage; all 45 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d2: copied `.harness/python-tools.lock` byte-identically into `rust/.harness/` at mode 0644 and extended exact drift coverage; all 46 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d3: copied `.harness/bun-tools/package.json` byte-identically into `rust/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 47 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d4: copied `.harness/bun-tools/bun.lock` byte-identically into `rust/.harness/bun-tools/` at mode 0644 and extended exact nested-path drift coverage; all 48 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d5: copied `.harness/go-tools/go.mod` byte-identically into `rust/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 49 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d6: copied `.harness/go-tools/go.sum` byte-identically into `rust/.harness/go-tools/` at mode 0644 and extended exact nested-path drift coverage; all 50 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d7: copied `.harness/rust-dist.lock` byte-identically into `rust/.harness/` at mode 0644 and extended exact drift coverage; all 51 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38d8: copied `.harness/cargo-modules.lock` byte-identically into `rust/.harness/` at mode 0644 and extended exact drift coverage; the complete Rust native-input bundle now passes all 52 checks under Bash 3.2.
- [x] (2026-09-01) Task 38e1: copied `.harness/python-downloads.json` byte-identically into `monorepo/.harness/` at mode 0644, completing its exact five-surface destination set; all 53 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e2: copied `.harness/python-tools.lock` byte-identically into `monorepo/.harness/` at mode 0644, completing its exact five-surface destination set; all 54 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e3: copied `.harness/bun-tools/package.json` byte-identically into `monorepo/.harness/bun-tools/` at mode 0644, completing its exact nested five-surface destination set; all 55 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e4: copied `.harness/bun-tools/bun.lock` byte-identically into `monorepo/.harness/bun-tools/` at mode 0644, completing its exact nested five-surface destination set; all 56 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e5: copied `.harness/go-tools/go.mod` byte-identically into `monorepo/.harness/go-tools/` at mode 0644, completing its exact nested five-surface destination set; all 57 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e6: copied `.harness/go-tools/go.sum` byte-identically into `monorepo/.harness/go-tools/` at mode 0644, completing its exact nested five-surface destination set; all 58 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e7: copied `.harness/rust-dist.lock` byte-identically into `monorepo/.harness/` at mode 0644, completing its exact five-surface destination set; all 59 checks pass under Bash 3.2.
- [x] (2026-09-01) Task 38e8: copied `.harness/cargo-modules.lock` byte-identically into `monorepo/.harness/` at mode 0644; all ten canonical artifacts now have exactly five template destinations, with 60/60 exact path/byte/mode checks passing under Bash 3.2.
- [x] (2026-09-01) Task 39: updated the root `Makefile` with precedence-aware one-time discovery, one union provision, lexical single-shot locked dependency dispatch, provisioner-only hooks, bootstrap/setup compatibility, and exact managed runners; 13/13 isolated Git-fixture checks pass, including stdin/CWD preservation and failure-before-mutation behavior.
- [x] (2026-09-01) Task 40: updated `monorepo/Makefile` with the same union/deduplication and workspace contract, managed serial/parallel dispatch, and exact pre-push stdin replay; 33/33 Bash 3.2 black-box checks pass, including protected-range blocking, TTY nonblocking behavior, parallel barriers, and failure-before-mutation.
- [x] (2026-09-01) Task 40a: hardened root public pre-push with one capture and environment-only immutable replay to the arch guard and every managed child; 43/43 checks pass for multi-ref ranges, literal-dollar temp paths, gate ordering, capture failures, all runner argv/CWD/stdin, and TTY nonblocking behavior.
- [x] (2026-09-01) Task 40b: replaced monorepo's recursive Make-variable spool with a shell-only environment handoff for serial and parallel workers; 37/37 checks pass for literal-dollar paths, capture failures, multi-ref parsing, deterministic buffering, all-child replay, and TTY behavior.
- [x] (2026-09-01) Task 41: migrated Python CI to exactly `make workspace` then `make ci`, retained full-history checkout, removed floating setup/native install steps, and added an extensible Bash 3.2 workflow-contract test.
- [x] (2026-09-01) Task 42: migrated Bun CI to exactly `make workspace` then `make ci`, removed floating Bun/uv and native dependency setup, and enrolled it in the shared workflow contract; both workflows pass.
- [x] (2026-09-01) Task 43: migrated Go CI to exactly `make workspace` then `make ci`, removed floating Go/uv, curl installer, and direct module/harness steps, and enrolled it in the shared contract; all three workflows pass.
- [x] (2026-09-01) Task 44: migrated Rust CI to exactly `make workspace` then `make ci`, removed floating toolchain/uv/cargo-tool installers, and enrolled it in the shared contract; all four language workflows pass.
- [x] (2026-09-01) Task 45: migrated monorepo CI to exactly `make workspace` then `make ci`, removed every floating toolchain/analyzer installer, and enrolled it in the shared contract; all five template workflows pass.
- [x] (2026-09-01) Task 46: added explicit `ubuntu-24.04` x86_64 and `macos-26` arm64 root convergence jobs covering online, curl-poisoned no-download rerun, warm offline, CI, and tracked/index cleanliness; all six workflows pass the shared structural contract and the root YAML parses safely.
- [x] (2026-09-01) Task 46.1: rewrote root `README.md` around the clean-clone workspace, offline/lock/hook/monorepo/CI contracts and exact pin table; 57/57 Bash 3.2 documentation assertions pass and obsolete ambient-tool setup guidance is absent.
- [x] (2026-09-01) Task 46.2: rewrote Python public setup/development/CI/hook guidance around managed Make and explicit frozen runner boundaries, locked deps, warm offline, and safe migration; root+Python docs pass 98/98 assertions.
- [x] (2026-09-01) Task 46.3: rewrote Bun public setup/development/CI/hook guidance around managed Make, frozen deps, explicit managed upgrades, exact lizard/knip pins, and warm offline; root+Python+Bun docs pass 142/142 assertions.
- [x] (2026-09-01) Task 46.4: rewrote Go public setup/commands/CI/hooks around managed Make, readonly deps, managed explicit upgrades, exact runtime/analyzer pins, and warm offline; all four docs pass 197/197 assertions.
- [x] (2026-09-01) Task 46.5: rewrote Rust public setup/commands/CI/hooks around managed Make, locked Cargo, exact toolchain/LLVM pins, warm offline, and deterministic unprovisioned cargo-mutants skip; five docs pass 261/261 assertions.
- [x] (2026-09-01) Task 46.6: rewrote monorepo public setup/targets/CI/hooks around one profile union, lexical locked deps, managed exact runners, aliases, warm offline, parallel quality dispatch, and stdin replay; all six docs pass 326/326 assertions.
- [x] (2026-09-01) Task 46.7: updated root `CLAUDE.md` with the Make-first workspace contract, exact managed runner forms, locked dependency and hook ownership rules, Stop/CI wiring, and canonical provisioner drift guidance; 410/410 documentation and 60/60 provisioner-drift assertions pass.
- [x] (2026-09-01) Task 46.8: synced root `AGENTS.md` from `CLAUDE.md` byte-for-byte and added drift-contract coverage; `make agents-md-drift`, 412/412 documentation checks, 60/60 provisioner-drift checks, Bash syntax, and diff hygiene pass.
- [x] (2026-09-01) Task 46.9: updated `python/CLAUDE.md` for the managed clean-clone workspace, frozen dependencies, Make-first targets, exact managed runner/analyzer commands, collision-safe provisioner-owned hooks, Make-rooted Stop/CI wiring, and tracked-tree cleanliness while preserving its Behavior contract; 477/477 documentation and 60/60 provisioner-drift checks pass.
- [x] (2026-09-01) Task 46.10: synced `python/AGENTS.md` from `python/CLAUDE.md` byte-for-byte and added a no-download `OFFLINE=1` fixture proving the Make target dispatches the exact managed parity command; 479/479 documentation and 60/60 provisioner-drift checks pass.
- [x] (2026-09-07) Task 46.11: updated `bun/CLAUDE.md` for clean-clone workspace convergence, exact managed Bun/lizard/knip execution, frozen dependencies, Make-first quality targets, provisioner-owned collision-safe hooks, Make-rooted Stop/CI wiring, and tracked-tree cleanliness while preserving the Behavior contract; 544/544 documentation and 60/60 provisioner-drift checks pass.
- [x] (2026-09-07) Task 46.12: synced `bun/AGENTS.md` from `bun/CLAUDE.md` byte-for-byte and added an isolated no-network `OFFLINE=1` fixture proving the exact managed Bun drift dispatch; 546/546 documentation and 60/60 provisioner-drift checks pass.
- [ ] Task 46.13: update `go/CLAUDE.md` for the finalized contract.
- [ ] Task 46.14: sync `go/AGENTS.md` and verify byte identity.
- [ ] Task 46.15: update `rust/CLAUDE.md` for the finalized contract.
- [ ] Task 46.16: sync `rust/AGENTS.md` and verify byte identity.
- [ ] Task 46.17: update `monorepo/CLAUDE.md` for the finalized contract.
- [ ] Task 46.18: sync `monorepo/AGENTS.md` and verify byte identity.
- [ ] Task 46.19: rewrite `skills/harness/SKILL.md` to establish its own directory as the skill root and assemble greenfield/brownfield Make facades from relative assets.
- [ ] Task 46.20: rewrite `skills/harness/reference/adoption-checklist.md` to use relative assets and the managed workspace contract.
- [ ] Task 46.21: rewrite `skills/harness/reference/behavior-contract.md` to use relative assets and managed Make hooks.
- [ ] Task 46.22: rewrite `skills/harness/reference/python.md` to use the embedded Python overlay and shared workspace asset.
- [ ] Task 46.23: rewrite `skills/harness/reference/bun.md` to use relative embedded assets.
- [ ] Task 46.24: rewrite `skills/harness/reference/go.md` to use relative embedded assets.
- [ ] Task 46.25: rewrite `skills/harness/reference/rust.md` to use relative embedded assets.
- [ ] Task 46.26: rewrite `skills/harness/reference/monorepo.md` to use relative embedded assets.
- [ ] Task 46.27: rewrite `skills/harness/reference/settings-json.md` for the exact `make stop-hook` wiring.
- [ ] Task 46.28: add the self-contained skill asset layout documentation and focused path/recursion assertions.
- [ ] Task 46.29: add a deterministic generator for one shared workspace bundle plus five recursion-free template overlays, with black-box byte/path/mode/stale-file coverage.
- [ ] Task 46.30: generate a per-path asset checklist from the finalized templates; expand it into one destination-file task per unchecked path before materialization.
- [ ] Task 46.31: execute the generated asset checklist serially and prove second-generation byte/path/mode identity.
- [ ] Task 46.32: generate a per-path checklist for five complete embedded skill copies, excluding nested embedded skills from overlays.
- [ ] Task 46.33: execute the embedded-copy checklist serially and prove every template copy is byte-identical to the canonical skill.
- [ ] Task 46.34: update the root `Makefile` with ordered provisioner/assets/embedded/deployed sync and drift targets; extend root orchestration coverage.
- [ ] Task 46.35: add final skill-package black-box coverage for stale managed-file removal, unrelated-skill preservation, redirected HOME deployment, idempotence, and greenfield assembly using only embedded assets.
- [ ] Task 46a: add and run a real greenfield E2E matrix for the meta-repo, Python, Bun, Go, Rust, and a populated monorepo, each in a committed Git repository with a cold redirected HOME; prove online convergence, a network-blocked no-download rerun, warm-offline convergence, exact tools/dependencies, `make check`, `make ci`, hook/Stop/skill readiness, and tracked/index cleanliness.
- [ ] Task 46b: extend and run the real E2E suite against clean tracked brownfields with existing source, byte/mode-stable unrelated untracked state, unrelated deployed skills, and actual historical no-`exec` harness hooks; prove safe migration and full convergence, and prove an unmanaged-hook collision refuses before any tool, dependency, hook, or skill mutation.
- [ ] Task 47: update the Phase 2 plan with final evidence, run every focused suite plus both real E2E repositories, Bash 3.2 syntax, `make ci`, and tracked-tree cleanliness.

## Surprises & Discoveries

- Observation: Phase 1 records package-backed pins but installs only standalone archives.
  Evidence: `.harness/workspace.sh` loops direct `tool` records during installation, while `.harness/workspace.lock` represents Python CLIs, Knip, Go analyzers, Rust, and cargo-modules as inert `pin` records.

- Observation: A top-level package digest does not lock a transitive dependency graph.
  Evidence: the Python wheels and Knip package declare dependency ranges; `go install package@version` ignores a caller module's `go.sum`; cargo-modules needs its packaged `Cargo.lock` plus registry checksums. Phase 2 therefore checks in ecosystem-native closure data before installing these tools.

- Observation: the public Phase 2 contract spans more files than the repository's fixed five-subtask rule permits in one batch.
  Evidence: six Makefiles and five existing CI workflows must change before considering tool locks or template copies. The user's serial-task instruction resolves this by making each checklist item an independently verified bounded task.

- Observation: keyed artifacts can extend the existing artifact record without changing direct-tool receipts.
  Evidence: format 2 adds a key column, while format 1 assigns the internal key `-`; direct tools reject non-`-` keys and continue emitting their existing receipt bytes.

- Observation: the approved Python CLI set resolves to 31 packages on CPython 3.13.
  Evidence: pinned uv 0.12.5 generated a universal, no-build requirements lock with SHA-256 hashes for every candidate distribution; the focused lock test verifies all 31 exact requirements.

- Observation: Knip 5.88.1 resolves to 60 registry package records in Bun's text lock.
  Evidence: Bun 1.3.14 generated the lock, every record has a SHA-512 integrity, and a frozen `--offline --lockfile-only` rerun leaves its bytes unchanged.

- Observation: Rust 1.97.1 was released from the dated 2026-07-16 distribution and needs 24 host component archives for the approved matrix.
  Evidence: the SHA-verified official channel manifest selects cargo, clippy, llvm-tools, rust-std, rustc, and rustfmt for each of four host triples; the normalized closure matches it exactly.

- Observation: the original task list omitted the native lock needed by `cargo-modules`.
  Evidence: its approved `.crate` digest authenticates the package archive, but deterministic `cargo install --locked` also depends on the packaged `Cargo.lock`; Task 8a closes that gap before the aggregate manifest binds ecosystem inputs.

- Observation: crates.io's API download route returned HTTP 403 in the provisioning environment while its official static artifact host served the identical approved bytes.
  Evidence: `https://static.crates.io/crates/cargo-modules/cargo-modules-0.26.0.crate` hashes to `ee1ab050...f294b`, so the canonical manifest now uses that stable artifact URL.

- Observation: uv creates both an exact-version Python directory and a major/minor alias symlink in its install directory.
  Evidence: the first real CPython smoke saw two executable candidates; excluding symlink candidates selects the one real runtime, which remains relocatable and reports exactly `Python 3.13.15` after atomic publication.

- Observation: uv can select one Python CLI root from the union lock without weakening hashes.
  Evidence: `uv pip install --require-hashes --constraint .harness/python-tools.lock lizard==1.22.2` installed only Lizard's three-package closure, while real probes showed `1.22.2`, `vulture 2.16`, and `pip-audit 2.10.1`.

- Observation: uv rejects combining `--no-build` with `--only-binary :all:`.
  Evidence: the first real offline CLI convergence failed on that mutually exclusive pair; `--no-build` alone enforces the intended wheel-only/no-build boundary and the real hash-locked installs then passed.

- Observation: component and black-box provisioner coverage is not end-to-end workspace evidence.
  Evidence: until the Makefiles and template-local provisioner assets are wired, no test invokes the public `make workspace` contract from a cold clone through dependency restoration, hooks, skills, `make check`, and final cleanliness. Tasks 46a and 46b make greenfield and brownfield convergence terminal gates.

- Observation: `cargo-llvm-cov` is a Cargo subcommand binary, not a conventional standalone CLI.
  Evidence: the real 0.8.7 artifact rejects direct `--version`; its exact successful probe is `cargo-llvm-cov llvm-cov --version`. Direct-tool verification now models that invocation explicitly.

- Observation: a hermetic rustup distribution server must also suppress rustup self-update.
  Evidence: the first real toolchain install consumed all six SHA-keyed component caches successfully, then failed while checking the intentionally absent local `rustup/release-stable.toml`. `--no-self-update` keeps the operation scoped to the pinned toolchain and the next real run converged.

- Observation: cargo-modules emits tracing-filter diagnostics during `--version` unless logging is explicitly disabled.
  Evidence: the real 0.26.0 binary printed warnings before its version line with the default environment; `RUST_LOG=off` and `NO_COLOR=1` produce the exact deterministic probe without changing normal execution.

- Observation: sealing Go's compiler and module caches must not disable repository-level workspace selection.
  Evidence: `GOROOT`, `GOENV=off`, `GOTOOLCHAIN=local`, readonly/offline policy, and managed binaries remove ambient tool selection, while exporting `GOWORK=off` would break committed `go.work` monorepos. The public execution boundary therefore leaves `GOWORK` unconstrained; only the private analyzer build disables it.

- Observation: script/manifest copies alone cannot make any standalone template provisionable.
  Evidence: format-2 parsing authenticates all eight native lock inputs before profile selection, and standalone templates have neither those files nor the `.harness/skills/harness` source required by preflight. The serial plan now includes every destination/input pair and pulls embedded-skill work ahead of the E2E gates.

- Observation: the checked-in Stop wiring and historical hook bytes do not yet match the managed Make boundary.
  Evidence: Python, Bun, Go, and Rust Stop JSON still invokes native runners; their existing installers emitted exact no-`exec` shims, while legacy recognition accepts only `exec` variants. Real brownfields would therefore fail before downloads even though the hooks came from this harness.

- Observation: entering a managed PATH is insufficient when a runner explicitly invokes a package launcher.
  Evidence: the language runners still use `uvx`, `bunx`, and versioned `go run` commands for tools already provisioned as exact binaries. Those paths can resolve or download a second tool graph, so one serial task per runner now replaces them with direct managed commands and poisons the launchers in tests.

- Observation: the normal auto-fixing check and a blanket byte-stability promise for arbitrary untracked source files are incompatible.
  Evidence: quality runners intentionally discover and format source-shaped untracked files, while the provisioner itself does not delete or replace untracked state. Terminal brownfield coverage will prove byte/mode stability for unrelated user state such as `VISION.md`; newly authored source remains subject to the documented auto-fix contract.

## Decision Log

- Decision: Treat the user's instruction to “create a task list and run one at a time” as task-wide authorization for serial execution of this plan.
  Rationale: It removes repeated social approval stops while retaining deterministic, file-bounded checkpoints and does not authorize commits, pushes, architecture changes, baselines, or external publication.
  Date/Author: 2026-08-24 / Codex

- Decision: Close the deferred package-backed provisioning gap before exposing public Make targets.
  Rationale: Routing `make workspace` to a provisioner that silently ignores declared tools would violate exact-tool and warm-offline guarantees.
  Date/Author: 2026-08-24 / Codex

- Decision: Use ecosystem-native lock data, referenced and hashed by the canonical manifest, instead of expanding thousands of transitive artifacts into the TSV manifest.
  Rationale: uv, Bun, Go, and Cargo already define reviewable immutable dependency formats. The provisioner must bind receipts to those files and invoke each ecosystem in frozen/readonly mode.
  Date/Author: 2026-08-24 / Codex

- Decision: Originally keep hook composition and embedded skill generation outside Phase 2. Superseded on 2026-09-01 by the terminal-E2E decision below.
  Rationale: The initial phase boundary assumed script/lock copies were enough to expose the execution path. The standalone audit disproved that assumption: preflight requires exact Stop wiring and a local skill source before any download.
  Date/Author: 2026-08-24 / Codex

- Decision: Define brownfield E2E as an existing repository whose tracked/index state is clean, while preserving untracked files and unrelated deployed skills.
  Rationale: the public workspace contract intentionally refuses dirty tracked/index state. Brownfield safety therefore means converging an established repository without rewriting its code or unrelated user state, migrating only an exact known legacy harness hook, and refusing unknown hook content before any mutation.
  Date/Author: 2026-09-01 / Codex

- Decision: Seal selected language tools by prepending exact managed profile directories and neutralizing ambient tool configuration, but preserve committed project configuration such as `go.work`.
  Rationale: determinism requires controlling executable and cache selection, not erasing repository-owned inputs that are part of the build contract.
  Date/Author: 2026-09-01 / Codex

- Decision: Pull native input copies, Stop migration, direct managed runner commands, and self-contained embedded skills into this execution plan before E2E.
  Rationale: these were labeled later hardening, but the public command cannot converge in a standalone VM without them. A green fake-provisioner test is not a substitute for the user-requested working workspace.
  Date/Author: 2026-09-01 / Codex

- Decision: “Preserve untracked files” means the provisioner never deletes or replaces them; the explicitly advertised auto-fixing `make check` may format source files it normally owns.
  Rationale: this preserves unrelated brownfield state byte-for-byte without disabling the quality loop for newly authored untracked source. E2E will hash content and mode for an unrelated untracked sentinel.
  Date/Author: 2026-09-01 / Codex

- Decision: Accept both manifest formats 1 and 2, and model native locks as hashed `input` records plus composite downloads as keyed `artifact` records.
  Rationale: Existing installations and fixtures remain valid, native lock files stay reviewable, and Rust can declare multiple component archives for one host without synthetic tool names.
  Date/Author: 2026-08-24 / Codex

## Outcomes & Retrospective

Phase 2 is in progress. Tasks 1 through 21 establish authenticated ecosystem closures, deterministic installers for every profile, a sealed managed execution boundary, and passing Python plus statically validated Bun Make contracts. The real-path audit expanded the remaining work so template/root wiring, complete assets, Stop/skill migration, CI convergence, and greenfield/brownfield E2E cannot be falsely satisfied by fake provisioners.

## Context and Orientation

The repository root is `/Users/juan/Code/harness`. Phase 1 added `.harness/workspace.sh`, a zero-extra-runtime Bash provisioner, and `.harness/workspace.lock`, its strict tab-separated data manifest. Phase 2 now installs and verifies both direct artifacts and package-backed tools from authenticated ecosystem closures, with atomic publication, warm-offline repair, managed environment variables, collision-safe root-entering Git hooks, skill deployment, and verification.

Each of `python`, `bun`, `go`, and `rust` is an independent template with a thin Makefile. `monorepo/Makefile` detects copied templates by runner marker and dispatches to them. The root Makefile performs the same detection for dogfooding. Phase 2 preserves that independence: each template receives byte-identical canonical provisioner assets and calls its local copy.

An “ecosystem lock” is checked-in metadata that fixes the complete dependency selection used to build or install a package-backed command. A “profile” is one of `common`, `python`, `bun`, `go`, or `rust`; selecting a language also selects common tools. “Warm offline” means all exact managed tools and dependency caches were created by an earlier online run, after which `OFFLINE=1` makes no network request.

## Plan of Work

Execute the Progress tasks in numeric order. First extend the canonical parser and add complete ecosystem lock inputs. Then implement each composite installer behind the existing `install`, `exec`, and `verify` commands, always using absolute managed executables and atomic staging. Poisoned ambient language commands in black-box tests must prove they are never selected.

Once the provisioner can supply every profile, encode the approved workspace behavior in each existing smoke feature and its language-local step implementation. Update the four language Makefiles so every existing harness target enters the managed profile, `deps` uses frozen or readonly resolution, `workspace` performs preflight, install, dependency restoration, readiness verification, and `make check`, and `bootstrap` aliases `workspace`.

Copy the complete ten-file provisioner closure into each standalone template and protect bytes and executable modes from the root. Update root and monorepo orchestration so detection produces a sorted unique profile union and each subproject's `deps` executes exactly once in lexical order, including hybrid-marker directories. Replace floating CI setup with the same workspace path, finalize public docs, generate one shared workspace skill asset plus five recursion-free template overlays, embed the complete self-contained skill in every template, then run the real greenfield/brownfield convergence matrix.

## Concrete Steps

Work from `/Users/juan/Code/harness`. After each task, run its focused test before checking the task box. Core commands include:

    bash -n .harness/workspace.sh
    bash tests/workspace_lock_test.sh
    bash tests/workspace_test.sh
    make -C python acceptance
    make -C bun acceptance
    make -C go acceptance
    make -C rust acceptance
    bash tests/workspace_orchestration_test.sh
    bash tests/workspace_workflows_test.sh

The final online convergence may access official release servers and registries. If the host sandbox blocks that network access, the command must request host authorization; repository approval is not needed. Tests before the real convergence use local fixtures and fake package managers.

## Validation and Acceptance

The focused lock tests must reject missing or changed ecosystem closure files, incomplete platform coverage, unsafe source paths, and mismatches between approved root versions and native locks. Provisioner tests must prove fresh convergence, byte-identical reuse, warm offline operation without downloader calls, cold offline failure before hooks or skills, atomic repair, and absolute managed tool selection with ambient language sentinels.

Each template's acceptance suite must prove `make workspace` orders preflight and tool installation before dependencies or workspace mutations, forwards the exact profile and offline policy, uses the committed dependency lock, and leaves `bootstrap` behavior identical. Root and monorepo tests must prove sorted unique profile selection, lexical one-time dispatch, hybrid-marker deduplication, and failure propagation before hooks or skills.

The phase is complete only when clean committed fixtures for the meta-repo, all four language templates, and a populated monorepo can run `make workspace`, immediately rerun it with network blocked and no downloads, run `make workspace OFFLINE=1`, run `make ci`, and retain clean tracked/index state. Brownfield fixtures must additionally prove historical-hook migration, unrelated untracked content/mode preservation, unrelated-skill preservation, and pre-mutation refusal for unknown hooks.

## Idempotence and Recovery

Every task is additive or uses atomic replacement. Package installation happens under a temporary sibling and publishes only after exact probes pass. Receipts include the native lock digest and builder identity, so changes invalidate reuse. Offline mode never repairs from the network. A failed dependency phase may leave package-manager cache entries but cannot install hooks, deploy skills, or alter tracked files.

The serial task list is restartable: inspect the first unchecked Progress item, verify the preceding item, and continue. Never mark an item complete based only on code inspection.

## Artifacts and Notes

Phase 1 verification before this plan passed 14 manifest cases and 46 provisioner cases under macOS Bash 3.2. The Phase 2 baseline worktree contains only the uncommitted Phase 1 files under `.codex/execplans`, `.harness`, and `tests`.

## Interfaces and Dependencies

The public interface remains:

    make workspace
    make workspace OFFLINE=1
    make bootstrap

The provisioner interface remains:

    .harness/workspace.sh preflight PROFILE...
    .harness/workspace.sh install PROFILE...
    .harness/workspace.sh exec PROFILE... -- COMMAND...
    .harness/workspace.sh verify PROFILE...

Format 2 of `.harness/workspace.lock` may add records for checked-in native lock inputs and repeated Rust distribution components, but it remains strict tab-separated data and is never sourced as shell. All native lock paths are repository-relative, must remain below `.harness`, and become part of the corresponding installation receipt.

Revision note (2026-08-24): Created the Phase 2 serial execution plan after the user replaced phase-sized approval pauses with a persistent one-task-at-a-time checklist.
