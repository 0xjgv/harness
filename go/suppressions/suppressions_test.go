package suppressions

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const ruleErrcheck = "errcheck"
const ruleFoo = "FOO"
const ruleConstAssign = "CONST_ASSIGN"

func TestParseLine(t *testing.T) {
	tests := []struct {
		name string
		line string
		want []Match
	}{
		{
			name: "plain code no match",
			line: "x := 1",
			want: nil,
		},
		{
			name: "bare nolint",
			line: "x := 1 // nolint",
			want: []Match{{Kind: kindNolint, Rules: nil}},
		},
		{
			name: "nolint with rules",
			line: "x := 1 // nolint: errcheck, gosec",
			want: []Match{{Kind: kindNolint, Rules: []string{ruleErrcheck, "gosec"}}},
		},
		{
			name: "lint ignore with rule",
			line: "const Foo = 1 // lint:ignore CONST_ASSIGN",
			want: []Match{{Kind: kindLintIgnore, Rules: []string{ruleConstAssign}}},
		},
		{
			name: "nolint with whitespace in rules",
			line: "x := 1 // nolint: foo  ,  bar  ",
			want: []Match{{Kind: kindNolint, Rules: []string{"foo", "bar"}}},
		},
		{
			name: "both kinds on one line",
			line: "x := 1 // nolint: errcheck // lint:ignore FOO",
			want: []Match{
				{Kind: kindNolint, Rules: []string{ruleErrcheck}},
				{Kind: kindLintIgnore, Rules: []string{ruleFoo}},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ParseLine(tt.line)
			if !reflect.DeepEqual(got, tt.want) {
				t.Errorf("ParseLine(%q) = %+v, want %+v", tt.line, got, tt.want)
			}
		})
	}
}

