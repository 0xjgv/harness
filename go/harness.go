//go:build ignore

package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"harness/crap"
	"harness/suppressions"
)

// ── Configuration ───────────────────────────────────────────────────

var root = func() string {
	wd, _ := os.Getwd()
	return wd
}()

const (
	lizard            = "lizard@1.22.2"
	complexityMaxArgs = "8"
	// reportOnlyLimit is a lizard `-i N` high enough that it never fails: the
	// gate reports instead of blocking when no floor is recorded, and the
	// baseline writer counts warnings from the summary row it still prints.
	reportOnlyLimit = "1000000"
	baselineFile    = ".harness-baseline"
	updateBaseline  = "go run harness.go suppressions --update-baseline"
	// updateBaselineMutation also measures mutation.min, which the automatic
	// pass leaves alone because a mutation run costs minutes.
	updateBaselineMutation = updateBaseline + " --with-mutation"
	crapMaxDefault         = 30.0
)

// ── Output ──────────────────────────────────────────────────────────

const (
	green = "\033[32m"
	red   = "\033[31m"
	blue  = "\033[34m"
	dim   = "\033[2m"
	reset = "\033[0m"
)

var verbose bool

func init() {
	for _, arg := range os.Args[1:] {
		if arg == "--verbose" {
			verbose = true
		}
	}
	_ = os.Chdir(root)
}

// ── Runner ──────────────────────────────────────────────────────────

type runResult struct {
	ok     bool
	output string
}

type runOpts struct {
	extract func(output string) string
	hint    string
	noExit  bool
	// stream inherits stdio for commands whose live output is part of the contract.
	stream bool
}

// gate is a read-only gate's label + command, shared by the standalone cmd* and the batch.
type gate struct {
	description string
	cmd         []string
	extract     func(output string) string
	hint        string
}

type gateResult struct {
	description string
	cmd         []string
	ok          bool
	exitCode    int
	output      string
	detail      string
	hint        string
}

// runCapture runs a gate's command with output captured (no printing, no exit):
// the goroutine-safe unit the parallel batch runs.
func runCapture(g gate) gateResult {
	c := exec.Command(g.cmd[0], g.cmd[1:]...)
	c.Dir = root
	out, err := c.CombinedOutput()
	output := string(out)
	ok := err == nil
	detail := ""
	code := 0
	if ok {
		if g.extract != nil {
			detail = g.extract(output)
		}
	} else {
		code = exitCode(err)
	}
	return gateResult{g.description, g.cmd, ok, code, output, detail, g.hint}
}

// printGateResult prints a gate's ✓/✗ line (with the failure body); exits on
// failure unless noExit. Returns ok.
func printGateResult(r gateResult, noExit bool) bool {
	if verbose {
		fmt.Printf("  %s→ %s%s\n", dim, strings.Join(r.cmd, " "), reset)
		if r.output != "" {
			fmt.Print(r.output)
		}
	}
	if r.ok {
		suffix := ""
		if r.detail != "" {
			suffix = fmt.Sprintf(" %s(%s)%s", dim, r.detail, reset)
		}
		fmt.Printf("  %s✓%s %s%s\n", green, reset, r.description, suffix)
		return true
	}
	fmt.Printf("  %s✗%s %s\n", red, reset, r.description)
	if !verbose && r.output != "" {
		fmt.Print(r.output)
	}
	if r.hint != "" {
		fmt.Printf("  ↳ fix: %s\n", r.hint)
	}
	if !noExit {
		os.Exit(r.exitCode)
	}
	return false
}

func run(description string, cmd []string, opts *runOpts) runResult {
	if verbose || (opts != nil && opts.stream) {
		fmt.Printf("  %s→ %s%s\n", dim, strings.Join(cmd, " "), reset)
		c := exec.Command(cmd[0], cmd[1:]...)
		c.Dir = root
		c.Stdout = os.Stdout
		c.Stderr = os.Stderr
		err := c.Run()
		if err != nil {
			fmt.Printf("  %s✗%s %s\n", red, reset, description)
			if opts != nil && opts.hint != "" {
				fmt.Printf("  ↳ fix: %s\n", opts.hint)
			}
			if opts == nil || !opts.noExit {
				os.Exit(exitCode(err))
			}
			return runResult{ok: false}
		}
		fmt.Printf("  %s✓%s %s\n", green, reset, description)
		return runResult{ok: true}
	}

	g := gate{description: description, cmd: cmd}
	if opts != nil {
		g.extract = opts.extract
		g.hint = opts.hint
	}
	r := runCapture(g)
	ok := printGateResult(r, opts != nil && opts.noExit)
	return runResult{ok: ok, output: r.output}
}

// runGatesParallel runs read-only gates concurrently, then prints each result in
// submission order. Returns true when every gate passed. Unlike the fail-fast
// standalone gates, this runs all gates to completion so one pass surfaces every
// failure; the caller exits non-zero afterward. Results are collected into an
// index-stable slice and printed in submission order (not as they finish) so a
// parallel run reads the same every time — matching the monorepo Makefile's dump.
// runGatesParallel returns whether every gate passed and, for any that
// didn't, their descriptions — the caller decides how loudly to surface
// the failure (e.g. stop-hook echoes them to stderr for Claude to see).
func runGatesParallel(gates []gate) (bool, []string) {
	if len(gates) == 0 {
		return true, nil
	}
	results := make([]gateResult, len(gates))
	var wg sync.WaitGroup
	for i, g := range gates {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = runCapture(g)
		}()
	}
	wg.Wait()

	allOk := true
	var failed []string
	for _, r := range results {
		if !printGateResult(r, true) {
			allOk = false
			failed = append(failed, r.description)
		}
	}
	return allOk, failed
}

func exitCode(err error) int {
	if exitErr, ok := err.(*exec.ExitError); ok {
		return exitErr.ExitCode()
	}
	return 1
}

// ── Extractors ──────────────────────────────────────────────────────

var testSummaryRe = regexp.MustCompile(`ok\s+\S+\s+([\d.]+)s`)

func extractTestSummary(output string) string {
	matches := testSummaryRe.FindAllStringSubmatch(output, -1)
	if len(matches) == 0 {
		return ""
	}
	total := 0.0
	for _, m := range matches {
		d, err := strconv.ParseFloat(m[1], 64)
		if err != nil {
			continue
		}
		total += d
	}
	return fmt.Sprintf("%d pkg, %.2fs", len(matches), total)
}

// ── Git helpers ─────────────────────────────────────────────────────

func stagedGoFiles() []string {
	c := exec.Command("git", "diff", "--cached", "--name-only", "--diff-filter=d", "--relative")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}

	var files []string
	for f := range strings.SplitSeq(strings.TrimSpace(string(out)), "\n") {
		if strings.HasSuffix(f, ".go") && f != "" {
			files = append(files, f)
		}
	}
	return files
}

func stagedPackages(files []string) []string {
	seen := make(map[string]bool)
	var pkgs []string
	for _, f := range files {
		dir := filepath.Dir(f)
		if dir == "" || dir == "." {
			return []string{"./..."}
		} else {
			dir = "./" + dir
		}
		if !seen[dir] {
			seen[dir] = true
			pkgs = append(pkgs, dir)
		}
	}
	return pkgs
}

func hasNonTestFiles(files []string) bool {
	for _, f := range files {
		if !strings.HasSuffix(f, "_test.go") {
			return true
		}
	}
	return false
}

// changedGoFiles returns .go files with uncommitted changes in this
// template's subtree. `git status --porcelain` prints repo-root-relative
// paths regardless of cwd, so the `-- .` pathspec scopes it to the current
// template and PorcelainChangedGoPath rejects anything git still reports
// outside that subtree (defense in depth) as well as deletions and renames'
// old-side paths.
func changedGoFiles() []string {
	c := exec.Command("git", "status", "--porcelain", "--", ".")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}

	prefix := gitPrefix()
	var files []string
	for line := range strings.SplitSeq(strings.TrimSpace(string(out)), "\n") {
		if f, ok := suppressions.PorcelainChangedGoPath(line, prefix); ok {
			files = append(files, f)
		}
	}
	return files
}

// ── Commands ────────────────────────────────────────────────────────

func cmdFix(pkgs []string) {
	if len(pkgs) == 0 {
		pkgs = []string{"./..."}
	}
	run("Fix & format", append([]string{"golangci-lint", "run", "--fix"}, pkgs...), nil)
}

func lintGate(pkgs []string) gate {
	if len(pkgs) == 0 {
		pkgs = []string{"./..."}
	}
	return gate{
		description: "Lint & format check",
		cmd:         append([]string{"golangci-lint", "run"}, pkgs...),
		hint:        "run `go run harness.go fix`",
	}
}

func cmdLint(pkgs []string) {
	g := lintGate(pkgs)
	run(g.description, g.cmd, nil)
}

func cmdTest() {
	run("Tests", []string{"go", "test", "./..."}, nil)
}

