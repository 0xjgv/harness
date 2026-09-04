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

// harnessRun is the state every scenario that executes the harness binary
// shares: the fixture directory to run it in, and the last run's output and
// exit code. One instance is registered once, because godog resolves a step
// against the *first* matching definition — registering `^I run "…"$` from two
// initializers would silently bind every feature to whichever registered
// first. A feature's own Given steps build their fixture and point dir at it.
type harnessRun struct {
	dir      string
	output   string
	exitCode int
}

var sharedRun = &harnessRun{}

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
		// features/steps/harness_run.go → go template root is two levels up.
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

// RemoveHarnessBin deletes the binary buildHarness compiled, if any. Without
// it every acceptance run leaves a multi-megabyte file behind in TMPDIR.
func RemoveHarnessBin() {
	if harnessBin != "" {
		_ = os.Remove(harnessBin)
	}
}

func (h *harnessRun) iRun(cmd string) error {
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
	c.Dir = h.dir
	out, _ := c.CombinedOutput()
	h.output = string(out)
	h.exitCode = c.ProcessState.ExitCode()
	return nil
}

func (h *harnessRun) exitCodeIs(code int) error {
	if h.exitCode != code {
		return fmt.Errorf("expected exit %d, got %d\n--- output ---\n%s", code, h.exitCode, h.output)
	}
	return nil
}

func (h *harnessRun) outputContains(text string) error {
	if !strings.Contains(h.output, text) {
		return fmt.Errorf("expected %q in output:\n%s", text, h.output)
	}
	return nil
}

func (h *harnessRun) outputDoesNotContain(text string) error {
	if strings.Contains(h.output, text) {
		return fmt.Errorf("unexpected %q in output:\n%s", text, h.output)
	}
	return nil
}

// InitializeHarnessRunScenario registers the steps that run the harness binary
// and assert on its result, shared by every feature that drives the harness.
func InitializeHarnessRunScenario(sc *godog.ScenarioContext) {
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*sharedRun = harnessRun{}
		return ctx, nil
	})
	sc.Step(`^I run "([^"]+)"$`, sharedRun.iRun)
	sc.Step(`^the exit code is (\d+)$`, sharedRun.exitCodeIs)
	sc.Step(`^the output contains "([^"]+)"$`, sharedRun.outputContains)
	sc.Step(`^the output does not contain "([^"]+)"$`, sharedRun.outputDoesNotContain)
}
