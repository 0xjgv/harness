package steps

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/cucumber/godog"
)

// shimScript is a stand-in golangci-lint: it answers `version` with the banner
// the real tool prints and treats every other invocation as a clean lint run,
// so these scenarios need no golangci-lint installed.
const shimScript = `#!/bin/sh
if [ "$1" = "version" ]; then
  echo "golangci-lint has version %s built with go1.24.0 from abc1234 on 2026-01-01T00:00:00Z"
fi
exit 0
`

// pinsWorld runs the real harness binary with a shimmed golangci-lint on PATH.
type pinsWorld struct {
	tmp      string
	shimDir  string
	output   string
	exitCode int
}

func (w *pinsWorld) golangciLintReportsVersion(version string) error {
	d, err := os.MkdirTemp("", "pins-")
	if err != nil {
		return err
	}
	w.tmp = d
	w.shimDir = filepath.Join(d, "bin")
	if err := os.Mkdir(w.shimDir, 0o750); err != nil {
		return err
	}
	shim := filepath.Join(w.shimDir, "golangci-lint")
	// 0o500 is the least a PATH shim can carry: owner read+execute, no write.
	//nolint:gosec // G306 rejects any execute bit; a shim that cannot run is useless.
	return os.WriteFile(shim, fmt.Appendf(nil, shimScript, version), 0o500)
}

func (w *pinsWorld) iRunAgainstThatGolangciLint(cmd string) error {
	shimPath := w.shimDir + string(os.PathListSeparator) + os.Getenv("PATH")
	out, code, err := runHarness(w.tmp, []string{"PATH=" + shimPath}, cmd)
	if err != nil {
		return err
	}
	w.output = out
	w.exitCode = code
	return nil
}

func (w *pinsWorld) shimmedExitCodeIs(code int) error {
	if w.exitCode != code {
		return fmt.Errorf("expected exit %d, got %d\n--- output ---\n%s", code, w.exitCode, w.output)
	}
	return nil
}

func (w *pinsWorld) shimmedOutputContains(text string) error {
	if !strings.Contains(w.output, text) {
		return fmt.Errorf("expected %q in output:\n%s", text, w.output)
	}
	return nil
}

func (w *pinsWorld) shimmedOutputDoesNotContain(text string) error {
	if strings.Contains(w.output, text) {
		return fmt.Errorf("unexpected %q in output:\n%s", text, w.output)
	}
	return nil
}

// InitializePinsScenario registers the pinned-tool step definitions with a
// fresh world per scenario. Called from features/acceptance_test.go alongside
// the smoke and crap initializers.
func InitializePinsScenario(sc *godog.ScenarioContext) {
	w := &pinsWorld{}
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*w = pinsWorld{}
		return ctx, nil
	})
	sc.After(func(ctx context.Context, _ *godog.Scenario, _ error) (context.Context, error) {
		if w.tmp != "" {
			_ = os.RemoveAll(w.tmp)
		}
		return ctx, nil
	})
	sc.Step(`^golangci-lint on PATH reports version "([^"]+)"$`, w.golangciLintReportsVersion)
	sc.Step(`^I run "([^"]+)" against that golangci-lint$`, w.iRunAgainstThatGolangciLint)
	sc.Step(`^the shimmed exit code is (\d+)$`, w.shimmedExitCodeIs)
	sc.Step(`^the shimmed output contains "([^"]+)"$`, w.shimmedOutputContains)
	sc.Step(`^the shimmed output does not contain "([^"]+)"$`, w.shimmedOutputDoesNotContain)
}