func cmdTestCov() {
	run("Tests with coverage", []string{
		"go", "test", "-race", "-count=1",
		"-coverprofile=coverage.out", "./...",
	}, nil)
	minPct := coverageMinDefault()
	pct, ok := coveragePercent()
	if !ok {
		fmt.Printf("  %s✗%s Coverage: coverage.out not found\n", red, reset)
		os.Exit(1)
	}
	if pct >= float64(minPct) {
		fmt.Printf("  %s✓%s Coverage >= %d%% %s(%.1f%%)%s\n", green, reset, minPct, dim, pct, reset)
		return
	}
	fmt.Printf("  %s✗%s Coverage >= %d%% %s(got %.1f%%)%s\n", red, reset, minPct, dim, pct, reset)
	os.Exit(1)
}

func auditGate() gate {
	return gate{
		description: "Dep audit",
		cmd:         []string{"go", "run", "golang.org/x/vuln/cmd/govulncheck@v1.1.4", "./..."},
		hint:        "bump the vulnerable dependency or escalate",
	}
}

// modTidyGate fails when go.mod/go.sum don't match what `go mod tidy` would
// produce — a direct import marked `// indirect` (or vice versa) silently
// bit-rots the require list otherwise. `-diff` reports without mutating.
func modTidyGate() gate {
	return gate{
		description: "go.mod tidy",
		cmd:         []string{"go", "mod", "tidy", "-diff"},
		hint:        "run `go mod tidy`",
	}
}

func cmdAudit() {
	g := auditGate()
	run(g.description, g.cmd, nil)
}

func cmdPostEdit() {
	if len(changedGoFiles()) == 0 {
		return
	}
	run("Fix & format", []string{"golangci-lint", "run", "--fix", "./..."}, &runOpts{noExit: true})
}

// cmdStopHook exits 2 (not 1) on failure: Claude Code treats a Stop hook's
// exit code 2 as blocking and feeds its stderr back to the model, but any
// other non-zero exit is a non-blocking error the model never sees. Codex
// doesn't care about the exit code itself — codex-stop-hook.sh maps any
// non-zero exit to {"decision":"block"} — so this only changes Claude's path.
func cmdStopHook() {
	fmt.Println("\n=== Stop Hook Checks ===\n")
	cmdPostEdit() // mutating — sequential, first
	checkArchConfigGuard(true, false, false)
	checkGherkinGuard(true, false, false)
	allOk, failed := runGatesParallel([]gate{complexityGate()}) // read-only batch
	if !allOk {
		fmt.Fprintf(os.Stderr, "stop-hook: failed gate(s): %s\n", strings.Join(failed, ", "))
		os.Exit(2)
	}
}

// ── Quality gates ───────────────────────────────────────────────────

const (
	archConfig         = ".go-arch-lint.yml"
	archConfigAllowEnv = "HARNESS_ALLOW_ARCH_CONFIG"
)

// flagValue returns the value of a `--name=value` flag from os.Args, or def.
func flagValue(name, def string) string {
	prefix := "--" + name + "="
	for _, a := range os.Args[1:] {
		if strings.HasPrefix(a, prefix) {
			return strings.TrimPrefix(a, prefix)
		}
	}
	return def
}

// hasFlag reports whether a bare `--name` flag is present in os.Args.
func hasFlag(name string) bool {
	want := "--" + name
	for _, a := range os.Args[1:] {
		if a == want {
			return true
		}
	}
	return false
}

func coverageMinDefault() int {
	raw := flagValue("min", "")
	if raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil {
			fmt.Printf("  %s✗%s Coverage: invalid --min=%q (must be an integer)\n", red, reset, raw)
			os.Exit(1)
		}
		return value
	}
	if baseline, ok := suppressions.ReadBaseline(root); ok {
		return baseline["coverage.min"]
	}
	return 0
}

func coveragePercent() (float64, bool) {
	c := exec.Command("go", "tool", "cover", "-func=coverage.out")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return 0, false
	}
	for line := range strings.SplitSeq(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[0] == "total:" {
			raw := strings.TrimSuffix(fields[2], "%")
			pct, err := strconv.ParseFloat(raw, 64)
			return pct, err == nil
		}
	}
	return 0, false
}

// acceptanceGatesOrWarn builds the godog Gherkin gate (run as a `go test`), or
// warns + returns nil when there are no scenarios — mirrors python's cmd_acceptance.
func acceptanceGatesOrWarn() []gate {
	featuresDir := filepath.Join(root, "features")
	var featureFiles []string
	_ = filepath.WalkDir(featuresDir, func(path string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() && strings.HasSuffix(path, ".feature") {
			featureFiles = append(featureFiles, path)
		}
		return nil
	})
	if len(featureFiles) == 0 {
		fmt.Printf("  %s⚠%s Acceptance: no .feature files in features/ (add one to enable this gate)\n", green, reset)
		return nil
	}
	return []gate{{
		description: "Acceptance (godog)",
		cmd:         []string{"go", "test", "./features/..."},
		hint:        "align implementation with the `.feature`, not vice versa",
	}}
}

func cmdAcceptance() {
	for _, g := range acceptanceGatesOrWarn() {
		run(g.description, g.cmd, nil)
	}
}

// archGatesOrWarn builds the import/dependency-boundary gate, or warns + returns
// nil when .go-arch-lint.yml is absent.
func archGatesOrWarn() []gate {
	if _, err := os.Stat(filepath.Join(root, archConfig)); err != nil {
		fmt.Printf("  %s⚠%s Arch: no %s — skipped\n", green, reset, archConfig)
		return nil
	}
	return []gate{{description: "Arch (go-arch-lint)", cmd: []string{
		"go", "run", "github.com/fe3dback/go-arch-lint@v1.15.0", "check",
	}, hint: "boundary crossed; surface the design decision to the human; don't edit arch config"}}
}

func cmdArch() {
	for _, g := range archGatesOrWarn() {
		run(g.description, g.cmd, nil)
	}
}

func gitLines(args ...string) []string {
	c := exec.Command("git", args...)
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}
	var lines []string
	for line := range strings.SplitSeq(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

func gitPrefix() string {
	lines := gitLines("rev-parse", "--show-prefix")
	if len(lines) == 0 {
		return ""
	}
	return strings.Trim(strings.TrimPrefix(filepath.ToSlash(lines[0]), "./"), "/")
}

// normalizeChangedPath delegates to the pure, unit-tested implementation in
// suppressions — harness.go carries `//go:build ignore` and is not part of
// any testable package.
func normalizeChangedPath(path, prefix string) string {
	return suppressions.NormalizeChangedPath(path, prefix)
}

// changedPathsFromBase resolves the base ref to diff against for the arch
// config guard. HARNESS_ARCH_BASE (explicit override) and GITHUB_BASE_REF
// (set by GitHub Actions, but only on `pull_request` events) take priority.
// Neither is set on a direct `push` to main, which would otherwise leave a
// same-branch push blind to an arch config change — so absent both, fall
// back to the first of origin/HEAD, origin/main, main that resolves. On main
// itself the `base...HEAD` triple-dot diff is empty (merge-base(main,main)
// == HEAD), so this adds no false positives.
func changedPathsFromBase() []string {
	var bases []string
	if base := os.Getenv("HARNESS_ARCH_BASE"); base != "" {
		bases = append(bases, base)
	}
	if githubBase := os.Getenv("GITHUB_BASE_REF"); githubBase != "" {
		bases = append(bases, "origin/"+githubBase)
	}
	if len(bases) == 0 {
		for _, candidate := range []string{"origin/HEAD", "origin/main", "main"} {
			if len(gitLines("rev-parse", "--verify", candidate)) > 0 {
				bases = append(bases, candidate)
				break
			}
		}
	}
	var paths []string
	for _, base := range bases {
		if len(gitLines("rev-parse", "--verify", base)) == 0 {
			continue
		}
		paths = append(paths, gitLines("diff", "--name-only", "--diff-filter=d", base+"...HEAD", "--", ".")...)
	}
	return paths
}

func changedPathsFromPrePushStdin() []string {
	info, err := os.Stdin.Stat()
	if err != nil || info.Mode()&os.ModeCharDevice != 0 {
		return nil
	}
	data, err := io.ReadAll(os.Stdin)
	if err != nil || len(strings.TrimSpace(string(data))) == 0 {
		return nil
	}
	zero := strings.Repeat("0", 40)
	var paths []string
	for line := range strings.SplitSeq(string(data), "\n") {
		parts := strings.Fields(line)
		if len(parts) < 4 {
			continue
		}
		localSha, remoteSha := parts[1], parts[3]
		if localSha == zero {
			continue
		}
		if remoteSha == zero {
			paths = append(paths, gitLines("diff-tree", "--no-commit-id", "--name-only", "-r", localSha, "--", ".")...)
		} else {
			paths = append(paths, gitLines("diff", "--name-only", "--diff-filter=d", remoteSha, localSha, "--", ".")...)
		}
	}
	return paths
}

// changedPaths gathers every changed path in this template's subtree: the
// working tree diff, staged diff, untracked files, and the diff against the
// resolved base branch (all three skipped when staged is true — only the
// staged diff applies), plus, when requested, refs read from pre-push's
// stdin. Shared by the arch config guard and the Gherkin-first guard so
// both git-diff invocations, base-ref resolution, and pre-push stdin
// parsing have exactly one implementation.
func changedPaths(staged, includePrePushStdin bool) []string {
	var paths []string
	if staged {
		paths = append(paths, gitLines("diff", "--cached", "--name-only", "--diff-filter=d", "--", ".")...)
	} else {
		paths = append(paths, gitLines("diff", "--name-only", "--diff-filter=d", "--", ".")...)
		paths = append(paths, gitLines("diff", "--cached", "--name-only", "--diff-filter=d", "--", ".")...)
		paths = append(paths, gitLines("ls-files", "--others", "--exclude-standard", "--", ".")...)
		paths = append(paths, changedPathsFromBase()...)
	}
	if includePrePushStdin {
		paths = append(paths, changedPathsFromPrePushStdin()...)
	}
	return paths
}

func changedArchConfigs(staged, includePrePushStdin bool) []string {
	seen := map[string]bool{}
	prefix := gitPrefix()
	for _, p := range changedPaths(staged, includePrePushStdin) {
		if normalizeChangedPath(p, prefix) == archConfig {
			seen[archConfig] = true
		}
	}
	var changed []string
	for p := range seen {
		changed = append(changed, p)
	}
	sort.Strings(changed)
	return changed
}

func checkArchConfigGuard(warnOnly, staged, includePrePushStdin bool) bool {
	changed := changedArchConfigs(staged, includePrePushStdin)
	if len(changed) == 0 {
		fmt.Printf("  %s✓%s Arch config guard\n", green, reset)
		return true
	}
	joined := strings.Join(changed, ", ")
	if os.Getenv(archConfigAllowEnv) == "1" {
		fmt.Printf("  %s⚠%s Arch config guard override: %s\n", green, reset, joined)
		return true
	}
	if warnOnly {
		fmt.Printf("  %s⚠%s Arch config changed: %s\n", green, reset, joined)
		fmt.Printf("  ↳ fix: review intentionally, then use %s=1 for commit/push/CI\n", archConfigAllowEnv)
		return true
	}
	fmt.Printf("  %s✗%s Arch config changed: %s\n", red, reset, joined)
	fmt.Printf("  ↳ fix: review intentionally, then rerun with %s=1\n", archConfigAllowEnv)
	return false
}

func cmdArchConfigGuard() {
	if !checkArchConfigGuard(hasFlag("warn"), hasFlag("staged"), false) {
		os.Exit(1)
	}
}

// ── Gherkin-first guard ─────────────────────────────────────────────

const gherkinGuardAllowEnv = "HARNESS_ALLOW_NO_FEATURE"

// hasAnyFeatureFiles reports whether the template contains at least one
// `.feature` file anywhere under root.
func hasAnyFeatureFiles() bool {
	found := false
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil || found {
			return nil
		}
		if !d.IsDir() && strings.HasSuffix(path, ".feature") {
			found = true
		}
		return nil
	})
	return found
}

