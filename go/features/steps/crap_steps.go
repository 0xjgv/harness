package steps

import (
	"context"
	"os"
	"path/filepath"

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

// crapWorld carries state across one scenario. The harness run itself lives
// in sharedRun (harness_run.go) so the `I run …` / `the exit code is …` steps
// are registered exactly once across all features.
type crapWorld struct {
	tmp string
}

func (w *crapWorld) makeTmp() error {
	d, err := os.MkdirTemp("", "crap-")
	if err != nil {
		return err
	}
	w.tmp = d
	sharedRun.dir = d
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
}
