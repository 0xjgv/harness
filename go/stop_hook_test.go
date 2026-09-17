package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
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

// runHarness runs a runner invocation in dir, with stdin (none when empty) and
// env (hookEnv when nil).
func runHarness(t *testing.T, cmd *exec.Cmd, dir, stdin string, env []string) harnessRun {
	t.Helper()
	cmd.Dir, cmd.Env = dir, env
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

func (r harnessRun) assert(t *testing.T, want harnessRun) {
	t.Helper()
	if r != want {
		t.Fatalf("got exit %d\n--- stdout ---\n%s--- stderr ---\n%s\nwant %+v", r.code, r.stdout, r.stderr, want)
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

// goProject is a git repo whose Go module (in subdir) uses this template's
// lint config and docs.
func goProject(t *testing.T, subdir string) (repo, project string) {
	t.Helper()
	repo = t.TempDir()
	mustRun(t, repo, exec.Command("git", "init", "-q", "-b", "main"))
	project = filepath.Join(repo, subdir)
	writeFile(t, project, "go.mod", "module scratch\n\ngo 1.24\n")
	writeFile(t, project, ".golangci.yaml", readFile(t, ".", ".golangci.yaml"))
	writeFile(t, project, "CLAUDE.md", "docs\n")
	writeFile(t, project, "AGENTS.md", "docs\n")
	return repo, project
}

// goFile is a lint-clean package app source holding decls.
func goFile(decls ...string) string {
	return "// Package app is scratch.\npackage app\n\n" + strings.Join(decls, "\n")
}

const helper = "// Helper returns one.\nfunc Helper() int {\n\treturn 1\n}\n"

// busyFunc is an exported function whose CCN is 21.
func busyFunc(name string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "// %s branches.\nfunc %s(value int) int {\n", name, name)
	for i := range 20 {
		fmt.Fprintf(&b, "\tif value == %d {\n\t\treturn %d\n\t}\n", i, i)
	}
	b.WriteString("\treturn -1\n}\n")
	return b.String()
}

func TestStopHookCleanTreeIsSilent(t *testing.T) {
	repo, project := goProject(t, "")
	writeFile(t, project, "app/app.go", goFile(helper))
	commitAll(t, repo)
	runHarness(t, exec.Command(harnessBin, "stop-hook"), project, "", nil).assert(t, harnessRun{})
}

// TestStopHookBlocksOnlyTouchedComplexity: the project sits in a subdirectory,
// the change is committed on a branch, and neither the untouched over-limit
// function nor a misformatted file in another package blocks or changes.
func TestStopHookBlocksOnlyTouchedComplexity(t *testing.T) {
	repo, project := goProject(t, "proj")
	misformatted := "// Package other is scratch.\npackage other\n\n// X is one.\nvar X=1\n"
	writeFile(t, project, "other/other.go", misformatted)
	writeFile(t, project, "app/app.go", goFile(busyFunc("Legacy"), helper))
	commitAll(t, repo)
	mustRun(t, repo, exec.Command("git", "update-ref", "refs/remotes/origin/main", "HEAD"))
	mustRun(t, repo, exec.Command("git", "checkout", "-q", "-b", "feature"))
	writeFile(t, project, "app/app.go", goFile(busyFunc("Legacy"), helper, busyFunc("Fresh")))
	commitAll(t, repo)

	stderr := "stop-hook failed: Complexity\napp/app.go:75: Fresh CCN 21 (limit 15)\n"
	runHarness(t, exec.Command(harnessBin, "stop-hook"), project, "", nil).assert(t, harnessRun{code: 2, stderr: stderr})
	runHarness(t, exec.Command(harnessBin, "stop-hook"), project, `{"stop_hook_active": true}`, nil).assert(t, harnessRun{
		code: 1, stderr: stderr + "harness: already blocked once on this stop; not blocking again\n",
	})
	if got := readFile(t, project, "other/other.go"); got != misformatted {
		t.Fatalf("post-edit touched a file outside the change:\n%s", got)
	}
}

func TestPostEditHookAsksForAReRead(t *testing.T) {
	_, project := goProject(t, "")
	writeFile(t, project, "app/app.go", "// Package app is scratch.\npackage app\n\n// X is one.\nvar X=1\n")
	writeFile(t, project, "app/clean.go", "package app\n\n// Y is two.\nvar Y = 2\n")
	event := func(path string) string { return fmt.Sprintf(`{"tool_input":{"file_path":%q}}`, path) }

	target := event(filepath.Join(project, "app", "app.go"))
	runHarness(t, exec.Command(harnessBin, "post-edit", "--hook"), project, target, nil).assert(t, harnessRun{
		stdout: `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":` +
			`"harness: reformatted app/app.go; re-read it before editing it again"}}` + "\n",
	})
	if got := readFile(t, project, "app/app.go"); !strings.Contains(got, "var X = 1\n") {
		t.Fatalf("app/app.go not formatted:\n%s", got)
	}
	for _, stdin := range []string{event("app/clean.go"), "{not json"} {
		runHarness(t, exec.Command(harnessBin, "post-edit", "--hook"), project, stdin, nil).assert(t, harnessRun{})
	}
}

func TestPreCommitStagesTheMirrorOfAStagedClaudeMd(t *testing.T) {
	repo, _ := goProject(t, "")
	commitAll(t, repo)
	writeFile(t, repo, "CLAUDE.md", "v2\n")
	mustRun(t, repo, exec.Command("git", "add", "CLAUDE.md"))
	got := runHarness(t, exec.Command(harnessBin, "pre-commit"), repo, "", nil)
	staged := mustRun(t, repo, exec.Command("git", "diff", "--cached", "--name-only"))
	if got.code != 0 || staged != "AGENTS.md\nCLAUDE.md" || readFile(t, repo, "AGENTS.md") != "v2\n" {
		t.Fatalf("exit %d, staged %q\n%s", got.code, staged, got.stdout)
	}
}

// TestPrePushRunsTestsWithoutGitHookEnv runs pre-push the way git does, with
// GIT_DIR exported. The suite leaves a marker when it runs, and fails if
// GIT_DIR reached it.
func TestPrePushRunsTestsWithoutGitHookEnv(t *testing.T) {
	repo, _ := goProject(t, "")
	writeFile(t, repo, "app/app.go", goFile(helper))
	writeFile(t, repo, "app/app_test.go", "package app\n\nimport (\n\t\"os\"\n\t\"testing\"\n)\n\n"+
		"func TestHook(t *testing.T) {\n\tif err := os.WriteFile(\"../tests-ran\", []byte(\"ran\"), 0o600); err != nil {\n"+
		"\t\tt.Fatal(err)\n\t}\n\tif os.Getenv(\"GIT_DIR\") != \"\" {\n\t\tt.Fatal(\"GIT_DIR leaked into tests\")\n\t}\n}\n")
	mustRun(t, repo, exec.Command("git", "checkout", "-q", "-b", "feature"))
	commitAll(t, repo)
	sha := mustRun(t, repo, exec.Command("git", "rev-parse", "HEAD"))
	env := hookEnv(
		"GIT_DIR="+filepath.Join(repo, ".git"),
		fmt.Sprintf("HARNESS_PRE_PUSH_REFS=refs/heads/feature %s refs/heads/feature %s", sha, strings.Repeat("0", 40)),
	)

	got := runHarness(t, exec.Command(harnessBin, "pre-push"), repo, "", env)
	if got.code != 0 || !strings.Contains(got.stdout, "Tests") || readFile(t, repo, "tests-ran") != "ran" {
		t.Fatalf("expected pre-push to pass with tests, got %d\n%s", got.code, got.stdout)
	}
}
