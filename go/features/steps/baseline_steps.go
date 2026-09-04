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

// duplicateGo is a synthetic source carrying the same 19-line body twice.
// lizard only reports a block once it spans 70+ unified tokens, so the body
// has to be this long for the copy to register as one duplicate block.
func duplicateGo() string {
	body := `func %s(values []int) string {
	total := 0
	count := 0
	for _, v := range values {
		if v < 0 {
			continue
		}
		total += v
		count++
		if v > 100 {
			total -= 100
		}
	}
	average := 0
	if count > 0 {
		average = total / count
	}
	return fmt.Sprintf("total=%%d count=%%d average=%%d", total, count, average)
}
`
	return "package main\n\nimport \"fmt\"\n\n" +
		fmt.Sprintf(body, "ReportAlpha") + "\n" + fmt.Sprintf(body, "ReportBeta")
}

func (w *crapWorld) duplicatedBlock() error {
	return w.writeFixture("duplicate.go", duplicateGo())
}

// ensureTmp makes the scenario's temp module unless a prior Given already did.
func (w *crapWorld) ensureTmp() error {
	if w.tmp != "" {
		return nil
	}
	return w.makeTmp()
}

// writeFixture drops one fixture file into the scenario's temp module, making
// the module first unless a prior Given already did.
func (w *crapWorld) writeFixture(name, content string) error {
	if err := w.ensureTmp(); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.tmp, name), []byte(content), 0o600)
}

func (w *crapWorld) complexFunction() error {
	return w.writeFixture("complex.go", complexGo())
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
	return w.writeFixture("stub_test.go", stubTestGo)
}

func (w *crapWorld) baselineWith(content string) error {
	return w.writeFixture(".harness-baseline", content+"\n")
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
	sc.Step(`^a duplicated block of code$`, w.duplicatedBlock)
	sc.Step(`^a \.harness-baseline recording (\d+) complexity violations$`, w.baselineRecordsComplexity)
	sc.Step(`^a \.harness-baseline carrying "([^"]+)"$`, w.baselineWith)
	sc.Step(`^the \.harness-baseline file contains "([^"]+)"$`, w.baselineFileContains)
	sc.Step(`^the \.harness-baseline file does not contain "([^"]+)"$`, w.baselineFileDoesNotContain)
}
