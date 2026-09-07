# Build the canonical deterministic workspace provisioner

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept current while the work proceeds. This document follows `/Users/juan/.codex/PLANS.md`.

## Purpose / Big Picture

Phase 1 creates the canonical, standalone provisioning engine that later phases will copy into every project template and invoke from `make workspace`. A user or agent will be able to inspect one data-only lock manifest, run one Bash program, and get deterministic platform selection, verified artifact installation, a managed execution environment, collision-safe Git hooks, and exact skill deployment without relying on ambient language runtimes. This phase does not add a public Make target or change any template; those integrations remain later phases.

The behavior is observable by running the root black-box tests. They construct temporary Git repositories, homes, release artifacts, and command shims, so they exercise the provisioner without touching the user's real tool directories or making network requests.

## Progress

- [x] (2026-08-24 12:01Z) Confirmed the worktree is clean and identified `.harness/workspace.sh` and `.harness/workspace.lock` as new canonical files.
- [x] (2026-08-24 12:01Z) Inspected current hook, skill, Make, and template conventions.
- [x] (2026-08-24 12:56Z) Added and validated the data-only lock manifest with every approved pin and official artifact digest.
- [x] (2026-08-24 12:56Z) Added the canonical provisioner and isolated black-box tests.
- [x] (2026-08-24 12:56Z) Ran Bash 3.2 syntax, manifest, behavior, preflight, whitespace, and worktree-cleanliness checks.

## Surprises & Discoveries

- Observation: The repository has no root test harness or existing `.harness` directory.
  Evidence: `rg --files` lists language-template tests only, and `find . -maxdepth 3 -type f -path './.*'` finds no `.harness` files.

- Observation: `VISION.md`, previously described as untracked in an earlier audit, is now tracked at `HEAD` and the worktree is clean.
  Evidence: `git ls-files --stage VISION.md` returns a stage-zero blob and `git status --porcelain=v1` is empty.

- Observation: Several approved tools are package-manager applications rather than standalone binaries, and their top-level package digests do not lock their transitive dependency graphs.
  Evidence: lizard, vulture, and pip-audit publish Python wheels; Knip publishes an npm tarball; the Go analyzers are module-backed; and cargo-modules is a crate. Official metadata supplies top-level hashes, but pip-audit and Knip have transitive dependencies that require ecosystem lock data.

- Observation: A brace counter and substring search cannot prove that a Stop command is attached to an executable Stop hook.
  Evidence: An adversarial fixture with the exact command under `description`, and another with `type` and `command` split across hook objects, both bypass substring checks but fail the recursive structural validator.

- Observation: Archive digests authenticate bytes but do not make generic extraction safe.
  Evidence: A fixture tarball containing a symlink passes its expected SHA-256 check; the member-name/type guard now rejects it before extraction.

- Observation: macOS still ships Bash 3.2, where `set -u` treats a declared empty indexed array as unbound during expansion.
  Evidence: The initial implementation failed on an empty cleanup/profile array under `/bin/bash 3.2.57`; the provisioner retains `errexit` and `pipefail` but performs explicit input validation instead of enabling global `nounset`.

## Decision Log

- Decision: Keep the manifest line-oriented and tab-separated instead of executable shell syntax or JSON.
  Rationale: The bootstrap layer includes Bash but not `jq` or Python. A strict parser can reject malformed records without evaluating manifest text as code.
  Date/Author: 2026-08-24 / Codex

- Decision: Test through isolated temporary repositories and fake release artifacts.
  Rationale: Phase 1 must prove network, checksum, atomicity, offline, Git-state, hook, and skill behavior without installing tools into the real home directory.
  Date/Author: 2026-08-24 / Codex

- Decision: Do not edit the root Makefile or any template in phase 1.
  Rationale: The user approved a staged implementation, and the repository requires bounded subtasks. Public routing and per-language dependency restoration belong to later phases.
  Date/Author: 2026-08-24 / Codex

- Decision: In phase 1, install only manifest records whose kind is `archive`; validate and expose ecosystem `pin` records without installing them yet.
  Rationale: Treating a top-level wheel, npm tarball, module, or crate hash as a complete dependency lock would violate the deterministic contract. The language-adoption phases will bind those pins to complete uv/Bun/Go/Cargo lock semantics. The canonical manifest still records every approved version and every official artifact digest currently available.
  Date/Author: 2026-08-24 / Codex

