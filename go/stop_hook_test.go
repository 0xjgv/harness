package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// End-to-end runs of the stop hook, the PostToolUse hook, and the hook-adjacent
// pre-commit / pre-push rules, through the compiled runner (harnessBin, built
// by TestMain in branch_guard_test.go) against throwaway git repos.

// TestHarnessUnits runs the pure-helper tests in stop_hook_unit_test.go.
// harness.go is `//go:build ignore`, so its functions compile only with the
// files named on the command line; running that here keeps them under
// `go test ./...`.
func TestHarnessUnits(t *testing.T) {
	cmd := exec.Command("go", "test", "-count=1", "harness.go", "stop_hook_unit_test.go")
	cmd.Env = hookEnv()
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("go test harness.go stop_hook_unit_test.go: %v\n%s", err, out)
	}
}

// hookEnv is the child environment for hook scenarios: no git hook variables
// and no base or guard overrides from the developer's shell.
func hookEnv(extra ...string) []string {
	var env []string
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(name, "GIT_") || strings.HasPrefix(name, "HARNESS_") || name == "GITHUB_BASE_REF" {
			continue
		}
		env = append(env, entry)
	}
	return append(env, extra...)
}

type harnessRun struct {
	code           int
	stdout, stderr string
}

// Runner invocations under test.
func stopHook() *exec.Cmd  { return exec.Command(harnessBin, "stop-hook") }
func postEdit() *exec.Cmd  { return exec.Command(harnessBin, "post-edit", "--hook") }
func preCommit() *exec.Cmd { return exec.Command(harnessBin, "pre-commit") }
func prePush() *exec.Cmd   { return exec.Command(harnessBin, "pre-push") }

// runHarness runs a runner invocation in dir. An empty stdin leaves the
// child's stdin at /dev/null, which the hooks never read.
func runHarness(t *testing.T, cmd *exec.Cmd, dir, stdin string, env []string) harnessRun {
	t.Helper()
	cmd.Dir = dir
	cmd.Env = env
	if env == nil {
		cmd.Env = hookEnv()
	}
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil && cmd.ProcessState == nil {
		t.Fatal(err)
	}
	return harnessRun{cmd.ProcessState.ExitCode(), stdout.String(), stderr.String()}
}

func (r harnessRun) assertSilent(t *testing.T) {
	t.Helper()
	if r != (harnessRun{}) {
		t.Fatalf("expected a silent exit 0, got %d\n--- stdout ---\n%s--- stderr ---\n%s", r.code, r.stdout, r.stderr)
	}
}

func (r harnessRun) assertBlocked(t *testing.T, code int, stderr ...string) {
	t.Helper()
	got := strings.Split(strings.TrimSuffix(r.stderr, "\n"), "\n")
	if r.code != code || r.stdout != "" || !reflect.DeepEqual(got, stderr) {
		t.Fatalf("expected exit %d with stderr %q and no stdout, got %d\n--- stdout ---\n%s--- stderr ---\n%s",
			code, stderr, r.code, r.stdout, r.stderr)
	}
}

