//go:build ignore

package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
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
	lizard              = "lizard@1.22.2"
	complexityMaxCCN    = 15
	complexityMaxArgs   = 8
	complexityMaxLength = 100
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
	// stream inherits stdio for commands whose live output is part of the contract.
	stream bool
}

// gate is a read-only gate's label + command, shared by the standalone cmd* and the batch.
type gate struct {
	description string
	cmd         []string
	extract     func(output string) string
	hint        string
	env         []string // nil inherits this process's environment
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
	c.Env = g.env
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

// packageDirs names the package directories holding files, for golangci-lint:
// "." for the module root, "./dir" otherwise.
func packageDirs(files []string) []string {
	seen := map[string]bool{}
	var dirs []string
	for _, f := range files {
		dir := filepath.ToSlash(filepath.Dir(f))
		if dir != "." {
			dir = "./" + dir
		}
		if !seen[dir] {
			seen[dir] = true
			dirs = append(dirs, dir)
		}
	}
	sort.Strings(dirs)
	return dirs
}

func filterFiles(files []string, keep func(string) bool) []string {
	var kept []string
	for _, f := range files {
		if keep(f) {
			kept = append(kept, f)
		}
	}
	return kept
}

// isGoSource reports whether path is a .go file that `./...` reaches: no path
// element is hidden, underscored, testdata, or vendor.
func isGoSource(path string) bool {
	if !strings.HasSuffix(path, ".go") {
		return false
	}
	for part := range strings.SplitSeq(path, "/") {
		if part == "testdata" || part == "vendor" || strings.HasPrefix(part, ".") || strings.HasPrefix(part, "_") {
			return false
		}
	}
	return true
}

// isLintTarget is a Go source golangci-lint loads. harness.go is `//go:build
// ignore`, so no package includes it.
func isLintTarget(path string) bool {
	return isGoSource(path) && path != "harness.go"
}

// isComplexityTarget matches the complexity gate: no tests, no runner.
func isComplexityTarget(path string) bool {
	return isLintTarget(path) && !strings.HasSuffix(path, "_test.go")
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// porcelainPath extracts the current path from a `git status --porcelain` line.
func porcelainPath(line string) string {
	path := line[3:]
	if i := strings.LastIndex(path, " -> "); i >= 0 {
		return path[i+len(" -> "):]
	}
	return path
}

// changedGoFiles lists Go files with uncommitted changes, relative to this
// project. Porcelain paths are repository-relative, so a project in a
// subdirectory strips its prefix; `--untracked-files=all` lists new files
// inside new directories.
func changedGoFiles() []string {
	c := exec.Command("git", "-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=all", "--", ".")
	c.Dir = root
	out, err := c.Output()
	if err != nil {
		return nil
	}

	prefix := gitPrefix()
	var files []string
	for line := range strings.SplitSeq(string(out), "\n") {
		if len(line) <= 3 || strings.Contains(line[:2], "D") {
			continue
		}
		if f := normalizeChangedPath(porcelainPath(line), prefix); isGoSource(f) {
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

func cmdAudit() {
	g := auditGate()
	run(g.description, g.cmd, nil)
}

// ── Agent hooks ─────────────────────────────────────────────────────
// The stop hook runs after every agent turn and judges the change, not the
// tree: lint left on changed lines, and functions pushed over (or further over)
// a complexity limit. golangci-lint's `unused` linter reports dead code through
// the lint residue. Pre-existing debt never blocks a stop; the whole-tree gates
// stay in check / ci / pre-push. Exit contract: silent 0 when clean, 2 with a
// stderr payload the agent reads, 1 when a tool could not run.

const (
	hookStdinTimeout = time.Second
	// hookFindingLimit is how many finding lines a stop-hook payload carries;
	// the rest are one command away.
	hookFindingLimit = 20
	stopHookRerun    = "go run harness.go stop-hook --verbose"
	loopGuardNotice  = "harness: same findings as the previous stop; not blocking again"
	postEditNotice   = "harness: reformatted %s; re-read it before editing it again"
)

// deltaBaseCandidates is where the stop hook's delta starts, after the
// HARNESS_ARCH_BASE and GITHUB_BASE_REF overrides. Never fetched: a hook must
// not touch the network.
var deltaBaseCandidates = []string{"origin/HEAD", "origin/main", "origin/master", "main", "master"}

// lineRange is an inclusive span of new-side line numbers.
type lineRange struct{ start, end int }

// changedLines maps a project-relative path to its changed spans.
type changedLines map[string][]lineRange

var wholeFile = []lineRange{{1, math.MaxInt}}

// complexityLinters judge what the complexity delta already judges, but only
// by whether the declaration line changed: a moved or re-signed legacy function
// would block. The stop hook leaves that dimension to the complexity delta.
var complexityLinters = map[string]bool{"gocyclo": true}

var (
	hunkRe         = regexp.MustCompile(`^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@`)
	compileErrorRe = regexp.MustCompile(`^(.+?\.go):(\d+)(?::\d+)?: (.+)$`)
	unsafeKeyRe    = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
)

type complexityLimit struct {
	label string
	value func(funcMetric) int
	limit int
}

var complexityLimits = []complexityLimit{
	{"CCN", func(m funcMetric) int { return m.ccn }, complexityMaxCCN},
	{"args", func(m funcMetric) int { return m.args }, complexityMaxArgs},
	{"length", func(m funcMetric) int { return m.length }, complexityMaxLength},
}

// deltaResult is one delta gate: findings block the stop; a problem means its
// tool failed.
type deltaResult struct {
	gate     string
	findings []string
	problem  string
}

// runTool returns the command's stdout, or an error when the command cannot
// start or exits with a code outside ok. dir "" is the working directory.
func runTool(tool string, cmd []string, ok []int, dir string) (string, error) {
	c := exec.Command(cmd[0], cmd[1:]...)
	c.Dir = dir
	var stdout, stderr bytes.Buffer
	c.Stdout, c.Stderr = &stdout, &stderr
	err := c.Run()
	var exitErr *exec.ExitError
	if err != nil && !errors.As(err, &exitErr) {
		return "", fmt.Errorf("%s not runnable: %w", tool, err)
	}
	code := c.ProcessState.ExitCode()
	if slices.Contains(ok, code) {
		return stdout.String(), nil
	}
	reason := fmt.Sprintf("%s exited %d", tool, code)
	detail := strings.TrimSpace(stderr.String())
	if detail == "" {
		detail = strings.TrimSpace(stdout.String())
	}
	if detail == "" {
		return "", errors.New(reason)
	}
	lines := strings.Split(detail, "\n")
	return "", fmt.Errorf("%s: %s", reason, strings.TrimSpace(lines[len(lines)-1]))
}

func gitOutput(args ...string) (string, error) {
	return runTool("git", append([]string{"git", "-c", "core.quotePath=false"}, args...), []int{0}, "")
}

func hasCommits() bool {
	return len(gitLines("rev-parse", "--verify", "--quiet", "HEAD")) > 0
}

// relativePath is path relative to base, with symlinks resolved on both sides
// when both exist (macOS /tmp is /private/tmp). A relative path is taken as
// already relative.
func relativePath(base, path string) string {
	if !filepath.IsAbs(path) {
		return filepath.ToSlash(filepath.Clean(path))
	}
	realPath, pathErr := filepath.EvalSymlinks(path)
	realBase, baseErr := filepath.EvalSymlinks(base)
	if pathErr == nil && baseErr == nil {
		path, base = realPath, realBase
	}
	rel, err := filepath.Rel(base, path)
	if err != nil {
		return filepath.ToSlash(path)
	}
	return filepath.ToSlash(rel)
}

// ── Changed lines ──

// baseRef is the first base ref that resolves: env overrides, then
// deltaBaseCandidates; "" when none does.
func baseRef() string {
	candidates := []string{os.Getenv("HARNESS_ARCH_BASE")}
	if githubBase := os.Getenv("GITHUB_BASE_REF"); githubBase != "" {
		candidates = append(candidates, "origin/"+githubBase)
	}
	for _, ref := range append(candidates, deltaBaseCandidates...) {
		if ref != "" && len(gitLines("rev-parse", "--verify", "--quiet", ref+"^{commit}")) > 0 {
			return ref
		}
	}
	return ""
}

// deltaBase is merge-base(base ref, HEAD); HEAD without a base ref; "" before
// the first commit.
func deltaBase() string {
	if !hasCommits() {
		return ""
	}
	if ref := baseRef(); ref != "" {
		if mergeBase := gitLines("merge-base", ref, "HEAD"); len(mergeBase) > 0 {
			return mergeBase[0]
		}
	}
	return "HEAD"
}

// diffPath is the new-side path of a `+++ b/<path>` header; false for a
// deleted file.
func diffPath(header string) (string, bool) {
	name := strings.TrimRight(strings.TrimPrefix(header, "+++ "), "\t")
	if name == "/dev/null" {
		return "", false
	}
	if len(name) > 1 && strings.HasPrefix(name, `"`) && strings.HasSuffix(name, `"`) {
		name = name[1 : len(name)-1]
	}
	return strings.TrimPrefix(name, "b/"), true
}

func addHunk(ranges changedLines, path, header string) {
	m := hunkRe.FindStringSubmatch(header)
	if m == nil || path == "" {
		return
	}
	count := 1
	if m[2] != "" {
		count, _ = strconv.Atoi(m[2])
	}
	if count == 0 {
		return
	}
	start, _ := strconv.Atoi(m[1])
	ranges[path] = append(ranges[path], lineRange{start, start + count - 1})
}

// parseDiffRanges reads the new-side line spans of a `git diff -U0` (a/ b/
// prefixes). File headers are read only between `diff --git` and the first
// hunk, so an added line whose text starts with `++ ` is never taken for one.
// A pure deletion (`+N,0`) adds no span, but its file is still listed.
func parseDiffRanges(diff string) changedLines {
	ranges := changedLines{}
	path, inHeader := "", false
	for line := range strings.SplitSeq(diff, "\n") {
		switch {
		case strings.HasPrefix(line, "diff --git "):
			path, inHeader = "", true
		case inHeader && strings.HasPrefix(line, "+++ "):
			name, ok := diffPath(line)
			path = name
			if ok {
				ranges[name] = []lineRange{}
			}
		case strings.HasPrefix(line, "@@"):
			inHeader = false
			addHunk(ranges, path, line)
		}
	}
	return ranges
}

// changedScope is the changed lines per path relative to this project: `git
// diff <base>` plus untracked files. It covers work committed on the branch and
// uncommitted work alike. Untracked files, and every file before the first
// commit, are in scope whole. Renames count as new files.
func changedScope(base string) (changedLines, error) {
	scope := changedLines{}
	listing := []string{"ls-files", "--others", "--exclude-standard", "--", "."}
	if base == "" {
		listing = slices.Insert(listing, 1, "--cached")
	} else {
		diff, err := gitOutput("diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", "--relative",
			"--src-prefix=a/", "--dst-prefix=b/", base, "--", ".")
		if err != nil {
			return nil, err
		}
		scope = parseDiffRanges(diff)
	}
	listed, err := gitOutput(listing...)
	if err != nil {
		return nil, err
	}
	for path := range strings.SplitSeq(listed, "\n") {
		if path != "" {
			scope[path] = slices.Clone(wholeFile)
		}
	}
	return scope, nil
}

func inRanges(line int, ranges []lineRange) bool {
	for _, r := range ranges {
		if r.start <= line && line <= r.end {
			return true
		}
	}
	return false
}

// scopedFiles lists the changed paths keep accepts that still exist.
func scopedFiles(scope changedLines, keep func(string) bool) []string {
	var files []string
	for path := range scope {
		if keep(path) && isFile(path) {
			files = append(files, path)
		}
	}
	sort.Strings(files)
	return files
}

// ── Lint residue ──

// golangciIssue is the part of a golangci-lint JSON issue the stop hook reads.
type golangciIssue struct {
	FromLinter string
	Text       string
	Pos        struct {
		Filename string
		Line     int
	}
}

type locatedMessage struct {
	path    string
	line    int
	message string
}

// issueLocations places an issue. A typecheck issue can carry the compiler's
// whole report in its text, one `path:line:col: message` per line; each line
// becomes its own location.
func issueLocations(issue golangciIssue, base string) []locatedMessage {
	var located []locatedMessage
	if issue.FromLinter == "typecheck" {
		for line := range strings.SplitSeq(issue.Text, "\n") {
			if m := compileErrorRe.FindStringSubmatch(line); m != nil {
				n, _ := strconv.Atoi(m[2])
				located = append(located, locatedMessage{relativePath(base, m[1]), n, m[3]})
			}
		}
	}
	if len(located) == 0 {
		message := strings.ReplaceAll(strings.TrimSpace(issue.Text), "\n", " ")
		located = append(located, locatedMessage{relativePath(base, issue.Pos.Filename), issue.Pos.Line, message})
	}
	return located
}

// golangciFindings turns a golangci-lint JSON report into `path:line: linter:
// message` lines on changed lines, minus complexityLinters. Compile errors
// (typecheck) are kept wherever they sit: they stop every other linter in
// their package.
func golangciFindings(data []byte, scope changedLines, base string) ([]string, error) {
	var report struct{ Issues []golangciIssue }
	if err := json.Unmarshal(data, &report); err != nil {
		return nil, fmt.Errorf("unreadable golangci-lint output: %w", err)
	}
	var findings []string
	for _, issue := range report.Issues {
		if issue.Pos.Filename == "" || issue.FromLinter == "" {
			return nil, errors.New("unreadable golangci-lint output: issue without a linter or file")
		}
		if complexityLinters[issue.FromLinter] {
			continue
		}
		for _, at := range issueLocations(issue, base) {
			if issue.FromLinter == "typecheck" || inRanges(at.line, scope[at.path]) {
				findings = append(findings, fmt.Sprintf("%s:%d: %s: %s", at.path, at.line, issue.FromLinter, at.message))
			}
		}
	}
	return findings, nil
}

// lintResidue is the lint the fix pass could not fix, on changed lines of the
// packages that hold changed files. golangci-lint filters to the delta itself
// (`--new-from-rev`); the scope filter keeps the same line set every gate uses.
func lintResidue(scope changedLines, base string) ([]string, error) {
	files := scopedFiles(scope, isLintTarget)
	if len(files) == 0 {
		return nil, nil
	}
	report, err := os.CreateTemp("", "harness-lint-*.json")
	if err != nil {
		return nil, err
	}
	_ = report.Close()
	defer os.Remove(report.Name())
	cmd := []string{
		"golangci-lint", "run", "--allow-serial-runners", "--show-stats=false", "--path-mode=abs",
		"--output.json.path=" + report.Name(), "--max-issues-per-linter=0", "--max-same-issues=0",
	}
	if base != "" {
		cmd = append(cmd, "--new-from-rev="+base)
	}
	if _, err := runTool("golangci-lint", append(cmd, packageDirs(files)...), []int{0, 1}, ""); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(report.Name())
	if err != nil {
		return nil, err
	}
	return golangciFindings(data, scope, root)
}

// ── Complexity delta ──

// lizardRow reads one `lizard --csv` row. Columns: nloc, ccn, tokens, params,
// length, location, file, name, long_name, start, end.
func lizardRow(record []string) (funcMetric, bool) {
	if len(record) < 11 {
		return funcMetric{}, false
	}
	var n [5]int
	for i, col := range []int{1, 3, 4, 9, 10} {
		value, err := strconv.Atoi(record[col])
		if err != nil {
			return funcMetric{}, false
		}
		n[i] = value
	}
	return funcMetric{
		file: record[6], name: record[7], key: record[8],
		ccn: n[0], args: n[1], length: n[2], line: n[3], end: n[4],
	}, true
}

// lizardRows reads every function row of `lizard --csv` output, in order.
// Go signatures contain commas, so the columns need a real CSV reader.
func lizardRows(text string) []funcMetric {
	reader := csv.NewReader(strings.NewReader(text))
	reader.FieldsPerRecord = -1
	reader.LazyQuotes = true
	var rows []funcMetric
	for {
		record, err := reader.Read()
		var parseErr *csv.ParseError
		if errors.As(err, &parseErr) {
			continue
		}
		if err != nil {
			return rows
		}
		if m, ok := lizardRow(record); ok {
			rows = append(rows, m)
		}
	}
}

// parseLizardCSV groups lizard rows by file, keyed by long_name (the
// signature): a signature survives a function moving within its file; a start
// line does not. A repeated signature is keyed `#2`, `#3` in file order.
func parseLizardCSV(text string) map[string][]funcMetric {
	functions := map[string][]funcMetric{}
	taken := map[[2]string]bool{}
	for _, m := range lizardRows(text) {
		longName := m.key
		for n := 2; taken[[2]string{m.file, m.key}]; n++ {
			m.key = fmt.Sprintf("%s#%d", longName, n)
		}
		taken[[2]string{m.file, m.key}] = true
		functions[m.file] = append(functions[m.file], m)
	}
	return functions
}

// baseTwin finds the base version of a current function: same signature, else
// the same name when that name is unique on both sides. The name fallback keeps
// a signature-only edit on a legacy function from reading as a new function.
func baseTwin(now funcMetric, current, base []funcMetric) (funcMetric, bool) {
	var twins []funcMetric
	for _, fn := range base {
		if fn.key == now.key {
			return fn, true
		}
		if fn.name == now.name {
			twins = append(twins, fn)
		}
	}
	sameName := 0
	for _, fn := range current {
		if fn.name == now.name {
			sameName++
		}
	}
	if sameName == 1 && len(twins) == 1 {
		return twins[0], true
	}
	return funcMetric{}, false
}

// functionRegressions is one line per limit now exceeds where the base version
// is absent or measured lower.
func functionRegressions(path string, now, was funcMetric, existed bool) []string {
	name := now.name
	if name == "" {
		name = "(anonymous)"
	}
	var lines []string
	for _, l := range complexityLimits {
		value := l.value(now)
		if value <= l.limit || (existed && value <= l.value(was)) {
			continue
		}
		shown := "new"
		if existed {
			shown = strconv.Itoa(l.value(was))
		}
		lines = append(lines, fmt.Sprintf("%s:%d: %s %s %s→%d (limit %d)", path, now.line, name, l.label, shown, value, l.limit))
	}
	return lines
}

// complexityDelta lists functions over a limit now that are new, or worse than
// their base version.
func complexityDelta(current, base map[string][]funcMetric) []string {
	var findings []string
	for _, path := range slices.Sorted(maps.Keys(current)) {
		for _, now := range current[path] {
			was, existed := baseTwin(now, current[path], base[path])
			findings = append(findings, functionRegressions(path, now, was, existed)...)
		}
	}
	return findings
}

func lizardFunctions(files []string, dir string) (map[string][]funcMetric, error) {
	// lizard with no file arguments walks the working directory; never let it.
	if len(files) == 0 {
		return map[string][]funcMetric{}, nil
	}
	out, err := runTool("lizard", append([]string{"uvx", lizard, "--csv"}, files...), []int{0}, dir)
	if err != nil {
		return nil, err
	}
	return parseLizardCSV(out), nil
}

// writeBaseSources writes each file's base version under dir and returns the
// files that existed at base.
func writeBaseSources(files []string, base, dir string) ([]string, error) {
	var written []string
	for _, path := range files {
		shown, err := exec.Command("git", "show", base+":./"+path).Output()
		if err != nil {
			continue // absent at base: every function in it is new
		}
		target := filepath.Join(dir, filepath.FromSlash(path))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return nil, err
		}
		if err := os.WriteFile(target, shown, 0o644); err != nil {
			return nil, err
		}
		written = append(written, path)
	}
	return written, nil
}

// complexityRegressions is the complexity this change introduced or worsened,
// over the complexity gate's targets.
func complexityRegressions(scope changedLines, base string) ([]string, error) {
	files := scopedFiles(scope, isComplexityTarget)
	current, err := lizardFunctions(files, "")
	if err != nil || len(current) == 0 {
		return nil, err
	}
	tmp, err := os.MkdirTemp("", "harness-base-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tmp)
	var written []string
	if base != "" {
		if written, err = writeBaseSources(files, base, tmp); err != nil {
			return nil, err
		}
	}
	before, err := lizardFunctions(written, tmp)
	if err != nil {
		return nil, err
	}
	return complexityDelta(current, before), nil
}

// ── Stop-hook verdict ──

type deltaGate struct {
	name    string
	measure func() ([]string, error)
}

// runDeltaGates runs lint residue and complexity delta, read-only, in parallel.
func runDeltaGates(scope changedLines, base string) []deltaResult {
	gates := []deltaGate{
		{"Lint", func() ([]string, error) { return lintResidue(scope, base) }},
		{"Complexity", func() ([]string, error) { return complexityRegressions(scope, base) }},
	}
	results := make([]deltaResult, len(gates))
	var wg sync.WaitGroup
	for i, g := range gates {
		wg.Add(1)
		go func() {
			defer wg.Done()
			findings, err := g.measure()
			results[i] = deltaResult{gate: g.name, findings: findings}
			if err != nil {
				results[i] = deltaResult{gate: g.name, problem: err.Error()}
			}
		}()
	}
	wg.Wait()
	return results
}

// capFindings keeps at most hookFindingLimit findings, then one line counting
// the rest. `--verbose` lifts the cap, which is what that line says to run.
func capFindings(findings []string) []string {
	if verbose || len(findings) <= hookFindingLimit {
		return findings
	}
	rest := len(findings) - hookFindingLimit
	return append(slices.Clone(findings[:hookFindingLimit]), fmt.Sprintf("… +%d more — run `%s`", rest, stopHookRerun))
}

// stopHookPayload is the stderr block an agent reads: the failed gates, then
// their findings; "" when clean.
func stopHookPayload(results []deltaResult) string {
	var gates, findings []string
	for _, r := range results {
		if len(r.findings) > 0 {
			gates = append(gates, r.gate)
			findings = append(findings, r.findings...)
		}
	}
	if len(gates) == 0 {
		return ""
	}
	header := "stop-hook failed: " + strings.Join(gates, ", ")
	return strings.Join(append([]string{header}, capFindings(findings)...), "\n")
}

func payloadDigest(payload string) string {
	sum := sha256.Sum256([]byte(payload))
	return hex.EncodeToString(sum[:])
}

// stopHookExit is 2 to block on findings, 1 for a tool failure or a repeated
// block, 0 when clean. A repeat is the stored digest of the same payload while
// the agent is already continuing because of a stop hook (`stop_hook_active`):
// blocking again would loop. A payload that changed blocks again.
func stopHookExit(payload string, failedTools int, event map[string]any, stored string) int {
	if payload != "" {
		if active, _ := event["stop_hook_active"].(bool); active && stored == payloadDigest(payload) {
			return 1
		}
		return 2
	}
	if failedTools > 0 {
		return 1
	}
	return 0
}

// loopGuardKey is a file-name-safe key for this project within its repository.
func loopGuardKey(prefix string) string {
	if key := strings.Trim(unsafeKeyRe.ReplaceAllString(prefix, "-"), "-"); key != "" {
		return key
	}
	return "root"
}

func loopGuardPath() string {
	lines := gitLines("rev-parse", "--git-path", "harness")
	if len(lines) == 0 {
		return ""
	}
	dir, err := filepath.Abs(lines[0])
	if err != nil {
		return ""
	}
	return filepath.Join(dir, "stop-hook-"+loopGuardKey(gitPrefix()))
}

func readDigest(path string) string {
	if path == "" {
		return ""
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// updateLoopGuard remembers a block's digest and forgets it once a stop is clean.
func updateLoopGuard(path string, code int, payload string) {
	if path == "" {
		return
	}
	switch code {
	case 2:
		_ = os.MkdirAll(filepath.Dir(path), 0o755)
		_ = os.WriteFile(path, []byte(payloadDigest(payload)), 0o644)
	case 0:
		_ = os.Remove(path)
	}
}

// reportStopHook prints the verdict to stderr (nothing when clean) and returns
// the exit code. The loop-guard digest covers only the payload.
func reportStopHook(results []deltaResult, event map[string]any) int {
	payload := stopHookPayload(results)
	var problems []string
	for _, r := range results {
		if r.problem != "" {
			problems = append(problems, fmt.Sprintf("stop-hook: %s could not run: %s", r.gate, r.problem))
		}
	}
	guard := loopGuardPath()
	code := stopHookExit(payload, len(problems), event, readDigest(guard))
	for _, line := range problems {
		fmt.Fprintln(os.Stderr, line)
	}
	if payload != "" {
		fmt.Fprintln(os.Stderr, payload)
	}
	if payload != "" && code == 1 {
		fmt.Fprintln(os.Stderr, loopGuardNotice)
	}
	updateLoopGuard(guard, code, payload)
	return code
}

// parseHookEvent reads the agent's hook JSON; {} for empty or invalid input.
func parseHookEvent(text string) map[string]any {
	var event map[string]any
	if err := json.Unmarshal([]byte(text), &event); err != nil || event == nil {
		return map[string]any{}
	}
	return event
}

// hookEvent reads the hook JSON from stdin under hookStdinTimeout; a terminal
// is never read.
func hookEvent() map[string]any {
	info, err := os.Stdin.Stat()
	if err != nil || info.Mode()&os.ModeCharDevice != 0 {
		return map[string]any{}
	}
	text, _ := readWithDeadline(os.Stdin, hookStdinTimeout)
	return parseHookEvent(text)
}

// postEditCommands formats the given files, then applies lint fixes in the
// packages that hold them, never `./...`. `--new-from-rev=HEAD` keeps the fixes
// on uncommitted code, so an untouched file in the same package stays as is.
func postEditCommands(files []string) [][]string {
	if len(files) == 0 {
		return nil
	}
	cmds := [][]string{append([]string{"golangci-lint", "fmt"}, files...)}
	pkgs := packageDirs(filterFiles(files, isLintTarget))
	if len(pkgs) == 0 {
		return cmds
	}
	fix := []string{"golangci-lint", "run", "--fix", "--allow-serial-runners"}
	if hasCommits() {
		fix = append(fix, "--new-from-rev=HEAD")
	}
	return append(cmds, append(fix, pkgs...))
}

// fixAndFormat runs postEditCommands silently; what is left surfaces as lint
// residue.
func fixAndFormat(files []string) {
	for _, cmd := range postEditCommands(files) {
		c := exec.Command(cmd[0], cmd[1:]...)
		c.Dir = root
		_ = c.Run()
	}
}

func agentsMdStale() bool {
	claude, err := os.ReadFile(filepath.Join(root, "CLAUDE.md"))
	if err != nil {
		return false
	}
	agents, err := os.ReadFile(filepath.Join(root, "AGENTS.md"))
	return err != nil || !bytes.Equal(agents, claude)
}

func mirrorClaudeMd() error {
	data, err := os.ReadFile(filepath.Join(root, "CLAUDE.md"))
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(root, "AGENTS.md"), data, 0o644)
}

// syncAgentsMdAfterEdit carries an uncommitted CLAUDE.md edit into AGENTS.md,
// silently. CLAUDE.md is canonical; an edit to AGENTS.md alone is left for
// pre-commit to report.
func syncAgentsMdAfterEdit() {
	if len(gitLines("status", "--porcelain", "--", "CLAUDE.md")) > 0 && agentsMdStale() {
		_ = mirrorClaudeMd()
	}
}

// cmdStopHook runs post-edit, then changed-lines lint and complexity delta.
// Silent on success. Findings exit 2 with a capped stderr payload; a tool that
// could not run exits 1; the same findings on a stop the agent is already
// continuing from exit 1 (loop guard). `check` and `ci` keep the whole-tree
// gates. The hook wiring runs a built binary: `go run` turns every non-zero
// exit into 1.
func cmdStopHook() {
	event := hookEvent() // stdin belongs to the hook event; read it before anything else
	fixAndFormat(changedGoFiles())
	syncAgentsMdAfterEdit()
	base := deltaBase()
	scope, err := changedScope(base)
	if err != nil {
		fmt.Fprintf(os.Stderr, "stop-hook: changed lines could not run: %v\n", err)
		os.Exit(1)
	}
	code := reportStopHook(runDeltaGates(scope, base), event)
	if verbose && code == 0 {
		shown := base
		if shown == "" {
			shown = "no commits"
		}
		fmt.Printf("stop-hook: clean (%d changed path(s) vs %s)\n", len(scope), shown)
	}
	if code != 0 {
		os.Exit(code)
	}
}

// hookTarget is the project Go file a PostToolUse event names, relative to
// base; "" for anything else.
func hookTarget(event map[string]any, base string) string {
	input, _ := event["tool_input"].(map[string]any)
	filePath, _ := input["file_path"].(string)
	if filePath == "" {
		return ""
	}
	if !filepath.IsAbs(filePath) {
		filePath = filepath.Join(base, filePath)
	}
	rel := relativePath(base, filePath)
	if rel == ".." || strings.HasPrefix(rel, "../") || filepath.IsAbs(rel) {
		return "" // outside this project: another harness owns it
	}
	if !isGoSource(rel) || !isFile(filepath.Join(base, rel)) {
		return ""
	}
	return rel
}

func printHookContext(context string) {
	var out struct {
		HookSpecificOutput struct {
			HookEventName     string `json:"hookEventName"`
			AdditionalContext string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	out.HookSpecificOutput.HookEventName = "PostToolUse"
	out.HookSpecificOutput.AdditionalContext = context
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(out)
}

// postEditHook fixes and formats the one file a PostToolUse event names. It
// never blocks, and prints one additionalContext line when the file changed so
// the agent re-reads it before its next edit.
func postEditHook() {
	target := hookTarget(hookEvent(), root)
	if target == "" {
		return
	}
	before, err := os.ReadFile(target)
	if err != nil {
		return
	}
	fixAndFormat([]string{target})
	after, err := os.ReadFile(target)
	if err != nil || bytes.Equal(before, after) {
		return
	}
	printHookContext(fmt.Sprintf(postEditNotice, target))
}

// cmdPostEdit formats and fixes files with uncommitted changes; `--hook`: the
// file a PostToolUse event names.
func cmdPostEdit() {
	if hasFlag("hook") {
		postEditHook()
		return
	}
	labels := []string{"Format changed files", "Fix changed packages"}
	for i, cmd := range postEditCommands(changedGoFiles()) {
		run(labels[i], cmd, &runOpts{noExit: true})
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
		value, _ := strconv.Atoi(raw)
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
		"go", "run", "github.com/fe3dback/go-arch-lint@v1.19.0", "check",
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

func normalizeChangedPath(path, prefix string) string {
	normalized := strings.TrimPrefix(filepath.ToSlash(strings.TrimSpace(path)), "./")
	if prefix != "" && strings.HasPrefix(normalized, prefix+"/") {
		return strings.TrimPrefix(normalized, prefix+"/")
	}
	return normalized
}

func changedPathsFromBase() []string {
	var bases []string
	if base := os.Getenv("HARNESS_ARCH_BASE"); base != "" {
		bases = append(bases, base)
	}
	if githubBase := os.Getenv("GITHUB_BASE_REF"); githubBase != "" {
		bases = append(bases, "origin/"+githubBase)
	}
	var paths []string
	for _, base := range bases {
		if len(gitLines("rev-parse", "--verify", base)) == 0 {
			continue
		}
		paths = append(paths, gitLines("diff", "--name-only", base+"...HEAD", "--", ".")...)
	}
	return paths
}

// prePushRefsEnv carries the refs a dispatcher already consumed from the hook,
// so a child harness sees the real push destinations instead of exhausted stdin.
const prePushRefsEnv = "HARNESS_PRE_PUSH_REFS"

// prePushRefs is the resolved push destination list. `incomplete` means data
// arrived but the sender never closed the pipe: acting on half a push's refs is
// worse than refusing, so both guards fail on it.
type prePushRefs struct {
	lines      []string
	incomplete bool
}

var (
	prePushRefsOnce   sync.Once
	prePushRefsResult prePushRefs
)

func refLinesOf(text string) []string {
	var lines []string
	for line := range strings.SplitSeq(text, "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

// readPrePushRefs resolves the pre-push refs once per process, shared by the
// branch guard and the arch-config guard: HARNESS_PRE_PUSH_REFS wins, then a
// non-tty stdin read, and a tty means a manual run with no refs.
func readPrePushRefs() prePushRefs {
	prePushRefsOnce.Do(func() {
		if text := os.Getenv(prePushRefsEnv); strings.TrimSpace(text) != "" {
			prePushRefsResult = prePushRefs{lines: refLinesOf(text)}
			return
		}
		info, err := os.Stdin.Stat()
		if err != nil || info.Mode()&os.ModeCharDevice != 0 {
			return
		}
		prePushRefsResult = readRefsWithDeadline(os.Stdin, time.Second)
	})
	return prePushRefsResult
}

// readRefsWithDeadline reads the hook's ref lines under limit. A deadline with
// nothing received is an idle pipe (an agent tool, not a hook) — no refs; a
// deadline with bytes received is incomplete input.
func readRefsWithDeadline(r io.Reader, limit time.Duration) prePushRefs {
	text, complete := readWithDeadline(r, limit)
	if !complete {
		return prePushRefs{incomplete: text != ""}
	}
	return prePushRefs{lines: refLinesOf(text)}
}

// readWithDeadline reads r to EOF, bounding the whole read (not just the first
// byte) by limit, so an idle pipe cannot hang the caller. It returns what
// arrived and whether EOF did.
func readWithDeadline(r io.Reader, limit time.Duration) (string, bool) {
	var (
		mu   sync.Mutex
		buf  strings.Builder
		done = make(chan struct{})
	)
	go func() {
		defer close(done)
		chunk := make([]byte, 4096)
		for {
			n, err := r.Read(chunk)
			if n > 0 {
				mu.Lock()
				buf.Write(chunk[:n])
				mu.Unlock()
			}
			if err != nil {
				return
			}
		}
	}()
	complete := true
	select {
	case <-done:
	case <-time.After(limit):
		complete = false
	}
	mu.Lock()
	defer mu.Unlock()
	return buf.String(), complete
}

// reportIncompleteRefs fails a guard that cannot trust its input.
func reportIncompleteRefs() bool {
	fmt.Printf("  %s\u2717%s Pre-push refs incomplete after 1s\n", red, reset)
	fmt.Printf("  \u21b3 fix: run from the git pre-push hook, or pass the refs in %s\n", prePushRefsEnv)
	return false
}

// archDiffBase names the upstream branch a new local branch forked from.
func archDiffBase() (string, bool) {
	candidates := []string{"origin/main", "origin/master"}
	if base := os.Getenv("HARNESS_ARCH_BASE"); base != "" {
		candidates = append(candidates, base)
	}
	for _, candidate := range candidates {
		if len(gitLines("rev-parse", "--verify", candidate)) > 0 {
			return candidate, true
		}
	}
	return "", false
}

// changedPathsForNewBranch lists what a branch the remote has never seen adds:
// everything since it forked from upstream, not just its tip commit. Without an
// upstream to fork from, the tip commit is all we can honestly report.
func changedPathsForNewBranch(localSha string) []string {
	if base, ok := archDiffBase(); ok {
		if mergeBase := gitLines("merge-base", base, localSha); len(mergeBase) > 0 {
			return gitLines("diff", "--name-only", mergeBase[0]+".."+localSha, "--", ".")
		}
	}
	return gitLines("diff-tree", "--no-commit-id", "--name-only", "-r", localSha, "--", ".")
}

func changedPathsFromPrePushRefs() []string {
	zero := strings.Repeat("0", 40)
	var paths []string
	for _, line := range readPrePushRefs().lines {
		parts := strings.Fields(line)
		if len(parts) < 4 {
			continue
		}
		localSha, remoteSha := parts[1], parts[3]
		if localSha == zero {
			continue // a deletion pushes no commits to inspect
		}
		if remoteSha == zero {
			paths = append(paths, changedPathsForNewBranch(localSha)...)
		} else {
			paths = append(paths, gitLines("diff", "--name-only", remoteSha, localSha, "--", ".")...)
		}
	}
	return paths
}

func changedArchConfigs(staged, includePrePushRefs bool) []string {
	var paths []string
	if staged {
		paths = append(paths, gitLines("diff", "--cached", "--name-only", "--", ".")...)
	} else {
		paths = append(paths, gitLines("diff", "--name-only", "--", ".")...)
		paths = append(paths, gitLines("diff", "--cached", "--name-only", "--", ".")...)
		paths = append(paths, gitLines("ls-files", "--others", "--exclude-standard", "--", ".")...)
		paths = append(paths, changedPathsFromBase()...)
	}
	if includePrePushRefs {
		paths = append(paths, changedPathsFromPrePushRefs()...)
	}

	seen := map[string]bool{}
	prefix := gitPrefix()
	for _, p := range paths {
		normalized := normalizeChangedPath(p, prefix)
		if normalized == archConfig {
			seen[normalized] = true
		}
	}
	var changed []string
	for p := range seen {
		changed = append(changed, p)
	}
	sort.Strings(changed)
	return changed
}

func checkArchConfigGuard(warnOnly, staged, includePrePushRefs bool) bool {
	if includePrePushRefs && readPrePushRefs().incomplete {
		return reportIncompleteRefs()
	}
	changed := changedArchConfigs(staged, includePrePushRefs)
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
	if !checkArchConfigGuard(hasFlag("warn"), hasFlag("staged"), hasFlag("pre-push")) {
		os.Exit(1)
	}
}

// ── Branch guard ────────────────────────────────────────────────────

const protectedPushAllowEnv = "HARNESS_ALLOW_PROTECTED_PUSH"

var protectedBranches = []string{"main", "master"}

// protectedPushTarget names the protected branch a push targets, or "" when the
// push is safe. Pure so it is testable without git: refLines are the pre-push
// ref lines, currentBranch the fallback used when there are none. Deleting a
// protected branch counts too — it is the most destructive push of all.
func protectedPushTarget(refLines []string, currentBranch string) string {
	sawRef := false
	for _, line := range refLines {
		parts := strings.Fields(line)
		if len(parts) < 4 {
			continue
		}
		sawRef = true
		for _, branch := range protectedBranches {
			if parts[2] == "refs/heads/"+branch {
				return branch
			}
		}
	}
	if sawRef {
		return ""
	}
	for _, branch := range protectedBranches {
		if currentBranch == branch {
			return branch
		}
	}
	return ""
}

func currentBranch() string {
	lines := gitLines("rev-parse", "--abbrev-ref", "HEAD")
	if len(lines) == 0 {
		return ""
	}
	return lines[0]
}

// checkBranchGuard refuses pushes that land on a protected branch. Humans keep
// merge authority; agents push feature branches and open a PR.
func checkBranchGuard() bool {
	refs := readPrePushRefs()
	if refs.incomplete {
		return reportIncompleteRefs()
	}
	target := protectedPushTarget(refs.lines, currentBranch())
	if target == "" {
		fmt.Printf("  %s\u2713%s Branch guard\n", green, reset)
		return true
	}
	if os.Getenv(protectedPushAllowEnv) == "1" {
		fmt.Printf("  %s\u26a0%s Branch guard override: %s\n", green, reset, target)
		return true
	}
	fmt.Printf("  %s\u2717%s Push targets protected branch: %s\n", red, reset, target)
	fmt.Printf("  \u21b3 fix: push a feature branch and open a PR; humans may set %s=1\n", protectedPushAllowEnv)
	return false
}

func cmdBranchGuard() {
	if !checkBranchGuard() {
		os.Exit(1)
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

// funcMetric is one function as `lizard --csv` measured it. CRAP computes
// coverage at join time from per-line hit counts; the stop hook's complexity
// delta matches functions by key.
type funcMetric struct {
	file   string
	key    string // long_name (the signature); a repeat in one file gets #2, #3
	name   string
	line   int
	end    int
	ccn    int
	args   int
	length int
}

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
	for _, m := range lizardRows(string(out)) {
		m.file = strings.TrimPrefix(m.file, "./")
		base := filepath.Base(m.file)
		if base == "harness.go" || strings.HasSuffix(base, "_test.go") {
			continue
		}
		// Skip anonymous closures: per-function coverage attribution would
		// roll into the enclosing function and mis-score the closure itself.
		if m.name == "" {
			continue
		}
		metrics = append(metrics, m)
	}
	return metrics
}

// ── Stages ──────────────────────────────────────────────────────────

// checkStopHooksPresent warns when the Claude/Codex Stop or the Claude
// PostToolUse wiring is missing.
func checkStopHooksPresent() {
	for _, w := range hookWirings {
		label := w.event + " hook wiring"
		if hookWired(w) {
			fmt.Printf("  %s✓%s %s (%s)\n", green, reset, label, w.path)
		} else {
			fmt.Printf("  %s⚠%s Missing %s: %s\n", red, reset, label, w.path)
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
	if !isFile(filepath.Join(root, "CLAUDE.md")) {
		fmt.Printf("  %s✗%s sync-agents-md: CLAUDE.md not found\n", red, reset)
		os.Exit(1)
	}
	if err := mirrorClaudeMd(); err != nil {
		fmt.Printf("  %s✗%s sync-agents-md: %v\n", red, reset, err)
		os.Exit(1)
	}
	fmt.Printf("  %s✓%s sync-agents-md: AGENTS.md ← CLAUDE.md\n", green, reset)
}

// syncAgentsMdStaged carries AGENTS.md into the commit of a staged CLAUDE.md.
// The `git add` keeps git's hook environment on purpose: GIT_INDEX_FILE is the
// index this commit is built from.
func syncAgentsMdStaged() {
	if len(gitLines("diff", "--cached", "--name-only", "--", "CLAUDE.md")) == 0 || !agentsMdStale() {
		return
	}
	if err := mirrorClaudeMd(); err != nil {
		fmt.Printf("  %s✗%s sync-agents-md: %v\n", red, reset, err)
		os.Exit(1)
	}
	c := exec.Command("git", "add", "--", "AGENTS.md")
	c.Dir = root
	if out, err := c.CombinedOutput(); err != nil {
		fmt.Printf("  %s✗%s sync-agents-md: git add AGENTS.md failed\n", red, reset)
		fmt.Print(string(out))
		os.Exit(1)
	}
	fmt.Printf("  %s✓%s sync-agents-md: AGENTS.md ← CLAUDE.md (staged)\n", green, reset)
}

func cmdCheck() {
	start := time.Now()
	fmt.Printf("\n%s[check]%s Running pre-flight checks...\n\n", blue, reset)

	results := []runResult{
		run("Fix & format", []string{"golangci-lint", "run", "--fix", "./..."}, &runOpts{noExit: true}),
		run("Tests", []string{"go", "test", "./..."}, &runOpts{extract: extractTestSummary, noExit: true}),
	}

	checkStopHooksPresent()
	checkArchConfigGuard(true, false, false)
	results = append(results, checkAgentsMdDrift(true))
	results = append(results, runResult{
		ok: suppressions.CheckBaseline(
			root,
			suppressions.ScanFindings(root),
			true,
			"go run harness.go suppressions --update-baseline",
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

// cmdPreCommit fixes and formats staged packages and mirrors a staged
// CLAUDE.md; tests run at pre-push. The drift check also runs when only the
// docs are staged, so a hand edit to AGENTS.md alone fails.
func cmdPreCommit() {
	fmt.Printf("\n%s[pre-commit]%s\n\n", blue, reset)
	checkArchConfigGuard(true, true, false)
	syncAgentsMdStaged()

	files := stagedGoFiles()
	if len(files) > 0 || len(gitLines("diff", "--cached", "--name-only", "--", "AGENTS.md", "CLAUDE.md")) > 0 {
		checkAgentsMdDrift(false)
	}
	if len(files) == 0 {
		fmt.Println("No staged Go files — skipping checks")
		return
	}

	cmdFix(stagedPackages(files))
}

func cmdCi() {
	fmt.Printf("\n%s[ci]%s\n\n", blue, reset)
	// Read-only gates run as a parallel batch (captured, printed in submission
	// order, run to completion). Coverage is captured and CRAP is advisory — after.
	gates := []gate{lintGate(nil), auditGate(), complexityGate()}
	gates = append(gates, acceptanceGatesOrWarn()...)
	gates = append(gates, archGatesOrWarn()...)
	allOk := runGatesParallel(gates)
	driftOk := checkAgentsMdDrift(true).ok
	cmdTestCov() // after the batch
	cmdCrap()    // advisory unless --enforce
	archConfigOk := checkArchConfigGuard(false, false, false)
	suppressionsOk := suppressions.CheckBaseline(
		root,
		suppressions.ScanFindings(root),
		true,
		"go run harness.go suppressions --update-baseline",
		true,
	)
	if !allOk || !driftOk || !archConfigOk || !suppressionsOk {
		os.Exit(1)
	}
}

// envWithoutGit is this environment minus the GIT_* variables git exports to
// hooks.
func envWithoutGit() []string {
	var env []string
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "GIT_") {
			env = append(env, kv)
		}
	}
	return env
}

// checkTests runs the suite captured, outside git's hook environment: git
// exports GIT_DIR (and, for commits, GIT_INDEX_FILE) to hooks, and a test that
// runs `git init` in a temp dir would otherwise write into this repository.
func checkTests() bool {
	return printGateResult(runCapture(gate{
		description: "Tests",
		cmd:         []string{"go", "test", "./..."},
		extract:     extractTestSummary,
		env:         envWithoutGit(),
	}), true)
}

// cmdPrePush is the read-only push gate: the offline checks pre-commit and
// stop-hook do not run. pre-commit covers fix/format on staged files;
// stop-hook covers the change's delta. This fills the gap with the
// deterministic, offline gates none of them run — tests, lint (golangci-lint
// covers format), agents-md drift, acceptance, arch — validating the whole
// pushed tree (after merges/rebases/--no-verify) before it leaves the machine.
// Tests run first and alone: they build a binary next to the sources. Network
// (audit) and advisory (coverage/CRAP) gates stay in ci.
// The branch guard runs first and short-circuits: on refusal (or incomplete
// refs) it prints and exits before the arch guard, drift check, or the
// parallel batch run at all — it is the gate that keeps merge authority with
// the human, so nothing else needs to run once it has failed.
func cmdPrePush() {
	fmt.Printf("\n%s[pre-push]%s\n\n", blue, reset)
	if !checkBranchGuard() {
		os.Exit(1)
	}
	archConfigOk := checkArchConfigGuard(false, false, true)
	testsOk := checkTests()
	gates := []gate{lintGate(nil)}
	gates = append(gates, acceptanceGatesOrWarn()...)
	gates = append(gates, archGatesOrWarn()...)
	allOk := runGatesParallel(gates)
	driftOk := checkAgentsMdDrift(true).ok
	if !allOk || !driftOk || !archConfigOk || !testsOk {
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
		"-C", strconv.Itoa(complexityMaxCCN), "-a", strconv.Itoa(complexityMaxArgs),
		"-L", strconv.Itoa(complexityMaxLength), "-i", "0",
		"-x", "*_test.go", "-x", "./harness.go",
	}, hint: fmt.Sprintf("extract helpers or flatten branches until CCN <= %d; do not raise the threshold", complexityMaxCCN)}
}

func cmdComplexity() {
	g := complexityGate()
	run(g.description, g.cmd, nil)
}

// ── Hook wiring (installed by `setup-hooks`) ────────────────────────
// Claude reads .claude/settings.json and runs the harness directly; Codex reads
// .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
// the exit code into the block/continue JSON Codex expects). PostToolUse is
// Claude-only: it formats the file an Edit/Write just touched.
//
// The Stop commands build the runner and run the binary: `go run` exits 1 for
// any non-zero exit of the program (and prints "exit status 2"), which would
// turn the stop hook's blocking exit 2 into a non-blocking 1. A failed build
// exits 1, which is the non-blocking outcome a broken runner deserves. The
// `harness` binary is gitignored. post-edit --hook always exits 0, so `go run`
// is fine there.
const (
	claudeSettings        = ".claude/settings.json"
	codexHooks            = ".codex/hooks.json"
	claudeSettingsSchema  = "https://json.schemastore.org/claude-code-settings.json"
	claudeStopCommand     = "cd $CLAUDE_PROJECT_DIR && go build -o harness harness.go && ./harness stop-hook"
	claudePostEditCommand = "cd $CLAUDE_PROJECT_DIR && go run harness.go post-edit --hook"
	codexStopCommand      = `cd "$(git rev-parse --show-toplevel)" && go build -o harness harness.go && .codex/hooks/codex-stop-hook.sh ./harness stop-hook`
)

// hookWiring is one harness hook in an agent settings file; marker identifies
// its handler on reinstall.
type hookWiring struct {
	path    string
	event   string
	marker  string
	matcher string
	handler map[string]any
}

var hookWirings = []hookWiring{
	{path: claudeSettings, event: "Stop", marker: "stop-hook", handler: map[string]any{
		"type": "command", "command": claudeStopCommand, "timeout": 300,
	}},
	{path: claudeSettings, event: "PostToolUse", marker: "post-edit --hook", matcher: "Edit|Write", handler: map[string]any{
		"type": "command", "command": claudePostEditCommand, "timeout": 60,
	}},
	{path: codexHooks, event: "Stop", marker: "stop-hook", handler: map[string]any{
		"type": "command", "command": codexStopCommand, "timeout": 300, "statusMessage": "Running stop-hook checks",
	}},
}

// gitHookPath resolves a git hook path via `git rev-parse` so worktrees and
// core.hooksPath land in the right place. GIT_* env is stripped so an ambient
// GIT_DIR from a parent process can't redirect us.
func gitHookPath(name string) string {
	c := exec.Command("git", "rev-parse", "--git-path", "hooks/"+name)
	c.Dir = root
	c.Env = envWithoutGit()
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

// isHarnessHandler reports whether handler is a command that already runs this
// harness hook (any form).
func isHarnessHandler(handler any, marker string) bool {
	m, ok := handler.(map[string]any)
	if !ok || m["type"] != "command" {
		return false
	}
	cmd, ok := m["command"].(string)
	return ok && strings.Contains(cmd, marker)
}

// readJSONObject reads a settings file; a missing or empty file is {}.
func readJSONObject(rel string) (map[string]any, error) {
	data := map[string]any{}
	raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil || strings.TrimSpace(string(raw)) == "" {
		return data, nil
	}
	return data, json.Unmarshal(raw, &data)
}

// hookWired reports whether the settings file has a handler for this hook
// under its event.
func hookWired(w hookWiring) bool {
	data, err := readJSONObject(w.path)
	if err != nil {
		return false
	}
	hooks, _ := data["hooks"].(map[string]any)
	groups, _ := hooks[w.event].([]any)
	for _, group := range groups {
		groupMap, _ := group.(map[string]any)
		handlers, _ := groupMap["hooks"].([]any)
		for _, handler := range handlers {
			if isHarnessHandler(handler, w.marker) {
				return true
			}
		}
	}
	return false
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

// replaceHarnessHandler swaps the first handler carrying the wiring's marker
// for the current one and drops any duplicates. Returns whether it found one.
func replaceHarnessHandler(groups []any, w hookWiring) bool {
	installed := false
	for _, group := range groups {
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
			if !isHarnessHandler(handler, w.marker) {
				next = append(next, handler)
			} else if !installed {
				next = append(next, cloneMap(w.handler))
				installed = true
			}
		}
		groupMap["hooks"] = next
	}
	return installed
}

func writeJSONObject(path string, data map[string]any) error {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(data); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, out.Bytes(), 0o644)
}

// installHook injects/refreshes one hook in its settings file, preserving every
// other hook. Idempotent: an existing handler carrying the wiring's marker
// (current or legacy) is replaced in place and duplicates are dropped, so
// re-running never accumulates entries. (encoding/json sorts object keys, so
// the file is rewritten in a stable order — cosmetic, and identical on every
// subsequent run.)
func installHook(w hookWiring) {
	data, err := readJSONObject(w.path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: invalid JSON: %v\n", w.path, err)
		os.Exit(1)
	}
	if _, ok := data["$schema"]; !ok && w.path == claudeSettings {
		data["$schema"] = claudeSettingsSchema
	}
	hooks := jsonObjectChild(data, "hooks", w.path)
	groups, _ := hooks[w.event].([]any)
	if !replaceHarnessHandler(groups, w) {
		group := map[string]any{"hooks": []any{cloneMap(w.handler)}}
		if w.matcher != "" {
			group["matcher"] = w.matcher
		}
		groups = append(groups, group)
	}
	hooks[w.event] = groups
	if err := writeJSONObject(filepath.Join(root, filepath.FromSlash(w.path)), data); err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", w.path, err)
		os.Exit(1)
	}
}

func cmdHooks() {
	installGitHook("pre-commit")
	installGitHook("pre-push")
	for _, w := range hookWirings {
		installHook(w)
	}
	fmt.Println("Installed pre-commit, pre-push, Claude/Codex Stop, and Claude PostToolUse hooks")
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

func cmdSuppressions() {
	findings := suppressions.ScanFindings(root)
	results := suppressions.BucketByKind(findings)
	if hasFlag("update-baseline") {
		if err := suppressions.WriteBaseline(root, results); err != nil {
			fmt.Printf("  %s✗%s .harness-baseline: %v\n", red, reset, err)
			os.Exit(1)
		}
		total := 0
		for _, entries := range results {
			total += len(entries)
		}
		fmt.Printf("  %s✓%s .harness-baseline: suppressions baseline set to %d\n", green, reset, total)
		return
	}
	suppressions.PrintReport(results)
	if !suppressions.CheckBaseline(
		root,
		findings,
		true,
		"go run harness.go suppressions --update-baseline",
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
	{"branch-guard", cmdBranchGuard, "Refuse pushes to main/master"},
	{"mutation", cmdMutation, "Mutation testing (gremlins, advisory)"},
	{"crap", cmdCrap, "CRAP complexity x coverage gate (advisory)"},
	{"suppressions", cmdSuppressions, "Show or update suppression baseline"},
	{"pre-commit", cmdPreCommit, "Staged fix/format; mirrors a staged CLAUDE.md"},
	{"pre-push", cmdPrePush, "Read-only push gate: branch guard, tests, lint, agents-md drift, acceptance, arch"},
	{"ci", cmdCi, "Full verification: lint, audit, complexity, agents-md drift, acceptance, coverage, crap, arch"},
	{"setup-hooks", cmdHooks, "Install git pre-commit + pre-push hooks and Claude/Codex agent hook wiring"},
	{"post-edit", cmdPostEdit, "Format changed files (--hook: the file a PostToolUse names)"},
	{"stop-hook", cmdStopHook, "post-edit, then changed-lines lint, complexity delta; silent on success, exit 2 with findings"},
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
