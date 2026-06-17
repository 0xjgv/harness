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
			want: []Match{{Kind: kindLintIgnore, Rules: []string{"CONST_ASSIGN"}}},
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
				{Kind: kindLintIgnore, Rules: []string{"FOO"}},
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

	wantLintIgnore := [][]string{{"CONST_ASSIGN"}}
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
			kindLintIgnore: {{"FOO"}},
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
