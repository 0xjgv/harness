// Package features runs Gherkin acceptance scenarios via godog.
//
// `harness acceptance` (and `harness ci`) invoke this through `go test`.
// godog 0.15+ is designed to run as a normal Go test, so no separate
// runner binary is needed — the scenarios live next to this file as
// `.feature` files and their steps under steps/.
package features

import (
	"os"
	"testing"

	"github.com/cucumber/godog"
	"github.com/cucumber/godog/colors"

	"harness/features/steps"
)

// TestFeatures executes every *.feature file in this directory.
func TestFeatures(t *testing.T) {
	suite := godog.TestSuite{
		ScenarioInitializer: steps.InitializeScenario,
		Options: &godog.Options{
			Format:   "pretty",
			Paths:    []string{"."},
			Output:   colors.Colored(os.Stdout),
			TestingT: t,
		},
	}
	if suite.Run() != 0 {
		t.Fatal("acceptance scenarios failed")
	}
}