// checkGherkinGuard mechanizes the "write a .feature before changing
// user-visible behavior" rule, mirroring checkArchConfigGuard's shape
// exactly: gather the same changed-path set (changedPaths, shared with the
// arch config guard), classify it, print a single ✓/⚠/✗ line, and return
// whether the caller should proceed. "Production source" = a changed
// non-test .go file outside features/, excluding harness.go itself (see
// suppressions.IsGherkinGuardProductionPath). Silently passes (no output at
// all) when the template has no .feature files anywhere yet — retrofitting
// the harness into a repo with no acceptance suite must never block.
func checkGherkinGuard(warnOnly, staged, includePrePushStdin bool) bool {
	prefix := gitPrefix()
	var productionPaths []string
	hasFeatureChange := false
	for _, p := range changedPaths(staged, includePrePushStdin) {
		normalized := normalizeChangedPath(p, prefix)
		if suppressions.IsGherkinGuardProductionPath(normalized) {
			productionPaths = append(productionPaths, normalized)
		}
		if strings.HasSuffix(normalized, ".feature") {
			hasFeatureChange = true
		}
	}
	hasProductionChange := len(productionPaths) > 0
	override := os.Getenv(gherkinGuardAllowEnv) == "1"
	joined := strings.Join(productionPaths, ", ")

	switch suppressions.EvaluateGherkinGuard(hasAnyFeatureFiles(), hasProductionChange, hasFeatureChange, override) {
	case suppressions.GherkinGuardSkip:
		return true
	case suppressions.GherkinGuardTrigger:
		if warnOnly {
			fmt.Printf("  %s⚠%s Gherkin-first: production source changed with no .feature: %s\n", green, reset, joined)
			fmt.Printf("  ↳ fix: add a scenario under features/, or set %s=1 after review\n", gherkinGuardAllowEnv)
			return true
		}
		fmt.Printf("  %s✗%s Gherkin-first: production source changed with no .feature: %s\n", red, reset, joined)
		fmt.Printf("  ↳ fix: add a scenario under features/, or set %s=1 after review\n", gherkinGuardAllowEnv)
		return false
	default: // GherkinGuardPass
		if override && hasProductionChange && !hasFeatureChange {
			fmt.Printf("  %s⚠%s Gherkin-first override: %s\n", green, reset, joined)
		} else {
			fmt.Printf("  %s✓%s Gherkin-first\n", green, reset)
		}
		return true
	}
}

func cmdGherkinGuard() {
	if !checkGherkinGuard(hasFlag("warn"), hasFlag("staged"), false) {
		os.Exit(1)
	}
}

// mutationTargets are the packages gremlins mutates when a run is not scoped
// to a change set — `--all`, an explicit path argument, or the baseline
// measurement. The template ships `suppressions` as its sample library
// package; point this at your own source packages as the module grows.
var mutationTargets = []string{"./suppressions"}

const (
	gremlinsPkg = "github.com/go-gremlins/gremlins/cmd/gremlins@v0.5.0"
	// mutationTimeout is generous on purpose. gremlins derives each mutant's
	// budget from its own baseline test run and drops every mutant that
	// overruns it out of *both* score counts, so a coefficient that is merely
	// adequate silently shrinks the sample and inflates the score: on a loaded
	// machine `=10` scored 100% off 8 mutants where `=30` scored 80% off 49,
	// twice, with no timeouts. A floor is only worth recording if it reproduces.
	mutationTimeout = "--timeout-coefficient=30"
	// mutationReportGlob matches every per-target gremlins report `clean`
	// removes and .gitignore keeps out of the tree.
	mutationReportGlob = "gremlins-report*.json"
)

// mutationReport is the slice of gremlins' machine-readable report the gate
// reads: counts, not percentages, so several scoped runs sum into one score
// instead of averaging percentages.
//
// gremlins leaves timed-out mutants out of both counts, so a machine loaded
// enough to blow the mutant timeout shrinks the denominator instead of
// scoring those mutants — one more reason this gate warns rather than blocks.
type mutationReport struct {
	Killed     int `json:"mutants_killed"`
	Lived      int `json:"mutants_lived"`
	NotCovered int `json:"mutants_not_covered"`
}

func (r mutationReport) add(other mutationReport) mutationReport {
	return mutationReport{
		Killed:     r.Killed + other.Killed,
		Lived:      r.Lived + other.Lived,
		NotCovered: r.NotCovered + other.NotCovered,
	}
}

// mutationReportPath names one target's report so several targets do not
// overwrite each other's artifact: `./suppressions` → gremlins-report-suppressions.json.
func mutationReportPath(target string) string {
	name := strings.Trim(strings.ReplaceAll(strings.TrimPrefix(target, "./"), "/", "-"), ".")
	if name == "" {
		return "gremlins-report.json"
	}
	return "gremlins-report-" + name + ".json"
}

// readMutationReport decodes gremlins' `-o` report. A file that is valid JSON
// but carries none of gremlins' fields is rejected rather than decoded to
// all-zeros and scored as a run that killed nothing: `--report=` aimed at the
// wrong file must not pass for a clean tree.
func readMutationReport(reportPath string) (mutationReport, error) {
	var report mutationReport
	if !filepath.IsAbs(reportPath) {
		reportPath = filepath.Join(root, reportPath)
	}
	data, err := os.ReadFile(reportPath)
	if err != nil {
		return report, fmt.Errorf("cannot read gremlins report: %w", err)
	}
	var probe struct {
		Total *int `json:"mutants_total"`
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		return report, fmt.Errorf("cannot parse gremlins report %s: %w", reportPath, err)
	}
	if probe.Total == nil {
		return report, fmt.Errorf("%s is not a gremlins report: no mutants_total field", reportPath)
	}
	if err := json.Unmarshal(data, &report); err != nil {
		return report, fmt.Errorf("cannot parse gremlins report %s: %w", reportPath, err)
	}
	return report, nil
}

// mutationBaseRef resolves the single ref the change set is diffed against.
// Separate from changedPathsFromBase, which may diff against two bases at
// once and so has no single ref to name.
func mutationBaseRef() string {
	candidates := []string{flagValue("base", ""), os.Getenv("HARNESS_ARCH_BASE")}
	if githubBase := os.Getenv("GITHUB_BASE_REF"); githubBase != "" {
		candidates = append(candidates, "origin/"+githubBase)
	}
	candidates = append(candidates, "origin/HEAD", "origin/main", "main")
	for _, candidate := range candidates {
		if candidate != "" && len(gitLines("rev-parse", "--verify", candidate)) > 0 {
			return candidate
		}
	}
	return ""
}

