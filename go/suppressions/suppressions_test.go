package suppressions

import (
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
	if err := WriteBaseline(tmp, results); err != nil {
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
