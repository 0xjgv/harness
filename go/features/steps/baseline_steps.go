package steps

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/cucumber/godog"
)

// complexGo is a synthetic source whose only function has CCN 21 — above the
// template's threshold of 15, so lizard flags exactly one violation in the
// scenario's temp module.
func complexGo() string {
	var b strings.Builder
	b.WriteString("package main\n\nfunc Complex(n int) int {\n")
	for i := range 20 {
		fmt.Fprintf(&b, "\tif n == %d {\n\t\treturn %d\n\t}\n", i, i)
	}
	b.WriteString("\treturn -1\n}\n")
	return b.String()
}

// ensureTmp makes the scenario's temp module unless a prior Given already did.
func (w *crapWorld) ensureTmp() error {
	if w.tmp != "" {
		return nil
	}
	return w.makeTmp()
}

func (w *crapWorld) complexFunction() error {
	if err := w.ensureTmp(); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.tmp, "complex.go"), []byte(complexGo()), 0o600)
}

// stubTestGo covers part of Stub, so the temp module has a real coverage
// percentage for `suppressions --update-baseline` to floor and record.
const stubTestGo = `package main

import "testing"

func TestStub(t *testing.T) {
	if Stub(0) != 0 {
		t.Fatal("Stub(0)")
	}
}
`

func (w *crapWorld) stubTest() error {
	if err := w.ensureTmp(); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.tmp, "stub_test.go"), []byte(stubTestGo), 0o600)
}

func (w *crapWorld) baselineWith(content string) error {
	if err := w.ensureTmp(); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.tmp, ".harness-baseline"), []byte(content+"\n"), 0o600)
}

func (w *crapWorld) baselineRecordsComplexity(count int) error {
	return w.baselineWith(fmt.Sprintf("complexity.max_violations %d", count))
}

func (w *crapWorld) baselineFile() (string, error) {
	data, err := os.ReadFile(filepath.Join(w.tmp, ".harness-baseline"))
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func (w *crapWorld) baselineFileContains(text string) error {
	content, err := w.baselineFile()
	if err != nil {
		return err
	}
	if !strings.Contains(content, text) {
		return fmt.Errorf("expected %q in .harness-baseline:\n%s", text, content)
	}
	return nil
}

func (w *crapWorld) baselineFileDoesNotContain(text string) error {
	content, err := w.baselineFile()
	if err != nil {
		return err
	}
	if strings.Contains(content, text) {
		return fmt.Errorf("unexpected %q in .harness-baseline:\n%s", text, content)
	}
	return nil
}

// initializeBaselineSteps registers the ratcheted-baseline steps against the
// same per-scenario world as the CRAP steps, so both feature files share one
// set of `I run` / `exit code` / `output contains` definitions.
func initializeBaselineSteps(sc *godog.ScenarioContext, w *crapWorld) {
	sc.Step(`^a function above the complexity threshold$`, w.complexFunction)
	sc.Step(`^a test that exercises the stub function$`, w.stubTest)
	sc.Step(`^a \.harness-baseline recording (\d+) complexity violations$`, w.baselineRecordsComplexity)
	sc.Step(`^a \.harness-baseline carrying "([^"]+)"$`, w.baselineWith)
	sc.Step(`^the \.harness-baseline file contains "([^"]+)"$`, w.baselineFileContains)
	sc.Step(`^the \.harness-baseline file does not contain "([^"]+)"$`, w.baselineFileDoesNotContain)
}