func TestScan(t *testing.T) {
	tmp := t.TempDir()

	goFile := filepath.Join(tmp, "a.go")
	if err := os.WriteFile(goFile, []byte(
		"x := 1 // nolint: errcheck\n"+
			"const Foo = 1 // lint:ignore CONST_ASSIGN\n"+
			"y := 2\n",
	), 0o600); err != nil {
		t.Fatal(err)
	}

	txtFile := filepath.Join(tmp, "skip.txt")
	if err := os.WriteFile(txtFile, []byte("// nolint\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	results := Scan(tmp)

	wantNolint := [][]string{{ruleErrcheck}}
	if !reflect.DeepEqual(results[kindNolint], wantNolint) {
		t.Errorf("results[nolint] = %+v, want %+v", results[kindNolint], wantNolint)
	}

	wantLintIgnore := [][]string{{ruleConstAssign}}
	if !reflect.DeepEqual(results[kindLintIgnore], wantLintIgnore) {
		t.Errorf("results[lint_ignore] = %+v, want %+v", results[kindLintIgnore], wantLintIgnore)
	}
}

// captureStdout runs fn and returns everything it wrote to os.Stdout.
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	orig := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = w
	fn()
	_ = w.Close()
	os.Stdout = orig
	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	return string(out)
}

func TestPrintReport(t *testing.T) {
	t.Run("empty", func(t *testing.T) {
		out := captureStdout(t, func() { PrintReport(map[string][][]string{}) })
		if !strings.Contains(out, "Suppressions: 0 total") {
			t.Errorf("expected zero-total line, got: %q", out)
		}
	})

	t.Run("populated", func(t *testing.T) {
		results := map[string][][]string{
			kindNolint:     {{ruleErrcheck}, {ruleErrcheck, "gosec"}},
			kindLintIgnore: {{ruleFoo}},
		}
		out := captureStdout(t, func() { PrintReport(results) })
		for _, want := range []string{
			"Suppressions: 3 total",
			"nolint: 2",
			"errcheck: 2",
			"lint_ignore: 1",
		} {
			if !strings.Contains(out, want) {
				t.Errorf("expected %q in report, got: %q", want, out)
			}
		}
	})
}

func TestBaselineReadWrite(t *testing.T) {
	tmp := t.TempDir()
	if err := os.WriteFile(filepath.Join(tmp, ".harness-baseline"), []byte(
		"suppressions.nolint 2\ncoverage.min 65\n",
	), 0o600); err != nil {
		t.Fatal(err)
	}

	got, ok := ReadBaseline(tmp)
	if !ok {
		t.Fatal("expected baseline to be read")
	}
	if got["suppressions.nolint"] != 2 || got["coverage.min"] != 65 {
		t.Fatalf("ReadBaseline() = %#v", got)
	}

	results := map[string][][]string{
		kindNolint:     {{ruleErrcheck}},
		kindLintIgnore: {{ruleFoo}},
	}
	if _, err := WriteBaseline(tmp, results, nil); err != nil {
		t.Fatal(err)
	}
	updated, ok := ReadBaseline(tmp)
	if !ok {
		t.Fatal("expected updated baseline")
	}
	if updated["suppressions.nolint"] != 1 ||
		updated["suppressions.lint_ignore"] != 1 ||
		updated["coverage.min"] != 65 {
		t.Fatalf("updated baseline = %#v", updated)
	}
}

func writeBaselineFile(t *testing.T, dir, content string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, ".harness-baseline"), []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func measurer(key string, m Measurement) Measurer {
	return Measurer{Key: key, Measure: func() Measurement { return m }}
}

func TestWriteBaselineMergesOverUnknownKeys(t *testing.T) {
	tmp := t.TempDir()
	writeBaselineFile(t, tmp, "custom.thing 7\nmutation.min 40\ncrap.max_violations 9\n")
	measurers := []Measurer{
		measurer("complexity.max_violations", Measured(3)),
		measurer("crap.max_violations", Unavailable("no tests")),
	}

	var written map[string]int
	var err error
	out := captureStdout(t, func() {
		written, err = WriteBaseline(tmp, map[string][][]string{kindNolint: {{ruleErrcheck}}}, measurers)
	})
	if err != nil {
		t.Fatal(err)
	}

	// Unknown keys preserved, measured key written, inapplicable key dropped with a warning.
	if written["custom.thing"] != 7 || written["mutation.min"] != 40 {
		t.Fatalf("unknown keys not preserved: %#v", written)
	}
	if written["complexity.max_violations"] != 3 {
		t.Fatalf("measured key not written: %#v", written)
	}
	if _, has := written["crap.max_violations"]; has {
		t.Fatalf("unavailable key kept: %#v", written)
	}
	if !strings.Contains(out, "crap.max_violations: dropped") {
		t.Fatalf("expected drop warning, got: %q", out)
	}
	want := "complexity.max_violations 3\ncustom.thing 7\nmutation.min 40\nsuppressions.lint_ignore 0\nsuppressions.nolint 1\n"
	if content := formatBaseline(written); content != want {
		t.Fatalf("baseline = %q, want %q", content, want)
	}
}

func TestWriteBaselineZeroesAVanishedSuppressionKind(t *testing.T) {
	tmp := t.TempDir()
	writeBaselineFile(t, tmp, "suppressions.nolint 5\n")

	written, err := WriteBaseline(tmp, map[string][][]string{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if value, has := written["suppressions.nolint"]; !has || value != 0 {
		t.Fatalf("vanished kind not recorded as 0: %#v", written)
	}
}

func TestWriteBaselineRecordsALegitimateZero(t *testing.T) {
	tmp := t.TempDir()

	written, err := WriteBaseline(tmp, nil, []Measurer{measurer("complexity.max_violations", Measured(0))})
	if err != nil {
		t.Fatal(err)
	}
	if value, has := written["complexity.max_violations"]; !has || value != 0 {
		t.Fatalf("measured 0 not recorded: %#v", written)
	}
}

func TestWriteBaselineAbortsAndWritesNothingOnAFailedMeasurement(t *testing.T) {
	tmp := t.TempDir()
	original := "coverage.min 40\n"
	writeBaselineFile(t, tmp, original)
	measurers := []Measurer{
		measurer("complexity.max_violations", Failed("lizard failed to run (exit 2)")),
		measurer("crap.max_violations", Measured(1)),
	}

	written, err := WriteBaseline(tmp, nil, measurers)

	var mErr *MeasurementError
	if !errors.As(err, &mErr) {
		t.Fatalf("expected *MeasurementError, got %v", err)
	}
	if mErr.Key != "complexity.max_violations" || !strings.Contains(mErr.Reason, "exit 2") {
		t.Fatalf("unexpected error: %#v", mErr)
	}
	if written != nil {
		t.Fatalf("expected no baseline on failure, got %#v", written)
	}
	after, _ := ReadBaseline(tmp)
	if len(after) != 1 || after["coverage.min"] != 40 {
		t.Fatalf("baseline file changed on failure: %#v", after)
	}
}

func TestMeasureRatchetedStopsAtTheFirstFailure(t *testing.T) {
	calls := 0
	count := func(m Measurement) func() Measurement {
		return func() Measurement { calls++; return m }
	}
	measured := MeasureRatcheted([]Measurer{
		{Key: "a", Measure: count(Measured(1))},
		{Key: "b", Measure: count(Failed("boom"))},
		{Key: "c", Measure: count(Measured(3))},
	})

	if calls != 2 || len(measured) != 2 || measured[1].Key != "b" || measured[1].Error != "boom" {
		t.Fatalf("calls=%d measured=%#v", calls, measured)
	}
}

func TestBaselineFloor(t *testing.T) {
	tmp := t.TempDir()
	if _, ok := BaselineFloor(tmp, "complexity.max_violations"); ok {
		t.Fatal("expected no floor without a baseline file")
	}
	writeBaselineFile(t, tmp, "coverage.min 0\ncomplexity.max_violations 12\n")
	if _, ok := BaselineFloor(tmp, "crap.max_violations"); ok {
		t.Fatal("expected no floor for an absent key")
	}
	if floor, ok := BaselineFloor(tmp, "complexity.max_violations"); !ok || floor != 12 {
		t.Fatalf("BaselineFloor = %d, %v", floor, ok)
	}
}

func TestCheckBaselineDetectsGrowth(t *testing.T) {
	tmp := t.TempDir()
	if err := os.WriteFile(filepath.Join(tmp, ".harness-baseline"), []byte("coverage.min 0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	findings := []Finding{{Match: Match{Kind: kindNolint}, Location: "a.go:1"}}

	if CheckBaseline(tmp, findings, true, "go run harness.go suppressions --update-baseline", false) {
		t.Fatal("expected suppression growth to fail")
	}
}
