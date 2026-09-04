package steps

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/cucumber/godog"
)

// gremlinsReport is the shape gremlins' `-o` report has at v0.5.0, cut down
// to the three counts the gate reads. Written as a fixture so the mutation
// scenarios exercise the floor comparison without paying minutes for a real
// mutation run — `harness mutation --report=` scores exactly this file.
const gremlinsReport = `{
  "go_module": "test",
  "test_efficacy": %s,
  "mutations_coverage": 100,
  "mutants_total": %d,
  "mutants_killed": %d,
  "mutants_lived": %d,
  "mutants_not_viable": 0,
  "mutants_not_covered": 2,
  "elapsed_time": 1.5
}
`

// efficacyOf mirrors gremlins' own killed/(killed+lived)*100, so the fixture
// stays internally consistent with the field the runner deliberately ignores.
func efficacyOf(killed, lived int) string {
	if killed+lived == 0 {
		return "0"
	}
	return fmt.Sprintf("%.2f", 100*float64(killed)/float64(killed+lived))
}

func (w *crapWorld) gremlinsReport(killed, lived int) error {
	if err := w.ensureTmp(); err != nil {
		return err
	}
	body := fmt.Sprintf(gremlinsReport, efficacyOf(killed, lived), killed+lived, killed, lived)
	return os.WriteFile(filepath.Join(w.tmp, "gremlins-report.json"), []byte(body), 0o600)
}

// initializeMutationSteps registers the mutation-gate steps against the same
// per-scenario world as the CRAP and baseline steps, so all three feature
// files share one set of `I run` / `exit code` / `output contains` definitions.
func initializeMutationSteps(sc *godog.ScenarioContext, w *crapWorld) {
	sc.Step(`^a gremlins report with (\d+) killed and (\d+) surviving mutants$`, w.gremlinsReport)
}
