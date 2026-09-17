//go:build ignore

// Unit tests for the pure helpers behind the stop hook and post-edit hook.
//
// harness.go is `//go:build ignore`, so these tests compile only with the files
// named on the command line: `go test harness.go stop_hook_unit_test.go`.
// TestHarnessUnits in stop_hook_test.go runs that command, which keeps them
// under `go test ./...`.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const sampleDiff = "diff --git a/app/app.go b/app/app.go\n" +
	"index 1..2 100644\n" +
	"--- a/app/app.go\n" +
	"+++ b/app/app.go\n" +
	"@@ -1,0 +2 @@ x\n" +
	"+y\n" +
	"@@ -10,2 +11,3 @@ func f() {\n" +
	"+++ looks like a header but is an added line\n" +
	"+b\n" +
	"+c\n" +
	"@@ -20,4 +22,0 @@\n" +
	"-gone\n" +
	"diff --git a/old.go b/old.go\n" +
	"deleted file mode 100644\n" +
	"--- a/old.go\n" +
	"+++ /dev/null\n" +
	"@@ -1,2 +0,0 @@\n" +
	"-x\n" +
	"diff --git a/sp ace.go b/sp ace.go\n" +
	"new file mode 100644\n" +
	"--- /dev/null\n" +
	"+++ b/sp ace.go\t\n" +
	"@@ -0,0 +1,4 @@\n" +
	"+a\n" +
	"diff --git a/only_deleted.go b/only_deleted.go\n" +
	"--- a/only_deleted.go\n" +
	"+++ b/only_deleted.go\n" +
	"@@ -3 +2,0 @@\n" +
	"-x\n"

func TestParseDiffRanges(t *testing.T) {
	cases := map[string]changedLines{
		sampleDiff: {
			"app/app.go":      {{2, 2}, {11, 13}},
			"sp ace.go":       {{1, 4}},
			"only_deleted.go": {},
		},
		"": {},
		"diff --git a/q.go b/q.go\n--- a/q.go\n+++ \"b/q.go\"\n@@ -1 +1 @@\n": {"q.go": {{1, 1}}},
	}
	for diff, want := range cases {
		if got := parseDiffRanges(diff); !reflect.DeepEqual(got, want) {
			t.Errorf("parseDiffRanges(%.40q) = %v, want %v", diff, got, want)
		}
	}
}

func TestTouchedOverLimit(t *testing.T) {
	functions := []funcMetric{
		{file: "a.go", name: "busy", line: 10, end: 110, ccn: 16, args: 9},
		{file: "a.go", name: "legacy", line: 120, end: 130, ccn: 20},
		{file: "a.go", line: 140, end: 150, ccn: 16},
		{file: "b.go", name: "atLimit", line: 1, end: 100, ccn: 15, args: 8},
	}
	scope := changedLines{"a.go": {{110, 110}, {145, 160}}, "b.go": wholeFile}
	want := []string{
		"a.go:10: busy CCN 16 (limit 15)",
		"a.go:10: busy args 9 (limit 8)",
		"a.go:10: busy length 101 (limit 100)",
		"a.go:140: (anonymous) CCN 16 (limit 15)",
	}
	if got := touchedOverLimit(functions, scope); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestStopHookPayloadCapsFindings(t *testing.T) {
	lint := make([]string, 23)
	for i := range lint {
		lint[i] = fmt.Sprintf("a.go:%d: x", i+1)
	}
	results := []deltaResult{
		{gate: "Lint", findings: lint},
		{gate: "Broken", err: os.ErrNotExist},
		{gate: "Complexity", findings: []string{"b.go:1: busy CCN 16 (limit 15)"}},
	}
	lines := strings.Split(stopHookPayload(results), "\n")
	if len(lines) != 22 || lines[0] != "stop-hook failed: Lint, Complexity" || lines[20] != "a.go:20: x" ||
		lines[21] != "… +4 more — run `go run harness.go stop-hook --verbose`" {
		t.Fatalf("payload = %q", lines)
	}
	verbose = true
	defer func() { verbose = false }()
	if got := strings.Count(stopHookPayload(results), "\n"); got != 24 {
		t.Fatalf("--verbose payload has %d newlines, want 24", got)
	}
	if got := stopHookPayload(results[1:2]); got != "" {
		t.Fatalf("a failed tool alone has no payload: %q", got)
	}
}

func TestStopHookExit(t *testing.T) {
	cases := []struct {
		payload string
		failed  int
		active  bool
		want    int
	}{
		{"", 0, false, 0},
		{"", 0, true, 0},
		{"", 1, false, 1},
		{"", 1, true, 1},
		{"P", 0, false, 2},
		{"P", 1, false, 2}, // a crashed gate never hides another gate's findings
		{"P", 0, true, 1},  // already blocked once on this stop
		{"P", 1, true, 1},
	}
	for _, c := range cases {
		if got := stopHookExit(c.payload, c.failed, c.active); got != c.want {
			t.Errorf("stopHookExit(%q, %d, %v) = %d, want %d", c.payload, c.failed, c.active, got, c.want)
		}
	}
}

func TestHookTarget(t *testing.T) {
	base, outside := t.TempDir(), t.TempDir()
	for _, path := range []string{"app/app.go", "app/data.txt", "testdata/tool.go"} {
		path = filepath.Join(base, path)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(outside, "x.go"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	cases := map[string]string{
		filepath.Join(base, "app", "app.go"):     "app/app.go",
		"app/app.go":                             "app/app.go",
		"app/missing.go":                         "",
		"app/data.txt":                           "",
		"testdata/tool.go":                       "",
		filepath.Join(outside, "x.go"):           "",
		"../" + filepath.Base(outside) + "/x.go": "",
		"":                                       "",
	}
	for filePath, want := range cases {
		if got := hookTarget(filePath, base); got != want {
			t.Errorf("hookTarget(%q) = %q, want %q", filePath, got, want)
		}
	}
}
