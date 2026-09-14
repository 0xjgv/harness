package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// harnessBin is the compiled task runner. harness.go carries `//go:build
// ignore`, so its functions cannot be imported and the guards are exercised
// through the binary instead. The artifact is built next to the source
// (matching the gitignored `*.test` pattern) and removed afterwards.
var harnessBin string

func TestMain(m *testing.M) {
	build := exec.Command("go", "build", "-o", "branch-guard.test", "harness.go")
	if out, err := build.CombinedOutput(); err != nil {
		fmt.Fprintf(os.Stderr, "go build harness.go: %v\n%s", err, out)
		os.Exit(1)
	}
	wd, err := os.Getwd()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	harnessBin = filepath.Join(wd, "branch-guard.test")
	code := m.Run()
	_ = os.Remove(harnessBin)
	os.Exit(code)
}

// childEnv drops the harness overrides the developer running the suite may have
// exported, so a scenario only sees the ones it sets itself.
func childEnv(extra []string) []string {
	stripped := map[string]bool{
		"HARNESS_ALLOW_PROTECTED_PUSH": true,
		"HARNESS_ALLOW_ARCH_CONFIG":    true,
		"HARNESS_PRE_PUSH_REFS":        true,
	}
	var env []string
	for _, entry := range os.Environ() {
		if name, _, ok := strings.Cut(entry, "="); ok && stripped[name] {
			continue
		}
		env = append(env, entry)
	}
	return append(env, extra...)
}

// runBranchGuard runs `harness branch-guard` in dir. An empty stdin string
// leaves the child's stdin at /dev/null — the "no pre-push refs" case.
func runBranchGuard(t *testing.T, dir, stdin string, env []string) (int, string) {
	t.Helper()
	cmd := exec.Command(harnessBin, "branch-guard")
	cmd.Dir = dir
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	cmd.Env = childEnv(env)
	out, _ := cmd.CombinedOutput()
	return cmd.ProcessState.ExitCode(), string(out)
}

func assertGuard(t *testing.T, code int, out string, wantCode int, wantText string) {
	t.Helper()
	if code != wantCode {
		t.Fatalf("expected exit %d, got %d\n--- output ---\n%s", wantCode, code, out)
	}
	if !strings.Contains(out, wantText) {
		t.Fatalf("expected %q in output:\n%s", wantText, out)
	}
}

// Repeated scenario fixtures: the line git sends for a push onto main, and the
// two lines the guard prints.
const (
	pushToMain   = "refs/heads/topic abc123 refs/heads/main def456\n"
	guardPassed  = "Branch guard"
	guardRefused = "Push targets protected branch: main"
)

func TestBranchGuardRefs(t *testing.T) {
	zero := strings.Repeat("0", 40)
	tests := []struct {
		name     string
		stdin    string
		env      []string
		wantCode int
		wantText string
	}{
		{
			name:     "push to main is refused",
			stdin:    pushToMain,
			wantCode: 1,
			wantText: guardRefused,
		},
		{
			name:     "push to a feature branch passes",
			stdin:    "refs/heads/topic abc123 refs/heads/feature/topic def456\n",
			wantCode: 0,
			wantText: guardPassed,
		},
		{
			name:     "deleting main is refused",
			stdin:    "(delete) " + zero + " refs/heads/main def456\n",
			wantCode: 1,
			wantText: guardRefused,
		},
		{
			name:     "deleting a feature branch passes",
			stdin:    "(delete) " + zero + " refs/heads/feature/topic def456\n",
			wantCode: 0,
			wantText: guardPassed,
		},
		{
			name:     "a tag named main is not a branch",
			stdin:    "refs/tags/main abc123 refs/tags/main def456\n",
			wantCode: 0,
			wantText: guardPassed,
		},
		{
			name:     "human override allows the push",
			stdin:    pushToMain,
			env:      []string{"HARNESS_ALLOW_PROTECTED_PUSH=1"},
			wantCode: 0,
			wantText: "Branch guard override: main",
		},
		{
			name:     "refs from the environment are used",
			env:      []string{"HARNESS_PRE_PUSH_REFS=refs/heads/topic abc123 refs/heads/main def456"},
			wantCode: 1,
			wantText: guardRefused,
		},
		{
			name:     "environment refs win over stdin",
			stdin:    pushToMain,
			env:      []string{"HARNESS_PRE_PUSH_REFS=refs/heads/topic abc123 refs/heads/feature/topic def456"},
			wantCode: 0,
			wantText: guardPassed,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			code, out := runBranchGuard(t, t.TempDir(), tt.stdin, tt.env)
			assertGuard(t, code, out, tt.wantCode, tt.wantText)
		})
	}
}