// changedGoFilesSince lists the .go files this branch changed against a base
// ref — the change-set source for every stage that has one, because a
// `git status` on a fresh CI checkout is empty and would score a green gate
// that tested nothing.
func changedGoFilesSince(base string) []string {
	prefix := gitPrefix()
	var files []string
	for _, p := range gitLines("diff", "--name-only", "--diff-filter=d", base+"...HEAD", "--", ".") {
		files = append(files, normalizeChangedPath(p, prefix))
	}
	return files
}

// mutationScope resolves the packages to mutate. An explicit path argument or
// `--all` pins the concrete targets; otherwise the scope is the package
// directories of the changed .go files. workingTree is false in ci, where the
// uncommitted set must never be the source (see changedGoFilesSince).
//
// Scoping is package-granular, not line-granular: gremlins' own `--diff` is
// unusable with a concrete package target. It keys the diff on git's
// repo-root-relative paths (internal/diff/parse.go:30) but matches them
// against filenames walked from the target directory
// (internal/engine/engine.go:68 builds `os.DirFS(module root + calling dir)`),
// so `go/suppressions/suppressions.go` never matches `suppressions.go`: every
// mutant comes back SKIPPED and the run reports zero mutants — a green gate
// that tested nothing.
func mutationScope(workingTree bool) []string {
	if args := filterFlags(os.Args[1:]); len(args) > 1 {
		return []string{args[1]}
	}
	if hasFlag("all") {
		return mutationTargets
	}
	var changed []string
	if base := mutationBaseRef(); base != "" {
		changed = changedGoFilesSince(base)
	}
	// A local run unions the branch diff with the uncommitted set: a base ref
	// almost always resolves, so treating the two as alternatives would leave
	// the developer who just edited a file — and has not committed it — with an
	// empty scope on the very change the gate exists to score.
	if workingTree {
		changed = append(changed, changedGoFiles()...)
	}
	return suppressions.MutationPackages(changed)
}

// dirHasTestFiles reports whether a gremlins target's own package directory
// holds a test. gremlins can only kill a mutant a test reaches, so an
// untested package scores nothing and would only dilute the run. A directory
// that cannot be read is an error, never "no tests": a mistyped path argument
// must not skip its way to a green gate.
func dirHasTestFiles(target string) (bool, error) {
	dir := filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(target, "./")))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false, err
	}
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), "_test.go") {
			return true, nil
		}
	}
	return false, nil
}

func withTestFiles(targets []string) ([]string, error) {
	var kept []string
	for _, target := range targets {
		hasTests, err := dirHasTestFiles(target)
		if err != nil {
			return nil, fmt.Errorf("%s is not a package directory: %w", target, err)
		}
		if hasTests {
			kept = append(kept, target)
			continue
		}
		fmt.Printf("  %s⚠%s Mutation: %s has no *_test.go — skipped\n", green, reset, target)
	}
	return kept, nil
}

// warmTestCache builds and runs the suite before gremlins does. gremlins
// derives each mutant's test timeout from the baseline test run: a cold build
// cache makes the first mutant's compile blow that budget and every mutant
// reports TIMED OUT. mutationTimeout is the other half of the same fix.
func warmTestCache(stream bool) {
	if stream {
		run("Warm test cache", []string{"go", "test", "-count=1", "./..."},
			&runOpts{extract: extractTestSummary, noExit: true})
		return
	}
	c := exec.Command("go", "test", "-count=1", "./...")
	c.Dir = root
	_, _ = c.CombinedOutput()
}

// gremlinsUnleash mutates one package and returns its report. gremlins takes
// a single concrete package path — `./...` gathers no coverage from this
// module because the root file (harness.go) is build-ignored — so several
// targets mean several runs, aggregated by the caller.
func gremlinsUnleash(target string, stream bool) (mutationReport, error) {
	reportPath := mutationReportPath(target)
	// Drop the previous run's report first: gremlins writes it only on
	// success, so a leftover would be read back as this run's result and
	// scored as fresh.
	_ = os.Remove(filepath.Join(root, reportPath))
	argv := []string{"go", "run", gremlinsPkg, "unleash", mutationTimeout, "-o", reportPath, target}

	c := exec.Command(argv[0], argv[1:]...)
	c.Dir = root
	var captured bytes.Buffer
	if stream {
		fmt.Printf("  %s→ %s%s\n", dim, strings.Join(argv, " "), reset)
		c.Stdout = os.Stdout
		c.Stderr = os.Stderr
	} else {
		c.Stdout = &captured
		c.Stderr = &captured
	}
	// A non-zero exit fails the target whatever it left on disk. gremlins exits
	// non-zero both for a real failure and for its own efficacy/coverage
	// thresholds, and a run it aborted part-way may still have written a
	// report — scoring that would be a floor comparison against a partial run,
	// which is worse than no comparison at all.
	if err := c.Run(); err != nil {
		if !stream && captured.Len() > 0 {
			fmt.Print(captured.String())
		}
		return mutationReport{}, fmt.Errorf("gremlins exited %d", exitCode(err))
	}
	return readMutationReport(reportPath)
}

// mutateTargets runs every target and sums the reports. A target that failed
// is a warning by default — mutation is advisory, and the packages that did
// run still carry a score — but it is counted, because a score summed over
// only the targets that survived is not the score the floor was measured
// against, and `--enforce` must not pass on it.
func mutateTargets(targets []string, stream bool) (total mutationReport, ran, failed int) {
	for _, target := range targets {
		report, err := gremlinsUnleash(target, stream)
		if err != nil {
			fmt.Printf("  %s⚠%s Mutation: %s — %v (advisory — not blocking)\n", green, reset, target, err)
			failed++
			continue
		}
		total = total.add(report)
		ran++
	}
	return total, ran, failed
}

// reportMutationScore compares a run's kill rate to the `mutation.min` floor.
// Mirrors cmdCrap exactly: advisory `⚠` by default, `✗` + exit 1 under
// --enforce, and report-only — even under --enforce — when no floor is
// recorded, because an absent key means the repo has never been measured,
// not that it must already be perfect.
func reportMutationScore(report mutationReport, enforce bool) {
	score, scored := suppressions.MutationScore(report.Killed, report.Lived)
	if !scored {
		fmt.Printf("  %s⚠%s Mutation: no mutant was killed or survived (%d not covered) — nothing to score\n",
			green, reset, report.NotCovered)
		return
	}
	floor, hasFloor := suppressions.BaselineFloor(root, "mutation.min")
	if !hasFloor {
		fmt.Printf("  %s⚠%s Mutation: %d%% mutants killed (report-only: no %s floor)\n",
			green, reset, score, baselineFile)
		fmt.Printf("  ↳ fix: run `%s` to record a floor\n", updateBaselineMutation)
		return
	}
	if score >= floor {
		fmt.Printf("  %s✓%s Mutation: %d%% mutants killed (baseline %d)\n", green, reset, score, floor)
		return
	}
	glyph, color := advisoryGlyphAndColor(enforce)
	suffix := " (advisory)"
	if enforce {
		suffix = ""
	}
	fmt.Printf("  %s%s%s Mutation: %d%% mutants killed (baseline %d)%s\n",
		color, glyph, reset, score, floor, suffix)
	fmt.Println("  ↳ fix: add tests that kill the surviving mutants gremlins listed")
	if enforce {
		os.Exit(1)
	}
}

// runMutation is the mutation gate: gremlins over the packages the change set
// touches, scored against `mutation.min`. Advisory unless --enforce, and
// wired into ci that way — a kill rate is too jittery a number to block a
// pipeline on, and too useful to hide.
//
// Output is printed unconditionally: an advisory report you cannot see is useless.
func runMutation(workingTree bool) {
	enforce := hasFlag("enforce")
	// --report scores a report that already exists instead of spending
	// minutes producing a new one — the way to re-check a finished run
	// against a changed floor. An unreadable path fails hard rather than
	// scoring as "nothing to score": a typo must not pass for a clean tree.
	if reportPath := flagValue("report", ""); reportPath != "" {
		report, err := readMutationReport(reportPath)
		if err != nil {
			fmt.Printf("  %s✗%s Mutation: %v\n", red, reset, err)
			os.Exit(1)
		}
		reportMutationScore(report, enforce)
		return
	}

	targets, err := withTestFiles(mutationScope(workingTree))
	if err != nil {
		fmt.Printf("  %s✗%s Mutation: %v\n", red, reset, err)
		os.Exit(1)
	}
	if len(targets) == 0 {
		fmt.Printf("  %s⚠%s Mutation: no changed Go package with tests (skipped)\n", green, reset)
		return
	}
	warmTestCache(true)
	total, ran, failed := mutateTargets(targets, true)
	if ran > 0 {
		reportMutationScore(total, enforce)
	}
	// Nothing scored, or scored over an incomplete set: advisory by default,
	// but a gate asked to enforce must not pass because the tool broke.
	if failed > 0 && enforce {
		os.Exit(1)
	}
}

