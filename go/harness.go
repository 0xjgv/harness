//go:build ignore

package main

import (
	"fmt"
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
	noExit  bool
	// stream inherits stdio for long commands (tests, coverage) so their live
	// output shows instead of being captured — captured silence looks like a hang.
	stream bool
}

// gate is a read-only gate's label + command, shared by the standalone cmd* and the batch.
type gate struct {
	description string
	cmd         []string
	extract     func(output string) string
}

type gateResult struct {
	description string
	cmd         []string
	ok          bool
	exitCode    int
	output      string
	detail      string
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
	return gateResult{g.description, g.cmd, ok, code, output, detail}
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
func runGatesParallel(gates []gate) bool {
	if len(gates) == 0 {
		return true
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
	for _, r := range results {
		if !printGateResult(r, true) {
			allOk = false
		}
	}
	return allOk
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

func lintGate(pkgs []string) gate {
	if len(pkgs) == 0 {
		pkgs = []string{"./..."}
	}
	return gate{description: "Lint & format check", cmd: append([]string{"golangci-lint", "run"}, pkgs...)}
}

func cmdLint(pkgs []string) {
	g := lintGate(pkgs)
	run(g.description, g.cmd, nil)
}

func cmdTest() {
	// Stream: `go test ./...` is a long command, so live output beats captured silence.
	run("Tests", []string{"go", "test", "./..."}, &runOpts{stream: true})
}

func cmdTestCov() {
	run("Tests with coverage", []string{
		"go", "test", "-race", "-count=1",
		"-coverprofile=coverage.out", "./...",
	}, &runOpts{stream: true})
}

func auditGate() gate {
	return gate{description: "Dep audit", cmd: []string{"go", "run", "golang.org/x/vuln/cmd/govulncheck@v1.1.4", "./..."}}
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

func cmdStopHook() {
	fmt.Println("\n=== Stop Hook Checks ===\n")
	cmdPostEdit()                                       // mutating — sequential, first
	allOk := runGatesParallel([]gate{complexityGate()}) // read-only batch
	cmdCrap()                                           // streaming advisory — after the batch
	if !allOk {
		os.Exit(1)
	}
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
	return []gate{{description: "Acceptance (godog)", cmd: []string{"go", "test", "./features/..."}}}
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
	}}}
}

func cmdArch() {
	for _, g := range archGatesOrWarn() {
		run(g.description, g.cmd, nil)
	}
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
func cmdCrap() {
	maxCrap, _ := strconv.ParseFloat(flagValue("max", "30"), 64)
	enforce := hasFlag("enforce")

	covPath := filepath.Join(root, "coverage.out")
	if !coverageFresh(covPath) {
		cmdTestCov()
	}
	covText, err := os.ReadFile(covPath)
	if err != nil {
		fmt.Printf("  %s✗%s CRAP: coverage.out not found after test-cov\n", red, reset)
		os.Exit(1)
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
		// pass; surface the failure and degrade to advisory unless --enforce.
		suffix := ""
		if !enforce {
			suffix = " (advisory)"
		}
		fmt.Printf("  %s✗%s CRAP: lizard failed to run%s\n", red, reset, suffix)
		if enforce {
			os.Exit(1)
		}
		return
	}

	type scored struct {
		crap   float64
		cov    float64
		metric funcMetric
	}
	var offenders []scored
	for _, m := range metrics {
		c := functionCoverage(cov[m.file], m.line, m.end)
		score := crap.Score(m.ccn, c)
		if score > maxCrap {
			offenders = append(offenders, scored{score, c, m})
		}
	}

	if len(offenders) == 0 {
		fmt.Printf("  %s✓%s CRAP: all functions below %.0f\n", green, reset, maxCrap)
		return
	}
	sort.Slice(offenders, func(i, j int) bool { return offenders[i].crap > offenders[j].crap })
	suffix := " (advisory)"
	if enforce {
		suffix = ""
	}
	fmt.Printf("  %s✗%s CRAP: %d function(s) exceed %.0f%s\n", red, reset, len(offenders), maxCrap, suffix)
	limit := min(len(offenders), 20)
	for _, o := range offenders[:limit] {
		m := o.metric
		fmt.Printf("    CRAP=%6.1f  CCN=%3d  cov=%5.1f%%  %s@%d %s\n",
			o.crap, m.ccn, o.cov*100, m.name, m.line, m.file)
	}
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

	checkHooksPresent()
	results = append(results, checkAgentsMdDrift(true))
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
	checkAgentsMdDrift(false)

	if hasNonTestFiles(files) {
		cmdTest()
	}
}

func cmdCi() {
	fmt.Printf("\n%s[ci]%s\n\n", blue, reset)
	// Read-only gates run as a parallel batch (captured, printed in submission
	// order, run to completion). Coverage streams and CRAP is advisory — after.
	gates := []gate{lintGate(nil), auditGate(), complexityGate()}
	gates = append(gates, acceptanceGatesOrWarn()...)
	gates = append(gates, archGatesOrWarn()...)
	allOk := runGatesParallel(gates)
	cmdTestCov() // streams; after the batch
	cmdCrap()    // advisory unless --enforce
	if !allOk {
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
func complexityGate() gate {
	return gate{description: "Complexity (lizard)", cmd: []string{
		"uvx", lizard, "-l", "go", ".",
		"-C", "15", "-a", complexityMaxArgs, "-L", "100", "-i", "0",
		"-x", "*_test.go", "-x", "./harness.go",
	}}
}

func cmdComplexity() {
	g := complexityGate()
	run(g.description, g.cmd, nil)
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
	checkStopHookPresent()
}

func checkStopHookPresent() {
	for _, rel := range []string{".claude/settings.json", ".codex/hooks.json"} {
		content, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		contentText := string(content)
		if err != nil || !strings.Contains(contentText, "Stop") || !strings.Contains(contentText, "stop-hook") {
			fmt.Printf("  %s⚠%s Missing Stop hook wiring: %s\n", red, reset, rel)
			continue
		}
		fmt.Printf("  %s✓%s Stop hook wiring (%s)\n", green, reset, rel)
	}
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
	{"complexity", cmdComplexity, "Cyclomatic complexity gate (lizard, CCN 15, args 8)"},
	{"acceptance", cmdAcceptance, "Run acceptance scenarios (godog)"},
	{"arch", cmdArch, "Architecture checks (go-arch-lint)"},
	{"mutation", cmdMutation, "Mutation testing (gremlins, advisory)"},
	{"crap", cmdCrap, "CRAP complexity x coverage gate (advisory)"},
	{"pre-commit", cmdPreCommit, "Staged checks + tests"},
	{"ci", cmdCi, "Full verification: lint, audit, complexity, acceptance, coverage, crap, arch"},
	{"setup-hooks", cmdHooks, "Install git pre-commit hook and verify stop-hook wiring"},
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
