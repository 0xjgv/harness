package steps

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/cucumber/godog"
)

// The fixture is a throwaway git project the harness binary runs inside:
// a module with two tested packages (pkga, pkgb) and one untested package
// (pkgc). Keep every file lint-clean — pre-commit's fix step shells out to
// golangci-lint and exits the process on failure.
const (
	scopingGoMod = `module scoped

go 1.24
`
	scopingSourceFmt = `package %s

// Value is the package's only export.
func Value() int {
	return %d
}
`
	scopingTestFmt = `package %s

import "testing"

func TestValue(t *testing.T) {
	if Value() == 0 {
		t.Fatal("unexpected zero")
	}
}
`
	// The harness checks AGENTS.md against CLAUDE.md byte-for-byte during
	// pre-commit, so the fixture ships both with identical content.
	scopingAgentsMd = "# Fixture\n"
	// The package every fixture ships with tests, so a scenario always has
	// one package the scoped gate could have run.
	scopingTestedPkg = "pkga"
	// A build-ignored root file, like the real harness.go: it belongs to no
	// package, so changing it maps to no `go test` target at all.
	scopingRunner = `//go:build ignore

package main

func main() {}
`
)

// scopingWorld builds the fixture repo for one scoping scenario.
type scopingWorld struct {
	dir string
}

// runInFixture runs a command built at the call site (literal arguments only,
// so no gosec suppression is needed) inside the fixture directory.
func (w *scopingWorld) runInFixture(c *exec.Cmd) error {
	c.Dir = w.dir
	if out, err := c.CombinedOutput(); err != nil {
		return fmt.Errorf("%v: %w\n%s", c.Args, err, out)
	}
	return nil
}

func (w *scopingWorld) writePackage(name string, withTest bool) error {
	dir := filepath.Join(w.dir, name)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return err
	}
	source := fmt.Sprintf(scopingSourceFmt, name, len(name))
	if err := os.WriteFile(filepath.Join(dir, name+".go"), []byte(source), 0o600); err != nil {
		return err
	}
	if !withTest {
		return nil
	}
	test := fmt.Sprintf(scopingTestFmt, name)
	return os.WriteFile(filepath.Join(dir, name+"_test.go"), []byte(test), 0o600)
}

// project creates the fixture, commits it, then stages an edit to one file.
// The commit matters: `git diff --cached` needs a HEAD to diff against, and a
// committed baseline is what makes the staged set exactly one file. `runner`
// adds a build-ignored root-level harness.go, the file that belongs to no
// package.
func (w *scopingWorld) project(changed string, packages map[string]bool, runner bool) error {
	d, err := os.MkdirTemp("", "scoping-")
	if err != nil {
		return err
	}
	w.dir = d
	sharedRun.dir = d
	write := func(name, content string) error {
		return os.WriteFile(filepath.Join(d, name), []byte(content), 0o600)
	}
	if err := write("go.mod", scopingGoMod); err != nil {
		return err
	}
	if err := write("CLAUDE.md", scopingAgentsMd); err != nil {
		return err
	}
	if err := write("AGENTS.md", scopingAgentsMd); err != nil {
		return err
	}
	for name, withTest := range packages {
		if err := w.writePackage(name, withTest); err != nil {
			return err
		}
	}
	if runner {
		if err := write("harness.go", scopingRunner); err != nil {
			return err
		}
	}
	if err := w.runInFixture(exec.Command("git", "init", "--quiet")); err != nil {
		return err
	}
	if err := w.runInFixture(exec.Command("git", "add", "-A")); err != nil {
		return err
	}
	if err := w.runInFixture(exec.Command("git",
		"-c", "user.name=t", "-c", "user.email=t@example.com",
		"commit", "--quiet", "-m", "fixture")); err != nil {
		return err
	}
	source, body := filepath.Join(changed, changed+".go"), ""
	if changed == "harness.go" {
		source, body = changed, scopingRunner+"\n// edited\n"
	} else {
		body = fmt.Sprintf(scopingSourceFmt, changed, len(changed)+1)
	}
	if err := os.WriteFile(filepath.Join(d, source), []byte(body), 0o600); err != nil {
		return err
	}
	return w.runInFixture(exec.Command("git", "add", "-A"))
}

func (w *scopingWorld) twoTestedPackages() error {
	return w.project(scopingTestedPkg, map[string]bool{scopingTestedPkg: true, "pkgb": true}, false)
}

func (w *scopingWorld) packageWithoutTests() error {
	return w.project("pkgc", map[string]bool{scopingTestedPkg: true, "pkgc": false}, false)
}

func (w *scopingWorld) runnerOnly() error {
	return w.project("harness.go", map[string]bool{scopingTestedPkg: true}, true)
}

// InitializeScopingScenario registers the test-scoping fixture steps. The
// steps that run the harness and assert on its output are registered once by
// InitializeHarnessRunScenario.
func InitializeScopingScenario(sc *godog.ScenarioContext) {
	w := &scopingWorld{}
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*w = scopingWorld{}
		return ctx, nil
	})
	sc.After(func(ctx context.Context, _ *godog.Scenario, _ error) (context.Context, error) {
		if w.dir != "" {
			_ = os.RemoveAll(w.dir)
		}
		return ctx, nil
	})
	sc.Step(`^a project with tests in two packages and a change staged in one$`, w.twoTestedPackages)
	sc.Step(`^a project with a change staged in a package that has no tests$`, w.packageWithoutTests)
	sc.Step(`^a project with a change staged in a build-ignored runner file only$`, w.runnerOnly)
}