func cmdMutation() { runMutation(true) }

// funcMetric pairs a function's location with its cyclomatic complexity.
// Coverage is computed at join time in cmdCrap from per-line hit counts.
type funcMetric struct {
	file string
	line int
	end  int
	name string
	ccn  int
}

// lizard --csv location field: "name@start-end@path" (quoted, may contain commas in sig).
var lizardLocRe = regexp.MustCompile(`"([^"@]*)@(\d+)-(\d+)@([^"]+)"`)

// cmdCrap computes CRAP = CCN² × (1-cov)³ + CCN per function. Advisory.
//
// Inputs: `lizard --csv` gives per-function complexity + line range, and
// coverage.out (parsed by crap.ParseCoverProfile) gives per-line hits. The
// per-function coverage is the fraction of in-range tracked lines that ran.
// Joining on file+line range, not name, sidesteps Go's "(*Foo).Bar" vs "Bar"
// receiver-name mismatch between cover output and lizard output.
// advisoryGlyphAndColor pairs an advisory gate's advisory/enforce output
// glyph with the matching ANSI color used throughout this runner (⚠ prints
// green, ✗ prints red, by convention here). Shared by CRAP and mutation, the
// two gates that warn by default. The enforce→glyph mapping itself lives in
// crap.AdvisoryGlyph — pure, unit tested — so this stays a one-line wrapper.
func advisoryGlyphAndColor(enforce bool) (glyph, color string) {
	glyph = crap.AdvisoryGlyph(enforce)
	color = green
	if enforce {
		color = red
	}
	return glyph, color
}

type crapOffender struct {
	crap   float64
	cov    float64
	metric funcMetric
}

// crapMeasurement is one CRAP scoring pass: the offenders above the
// threshold, or the reason a tool could not produce a score.
type crapMeasurement struct {
	offenders []crapOffender
	problem   string
}

// crapMeasure scores every function's CRAP against maxCrap, refreshing
// coverage if stale.
func crapMeasure(maxCrap float64) crapMeasurement {
	covPath := filepath.Join(root, "coverage.out")
	if !coverageFresh(covPath) {
		cmdTestCov()
	}
	covText, err := os.ReadFile(covPath)
	if err != nil {
		return crapMeasurement{problem: "coverage.out not found after test-cov"}
	}

	// coverprofile paths are module-qualified ("harness/suppressions/foo.go");
	// lizard reports module-relative paths ("suppressions/foo.go"). Strip the
	// module prefix once so the two key spaces align.
	rawCov := crap.ParseCoverProfile(string(covText))
	modPrefix := goModulePath() + "/"
	cov := make(map[string]map[int]int, len(rawCov))
	for k, v := range rawCov {
		rel := strings.TrimPrefix(k, modPrefix)
		cov[rel] = v
	}

	metrics := complexityMetrics()
	if metrics == nil {
		// Lizard produced no usable output (uvx missing, lizard crash, format
		// drift). Reporting "all functions below max" would be a silent false-
		// pass; surface the failure.
		return crapMeasurement{problem: "lizard failed to run"}
	}

	var offenders []crapOffender
	for _, m := range metrics {
		c := functionCoverage(cov[m.file], m.line, m.end)
		score := crap.Score(m.ccn, c)
		if score > maxCrap {
			offenders = append(offenders, crapOffender{score, c, m})
		}
	}
	sort.Slice(offenders, func(i, j int) bool { return offenders[i].crap > offenders[j].crap })
	return crapMeasurement{offenders: offenders}
}

// printCrapOffenders lists the worst offenders, capped so a legacy tree does
// not bury the rest of the run.
func printCrapOffenders(offenders []crapOffender) {
	limit := min(len(offenders), 20)
	for _, o := range offenders[:limit] {
		m := o.metric
		fmt.Printf("    CRAP=%6.1f  CCN=%3d  cov=%5.1f%%  %s@%d %s\n",
			o.crap, m.ccn, o.cov*100, m.name, m.line, m.file)
	}
}

func cmdCrap() {
	maxCrap := crapMaxDefault
	if raw := flagValue("max", ""); raw != "" {
		value, err := strconv.ParseFloat(raw, 64)
		if err != nil {
			fmt.Printf("  %s✗%s CRAP: invalid --max=%q (must be a number)\n", red, reset, raw)
			os.Exit(1)
		}
		maxCrap = value
	}
	enforce := hasFlag("enforce")
	suffix := " (advisory)"
	if enforce {
		suffix = ""
	}
	glyph, color := advisoryGlyphAndColor(enforce)

	measurement := crapMeasure(maxCrap)
	if measurement.problem != "" {
		// Degrade to advisory unless --enforce.
		fmt.Printf("  %s%s%s CRAP: %s%s\n", color, glyph, reset, measurement.problem, suffix)
		if enforce {
			os.Exit(1)
		}
		return
	}

	offenders := measurement.offenders
	if len(offenders) == 0 {
		fmt.Printf("  %s✓%s CRAP: all functions below %.0f\n", green, reset, maxCrap)
		return
	}
	// The baseline is a count floor: a repo adopting the harness starts wherever
	// it already is, and that number may only come down.
	floor, hasFloor := suppressions.BaselineFloor(root, "crap.max_violations")
	if !hasFloor {
		// Nothing recorded is not a floor of 0; it is a repo that has never been
		// measured. Report what is there and pass — `--enforce` included — so
		// retrofitting the harness into a legacy tree is green on day one.
		fmt.Printf("  %s⚠%s CRAP: %d function(s) exceed %.0f (report-only: no %s floor)\n",
			green, reset, len(offenders), maxCrap, baselineFile)
		printCrapOffenders(offenders)
		fmt.Printf("  ↳ fix: run `%s` to record a floor\n", updateBaseline)
		return
	}
	if len(offenders) <= floor {
		fmt.Printf("  %s✓%s CRAP: %d function(s) exceed %.0f (baseline %d)\n", green, reset, len(offenders), maxCrap, floor)
		return
	}
	fmt.Printf("  %s%s%s CRAP: %d function(s) exceed %.0f (baseline %d)%s\n", color, glyph, reset, len(offenders), maxCrap, floor, suffix)
	printCrapOffenders(offenders)
	if enforce {
		os.Exit(1)
	}
}

func coverageFresh(covPath string) bool {
	covInfo, err := os.Stat(covPath)
	if err != nil {
		return false
	}
	covTime := covInfo.ModTime()
	fresh := true
	err = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			fresh = false
			return err
		}
		if d.IsDir() {
			switch d.Name() {
			case ".git", ".idea", ".vscode":
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			fresh = false
			return err
		}
		if info.ModTime().After(covTime) {
			fresh = false
		}
		return nil
	})
	return err == nil && fresh
}

// functionCoverage returns the fraction of tracked lines in [start,end] that
// ran at least once. Lines absent from fileMap are untracked (not counted).
// Returns 0 for a function whose lines are all untracked or fileMap is nil.
func functionCoverage(fileMap map[int]int, start, end int) float64 {
	if fileMap == nil {
		return 0
	}
	var tracked, covered int
	for ln := start; ln <= end; ln++ {
		hits, ok := fileMap[ln]
		if !ok {
			continue
		}
		tracked++
		if hits > 0 {
			covered++
		}
	}
	if tracked == 0 {
		return 0
	}
	return float64(covered) / float64(tracked)
}

// goModulePath returns the module path declared in go.mod, or "" if absent.
func goModulePath() string {
	data, err := os.ReadFile(filepath.Join(root, "go.mod"))
	if err != nil {
		return ""
	}
	for line := range strings.SplitSeq(string(data), "\n") {
		line = strings.TrimSpace(line)
		if rest, ok := strings.CutPrefix(line, "module "); ok {
			return strings.TrimSpace(rest)
		}
	}
	return ""
}

// complexityMetrics runs `lizard --csv` over the module and yields per-function
// (file, line range, name, ccn) tuples for CRAP scoring.
//
// `harness.go` carries `//go:build ignore`: it is not part of any testable
// package, so no coverage data can exist for it. Test files are also skipped
// because `go test -cover` records coverage only for the SUT.
//
// On lizard failure (non-zero exit), returns nil. The caller must NOT trust
// partial output: if lizard crashed mid-walk, a partial slice would let
// high-CCN functions slip through the gate silently.
func complexityMetrics() []funcMetric {
	c := exec.Command("uvx", lizard, "-l", "go", ".", "--csv")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}
	var metrics []funcMetric
	for row := range strings.SplitSeq(string(out), "\n") {
		cols := strings.SplitN(row, ",", 11)
		if len(cols) < 11 {
			continue
		}
		ccn, err := strconv.Atoi(cols[1])
		if err != nil {
			continue
		}
		m := lizardLocRe.FindStringSubmatch(row)
		if m == nil {
			continue
		}
		name := m[1]
		ln, _ := strconv.Atoi(m[2])
		end, _ := strconv.Atoi(m[3])
		path := strings.TrimPrefix(m[4], "./")
		base := filepath.Base(path)
		if base == "harness.go" || strings.HasSuffix(base, "_test.go") {
			continue
		}
		// Skip anonymous closures: per-function coverage attribution would
		// roll into the enclosing function and mis-score the closure itself.
		if name == "" {
			continue
		}
		metrics = append(metrics, funcMetric{
			file: path, line: ln, end: end, name: name, ccn: ccn,
		})
	}
	return metrics
}

