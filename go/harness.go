//go:build ignore

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
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
	// go run sets the working directory correctly, but be explicit.
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
	pkgs := len(matches)
	// Sum durations
	return fmt.Sprintf("%d pkg, %ss", pkgs, matches[len(matches)-1][1])
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
	c := exec.Command("git", "diff", "--cached", "--name-only", "--diff-filter=d")
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

// ── Stages ──────────────────────────────────────────────────────────

func cmdCheck() {
	start := time.Now()
	fmt.Printf("\n%s[check]%s Running pre-flight checks...\n\n", blue, reset)

	results := []runResult{
		run("Fix & format", []string{"golangci-lint", "run", "--fix", "./..."}, &runOpts{noExit: true}),
		run("Tests", []string{"go", "test", "./..."}, &runOpts{extract: extractTestSummary, noExit: true}),
	}

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
	cmdTestCov()
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
	{"check", func() { cmdCheck() }, "Full pre-flight: fix + format + lint + test"},
	{"fix", func() { cmdFix(nil) }, "Fix lint errors + format code"},
	{"lint", func() { cmdLint(nil) }, "Lint + format check (read-only)"},
	{"test", func() { cmdTest() }, "Run tests"},
	{"audit", func() { cmdAudit() }, "Audit dependencies for known vulnerabilities"},
	{"pre-commit", func() { cmdPreCommit() }, "Staged checks + tests"},
	{"ci", func() { cmdCi() }, "Lint + tests with race detector and coverage"},
	{"setup-hooks", func() { cmdHooks() }, "Install git pre-commit hook"},
	{"post-edit", func() { cmdPostEdit() }, "Format if source files changed (Claude Code hook)"},
	{"clean", func() { cmdClean() }, "Remove coverage and test cache"},
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