// TestBranchGuardIncompleteRefs holds the pipe open after a partial write: the
// guard must refuse rather than guess from half a push's refs.
func TestBranchGuardIncompleteRefs(t *testing.T) {
	cmd := exec.Command(harnessBin, "branch-guard")
	cmd.Dir = t.TempDir()
	cmd.Env = childEnv(nil)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	pipe, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if _, err := pipe.Write([]byte("refs/heads/topic abc123 refs/heads/feature/topic def456")); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	_ = cmd.Wait()
	elapsed := time.Since(start)
	_ = pipe.Close()
	assertGuard(t, cmd.ProcessState.ExitCode(), out.String(), 1, "Pre-push refs incomplete after 1s")
	if elapsed > 5*time.Second {
		t.Fatalf("guard waited %s for an open pipe", elapsed)
	}
}

func mustRun(t *testing.T, dir string, cmd *exec.Cmd) string {
	t.Helper()
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %v\n%s", cmd.Args, err, out)
	}
	return strings.TrimSpace(string(out))
}

// commitAll stages everything and commits it. Identity is passed per command so
// the test never depends on the developer's git config.
func commitAll(t *testing.T, dir string) {
	t.Helper()
	mustRun(t, dir, exec.Command("git", "add", "-A"))
	mustRun(t, dir, exec.Command("git",
		"-c", "user.email=harness@example.com", "-c", "user.name=harness",
		"-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "wip"))
}

func TestBranchGuardFallsBackToCurrentBranch(t *testing.T) {
	protected := t.TempDir()
	mustRun(t, protected, exec.Command("git", "init", "-b", "main"))
	commitAll(t, protected)
	code, out := runBranchGuard(t, protected, "", nil)
	assertGuard(t, code, out, 1, guardRefused)

	feature := t.TempDir()
	mustRun(t, feature, exec.Command("git", "init", "-b", "feature/topic"))
	commitAll(t, feature)
	code, out = runBranchGuard(t, feature, "", nil)
	assertGuard(t, code, out, 0, guardPassed)
}

// TestArchConfigGuardScansWholeNewBranch pushes a branch the remote has never
// seen: the arch config changed two commits back, so a tip-commit diff would
// miss it and the whole-branch diff must not.
func TestArchConfigGuardScansWholeNewBranch(t *testing.T) {
	dir := t.TempDir()
	mustRun(t, dir, exec.Command("git", "init", "-b", "topic"))
	if err := os.WriteFile(filepath.Join(dir, "README.md"), []byte("root\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	commitAll(t, dir)
	mustRun(t, dir, exec.Command("git", "update-ref", "refs/remotes/origin/main", "HEAD"))

	if err := os.WriteFile(filepath.Join(dir, ".go-arch-lint.yml"), []byte("version: 3\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	commitAll(t, dir)
	if err := os.WriteFile(filepath.Join(dir, "unrelated.txt"), []byte("noise\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	commitAll(t, dir)

	localSha := mustRun(t, dir, exec.Command("git", "rev-parse", "HEAD"))
	refs := fmt.Sprintf("HARNESS_PRE_PUSH_REFS=refs/heads/topic %s refs/heads/topic %s",
		localSha, strings.Repeat("0", 40))

	cmd := exec.Command(harnessBin, "arch-config-guard", "--pre-push")
	cmd.Dir = dir
	cmd.Env = childEnv([]string{refs})
	out, _ := cmd.CombinedOutput()
	assertGuard(t, cmd.ProcessState.ExitCode(), string(out), 1, "Arch config changed: .go-arch-lint.yml")
}

// TestArchConfigGuardDetectsDeletion ensures a branch that deletes the
// protected config is caught: `--diff-filter=d` (which hides deletions) must
// not be in any of the arch-config detection diffs. The config is committed,
// origin/main pinned at that commit, then a new branch deletes it and adds an
// unrelated commit on top — the guard must still report the deletion.
func TestArchConfigGuardDetectsDeletion(t *testing.T) {
	dir := t.TempDir()
	mustRun(t, dir, exec.Command("git", "init", "-b", "main"))
	if err := os.WriteFile(filepath.Join(dir, ".go-arch-lint.yml"), []byte("version: 3\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	commitAll(t, dir)
	mustRun(t, dir, exec.Command("git", "update-ref", "refs/remotes/origin/main", "HEAD"))

	mustRun(t, dir, exec.Command("git", "checkout", "-b", "topic"))
	if err := os.Remove(filepath.Join(dir, ".go-arch-lint.yml")); err != nil {
		t.Fatal(err)
	}
	commitAll(t, dir)
	if err := os.WriteFile(filepath.Join(dir, "unrelated.txt"), []byte("noise\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	commitAll(t, dir)

	localSha := mustRun(t, dir, exec.Command("git", "rev-parse", "HEAD"))
	refs := fmt.Sprintf("HARNESS_PRE_PUSH_REFS=refs/heads/topic %s refs/heads/topic %s",
		localSha, strings.Repeat("0", 40))

	cmd := exec.Command(harnessBin, "arch-config-guard", "--pre-push")
	cmd.Dir = dir
	cmd.Env = childEnv([]string{refs})
	out, _ := cmd.CombinedOutput()
	assertGuard(t, cmd.ProcessState.ExitCode(), string(out), 1, "Arch config changed: .go-arch-lint.yml")
}

// TestPreCommitWarnsOnArchConfigOnlyStage covers G2: the arch-config warn
// must fire even when the protected config is the only staged file — before
// the "no staged Go files" early return.
func TestPreCommitWarnsOnArchConfigOnlyStage(t *testing.T) {
	dir := t.TempDir()
	mustRun(t, dir, exec.Command("git", "init", "-b", "main"))
	if err := os.WriteFile(filepath.Join(dir, ".go-arch-lint.yml"), []byte("version: 3\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	mustRun(t, dir, exec.Command("git", "add", ".go-arch-lint.yml"))

	cmd := exec.Command(harnessBin, "pre-commit")
	cmd.Dir = dir
	cmd.Env = childEnv(nil)
	out, _ := cmd.CombinedOutput()
	assertGuard(t, cmd.ProcessState.ExitCode(), string(out), 0, "Arch config changed")
	if !strings.Contains(string(out), "No staged Go files") {
		t.Fatalf("expected the early-return message after the arch warning:\n%s", out)
	}
}

// TestPrePushRefusesBeforeOtherGates covers G3: on a protected branch with no
// pre-push refs (git passes none on `git push --dry-run` from a tty-less
// runner), the guard must print only the refusal and exit 1 — no arch guard,
// no drift, no gate batch output.
func TestPrePushRefusesBeforeOtherGates(t *testing.T) {
	dir := t.TempDir()
	mustRun(t, dir, exec.Command("git", "init", "-b", "main"))
	commitAll(t, dir)

	cmd := exec.Command(harnessBin, "pre-push")
	cmd.Dir = dir
	cmd.Stdin = nil // no pre-push refs at all: falls back to the current branch
	cmd.Env = childEnv(nil)
	out, _ := cmd.CombinedOutput()
	assertGuard(t, cmd.ProcessState.ExitCode(), string(out), 1, guardRefused)
	if strings.Contains(string(out), "Arch config guard") || strings.Contains(string(out), "Lint") ||
		strings.Contains(string(out), "agents-md-drift") || strings.Contains(string(out), "Acceptance") {
		t.Fatalf("expected only the refusal, other gates ran too:\n%s", out)
	}
}