// ── Stages ──────────────────────────────────────────────────────────

// checkStopHooksPresent warns when Claude/Codex Stop hook wiring is missing.
func checkStopHooksPresent() {
	for _, rel := range []string{".claude/settings.json", ".codex/hooks.json"} {
		data, _ := os.ReadFile(filepath.Join(root, rel))
		text := string(data)
		if strings.Contains(text, "Stop") && strings.Contains(text, "stop-hook") {
			fmt.Printf("  %s✓%s Stop hook wiring (%s)\n", green, reset, rel)
		} else {
			fmt.Printf("  %s⚠%s Missing Stop hook wiring: %s\n", red, reset, rel)
		}
	}
}

// firstDiffLine returns the 1-based line number of the first divergence.
func firstDiffLine(a, b string) int {
	al := strings.Split(a, "\n")
	bl := strings.Split(b, "\n")
	n := len(al)
	if len(bl) < n {
		n = len(bl)
	}
	for i := 0; i < n; i++ {
		if al[i] != bl[i] {
			return i + 1
		}
	}
	return n + 1
}

// checkAgentsMdDrift fails if AGENTS.md differs byte-for-byte from CLAUDE.md.
// Returns ok=true on identity, ok=false otherwise. When noExit is false, exits 1 on mismatch.
func checkAgentsMdDrift(noExit bool) runResult {
	claudePath := filepath.Join(root, "CLAUDE.md")
	agentsPath := filepath.Join(root, "AGENTS.md")
	fail := func(msg string) runResult {
		fmt.Printf("  %s✗%s agents-md-drift: %s\n", red, reset, msg)
		if !noExit {
			os.Exit(1)
		}
		return runResult{ok: false, output: msg}
	}
	a, err := os.ReadFile(claudePath)
	if err != nil {
		return fail("CLAUDE.md not found")
	}
	b, err := os.ReadFile(agentsPath)
	if err != nil {
		return fail("AGENTS.md missing — run `harness sync-agents-md`")
	}
	if string(a) == string(b) {
		fmt.Printf("  %s✓%s agents-md-drift\n", green, reset)
		return runResult{ok: true}
	}
	line := firstDiffLine(string(a), string(b))
	return fail(fmt.Sprintf(
		"AGENTS.md differs from CLAUDE.md (first diff at line %d) — run `harness sync-agents-md`",
		line,
	))
}

func cmdAgentsMdDrift() { checkAgentsMdDrift(false) }

// cmdSyncAgentsMd overwrites AGENTS.md with CLAUDE.md contents.
func cmdSyncAgentsMd() {
	claudePath := filepath.Join(root, "CLAUDE.md")
	a, err := os.ReadFile(claudePath)
	if err != nil {
		fmt.Printf("  %s✗%s sync-agents-md: CLAUDE.md not found\n", red, reset)
		os.Exit(1)
	}
	if err := os.WriteFile(filepath.Join(root, "AGENTS.md"), a, 0o644); err != nil {
		fmt.Printf("  %s✗%s sync-agents-md: %v\n", red, reset, err)
		os.Exit(1)
	}
	fmt.Printf("  %s✓%s sync-agents-md: AGENTS.md ← CLAUDE.md\n", green, reset)
}

func cmdCheck() {
	start := time.Now()
	fmt.Printf("\n%s[check]%s Running pre-flight checks...\n\n", blue, reset)

	results := []runResult{
		run("Fix & format", []string{"golangci-lint", "run", "--fix", "./..."}, &runOpts{noExit: true}),
		run("Tests", []string{"go", "test", "./..."}, &runOpts{extract: extractTestSummary, noExit: true}),
	}

	// Read-only parallel batch, after the mutating fix step above. Invariant:
	// `check` runs every gate that is offline, fast, and takes no build lock —
	// lint is already covered by Fix & format's own --fix exit code, but arch
	// (go-arch-lint) does NOT qualify: `go run github.com/fe3dback/go-arch-lint@…`
	// fetches a module, which hits the network on a cold module cache, so it
	// stays ci/pre-push-only. Folded into `results` gate-by-gate (not as one
	// combined entry) so the N passed/M failed summary below reflects each
	// gate individually.
	checkGates := []gate{complexityGate(), modTidyGate()}
	checkGates = append(checkGates, acceptanceGatesOrWarn()...)
	_, failedCheckGates := runGatesParallel(checkGates)
	failedCheckGateSet := make(map[string]bool, len(failedCheckGates))
	for _, d := range failedCheckGates {
		failedCheckGateSet[d] = true
	}
	for _, g := range checkGates {
		results = append(results, runResult{ok: !failedCheckGateSet[g.description]})
	}

	checkStopHooksPresent()
	checkArchConfigGuard(true, false, false)
	checkGherkinGuard(true, false, false)
	results = append(results, checkAgentsMdDrift(true))
	results = append(results, runResult{
		ok: suppressions.CheckBaseline(
			root,
			suppressions.ScanFindings(root),
			true,
			updateBaseline,
			true,
		),
	})

	elapsed := time.Since(start).Seconds()
	passed := 0
	failed := 0
	for _, r := range results {
		if r.ok {
			passed++
		} else {
			failed++
		}
	}

	fmt.Println()
	if failed > 0 {
		fmt.Printf("%sFAIL%s %d passed, %d failed %s(%.1fs)%s\n", red, reset, passed, failed, dim, elapsed, reset)
		os.Exit(1)
	}
	fmt.Printf("%sOK%s %d passed %s(%.1fs)%s\n", green, reset, passed, dim, elapsed, reset)
}

// cmdPreCommit runs the arch-config and gherkin-first guards before the
// staged-files early return: both are staged-mode and cheap, and a commit
// that stages only a non-Go file (an arch config edit alone, a .md, a
// lockfile) must not bypass them.
func cmdPreCommit() {
	fmt.Printf("\n%s[pre-commit]%s\n\n", blue, reset)

	if !checkArchConfigGuard(false, true, false) {
		os.Exit(1)
	}
	if !checkGherkinGuard(false, true, false) {
		os.Exit(1)
	}

	files := stagedGoFiles()
	if len(files) == 0 {
		fmt.Println("No staged Go files — skipping checks")
		return
	}

	pkgs := stagedPackages(files)
	cmdFix(pkgs)
	restageFixedFiles(files)
	checkAgentsMdDrift(false)

	if hasNonTestFiles(files) {
		cmdTest()
	}
}

// restageFixedFiles re-adds staged files after cmdFix rewrites the working
// tree, so the commit records the fixed blob instead of the pre-fix one.
// Files cmdFix deleted (rare — a fix that removes a file outright) are left
// out; `git add` on a missing path fails and there is nothing to re-stage.
// Note: if a file was only partially staged, `git add` also stages its
// remaining unstaged hunks — there is no way to re-stage just the fixed
// hunk without the file's other pending edits.
func restageFixedFiles(files []string) {
	var existing []string
	for _, f := range files {
		if _, err := os.Stat(filepath.Join(root, f)); err == nil {
			existing = append(existing, f)
		}
	}
	if len(existing) == 0 {
		return
	}
	c := exec.Command("git", append([]string{"add", "--"}, existing...)...)
	c.Dir = root
	if err := c.Run(); err != nil {
		fmt.Printf("  %s✗%s Re-stage fixed files: %v\n", red, reset, err)
		os.Exit(1)
	}
	fmt.Printf("  %s✓%s Re-staged %d fixed file(s)\n", green, reset, len(existing))
}

func cmdCi() {
	fmt.Printf("\n%s[ci]%s\n\n", blue, reset)
	// Read-only gates run as a parallel batch (captured, printed in submission
	// order, run to completion). Coverage is captured and CRAP is advisory — after.
	gates := []gate{lintGate(nil), auditGate(), complexityGate(), modTidyGate()}
	gates = append(gates, acceptanceGatesOrWarn()...)
	gates = append(gates, archGatesOrWarn()...)
	allOk, _ := runGatesParallel(gates)
	cmdTestCov()       // after the batch
	cmdCrap()          // advisory unless --enforce
	runMutation(false) // advisory unless --enforce; change set from the base ref only
	archConfigOk := checkArchConfigGuard(false, false, false)
	gherkinOk := checkGherkinGuard(false, false, false)
	suppressionsOk := suppressions.CheckBaseline(
		root,
		suppressions.ScanFindings(root),
		true,
		updateBaseline,
		true,
	)
	if !allOk || !archConfigOk || !gherkinOk || !suppressionsOk {
		os.Exit(1)
	}
}

