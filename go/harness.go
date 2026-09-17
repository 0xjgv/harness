//go:build ignore

package main

import (
	"bytes"
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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

// packageDirs names the package directories holding the golangci-lint targets
// among files: "." for the module root, "./dir" otherwise.
func packageDirs(files []string) []string {
	seen := map[string]bool{}
	var dirs []string
	for _, f := range files {
		if !isLintTarget(f) {
			continue
		}
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

// isLintTarget is a Go source a package includes (harness.go is build-ignored).
func isLintTarget(path string) bool {
	return isGoSource(path) && path != "harness.go"
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// changedGoFiles lists Go files with uncommitted changes (untracked included),
// relative to this project: porcelain paths are repository-relative.
func changedGoFiles() []string {
	c := exec.Command("git", "-c", "core.quotePath=false", "status", "--porcelain", "--no-renames", "--untracked-files=all", "--", ".")
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
		if f := normalizeChangedPath(line[3:], prefix); isGoSource(f) {
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
// The stop hook judges the change, not the tree: lint left on changed lines
// (`unused` covers dead code) and over-limit functions the change touches.
// Whole-tree gates stay in check / ci / pre-push.

const hookFindingLimit = 20 // finding lines per payload; `--verbose` lifts it

// lineRange is an inclusive span of new-side line numbers.
type lineRange struct{ start, end int }

// changedLines maps a project-relative path to its changed spans.
type changedLines map[string][]lineRange

var wholeFile = []lineRange{{1, math.MaxInt}}

var hunkRe = regexp.MustCompile(`^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@`)

// deltaResult is one delta gate: findings block the stop; err means its tool failed.
type deltaResult struct {
	gate     string
	findings []string
	err      error
}

// runTool returns the command's stdout; a non-zero exit is an error carrying
// the last line of its stderr.
func runTool(cmd ...string) (string, error) {
	out, err := exec.Command(cmd[0], cmd[1:]...).Output()
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		detail := cmp.Or(strings.TrimSpace(string(exitErr.Stderr)), "no output")
		err = fmt.Errorf("%s exited %d: %s", cmd[0], exitErr.ExitCode(), detail[strings.LastIndex(detail, "\n")+1:])
	}
	return string(out), err
}

func gitOutput(args ...string) (string, error) {
	return runTool(append([]string{"git", "-c", "core.quotePath=false"}, args...)...)
}

func hasCommits() bool {
	return len(gitLines("rev-parse", "--verify", "--quiet", "HEAD")) > 0
}

// deltaBase is the merge-base of HEAD with the first base ref that has one:
// HARNESS_ARCH_BASE, GITHUB_BASE_REF, then the usual default branches, never
// fetched. HEAD without one; "" before the first commit.
func deltaBase() string {
	if !hasCommits() {
		return ""
	}
	refs := []string{os.Getenv("HARNESS_ARCH_BASE")}
	if githubBase := os.Getenv("GITHUB_BASE_REF"); githubBase != "" {
		refs = append(refs, "origin/"+githubBase)
	}
	for _, ref := range append(refs, "origin/HEAD", "origin/main", "origin/master", "main", "master") {
		if mergeBase := gitLines("merge-base", ref, "HEAD"); ref != "" && len(mergeBase) > 0 {
			return mergeBase[0]
		}
	}
	return "HEAD"
}

// parseDiffRanges reads the new-side line spans of a `git diff -U0` (a/ b/
// prefixes). File headers are read only between `diff --git` and the first
// hunk, so an added line whose text starts with `++ ` is never taken for one.
// A pure deletion (`+N,0`) adds no span, but its file is still listed.
func parseDiffRanges(diff string) changedLines {
	ranges := changedLines{}
	path, inHeader := "", false
	for line := range strings.SplitSeq(diff, "\n") {
		m := hunkRe.FindStringSubmatch(line)
		switch {
		case strings.HasPrefix(line, "diff --git "):
			path, inHeader = "", true
		case inHeader && strings.HasPrefix(line, "+++ "):
			path = strings.TrimPrefix(strings.Trim(strings.TrimRight(line[4:], "\t"), `"`), "b/")
			if path == "/dev/null" {
				path = ""
			} else {
				ranges[path] = []lineRange{}
			}
		case m != nil && path != "":
			inHeader = false
			start, _ := strconv.Atoi(m[1])
			count, err := strconv.Atoi(m[2])
			if err != nil {
				count = 1 // `+N` without a count is one line
			}
			if count > 0 {
				ranges[path] = append(ranges[path], lineRange{start, start + count - 1})
			}
		case strings.HasPrefix(line, "@@"):
			inHeader = false
		}
	}
	return ranges
}

// changedScope is the changed lines per project-relative path: `git diff <base>`
// (committed and uncommitted work; a rename is a new file) plus untracked files,
// which, like every file before the first commit, are in scope whole.
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

// touches reports whether the lines start..end overlap any of ranges.
func touches(ranges []lineRange, start, end int) bool {
	for _, r := range ranges {
		if r.start <= end && start <= r.end {
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

// lintResidue is the lint the fix pass left on changed lines of the changed
// packages. gocyclo is left to complexityResidue, which reports the same
// functions. A compile error (typecheck) blocks wherever it sits: it stops
// every other linter in its package.
func lintResidue(scope changedLines, base string) ([]string, error) {
	files := scopedFiles(scope, isLintTarget)
	if len(files) == 0 {
		return nil, nil
	}
	cmd := []string{
		"golangci-lint", "run", "--allow-serial-runners", "--show-stats=false", "--issues-exit-code=0",
		"--max-issues-per-linter=0", "--max-same-issues=0", "--output.text.path=stderr", "--output.json.path=stdout",
	}
	if base != "" {
		cmd = append(cmd, "--new-from-rev="+base)
	}
	out, err := runTool(append(cmd, packageDirs(files)...)...)
	if err != nil {
		return nil, err
	}
	var report struct {
		Issues []struct {
			FromLinter, Text string
			Pos              struct {
				Filename string
				Line     int
			}
		}
	}
	if err := json.Unmarshal([]byte(out), &report); err != nil {
		return nil, fmt.Errorf("unreadable golangci-lint output: %w", err)
	}
	var findings []string
	for _, issue := range report.Issues {
		at := issue.Pos
		if issue.FromLinter == "gocyclo" || (issue.FromLinter != "typecheck" && !touches(scope[at.Filename], at.Line, at.Line)) {
			continue
		}
		message := strings.ReplaceAll(strings.TrimSpace(issue.Text), "\n", " ")
		findings = append(findings, fmt.Sprintf("%s:%d: %s: %s", at.Filename, at.Line, issue.FromLinter, message))
	}
	return findings, nil
}

// complexityResidue measures the changed non-test files with lizard and
// reports the over-limit functions the change touches.
func complexityResidue(scope changedLines) ([]string, error) {
	files := scopedFiles(scope, func(path string) bool {
		return isLintTarget(path) && !strings.HasSuffix(path, "_test.go")
	})
	// lizard with no file arguments walks the working directory; never let it.
	if len(files) == 0 {
		return nil, nil
	}
	out, err := runTool(append([]string{"uvx", lizard, "--csv"}, files...)...)
	if err != nil {
		return nil, err
	}
	return touchedOverLimit(parseLizardCSV(out), scope), nil
}

// touchedOverLimit is one `path:line: name CCN 19 (limit 15)` line per limit
// exceeded by a function whose span overlaps a changed range.
func touchedOverLimit(functions []funcMetric, scope changedLines) []string {
	var findings []string
	for _, fn := range functions {
		if !touches(scope[fn.file], fn.line, fn.end) {
			continue
		}
		for _, m := range []struct {
			label        string
			value, limit int
		}{{"CCN", fn.ccn, complexityMaxCCN}, {"args", fn.args, complexityMaxArgs}, {"length", fn.end - fn.line + 1, complexityMaxLength}} {
			if m.value > m.limit {
				findings = append(findings, fmt.Sprintf("%s:%d: %s %s %d (limit %d)",
					fn.file, fn.line, cmp.Or(fn.name, "(anonymous)"), m.label, m.value, m.limit))
			}
		}
	}
	return findings
}

// runDeltaGates runs lint residue and complexity, read-only, in parallel.
func runDeltaGates(scope changedLines, base string) []deltaResult {
	lint, complexity := deltaResult{gate: "Lint"}, deltaResult{gate: "Complexity"}
	done := make(chan struct{})
	go func() {
		lint.findings, lint.err = lintResidue(scope, base)
		close(done)
	}()
	complexity.findings, complexity.err = complexityResidue(scope)
	<-done
	return []deltaResult{lint, complexity}
}

// stopHookPayload is the stderr block an agent reads: the failed gates, then
// their findings, capped; "" when clean.
func stopHookPayload(results []deltaResult) string {
	gates, findings := []string{}, []string{}
	for _, r := range results {
		if len(r.findings) > 0 {
			gates = append(gates, r.gate)
			findings = append(findings, r.findings...)
		}
	}
	if len(gates) == 0 {
		return ""
	}
	if rest := len(findings) - hookFindingLimit; rest > 0 && !verbose {
		findings = append(findings[:hookFindingLimit], fmt.Sprintf("… +%d more — run `go run harness.go stop-hook --verbose`", rest))
	}
	return "stop-hook failed: " + strings.Join(gates, ", ") + "\n" + strings.Join(findings, "\n")
}

// stopHookExit is 2 to block on findings, 0 when clean, and 1 for a tool
// failure or for findings on a stop that already blocked once
// (`stop_hook_active`): blocking again could loop.
func stopHookExit(payload string, failedTools int, active bool) int {
	switch {
	case payload != "" && !active:
		return 2
	case payload != "" || failedTools > 0:
		return 1
	}
	return 0
}

// hookEvent is the part of a Claude/Codex hook event the runner reads.
type hookEvent struct {
	StopHookActive bool `json:"stop_hook_active"`
	ToolInput      struct {
		FilePath string `json:"file_path"`
	} `json:"tool_input"`
}

// readHookEvent reads the hook JSON from stdin with the pre-push reader's 1s
// deadline. A terminal is never read; empty or invalid input is an empty event.
func readHookEvent() hookEvent {
	var event hookEvent
	info, err := os.Stdin.Stat()
	if err != nil || info.Mode()&os.ModeCharDevice != 0 {
		return event
	}
	// JSON strings hold no raw newlines, so the non-empty lines rejoin losslessly.
	input := readRefsWithDeadline(os.Stdin, time.Second)
	_ = json.Unmarshal([]byte(strings.Join(input.lines, "\n")), &event)
	return event
}

// postEditCommands formats files, then fixes lint in their packages (never
// `./...`), only on uncommitted code.
func postEditCommands(files []string) [][]string {
	if len(files) == 0 {
		return nil
	}
	cmds := [][]string{append([]string{"golangci-lint", "fmt"}, files...)}
	pkgs := packageDirs(files)
	if len(pkgs) == 0 {
		return cmds
	}
	fix := []string{"golangci-lint", "run", "--fix", "--allow-serial-runners"}
	if hasCommits() {
		fix = append(fix, "--new-from-rev=HEAD")
	}
	return append(cmds, append(fix, pkgs...))
}

// fixAndFormat runs postEditCommands silently; what is left is lint residue.
func fixAndFormat(files []string) {
	for _, cmd := range postEditCommands(files) {
		c := exec.Command(cmd[0], cmd[1:]...)
		c.Dir = root
		_ = c.Run()
	}
}

// staleClaudeMd is CLAUDE.md's content and whether AGENTS.md differs from it.
func staleClaudeMd() ([]byte, bool) {
	claude, err := os.ReadFile(filepath.Join(root, "CLAUDE.md"))
	if err != nil {
		return nil, false
	}
	agents, err := os.ReadFile(filepath.Join(root, "AGENTS.md"))
	return claude, err != nil || !bytes.Equal(agents, claude)
}

// syncAgentsMdAfterEdit copies an uncommitted CLAUDE.md edit into AGENTS.md; an
// edit to AGENTS.md alone is left for pre-commit to report.
func syncAgentsMdAfterEdit() {
	if len(gitLines("status", "--porcelain", "--", "CLAUDE.md")) == 0 {
		return
	}
	if claude, stale := staleClaudeMd(); stale {
		_ = os.WriteFile(filepath.Join(root, "AGENTS.md"), claude, 0o644)
	}
}

// cmdStopHook runs post-edit, then the delta gates; silent when clean (see
// stopHookExit). The hooks run a built binary: `go run` turns exit 2 into 1.
func cmdStopHook() {
	event := readHookEvent() // stdin belongs to the hook event; read it before anything else
	fixAndFormat(changedGoFiles())
	syncAgentsMdAfterEdit()
	base := deltaBase()
	scope, err := changedScope(base)
	if err != nil {
		fmt.Fprintf(os.Stderr, "stop-hook: changed lines could not run: %v\n", err)
		os.Exit(1)
	}
	results := runDeltaGates(scope, base)
	failed := 0
	for _, r := range results {
		if r.err != nil {
			failed++
			fmt.Fprintf(os.Stderr, "stop-hook: %s could not run: %v\n", r.gate, r.err)
		}
	}
	payload := stopHookPayload(results)
	code := stopHookExit(payload, failed, event.StopHookActive)
	if payload != "" && code == 1 {
		payload += "\nharness: already blocked once on this stop; not blocking again"
	}
	if payload != "" {
		fmt.Fprintln(os.Stderr, payload)
	}
	os.Exit(code)
}

// hookTarget is the project Go file at filePath, relative to base; "" for
// anything else. Symlinks resolve on both sides (macOS /tmp is /private/tmp);
// isGoSource rejects "." and `..` paths.
func hookTarget(filePath, base string) string {
	if !filepath.IsAbs(filePath) {
		filePath = filepath.Join(base, filePath)
	}
	realPath, pathErr := filepath.EvalSymlinks(filePath)
	realBase, baseErr := filepath.EvalSymlinks(base)
	if pathErr == nil && baseErr == nil {
		filePath, base = realPath, realBase
	}
	rel, err := filepath.Rel(base, filePath)
	rel = filepath.ToSlash(rel)
	if err != nil || !isGoSource(rel) || !isFile(filepath.Join(base, rel)) {
		return ""
	}
	return rel
}

// postEditHook fixes and formats the file a PostToolUse event names. It never
// blocks; when the file changed it asks the agent to re-read it.
func postEditHook() {
	target := hookTarget(readHookEvent().ToolInput.FilePath, root)
	if target == "" {
		return
	}
	before, errBefore := os.ReadFile(target)
	fixAndFormat([]string{target})
	after, errAfter := os.ReadFile(target)
	if errBefore != nil || errAfter != nil || bytes.Equal(before, after) {
		return
	}
	context, _ := json.Marshal("harness: reformatted " + target + "; re-read it before editing it again")
	fmt.Printf("{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":%s}}\n", context)
}

// cmdPostEdit formats and fixes files with uncommitted changes; `--hook`: the
// file a PostToolUse event names.
func cmdPostEdit() {
	if hasFlag("hook") {
		postEditHook()
		return
	}
	for _, cmd := range postEditCommands(changedGoFiles()) {
		run(strings.Join(cmd[:2], " "), cmd, &runOpts{noExit: true})
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

// readRefsWithDeadline reads r to EOF, bounding the whole read by limit. A
// deadline with nothing received is an idle pipe (an agent tool, not a hook) —
// no refs; a deadline with bytes received is incomplete input.
func readRefsWithDeadline(r io.Reader, limit time.Duration) prePushRefs {
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
	select {
	case <-done:
	case <-time.After(limit):
		mu.Lock()
		defer mu.Unlock()
		return prePushRefs{incomplete: buf.Len() > 0}
	}
	mu.Lock()
	defer mu.Unlock()
	return prePushRefs{lines: refLinesOf(buf.String())}
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

// funcMetric pairs a function's location with its cyclomatic complexity.
// Coverage is computed at join time in cmdCrap from per-line hit counts.
type funcMetric struct {
	file string
	line int
	end  int
	name string
	ccn  int
	args int
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
	for _, m := range parseLizardCSV(string(out)) {
		// Skip the runner, tests, and anonymous closures: per-function coverage
		// attribution would roll a closure into its enclosing function.
		if base := filepath.Base(m.file); base == "harness.go" || strings.HasSuffix(base, "_test.go") || m.name == "" {
			continue
		}
		metrics = append(metrics, m)
	}
	return metrics
}

// parseLizardCSV reads the function rows of `lizard --csv` output.
func parseLizardCSV(out string) []funcMetric {
	var metrics []funcMetric
	for row := range strings.SplitSeq(out, "\n") {
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
		args, _ := strconv.Atoi(cols[3])
		metrics = append(metrics, funcMetric{
			file: path, line: ln, end: end, name: name, ccn: ccn, args: args,
		})
	}
	return metrics
}

// ── Stages ──────────────────────────────────────────────────────────

// checkStopHooksPresent warns when the Claude/Codex Stop or the Claude
// PostToolUse wiring is missing.
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
	data, _ := os.ReadFile(filepath.Join(root, ".claude/settings.json"))
	if strings.Contains(string(data), "PostToolUse") && strings.Contains(string(data), "post-edit --hook") {
		fmt.Printf("  %s✓%s PostToolUse hook wiring (.claude/settings.json)\n", green, reset)
	} else {
		fmt.Printf("  %s⚠%s Missing PostToolUse hook wiring: .claude/settings.json\n", red, reset)
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

// syncAgentsMdStaged carries AGENTS.md into the commit of a staged CLAUDE.md;
// `git add` keeps git's hook env: GIT_INDEX_FILE is this commit's index.
func syncAgentsMdStaged() {
	if _, stale := staleClaudeMd(); !stale || len(gitLines("diff", "--cached", "--name-only", "--", "CLAUDE.md")) == 0 {
		return
	}
	cmdSyncAgentsMd()
	c := exec.Command("git", "add", "--", "AGENTS.md")
	c.Dir = root
	if out, err := c.CombinedOutput(); err != nil {
		fmt.Printf("  %s✗%s sync-agents-md: git add AGENTS.md failed\n%s", red, reset, out)
		os.Exit(1)
	}
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

// cmdPreCommit fixes and formats staged packages and mirrors a staged CLAUDE.md;
// tests run at pre-push. A hand edit to AGENTS.md alone fails the drift check.
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

// cmdPrePush is the read-only push gate: the offline checks pre-commit and
// stop-hook do not run. pre-commit covers fix/format on staged files;
// stop-hook covers the change. This fills the gap with the deterministic, offline
// gates none of them run — tests, lint (golangci-lint covers format), agents-md drift,
// acceptance, arch — validating the whole pushed tree (after merges/rebases/
// --no-verify) before it leaves the machine. Network (audit) and advisory
// (coverage/CRAP) gates stay in ci.
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
	// Tests go first and alone (they build a binary next to the sources), without
	// git's hook variables: a test's `git init` would otherwise hit this repository.
	for _, kv := range os.Environ() {
		if name, _, _ := strings.Cut(kv, "="); strings.HasPrefix(name, "GIT_") {
			_ = os.Unsetenv(name)
		}
	}
	cmdTest()
	gates := []gate{lintGate(nil)}
	gates = append(gates, acceptanceGatesOrWarn()...)
	gates = append(gates, archGatesOrWarn()...)
	allOk := runGatesParallel(gates)
	driftOk := checkAgentsMdDrift(true).ok
	if !allOk || !driftOk || !archConfigOk {
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
// the exit code into the block/continue JSON Codex expects). The Stop commands
// build the runner and run the binary: `go run` exits 1 for any non-zero exit,
// which would turn the blocking exit 2 into a non-blocking 1. The checked-in
// .claude/settings.json also carries the PostToolUse hook (`post-edit --hook`).
const (
	claudeSettingsSchema = "https://json.schemastore.org/claude-code-settings.json"
	claudeStopCommand    = "cd $CLAUDE_PROJECT_DIR && go build -o harness harness.go && ./harness stop-hook"
	codexStopCommand     = `cd "$(git rev-parse --show-toplevel)" && go build -o harness harness.go && .codex/hooks/codex-stop-hook.sh ./harness stop-hook`
)

func claudeStopHook() map[string]any {
	return map[string]any{"type": "command", "command": claudeStopCommand, "timeout": 300}
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
// entries. (encoding/json sorts object keys, so the file is rewritten in a stable
// order — cosmetic, and identical on every subsequent run.)
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

	out, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: marshal failed: %v\n", rel, err)
		os.Exit(1)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		os.Exit(1)
	}
	if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
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
	{"setup-hooks", cmdHooks, "Install git pre-commit + pre-push hooks and Claude/Codex Stop wiring"},
	{"post-edit", cmdPostEdit, "Format changed files (--hook: the file a PostToolUse names)"},
	{"stop-hook", cmdStopHook, "post-edit, then changed-lines lint, touched-function complexity; silent on success, exit 2 with findings"},
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
