//go:build ignore

package main

import (
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"harness/suppressions"
)

// ── Configuration ───────────────────────────────────────────────────

var root = func() string {
	wd, _ := os.Getwd()
	return wd
}()

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
	noExit  bool
}

func run(description string, cmd []string, opts *runOpts) runResult {
	if verbose {
		fmt.Printf("  %s→ %s%s\n", dim, strings.Join(cmd, " "), reset)
	}

	c := exec.Command(cmd[0], cmd[1:]...)
	c.Dir = root

	if verbose {
		c.Stdout = os.Stdout
		c.Stderr = os.Stderr
		err := c.Run()
		if err != nil {
			fmt.Printf("  %s✗%s %s\n", red, reset, description)
			if opts == nil || !opts.noExit {
				os.Exit(exitCode(err))
			}
			return runResult{ok: false}
		}
		fmt.Printf("  %s✓%s %s\n", green, reset, description)
		return runResult{ok: true}
	}

	out, err := c.CombinedOutput()
	output := string(out)

	if err == nil {
		detail := ""
		if opts != nil && opts.extract != nil {
			detail = opts.extract(output)
		}
		suffix := ""
		if detail != "" {
			suffix = fmt.Sprintf(" %s(%s)%s", dim, detail, reset)
		}
		fmt.Printf("  %s✓%s %s%s\n", green, reset, description, suffix)
		return runResult{ok: true, output: output}
	}

	fmt.Printf("  %s✗%s %s\n", red, reset, description)
	if output != "" {
		fmt.Print(output)
	}
	if opts == nil || !opts.noExit {
		os.Exit(exitCode(err))
	}
	return runResult{ok: false, output: output}
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

var coverageRe = regexp.MustCompile(`coverage:\s+([\d.]+)%`)

func extractCoverageSummary(output string) string {
	matches := coverageRe.FindAllStringSubmatch(output, -1)
	if len(matches) == 0 {
		return ""
	}
	last := matches[len(matches)-1][1]
	return fmt.Sprintf("%s%% coverage", last)
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
			dir = "."
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

func changedGoFiles() []string {
	c := exec.Command("git", "status", "--porcelain")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}

	var files []string
	for line := range strings.SplitSeq(strings.TrimSpace(string(out)), "\n") {
		if len(line) < 4 {
			continue
		}
		f := line[3:]
		if strings.HasSuffix(f, ".go") {
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

func cmdLint(pkgs []string) {
	if len(pkgs) == 0 {
		pkgs = []string{"./..."}
	}
	run("Lint & format check", append([]string{"golangci-lint", "run"}, pkgs...), nil)
}

func cmdTest() {
	run("Tests", []string{"go", "test", "./..."}, &runOpts{extract: extractTestSummary})
}

func cmdTestCov() {
	run("Tests with coverage", []string{
		"go", "test", "-race", "-count=1",
		"-coverprofile=coverage.out", "./...",
	}, &runOpts{extract: extractCoverageSummary})
}

func cmdAudit() {
	run("Dep audit", []string{"go", "run", "golang.org/x/vuln/cmd/govulncheck@v1.1.4", "./..."}, nil)
}

func cmdPostEdit() {
	if len(changedGoFiles()) == 0 {
		return
	}
	run("Fix & format", []string{"golangci-lint", "run", "--fix", "./..."}, &runOpts{noExit: true})
}

// ── Quality gates ───────────────────────────────────────────────────

const archConfig = ".go-arch-lint.yml"

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

// cmdAcceptance runs Gherkin scenarios via godog (as a `go test`).
// An empty features dir warns and exits 0 — mirrors python's cmd_acceptance.
func cmdAcceptance() {
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
		return
	}
	run("Acceptance (godog)", []string{"go", "test", "./features/..."}, nil)
}

// cmdArch runs the import/dependency-boundary linter against .go-arch-lint.yml.
func cmdArch() {
	if _, err := os.Stat(filepath.Join(root, archConfig)); err != nil {
		fmt.Printf("  %s⚠%s Arch: no %s — skipped\n", green, reset, archConfig)
		return
	}
	run("Arch (go-arch-lint)", []string{
		"go", "run", "github.com/fe3dback/go-arch-lint@v1.15.0", "check",
	}, nil)
}

// mutationTarget is the package gremlins mutates. The template ships
// `suppressions` as its sample library package — point this (or pass a path
// argument) at your own source packages as the module grows.
const mutationTarget = "./suppressions"

// cmdMutation runs gremlins mutation testing. Advisory — not wired into ci.
//
// Two hard-won notes baked into this command:
//   - gremlins derives each mutant's test timeout from the baseline test run.
//     A cold build cache makes the first mutant compile blow that budget and
//     every mutant reports TIMED OUT. Warming the cache with `go test` first,
//     plus a generous --timeout-coefficient, makes results meaningful.
//   - gremlins must be pointed at a concrete package. `./...` from this module
//     gathers no coverage because the root file (harness.go) is build-ignored,
//     so gremlins reports "No results". Target source packages explicitly.
//
// Output is printed unconditionally: an advisory report you cannot see is useless.
func cmdMutation() {
	target := mutationTarget
	if args := filterFlags(os.Args[1:]); len(args) > 1 {
		target = args[1]
	}
	run("Warm test cache", []string{"go", "test", "-count=1", "./..."},
		&runOpts{extract: extractTestSummary, noExit: true})

	fmt.Printf("  %s→%s gremlins unleash %s\n", dim, reset, target)
	c := exec.Command("go", "run",
		"github.com/go-gremlins/gremlins/cmd/gremlins@v0.5.0",
		"unleash", "--timeout-coefficient=10", target)
	c.Dir = root
	c.Stdout = os.Stdout
	c.Stderr = os.Stderr
	if err := c.Run(); err != nil {
		fmt.Printf("  %s⚠%s Mutation: gremlins exited non-zero (advisory — not blocking)\n", green, reset)
		return
	}
	fmt.Printf("  %s✓%s Mutation (gremlins)\n", green, reset)
}

// funcMetric pairs a function's complexity with its coverage for CRAP scoring.
type funcMetric struct {
	file string
	line int
	name string
	ccn  int
	cov  float64
}

var (
	gocycloRe = regexp.MustCompile(`^(\d+)\s+\S+\s+(\S+)\s+(\S+):(\d+):\d+`)
	coverRe   = regexp.MustCompile(`^(\S+):(\d+):\s+(\S+)\s+([\d.]+)%`)
)

// cmdCrap computes CRAP = CCN² × (1-cov)³ + CCN per function. Advisory.
//
// Inputs are Go-native: gocyclo gives per-function complexity, and
// `go tool cover -func` (over coverage.out) gives per-function coverage.
// They are joined on (file basename, start line, function name).
func cmdCrap() {
	maxCrap, _ := strconv.ParseFloat(flagValue("max", "30"), 64)
	changedOnly := hasFlag("changed-only")

	covPath := filepath.Join(root, "coverage.out")
	if _, err := os.Stat(covPath); err != nil {
		fmt.Printf("  %s✗%s CRAP: coverage.out not found — run `harness test-cov` (or `harness ci`) first\n", red, reset)
		os.Exit(1)
	}

	covByFunc := coverageByFunc(covPath)
	metrics := complexityMetrics(covByFunc)
	if metrics == nil {
		fmt.Printf("  %s✗%s CRAP: gocyclo produced no output\n", red, reset)
		os.Exit(1)
	}

	var changed map[string]bool
	if changedOnly {
		changed = changedFilesVsMain()
	}

	var offenders []struct {
		crap   float64
		metric funcMetric
	}
	for _, m := range metrics {
		if changed != nil && !changed[m.file] {
			continue
		}
		crap := float64(m.ccn*m.ccn)*math.Pow(1-m.cov, 3) + float64(m.ccn)
		if crap > maxCrap {
			offenders = append(offenders, struct {
				crap   float64
				metric funcMetric
			}{crap, m})
		}
	}

	if len(offenders) == 0 {
		fmt.Printf("  %s✓%s CRAP: all functions below %.0f\n", green, reset, maxCrap)
		return
	}
	sort.Slice(offenders, func(i, j int) bool { return offenders[i].crap > offenders[j].crap })
	fmt.Printf("  %s✗%s CRAP: %d function(s) exceed %.0f\n", red, reset, len(offenders), maxCrap)
	limit := min(len(offenders), 20)
	for _, o := range offenders[:limit] {
		m := o.metric
		fmt.Printf("    CRAP=%6.1f  CCN=%3d  cov=%5.1f%%  %s@%d %s\n",
			o.crap, m.ccn, m.cov*100, m.name, m.line, m.file)
	}
	os.Exit(1)
}

// coverageByFunc parses `go tool cover -func` into a (file:line:name) keyed map.
func coverageByFunc(covPath string) map[string]float64 {
	c := exec.Command("go", "tool", "cover", "-func="+covPath)
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}
	result := map[string]float64{}
	for line := range strings.SplitSeq(string(out), "\n") {
		m := coverRe.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		pct, _ := strconv.ParseFloat(m[4], 64)
		key := filepath.Base(m[1]) + ":" + m[2] + ":" + m[3]
		result[key] = pct / 100
	}
	return result
}

// complexityMetrics runs gocyclo and joins each function with its coverage.
//
// `harness.go` carries `//go:build ignore`: it is not part of any testable
// package, so no coverage data can exist for it. It is skipped here for the
// same reason python's cmd_crap scans only src/ — CRAP needs both inputs.
func complexityMetrics(covByFunc map[string]float64) []funcMetric {
	c := exec.Command("go", "run", "github.com/fzipp/gocyclo/cmd/gocyclo@v0.6.0", ".")
	c.Dir = root
	out, err := c.Output()
	if err != nil && len(out) == 0 {
		return nil
	}
	var metrics []funcMetric
	for line := range strings.SplitSeq(string(out), "\n") {
		m := gocycloRe.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		if m[3] == "harness.go" {
			continue
		}
		ccn, _ := strconv.Atoi(m[1])
		ln, _ := strconv.Atoi(m[4])
		key := filepath.Base(m[3]) + ":" + m[4] + ":" + m[2]
		metrics = append(metrics, funcMetric{
			file: m[3], line: ln, name: m[2], ccn: ccn, cov: covByFunc[key],
		})
	}
	return metrics
}

// changedFilesVsMain returns the set of .go files changed vs origin/main.
func changedFilesVsMain() map[string]bool {
	c := exec.Command("git", "diff", "--name-only", "origin/main...HEAD")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return map[string]bool{}
	}
	changed := map[string]bool{}
	for f := range strings.SplitSeq(strings.TrimSpace(string(out)), "\n") {
		if strings.HasSuffix(f, ".go") {
			changed[f] = true
		}
	}
	return changed
}

// ── Stages ──────────────────────────────────────────────────────────

// checkHooksPresent warns when required hook scripts are missing (drift detection).
func checkHooksPresent() {
	required := []string{
		".claude/scripts/session-start.sh",
		".claude/scripts/ups-classify.sh",
		".claude/scripts/pre-bash-gate.sh",
		".claude/scripts/pre-edit-gate.sh",
	}
	var missing []string
	for _, p := range required {
		if _, err := os.Stat(filepath.Join(root, p)); err != nil {
			missing = append(missing, p)
		}
	}
	if len(missing) > 0 {
		fmt.Printf("  %s⚠%s Missing hook scripts: %s\n", red, reset, strings.Join(missing, ", "))
	}
}

func cmdCheck() {
	start := time.Now()
	fmt.Printf("\n%s[check]%s Running pre-flight checks...\n\n", blue, reset)

	results := []runResult{
		run("Fix & format", []string{"golangci-lint", "run", "--fix", "./..."}, &runOpts{noExit: true}),
		run("Tests", []string{"go", "test", "./..."}, &runOpts{extract: extractTestSummary, noExit: true}),
	}

	checkHooksPresent()
	suppressions.PrintReport(suppressions.Scan(root))

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

func cmdPreCommit() {
	files := stagedGoFiles()
	if len(files) == 0 {
		fmt.Println("No staged Go files — skipping checks")
		return
	}

	fmt.Printf("\n%s[pre-commit]%s\n\n", blue, reset)

	pkgs := stagedPackages(files)
	cmdFix(pkgs)

	if hasNonTestFiles(files) {
		cmdTest()
	}
}

func cmdCi() {
	fmt.Printf("\n%s[ci]%s\n\n", blue, reset)
	cmdLint(nil)
	cmdAudit()
	cmdComplexity()
	cmdAcceptance()
	cmdTestCov()
	cmdArch()
}

// cmdComplexity runs the read-only cyclomatic-complexity gate.
// golangci-lint's gocyclo linter already enforces a per-function ceiling
// (see .golangci.yaml); this stage surfaces it as its own ci step so the
// pipeline position mirrors the python template (… → complexity → …).
func cmdComplexity() {
	run("Complexity (gocyclo, CCN 15)", []string{
		"go", "run", "github.com/fzipp/gocyclo/cmd/gocyclo@v0.6.0",
		"-over", "15", "-ignore", "_test\\.go", ".",
	}, nil)
}

func cmdHooks() {
	hookDir := filepath.Join(root, ".git", "hooks")
	hookPath := filepath.Join(hookDir, "pre-commit")

	if err := os.MkdirAll(hookDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create hooks directory: %v\n", err)
		os.Exit(1)
	}
	if err := os.WriteFile(hookPath, []byte("#!/bin/sh\ngo run harness.go pre-commit\n"), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to write hook: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("Installed pre-commit hook")
}

func cmdClean() {
	fmt.Printf("\n%s[clean]%s\n\n", blue, reset)
	for _, name := range []string{"coverage.out"} {
		p := filepath.Join(root, name)
		if _, err := os.Stat(p); err == nil {
			os.Remove(p)
			fmt.Printf("  %s✓%s Removed %s\n", green, reset, name)
		}
	}
	// Clear Go test cache
	run("Clear test cache", []string{"go", "clean", "-testcache"}, nil)
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
	{"audit", cmdAudit, "Audit dependencies for known vulnerabilities"},
	{"complexity", cmdComplexity, "Cyclomatic complexity gate (gocyclo, CCN 15)"},
	{"acceptance", cmdAcceptance, "Run acceptance scenarios (godog)"},
	{"arch", cmdArch, "Architecture checks (go-arch-lint)"},
	{"mutation", cmdMutation, "Mutation testing (gremlins, advisory)"},
	{"crap", cmdCrap, "CRAP complexity x coverage gate (advisory)"},
	{"pre-commit", cmdPreCommit, "Staged checks + tests"},
	{"ci", cmdCi, "Full verification: lint, audit, complexity, acceptance, coverage, arch"},
	{"setup-hooks", cmdHooks, "Install git pre-commit hook"},
	{"post-edit", cmdPostEdit, "Format if source files changed (Claude Code hook)"},
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