// cmdPrePush is the read-only push gate: the offline checks pre-commit and
// stop-hook do not run. pre-commit covers fix/format/test on staged files;
// stop-hook adds complexity. This fills the gap with the deterministic, offline
// gates none of them run — lint (golangci-lint covers format), acceptance, arch —
// validating the whole pushed tree (after merges/rebases/--no-verify) before it
// leaves the machine. Network (audit) and advisory (coverage/CRAP) gates stay in ci.
func cmdPrePush() {
	fmt.Printf("\n%s[pre-push]%s\n\n", blue, reset)
	archConfigOk := checkArchConfigGuard(false, false, true)
	gherkinOk := checkGherkinGuard(false, false, true)
	gates := []gate{lintGate(nil)}
	gates = append(gates, acceptanceGatesOrWarn()...)
	gates = append(gates, archGatesOrWarn()...)
	ok, _ := runGatesParallel(gates)
	if !ok || !archConfigOk || !gherkinOk {
		os.Exit(1)
	}
}

// cmdComplexity runs the read-only cyclomatic-complexity gate.
// golangci-lint's gocyclo linter already enforces a per-function ceiling
// over src (see .golangci.yaml); this stage runs lizard for parity with the
// bun/python templates (… → complexity → …).
//
// Excludes: `_test.go` (test code has different complexity norms — table-
// driven tests legitimately branch a lot) and `harness.go` (carries
// `//go:build ignore`, not part of any production package). The cmdCrap join
// applies the same exclusions so both gates target the same code set.
func complexityArgv(maxViolations string) []string {
	// lizard's own `-i N` is the count ratchet: it exits 0 while the number of
	// flagged functions stays at or below N, so lizard does the counting.
	return []string{
		"uvx", lizard, "-l", "go", ".",
		"-C", "15", "-a", complexityMaxArgs, "-L", "100", "-i", maxViolations,
		"-x", "*_test.go", "-x", "./harness.go",
	}
}

// complexityGate is the lizard gate at the committed floor, or report-only
// when there is none. With no floor recorded, `-i 0` would demand a legacy
// tree already be perfect — exactly the day-one red that stops the harness
// being adopted. Measure instead.
func complexityGate() gate {
	floor, ok := suppressions.BaselineFloor(root, "complexity.max_violations")
	if !ok {
		return gate{
			description: fmt.Sprintf("Complexity (lizard, report-only: no %s floor)", baselineFile),
			cmd:         complexityArgv(reportOnlyLimit),
			hint:        fmt.Sprintf("run `%s` to record a floor", updateBaseline),
		}
	}
	description := "Complexity (lizard)"
	if floor > 0 {
		description = fmt.Sprintf("Complexity (lizard, baseline %d)", floor)
	}
	return gate{
		description: description,
		cmd:         complexityArgv(strconv.Itoa(floor)),
		hint:        "extract helpers or flatten branches until CCN <= 15; do not raise the threshold",
	}
}

func cmdComplexity() {
	g := complexityGate()
	run(g.description, g.cmd, &runOpts{hint: g.hint})
}

// lizardWarningCount reads the `Warning cnt` column out of lizard's final
// summary row. ok=false when there is no summary row to read.
func lizardWarningCount(stdout string) (int, bool) {
	lines := strings.Split(stdout, "\n")
	for index, line := range lines {
		if !strings.HasPrefix(line, "Total nloc") {
			continue
		}
		for _, row := range lines[index+1:] {
			fields := strings.Fields(row)
			if len(fields) < 6 || strings.Trim(row, "- ") == "" {
				continue
			}
			count, err := strconv.Atoi(fields[5])
			return count, err == nil
		}
	}
	return 0, false
}

// ── Ratcheted baseline ──────────────────────────────────────────────
// Every key `suppressions --update-baseline` measures. Later gates append
// their own {key, measure} entry here; suppressions.WriteBaseline rewrites
// exactly these keys and carries every other key (`coverage.min`,
// `mutation.min`, anything hand-written) through untouched.
var ratcheted = []suppressions.Measurer{
	{Key: "coverage.min", Measure: measuredCoverageMin},
	{Key: "complexity.max_violations", Measure: measuredComplexityViolations},
	{Key: "crap.max_violations", Measure: measuredCrapViolations},
}

// measuredCoverageMin is total coverage floored to an integer — the floor the
// coverage gate then enforces. Measured first so the profile it writes is
// still fresh when the CRAP measurement joins against it. Mirrors python's
// _measured_coverage: refresh only a stale profile, and a tree with no tests
// has no percentage to record.
func measuredCoverageMin() suppressions.Measurement {
	if !hasTestFiles() {
		return suppressions.Unavailable("no *_test.go files")
	}
	if !coverageFresh(filepath.Join(root, "coverage.out")) {
		c := exec.Command("go", "test", "-race", "-count=1", "-coverprofile=coverage.out", "./...")
		c.Dir = root
		if _, err := c.CombinedOutput(); err != nil {
			return suppressions.Failed(fmt.Sprintf("the test run under coverage failed (exit %d)", exitCode(err)))
		}
	}
	pct, ok := coveragePercent()
	if !ok {
		return suppressions.Failed("`go tool cover` produced no total")
	}
	// Truncate, never round up: a floor above the measured number fails the very
	// next run.
	return suppressions.Measured(int(pct))
}

// measuredComplexityViolations counts the functions lizard flags at the
// template's thresholds.
func measuredComplexityViolations() suppressions.Measurement {
	argv := complexityArgv(reportOnlyLimit)
	c := exec.Command(argv[0], argv[1:]...)
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return suppressions.Failed(fmt.Sprintf("lizard failed to run (exit %d)", exitCode(err)))
	}
	count, ok := lizardWarningCount(string(out))
	if !ok {
		return suppressions.Failed("lizard printed no summary row to count warnings from")
	}
	return suppressions.Measured(count)
}

// measuredCrapViolations counts the functions above the default CRAP threshold.
func measuredCrapViolations() suppressions.Measurement {
	if !hasTestFiles() {
		return suppressions.Unavailable("no *_test.go files")
	}
	measurement := crapMeasure(crapMaxDefault)
	if measurement.problem != "" {
		return suppressions.Failed(measurement.problem)
	}
	return suppressions.Measured(len(measurement.offenders))
}

// baselineMeasurers is the set `suppressions --update-baseline` measures.
// `mutation.min` joins it only under `--with-mutation`: a mutation run costs
// minutes, so the automatic pass carries the key through untouched instead of
// making every baseline refresh pay for it.
func baselineMeasurers() []suppressions.Measurer {
	if !hasFlag("with-mutation") {
		return ratcheted
	}
	return append(append([]suppressions.Measurer{}, ratcheted...),
		suppressions.Measurer{Key: "mutation.min", Measure: measuredMutationMin})
}

// measuredMutationMin is the kill rate over the concrete package targets —
// never over a change set. A floor derived from whatever happened to change
// is not comparable to the next run's score, and would flap on every branch.
//
// The consequence, worth knowing: a whole-tree floor compared against a
// scoped run can warn when the changed package is weaker than the module
// average. That is why the gate is advisory unless `--enforce`.
func measuredMutationMin() suppressions.Measurement {
	targets, err := withTestFiles(mutationTargets)
	if err != nil {
		return suppressions.Failed(err.Error())
	}
	if len(targets) == 0 {
		return suppressions.Unavailable("no package with *_test.go to mutate")
	}
	fmt.Printf("  %s→%s measuring mutation.min: gremlins on %s\n", dim, reset, strings.Join(targets, " "))
	warmTestCache(verbose)
	report, _, failed := mutateTargets(targets, verbose)
	if failed > 0 {
		return suppressions.Failed(fmt.Sprintf("gremlins failed on %d target package(s)", failed))
	}
	score, scored := suppressions.MutationScore(report.Killed, report.Lived)
	if !scored {
		return suppressions.Unavailable("gremlins killed no mutants and none survived")
	}
	return suppressions.Measured(score)
}

// hasTestFiles reports whether any *_test.go exists under root (vendor/ and
// hidden directories excluded).
func hasTestFiles() bool {
	found := false
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			name := d.Name()
			if name == "vendor" || (strings.HasPrefix(name, ".") && path != root) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(d.Name(), "_test.go") {
			found = true
			return filepath.SkipAll
		}
		return nil
	})
	return found
}

// ── Hook wiring (installed by `setup-hooks`) ────────────────────────
// Claude reads .claude/settings.json and runs the harness directly; Codex reads
// .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
// the exit code into the block/continue JSON Codex expects).
const (
	claudeSettingsSchema = "https://json.schemastore.org/claude-code-settings.json"
	claudeStopCommand    = "cd $CLAUDE_PROJECT_DIR && go run harness.go stop-hook || exit 2"
	codexStopCommand     = `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh go run harness.go stop-hook`
)

func claudeStopHook() map[string]any {
	return map[string]any{"type": "command", "command": claudeStopCommand}
}

func codexStopHook() map[string]any {
	return map[string]any{
		"type":          "command",
		"command":       codexStopCommand,
		"timeout":       300,
		"statusMessage": "Running stop-hook checks",
	}
}

// gitHookPath resolves a git hook path via `git rev-parse` so worktrees and
// core.hooksPath land in the right place. GIT_* env is stripped so an ambient
// GIT_DIR from a parent process can't redirect us.
func gitHookPath(name string) string {
	var env []string
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "GIT_") {
			env = append(env, kv)
		}
	}
	c := exec.Command("git", "rev-parse", "--git-path", "hooks/"+name)
	c.Dir = root
	c.Env = env
	if out, err := c.Output(); err == nil {
		if p := strings.TrimSpace(string(out)); p != "" {
			if filepath.IsAbs(p) {
				return p
			}
			return filepath.Join(root, p)
		}
	}
	return filepath.Join(root, ".git", "hooks", name)
}