func writeFile(t *testing.T, dir, rel, text string) {
	t.Helper()
	path := filepath.Join(dir, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func readFile(t *testing.T, dir, rel string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Clean(filepath.Join(dir, filepath.FromSlash(rel))))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// goProject is a git repo whose Go module (optionally in subdir) uses this
// template's lint config.
func goProject(t *testing.T, subdir string) (repo, project string) {
	t.Helper()
	repo = t.TempDir()
	mustRun(t, repo, exec.Command("git", "init", "-q", "-b", "main"))
	project = filepath.Join(repo, subdir)
	writeFile(t, project, "go.mod", "module scratch\n\ngo 1.24\n")
	writeFile(t, project, ".golangci.yaml", readFile(t, ".", ".golangci.yaml"))
	return repo, project
}

// featureBranch commits text as app/app.go on main, marks it origin/main, and
// branches off.
func featureBranch(t *testing.T, repo, project, text string) {
	t.Helper()
	writeFile(t, project, "app/app.go", text)
	commitAll(t, repo)
	mustRun(t, repo, exec.Command("git", "update-ref", "refs/remotes/origin/main", "HEAD"))
	mustRun(t, repo, exec.Command("git", "checkout", "-q", "-b", "feature"))
}

// goFile is a lint-clean package app source holding decls.
func goFile(decls ...string) string {
	return "// Package app is scratch.\npackage app\n\n" + strings.Join(decls, "\n")
}

const helper = "// Helper returns one.\nfunc Helper() int {\n\treturn 1\n}\n"

// busyFunc is an exported function Busy whose CCN is branches + 1.
func busyFunc(branches int) string {
	var b strings.Builder
	b.WriteString("// Busy branches.\nfunc Busy(value int) int {\n")
	for i := range branches {
		fmt.Fprintf(&b, "\tif value == %d {\n\t\treturn %d\n\t}\n", i, i)
	}
	b.WriteString("\treturn -1\n}\n")
	return b.String()
}

func TestStopHookCleanTreeIsSilent(t *testing.T) {
	repo, project := goProject(t, "")
	writeFile(t, project, "app/app.go", goFile(helper))
	commitAll(t, repo)
	runHarness(t, stopHook(), project, "", nil).assertSilent(t)
}

// TestStopHookIgnoresUntouchedDebt: the project sits in a subdirectory, the
// change is committed on a branch, and a misformatted file in another package
// is left as it is.
func TestStopHookIgnoresUntouchedDebt(t *testing.T) {
	repo, project := goProject(t, "proj")
	misformatted := "// Package other is scratch.\npackage other\n\n// X is one.\nvar X=1\n"
	writeFile(t, project, "other/other.go", misformatted)
	featureBranch(t, repo, project, goFile(busyFunc(20), helper))
	writeFile(t, project, "app/app.go", goFile(busyFunc(20), helper, "// Extra is new.\nconst Extra = 2\n"))
	commitAll(t, repo)
	writeFile(t, project, "app/more.go", goFile("// More is new.\nconst More = 3\n"))

	runHarness(t, stopHook(), project, "", nil).assertSilent(t)
	if got := readFile(t, project, "other/other.go"); got != misformatted {
		t.Fatalf("post-edit touched a file outside the change:\n%s", got)
	}
}

func TestStopHookBlocksANewComplexFunctionThenTheLoopGuardReleases(t *testing.T) {
	repo, project := goProject(t, "proj")
	featureBranch(t, repo, project, goFile(helper))
	writeFile(t, project, "app/app.go", goFile(helper, busyFunc(20)))
	commitAll(t, repo)
	active := `{"stop_hook_active": true}`
	findings := []string{"stop-hook failed: Complexity", "app/app.go:10: Busy CCN new→21 (limit 15)"}

	runHarness(t, stopHook(), project, active, nil).assertBlocked(t, 2, findings...)
	runHarness(t, stopHook(), project, active, nil).assertBlocked(t, 1, append(findings, wantLoopGuardNotice)...)
	state := filepath.Join(mustRun(t, repo, exec.Command("git", "rev-parse", "--absolute-git-dir")), "harness")
	if entries, _ := os.ReadDir(state); len(entries) != 1 || entries[0].Name() != "stop-hook-proj" {
		t.Fatalf("loop-guard state = %v, want [stop-hook-proj]", entries)
	}

	writeFile(t, project, "app/app.go", goFile(helper)) // fixed: a clean stop forgets it
	runHarness(t, stopHook(), project, active, nil).assertSilent(t)
	if entries, _ := os.ReadDir(state); len(entries) != 0 {
		t.Fatalf("loop-guard state after a clean stop = %v", entries)
	}
}

// wantLoopGuardNotice mirrors the runner's loopGuardNotice; harness.go is not
// part of this package.
const wantLoopGuardNotice = "harness: same findings as the previous stop; not blocking again"

// TestStopHookJudgesComplexityAgainstTheBase: worse blocks; better, or the same
// function with a new signature (its declaration line changed), passes.
func TestStopHookJudgesComplexityAgainstTheBase(t *testing.T) {
	repo, project := goProject(t, "")
	featureBranch(t, repo, project, goFile(busyFunc(16)))

	writeFile(t, project, "app/app.go", goFile(busyFunc(18)))
	runHarness(t, stopHook(), project, "", nil).assertBlocked(t, 2,
		"stop-hook failed: Complexity", "app/app.go:5: Busy CCN 17→19 (limit 15)")

	writeFile(t, project, "app/app.go", goFile(busyFunc(15)))
	runHarness(t, stopHook(), project, "", nil).assertSilent(t)

	writeFile(t, project, "app/app.go", goFile(strings.ReplaceAll(busyFunc(16), "value", "v")))
	runHarness(t, stopHook(), project, "", nil).assertSilent(t)
}

func TestStopHookBlocksLintOnChangedLinesOnly(t *testing.T) {
	unused := "func unusedHelper() int {\n\treturn 2\n}\n"
	repo, project := goProject(t, "")
	writeFile(t, project, "app/app.go", goFile(helper))
	commitAll(t, repo)

	writeFile(t, project, "app/app.go", goFile(helper, unused))
	runHarness(t, stopHook(), project, "", nil).assertBlocked(t, 2,
		"stop-hook failed: Lint", "app/app.go:9: unused: func unusedHelper is unused")

	commitAll(t, repo)
	writeFile(t, project, "app/app.go", goFile(helper, unused, "// Extra is new.\nconst Extra = 2\n"))
	runHarness(t, stopHook(), project, "", nil).assertSilent(t)
}

func TestStopHookToolFailureExits1Not2(t *testing.T) {
	repo, project := goProject(t, "")
	writeFile(t, project, "app/app.go", goFile(helper))
	commitAll(t, repo)
	writeFile(t, project, ".golangci.yaml", "version: \"2\"\nlinters:\n  default: bogus\n")
	writeFile(t, project, "app/app.go", goFile(helper, "// Extra is new.\nconst Extra = 2\n"))

	got := runHarness(t, stopHook(), project, "", nil)
	if got.code != 1 || got.stdout != "" ||
		!strings.HasPrefix(got.stderr, "stop-hook: Lint could not run: golangci-lint exited ") ||
		strings.Contains(got.stderr, "stop-hook failed") {
		t.Fatalf("expected a tool failure (exit 1), got %d\n--- stdout ---\n%s--- stderr ---\n%s", got.code, got.stdout, got.stderr)
	}
}

func TestPostEditHookAsksForAReRead(t *testing.T) {
	_, project := goProject(t, "")
	writeFile(t, project, "app/app.go", "// Package app is scratch.\npackage app\n\n// X is one.\nvar X=1\n")
	writeFile(t, project, "app/clean.go", "package app\n\n// Y is two.\nvar Y = 2\n")
	event := func(path string) string { return fmt.Sprintf(`{"tool_input":{"file_path":%q}}`, path) }
	template, err := filepath.Abs("harness.go")
	if err != nil {
		t.Fatal(err)
	}

	messy := runHarness(t, postEdit(), project, event(filepath.Join(project, "app", "app.go")), nil)
	want := `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":` +
		`"harness: reformatted app/app.go; re-read it before editing it again"}}` + "\n"
	if messy != (harnessRun{stdout: want}) {
		t.Fatalf("misformatted file: got %+v", messy)
	}
	if got := readFile(t, project, "app/app.go"); !strings.Contains(got, "var X = 1\n") {
		t.Fatalf("app/app.go not formatted:\n%s", got)
	}
	for _, stdin := range []string{event("app/clean.go"), event(template), "{not json"} {
		runHarness(t, postEdit(), project, stdin, nil).assertSilent(t)
	}
}

// docsProject is a git repo with CLAUDE.md and AGENTS.md committed in sync.
func docsProject(t *testing.T) string {
	t.Helper()
	repo := t.TempDir()
	mustRun(t, repo, exec.Command("git", "init", "-q", "-b", "main"))
	writeFile(t, repo, "CLAUDE.md", "v1\n")
	writeFile(t, repo, "AGENTS.md", "v1\n")
	commitAll(t, repo)
	return repo
}

func TestStopHookMirrorsAnUncommittedClaudeMd(t *testing.T) {
	repo := docsProject(t)
	writeFile(t, repo, "AGENTS.md", "v1 hand edit\n")
	writeFile(t, repo, "CLAUDE.md", "v2\n")
	runHarness(t, stopHook(), repo, "", nil).assertSilent(t)
	if got := readFile(t, repo, "AGENTS.md"); got != "v2\n" {
		t.Fatalf("AGENTS.md = %q, want v2", got)
	}

	// After the first sync AGENTS.md is itself modified; later edits still sync.
	writeFile(t, repo, "CLAUDE.md", "v3\n")
	mustRun(t, repo, exec.Command("git", "add", "CLAUDE.md"))
	runHarness(t, stopHook(), repo, "", nil).assertSilent(t)
	if got := readFile(t, repo, "AGENTS.md"); got != "v3\n" {
		t.Fatalf("AGENTS.md = %q, want v3", got)
	}
}

func TestStopHookLeavesAnAgentsMdOnlyEdit(t *testing.T) {
	repo := docsProject(t)
	writeFile(t, repo, "AGENTS.md", "hand edit\n")
	runHarness(t, stopHook(), repo, "", nil).assertSilent(t)
	if got := readFile(t, repo, "AGENTS.md"); got != "hand edit\n" {
		t.Fatalf("AGENTS.md = %q, want the hand edit", got)
	}
}

func TestPreCommitStagesTheMirrorOfAStagedClaudeMd(t *testing.T) {
	repo := docsProject(t)
	writeFile(t, repo, "CLAUDE.md", "v2\n")
	mustRun(t, repo, exec.Command("git", "add", "CLAUDE.md"))
	got := runHarness(t, preCommit(), repo, "", nil)
	staged := mustRun(t, repo, exec.Command("git", "diff", "--cached", "--name-only"))
	if got.code != 0 || staged != "AGENTS.md\nCLAUDE.md" || !strings.Contains(got.stdout, "AGENTS.md ← CLAUDE.md (staged)") {
		t.Fatalf("exit %d, staged %q\n%s", got.code, staged, got.stdout)
	}
}

func TestPreCommitFailsOnAHandEditedMirror(t *testing.T) {
	for name, withSource := range map[string]bool{"mirror alone": false, "mirror and source": true} {
		t.Run(name, func(t *testing.T) {
			repo := docsProject(t)
			writeFile(t, repo, "AGENTS.md", "hand edit\n")
			writeFile(t, repo, "main.go", "package main\n")
			mustRun(t, repo, exec.Command("git", "add", "AGENTS.md"))
			if withSource {
				mustRun(t, repo, exec.Command("git", "add", "main.go"))
			}
			got := runHarness(t, preCommit(), repo, "", nil)
			if got.code != 1 || !strings.Contains(got.stdout, "AGENTS.md differs from CLAUDE.md") {
				t.Fatalf("expected a drift failure, got %d\n%s", got.code, got.stdout)
			}
			if mirror := readFile(t, repo, "AGENTS.md"); mirror != "hand edit\n" {
				t.Fatalf("AGENTS.md = %q, want the hand edit", mirror)
			}
		})
	}
}

// testedProject is a lint-clean Go module whose one test runs body.
func testedProject(t *testing.T, body string) (repo string) {
	t.Helper()
	repo, _ = goProject(t, "")
	writeFile(t, repo, "CLAUDE.md", "docs\n")
	writeFile(t, repo, "AGENTS.md", "docs\n")
	writeFile(t, repo, "app/app.go", goFile(helper))
	writeFile(t, repo, "app/app_test.go", testFile(body))
	return repo
}

func testFile(body string) string {
	return "package app\n\nimport (\n\t\"os\"\n\t\"testing\"\n)\n\n" +
		"func TestHook(t *testing.T) {\n\t_ = os.Getenv(\"HOME\")\n\t" + body + "\n}\n"
}

func TestPreCommitNoLongerRunsTests(t *testing.T) {
	repo := testedProject(t, `t.Fatal("pre-commit must not run tests")`)
	mustRun(t, repo, exec.Command("git", "add", "-A"))
	got := runHarness(t, preCommit(), repo, "", nil)
	if got.code != 0 || strings.Contains(got.stdout, "Tests") {
		t.Fatalf("expected pre-commit to pass without tests, got %d\n%s", got.code, got.stdout)
	}
}

// TestPrePushRunsTestsWithoutGitHookEnv runs pre-push the way git does, with
// GIT_DIR exported. The suite leaves a marker when it runs, and fails if
// GIT_DIR reached it.
func TestPrePushRunsTestsWithoutGitHookEnv(t *testing.T) {
	repo := testedProject(t, "if err := os.WriteFile(\"../tests-ran\", []byte(\"ran\"), 0o600); err != nil {\n"+
		"\t\tt.Fatal(err)\n\t}\n\tif os.Getenv(\"GIT_DIR\") != \"\" {\n\t\tt.Fatal(\"GIT_DIR leaked into tests\")\n\t}")
	mustRun(t, repo, exec.Command("git", "checkout", "-q", "-b", "feature"))
	commitAll(t, repo)
	sha := mustRun(t, repo, exec.Command("git", "rev-parse", "HEAD"))
	env := hookEnv(
		"GIT_DIR="+filepath.Join(repo, ".git"),
		fmt.Sprintf("HARNESS_PRE_PUSH_REFS=refs/heads/feature %s refs/heads/feature %s", sha, strings.Repeat("0", 40)),
	)

	got := runHarness(t, prePush(), repo, "", env)
	if got.code != 0 || !strings.Contains(got.stdout, "Tests") {
		t.Fatalf("expected pre-push to pass with tests, got %d\n%s", got.code, got.stdout)
	}
	if marker := readFile(t, repo, "tests-ran"); marker != "ran" {
		t.Fatalf("tests-ran = %q, want the suite's marker", marker)
	}
}
