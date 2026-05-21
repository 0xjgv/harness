// Package steps holds godog step definitions for acceptance features.
//
// Add new step files here as scenarios grow; register them from
// InitializeScenario so the acceptance runner picks them up.
package steps

import (
	"context"
	"fmt"
	"os"

	"github.com/cucumber/godog"

	"harness/suppressions"
)

// smokeWorld carries state across the steps of a single scenario.
type smokeWorld struct {
	dir   string
	count int
}

func (w *smokeWorld) anEmptyDirectory() error {
	d, err := os.MkdirTemp("", "smoke")
	if err != nil {
		return err
	}
	w.dir = d
	return nil
}

func (w *smokeWorld) theSuppressionsScannerRuns() error {
	results := suppressions.Scan(w.dir)
	for _, v := range results {
		w.count += len(v)
	}
	return nil
}

func (w *smokeWorld) itReportsZeroSuppressions() error {
	if w.count != 0 {
		return fmt.Errorf("expected 0 suppressions, got %d", w.count)
	}
	return nil
}

// InitializeScenario registers step definitions with a fresh world per scenario.
func InitializeScenario(sc *godog.ScenarioContext) {
	w := &smokeWorld{}
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*w = smokeWorld{}
		return ctx, nil
	})
	sc.Step(`^an empty directory$`, w.anEmptyDirectory)
	sc.Step(`^the suppressions scanner runs$`, w.theSuppressionsScannerRuns)
	sc.Step(`^it reports zero suppressions$`, w.itReportsZeroSuppressions)
}
