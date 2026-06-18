package steps

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/cucumber/godog"
)

// Function with CCN 9 — paired with hits=0 lines this scores CRAP = 90,
// well above --max=0. Placed in a synthetic Go module under a tmp dir so
// the harness sees it via `uvx lizard` + `go tool cover`.
const stubGo = `package main

func Stub(n int) int {
	if n < 1 {
		return 0
	}
	if n < 2 {
		return 1
	}
	if n < 3 {
		return 2
	}
	if n < 4 {
		return 3
	}
	if n < 5 {
		return 4
	}
	if n < 6 {
		return 5
	}
	if n < 7 {
		return 6
	}
	if n < 8 {
		return 7
	}
	return 8
}

func main() {}
`

const stubGoMod = `module test

go 1.22
`

// `go tool cover -func=` reads this and reports 0% coverage for the Stub
// function. Filename is the module-qualified path; line/column ranges
// only need to exist in the underlying source for `go tool cover` to print
// the function entry the harness joins on.
const stubCoverOut = `mode: set
test/stub.go:3.27,4.10 1 0
test/stub.go:4.10,6.3 1 0
test/stub.go:7.10,9.3 1 0
test/stub.go:10.10,12.3 1 0
test/stub.go:13.10,15.3 1 0
test/stub.go:16.10,18.3 1 0
test/stub.go:19.10,21.3 1 0
test/stub.go:22.10,24.3 1 0
test/stub.go:25.10,27.3 1 0
test/stub.go:28.2,28.10 1 0
`

// crapWorld carries state across one scenario.
type crapWorld struct {
	tmp      string
	exitCode int
	output   string
}

// Build the harness binary once per test run. `harness.go` carries
// `//go:build ignore`, so `go test` does not build it transitively — we
// shell out to `go build` ourselves and reuse the artifact across scenarios.
var (
	harnessBin     string
	errHarnessBin  error
	harnessBinOnce sync.Once
)

func buildHarness() (string, error) {
	harnessBinOnce.Do(func() {
		_, file, _, ok := runtime.Caller(0)
		if !ok {
			errHarnessBin = fmt.Errorf("cannot locate harness source")
			return
		}
		// features/steps/crap_steps.go → go template root is two levels up.
		goRoot := filepath.Dir(filepath.Dir(filepath.Dir(file)))
		bin, err := os.CreateTemp("", "harness-bin-*")
		if err != nil {
			errHarnessBin = err
			return
		}
		_ = bin.Close()
		_ = os.Remove(bin.Name())
		//nolint:gosec // test fixture builds the local harness with fixed argv.
		cmd := exec.Command("go", "build", "-o", bin.Name(), "harness.go")
		cmd.Dir = goRoot
		if out, err := cmd.CombinedOutput(); err != nil {
			errHarnessBin = fmt.Errorf("go build: %w\n%s", err, out)
			return
		}
		harnessBin = bin.Name()
	})
	return harnessBin, errHarnessBin
}

func (w *crapWorld) makeTmp() error {
	d, err := os.MkdirTemp("", "crap-")
	if err != nil {
		return err
	}
	w.tmp = d
	if err := os.WriteFile(filepath.Join(d, "go.mod"), []byte(stubGoMod), 0o600); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(d, "stub.go"), []byte(stubGo), 0o600)
}

func (w *crapWorld) artifactPresent() error {
	if err := w.makeTmp(); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.tmp, "coverage.out"), []byte(stubCoverOut), 0o600)
}

func (w *crapWorld) artifactMissing() error {
	return w.makeTmp()
}

func (w *crapWorld) iRun(cmd string) error {
	bin, err := buildHarness()
	if err != nil {
		return err
	}
	// Drop leading "harness" — the rest is forwarded to the harness binary.
	parts := strings.Fields(cmd)
	if len(parts) > 0 && parts[0] == "harness" {
		parts = parts[1:]
	}
	//nolint:gosec // test fixture invokes the local harness binary with scenario arguments.
	c := exec.Command(bin, parts...)
	c.Dir = w.tmp
	out, _ := c.CombinedOutput()
	w.output = string(out)
	w.exitCode = c.ProcessState.ExitCode()
	return nil
}

func (w *crapWorld) exitCodeIs(code int) error {
	if w.exitCode != code {
		return fmt.Errorf("expected exit %d, got %d\n--- output ---\n%s", code, w.exitCode, w.output)
	}
	return nil
}

func (w *crapWorld) outputContains(text string) error {
	if !strings.Contains(w.output, text) {
		return fmt.Errorf("expected %q in output:\n%s", text, w.output)
	}
	return nil
}

func (w *crapWorld) outputDoesNotContain(text string) error {
	if strings.Contains(w.output, text) {
		return fmt.Errorf("unexpected %q in output:\n%s", text, w.output)
	}
	return nil
}

// InitializeCrapScenario registers crap step definitions with a fresh world
// per scenario. Called from features/acceptance_test.go alongside the smoke
// scenario initializer.
func InitializeCrapScenario(sc *godog.ScenarioContext) {
	w := &crapWorld{}
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*w = crapWorld{}
		return ctx, nil
	})
	sc.After(func(ctx context.Context, _ *godog.Scenario, _ error) (context.Context, error) {
		if w.tmp != "" {
			_ = os.RemoveAll(w.tmp)
		}
		return ctx, nil
	})
	sc.Step(`^a coverage artifact for a high-CCN, zero-coverage function$`, w.artifactPresent)
	sc.Step(`^no coverage artifact$`, w.artifactMissing)
	sc.Step(`^I run "([^"]+)"$`, w.iRun)
	sc.Step(`^the exit code is (\d+)$`, w.exitCodeIs)
	sc.Step(`^the output contains "([^"]+)"$`, w.outputContains)
	sc.Step(`^the output does not contain "([^"]+)"$`, w.outputDoesNotContain)
}