func installGitHook(name string) {
	path := gitHookPath(name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create hooks directory: %v\n", err)
		os.Exit(1)
	}
	content := fmt.Sprintf("#!/bin/sh\ngo run harness.go %s\n", name)
	if err := os.WriteFile(path, []byte(content), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to write hook: %v\n", err)
		os.Exit(1)
	}
}

func isStopHookHandler(handler any) bool {
	m, ok := handler.(map[string]any)
	if !ok || m["type"] != "command" {
		return false
	}
	cmd, ok := m["command"].(string)
	return ok && strings.Contains(cmd, "stop-hook")
}

func jsonObjectChild(data map[string]any, key, label string) map[string]any {
	if data[key] == nil {
		data[key] = map[string]any{}
	}
	child, ok := data[key].(map[string]any)
	if !ok {
		fmt.Fprintf(os.Stderr, "%s:%s must contain a JSON object\n", label, key)
		os.Exit(1)
	}
	return child
}

func cloneMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// installStopHook injects/refreshes the Stop hook in a settings file, preserving
// every other hook. Idempotent: an existing stop-hook handler (current or legacy)
// is replaced in place and duplicates are dropped, so re-running never accumulates
// entries. SetEscapeHTML(false) keeps "&&" in hook commands from being written out
// as "&&", matching the committed template's literal "&&" byte-for-byte.
// Residual: encoding/json still sorts every object's keys alphabetically — that is
// inherent to Marshal/Encoder.Encode, not specific to MarshalIndent, and not fixable
// here without hand-rolling a JSON writer. A committed file whose keys aren't already
// alphabetical (e.g. this repo's "type" before "command") gets reordered on first
// run. That reordering is stable and idempotent — identical on every subsequent
// run — just not byte-identical to the hand-written committed file on first run.
func installStopHook(rel string, hook map[string]any, claudeSettings bool) {
	path := filepath.Join(root, filepath.FromSlash(rel))
	data := map[string]any{}
	if raw, err := os.ReadFile(path); err == nil {
		if trimmed := strings.TrimSpace(string(raw)); trimmed != "" {
			if err := json.Unmarshal([]byte(trimmed), &data); err != nil {
				fmt.Fprintf(os.Stderr, "%s: invalid JSON: %v\n", rel, err)
				os.Exit(1)
			}
		}
	}
	if claudeSettings {
		if _, ok := data["$schema"]; !ok {
			data["$schema"] = claudeSettingsSchema
		}
	}

	hooks := jsonObjectChild(data, "hooks", rel)
	stopGroups, _ := hooks["Stop"].([]any)
	installed := false
	for _, group := range stopGroups {
		groupMap, ok := group.(map[string]any)
		if !ok {
			continue
		}
		groupHooks, ok := groupMap["hooks"].([]any)
		if !ok {
			continue
		}
		next := []any{}
		for _, handler := range groupHooks {
			if isStopHookHandler(handler) {
				if !installed {
					next = append(next, cloneMap(hook))
					installed = true
				}
				continue
			}
			next = append(next, handler)
		}
		groupMap["hooks"] = next
	}
	if !installed {
		stopGroups = append(stopGroups, map[string]any{"hooks": []any{cloneMap(hook)}})
	}
	hooks["Stop"] = stopGroups

	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(data); err != nil {
		fmt.Fprintf(os.Stderr, "%s: marshal failed: %v\n", rel, err)
		os.Exit(1)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		os.Exit(1)
	}
	// Encoder.Encode already appends a trailing newline.
	if err := os.WriteFile(path, buf.Bytes(), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		os.Exit(1)
	}
}

func cmdHooks() {
	installGitHook("pre-commit")
	installGitHook("pre-push")
	installStopHook(".codex/hooks.json", codexStopHook(), false)
	installStopHook(".claude/settings.json", claudeStopHook(), true)
	fmt.Println("Installed pre-commit, pre-push, and Claude/Codex Stop hooks")
}

func cmdClean() {
	fmt.Printf("\n%s[clean]%s\n\n", blue, reset)
	names := []string{"coverage.out"}
	matches, _ := filepath.Glob(filepath.Join(root, mutationReportGlob))
	for _, match := range matches {
		names = append(names, filepath.Base(match))
	}
	for _, name := range names {
		p := filepath.Join(root, name)
		if _, err := os.Stat(p); err == nil {
			os.Remove(p)
			fmt.Printf("  %s✓%s Removed %s\n", green, reset, name)
		}
	}
	// Clear Go test cache
	run("Clear test cache", []string{"go", "clean", "-testcache"}, nil)
}

func cmdSuppressions() {
	findings := suppressions.ScanFindings(root)
	results := suppressions.BucketByKind(findings)
	if hasFlag("update-baseline") {
		measurers := baselineMeasurers()
		baseline, err := suppressions.WriteBaseline(root, results, measurers)
		var unmeasured *suppressions.MeasurementError
		if errors.As(err, &unmeasured) {
			fmt.Printf("  %s✗%s %s not written — could not measure:\n", red, reset, baselineFile)
			fmt.Printf("    %s: %s\n", unmeasured.Key, unmeasured.Reason)
			fmt.Println("  ↳ fix: make the measurement pass, then rerun `suppressions --update-baseline`")
			os.Exit(1)
		}
		if err != nil {
			fmt.Printf("  %s✗%s %s: %v\n", red, reset, baselineFile, err)
			os.Exit(1)
		}
		total := 0
		for _, entries := range results {
			total += len(entries)
		}
		recorded := []string{fmt.Sprintf("suppressions %d", total)}
		for _, m := range measurers {
			if value, ok := baseline[m.Key]; ok {
				recorded = append(recorded, fmt.Sprintf("%s %d", m.Key, value))
			}
		}
		fmt.Printf("  %s✓%s %s: %s\n", green, reset, baselineFile, strings.Join(recorded, ", "))
		return
	}
	suppressions.PrintReport(results)
	if !suppressions.CheckBaseline(
		root,
		findings,
		true,
		updateBaseline,
		false,
	) {
		os.Exit(1)
	}
}

// ── CLI dispatch ────────────────────────────────────────────────────

type task struct {
	name string
	fn   func()
	desc string
}

var tasks = []task{
	{"check", cmdCheck, "Full pre-flight: fix + format + lint + test"},
	{"fix", func() { cmdFix(nil) }, "Fix lint errors + format code"},
	{"lint", func() { cmdLint(nil) }, "Lint + format check (read-only)"},
	{"test", cmdTest, "Run tests"},
	{"test-cov", cmdTestCov, "Run tests with race detector and coverage"},
	{"coverage", cmdTestCov, "Run tests with race detector and coverage"},
	{"audit", cmdAudit, "Audit dependencies for known vulnerabilities"},
	{"complexity", cmdComplexity, "Cyclomatic complexity gate (lizard, CCN 15, args 8)"},
	{"acceptance", cmdAcceptance, "Run acceptance scenarios (godog)"},
	{"arch", cmdArch, "Architecture checks (go-arch-lint)"},
	{"arch-config-guard", cmdArchConfigGuard, "Block unreviewed arch config changes"},
	{"gherkin-guard", cmdGherkinGuard, "Block production source changes with no accompanying .feature scenario"},
	{"mutation", cmdMutation, "Mutation testing on changed packages (gremlins, advisory)"},
	{"crap", cmdCrap, "CRAP complexity x coverage gate (advisory)"},
	{"suppressions", cmdSuppressions, "Show or update suppression baseline"},
	{"pre-commit", cmdPreCommit, "Staged checks + tests"},
	{"pre-push", cmdPrePush, "Read-only push gate: lint, acceptance, arch"},
	{"ci", cmdCi, "Full verification: lint, audit, complexity, acceptance, coverage, crap, arch"},
	{"setup-hooks", cmdHooks, "Install git pre-commit + pre-push hooks and Claude/Codex Stop wiring"},
	{"post-edit", cmdPostEdit, "Format if source files changed"},
	{"stop-hook", cmdStopHook, "Format changed files, then run stop-hook checks"},
	{"agents-md-drift", cmdAgentsMdDrift, "Fail if AGENTS.md differs from CLAUDE.md"},
	{"sync-agents-md", cmdSyncAgentsMd, "Overwrite AGENTS.md from CLAUDE.md"},
	{"clean", cmdClean, "Remove coverage and test cache"},
}

func main() {
	args := filterFlags(os.Args[1:])

	if len(args) == 0 {
		cmdCheck()
		return
	}

	for _, t := range tasks {
		if t.name == args[0] {
			t.fn()
			return
		}
	}
	fmt.Fprintf(os.Stderr, "Unknown command: %s\n", args[0])
	os.Exit(1)
}

func filterFlags(args []string) []string {
	var out []string
	for _, a := range args {
		if !strings.HasPrefix(a, "-") {
			out = append(out, a)
		}
	}
	return out
}
