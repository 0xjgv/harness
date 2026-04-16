package suppressions

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

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
			want: []Match{{Kind: "nolint", Rules: nil}},
		},
		{
			name: "nolint with rules",
			line: "x := 1 // nolint: errcheck, gosec",
			want: []Match{{Kind: "nolint", Rules: []string{"errcheck", "gosec"}}},
		},
		{
			name: "lint ignore with rule",
			line: "const Foo = 1 // lint:ignore CONST_ASSIGN",
			want: []Match{{Kind: "lint_ignore", Rules: []string{"CONST_ASSIGN"}}},
		},
		{
			name: "nolint with whitespace in rules",
			line: "x := 1 // nolint: foo  ,  bar  ",
			want: []Match{{Kind: "nolint", Rules: []string{"foo", "bar"}}},
		},
		{
			name: "both kinds on one line",
			line: "x := 1 // nolint: errcheck // lint:ignore FOO",
			want: []Match{
				{Kind: "nolint", Rules: []string{"errcheck"}},
				{Kind: "lint_ignore", Rules: []string{"FOO"}},
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

	wantNolint := [][]string{{"errcheck"}}
	if !reflect.DeepEqual(results["nolint"], wantNolint) {
		t.Errorf("results[nolint] = %+v, want %+v", results["nolint"], wantNolint)
	}

	wantLintIgnore := [][]string{{"CONST_ASSIGN"}}
	if !reflect.DeepEqual(results["lint_ignore"], wantLintIgnore) {
		t.Errorf("results[lint_ignore] = %+v, want %+v", results["lint_ignore"], wantLintIgnore)
	}
}