- Decision: Parse Stop configuration as a strict JSON subset with recursive object/array structure and require `type` plus the exact command in the same `hooks.Stop[].hooks[]` object; compare the Codex wrapper byte-for-byte.
  Rationale: No JSON runtime belongs in the bootstrap prerequisites, while substring checks permit non-executable lookalikes. Escaped object keys are rejected, duplicate keys fail, and unrelated settings remain allowed.
  Date/Author: 2026-08-24 / Codex

- Decision: Reject archive links and unsafe member names, extract only declared payloads, and bind installed-tool receipts to the complete tool and artifact records.
  Rationale: This prevents traversal during extraction and makes any URL, format, payload, path, probe, or digest change invalidate reuse.
  Date/Author: 2026-08-24 / Codex

- Decision: Keep ambient system paths after managed paths in phase 1.
  Rationale: The provisioner itself and later harness commands still need bootstrap utilities such as Git and Make. Every directly managed executable takes precedence. Ecosystem-backed pins are deliberately unavailable until later phases install their complete dependency graphs and route public targets through the matching profile.
  Date/Author: 2026-08-24 / Codex

## Outcomes & Retrospective

Phase 1 is complete. `.harness/workspace.sh` now validates the lock and platform, aggregates bootstrap failures, rejects dirty Git state and hook collisions, installs authenticated standalone artifacts with rollback, supplies a managed execution environment, writes root-entering Git hooks, deploys exactly one managed skill directory per agent, and verifies all resulting state. `.harness/workspace.lock` records every approved version and the official artifacts available for it.

Validation passed under macOS Bash 3.2: 14 lock-manifest cases and 46 provisioner cases, plus production lock validation, supported-platform detection, a production-root preflight with redirected `HOME`, syntax checks, and whitespace checks. The tests use no network and do not touch the real tool store, hooks, or skill deployments.

Deferred by the approved phase boundary: root/template `make workspace` routing, complete ecosystem dependency locks and installation, per-language locked dependency restoration, generated embedded skill assets and cross-surface drift targets, CI adoption, and the normal final `make check`. Those changes consume this canonical interface in later phases.

## Context and Orientation

The repository root is `/Users/juan/Code/harness`. It contains five independent templates plus a canonical skill under `skills/harness`. No common provisioning program exists today. Each template assumes its language package manager already exists, and several hook installers overwrite hook files directly.

The new `.harness/workspace.lock` is the only source of tool versions, artifact locations, SHA-256 digests, archive layouts, and ecosystem package pins. It is data, not a shell program. The new `.harness/workspace.sh` reads that file using Bash built-ins and simple text tools. A profile is a named set of tools: `common`, `python`, `bun`, `go`, or `rust`. Later Makefiles will pass the union of profiles required by a repository.

An artifact installation is a versioned directory below `$HOME/.local/share/harness/tools/<tool>/<version>`. The provisioner downloads into a temporary sibling directory, verifies SHA-256 before extraction, verifies the expected executable after extraction, and renames the completed directory into place. The managed execution command builds `PATH` from those exact directories and sets repository-owned cache paths. Ambient versions do not satisfy installation or verification.

## Plan of Work

First, add `.harness/workspace.lock`. Its header declares a format version. Tool records declare the profile, exact version, installation kind, and version probe. Artifact records map a tool to one of the four supported platform keys (`darwin-x86_64`, `darwin-arm64`, `linux-x86_64`, `linux-arm64`) and contain the official URL, SHA-256 digest, archive format, and path to the executable or directory inside the archive. Package records pin tools installed through uv, Bun, Go, or Cargo even when they do not have standalone platform archives.

Second, add `.harness/workspace.sh`. It must support `preflight`, `install`, `exec`, `install-hooks`, `sync-skills`, and `verify`. `preflight` aggregates missing bootstrap commands, rejects unsupported kernels, CPUs, and non-glibc Linux, requires a writable home and a Git worktree with no tracked or staged changes, validates the manifest, validates committed Stop wiring, and refuses unmanaged `pre-commit` or `pre-push` hooks. In this phase, `install` selects direct `archive` tools for the requested profiles, refuses network access when `OFFLINE=1`, downloads with curl otherwise, checks SHA-256, extracts into temporary directories, and atomically publishes valid installations. Ecosystem `pin` records remain data until their language-adoption phase adds complete transitive locks. `exec` sets exact versioned direct-tool and cache paths before replacing itself with the requested command. `install-hooks` writes deterministic root-entering Make shims only after both hook destinations are known to be safe. `sync-skills` replaces only the two managed harness skill directories from the repository's canonical or embedded skill source. `verify` repeats the non-mutating integrity, hook, Stop, and skill comparisons.

