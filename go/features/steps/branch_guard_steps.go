package steps

import (
	"context"
	"fmt"
	"os"
	"strings"

	"github.com/cucumber/godog"
)

// branchGuardWorld carries state across one scenario. Step phrasings are
// deliberately distinct from the crap steps so godog binds them to this world.
type branchGuardWorld struct {
	tmp      string
	refLine  string
	exitCode int
	output   string
}

// refTargeting builds a git pre-push stdin line
// (`<local ref> <local sha> <remote ref> <remote sha>`) for the given remote ref.
func (w *branchGuardWorld) refTargeting(remoteRef string) error {
	return w.ref("refs/heads/topic abc123 " + remoteRef + " def456")
}

// deletionTargeting builds the line git sends when a branch is deleted: the
// local sha is all zeros.
func (w *branchGuardWorld) deletionTargeting(remoteRef string) error {
	return w.ref(fmt.Sprintf("(delete) %s %s def456", strings.Repeat("0", 40), remoteRef))
}

func (w *branchGuardWorld) ref(line string) error {
	d, err := os.MkdirTemp("", "branch-guard-")
	if err != nil {
		return err
	}
	w.tmp = d
	w.refLine = line + "\n"
	return nil
}

func (w *branchGuardWorld) guardRuns() error {
	code, out, err := runHarnessBin(w.tmp, w.refLine, "branch-guard")
	if err != nil {
		return err
	}
	w.output = out
	w.exitCode = code
	return nil
}

func (w *branchGuardWorld) guardExits(code int) error {
	if w.exitCode != code {
		return fmt.Errorf("expected exit %d, got %d\n--- output ---\n%s", code, w.exitCode, w.output)
	}
	return nil
}

func (w *branchGuardWorld) guardOutputContains(text string) error {
	if !strings.Contains(w.output, text) {
		return fmt.Errorf("expected %q in output:\n%s", text, w.output)
	}
	return nil
}

// InitializeBranchGuardScenario registers branch-guard step definitions with a
// fresh world per scenario. Called from features/acceptance_test.go.
func InitializeBranchGuardScenario(sc *godog.ScenarioContext) {
	w := &branchGuardWorld{}
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*w = branchGuardWorld{}
		return ctx, nil
	})
	sc.After(func(ctx context.Context, _ *godog.Scenario, _ error) (context.Context, error) {
		if w.tmp != "" {
			_ = os.RemoveAll(w.tmp)
		}
		return ctx, nil
	})
	sc.Step(`^a pre-push ref targeting "([^"]+)"$`, w.refTargeting)
	sc.Step(`^a pre-push deletion of "([^"]+)"$`, w.deletionTargeting)
	sc.Step(`^the branch guard runs$`, w.guardRuns)
	sc.Step(`^the branch guard exits (\d+)$`, w.guardExits)
	sc.Step(`^the branch guard output contains "([^"]+)"$`, w.guardOutputContains)
}