Third, add executable shell tests. They must use redirected `HOME`, temporary Git repositories, fake `uname`/glibc probes, local `file://` or fake-curl release artifacts, and fixture manifests. Tests must cover all platform mappings and unsupported platforms, aggregated prerequisites, manifest rejection, checksum failure, atomic recovery, warm reuse, cold offline failure, exact managed environment variables, tracked/index rejection with untracked preservation, unmanaged hook refusal, legacy migration, worktree/core.hooksPath behavior, and skill replacement that preserves unrelated sibling skills.

## Concrete Steps

Work from `/Users/juan/Code/harness`.

Create and inspect the plan and production files:

    bash -n .harness/workspace.sh
    .harness/workspace.sh help

Run the isolated tests:

    bash tests/workspace_lock_test.sh
    bash tests/workspace_test.sh

Run repository-level static checks that do not require the future Make integration:

    git diff --check
    git status --porcelain=v1

The test scripts should print one concise `ok` line per case and exit zero. The final Git status should list only the intended new phase-one files.

## Validation and Acceptance

The lock test must reject malformed field counts, duplicate tool/platform records, unknown profiles, invalid versions, non-HTTPS artifact URLs outside explicit test fixtures, non-64-character lowercase SHA-256 digests, and missing platform coverage for binary tools. It must accept the checked-in production manifest.

The provisioner test must demonstrate that a fresh online direct-artifact install places a verified executable under the redirected managed-tool root, a second install performs no download, a warm offline install succeeds, and a cold offline install fails before hooks or skills change. A deliberately corrupt artifact must fail without replacing an existing correct installation. A corrupt existing installation must be replaced during an online run.

The test must also demonstrate that untracked files survive, dirty tracked or staged files block preflight, either unmanaged Git hook blocks both hook writes, exact legacy harness shims migrate to the new root-entering Make shims, and repeated hook and skill operations are byte-identical.

## Idempotence and Recovery

All phase-one commands are retryable. Downloads and extraction happen under temporary sibling paths and are published only after validation. Failed temporary directories are removed by traps. An offline miss never invokes curl and never changes hooks or deployed skills. Hook collision checks finish for both destinations before either hook is staged. Skill deployment stages a complete managed directory before replacing the prior managed directory; unrelated skill directories are outside the replacement target.

The tests redirect every mutable location to a temporary directory and remove it at exit. They never use the real `$HOME`, real Git hooks, or network.

## Artifacts and Notes

The production manifest contains official artifact URLs and fixed SHA-256 values. Tests use a separate fixture manifest so corrupt-artifact cases do not weaken or alter production pins.

No lockfile, suppression baseline, architecture file, agent policy, remote, commit, or branch may be changed by the provisioner.

## Interfaces and Dependencies

`.harness/workspace.sh` is an executable Bash script with this command interface:

    workspace.sh preflight [profile ...]
    workspace.sh install [profile ...]
    workspace.sh exec [profile ...] -- command [argument ...]
    workspace.sh install-hooks
    workspace.sh sync-skills
    workspace.sh verify [profile ...]
    workspace.sh help

With no profile, profile selection defaults to `common`. Later root and monorepo Makefiles will pass a de-duplicated profile union. In addition to the public bootstrap layer, preflight explicitly reports any missing standard utilities the implementation invokes (`awk`, `mktemp`, `mkdir`, `cp`, `chmod`, `mv`, `rm`, `cmp`, and `diff`) rather than failing midway. Tests may set `HARNESS_WORKSPACE_LOCK`, `HARNESS_WORKSPACE_ROOT`, and `HARNESS_WORKSPACE_TOOLS` to isolated fixture paths; production callers leave them unset.

Revision note (2026-08-24): Created the phase-one ExecPlan before source edits to record scope, safety properties, interfaces, and acceptance evidence. Updated it after official artifact research showed that ecosystem top-level packages need later full dependency locks rather than phase-one pseudo-locking.
