//go:build ignore

// Unit tests for the pure helpers behind the stop hook and post-edit hook.
//
// harness.go is `//go:build ignore`, so these tests compile only with the files
// named on the command line: `go test harness.go stop_hook_unit_test.go`.
// TestHarnessUnits in stop_hook_test.go runs that command, which keeps them
// under `go test ./...`.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"pgregory.net/rapid"
)

// ── Changed lines ──

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
	want := changedLines{
		"app/app.go":      {{2, 2}, {11, 13}},
		"sp ace.go":       {{1, 4}},
		"only_deleted.go": {},
	}
	if got := parseDiffRanges(sampleDiff); !reflect.DeepEqual(got, want) {
		t.Fatalf("parseDiffRanges = %v, want %v", got, want)
	}
}

func TestParseDiffRangesHunkHeadersRoundTrip(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := rapid.IntRange(0, 20).Draw(t, "hunks")
		var body strings.Builder
		want := []lineRange{}
		for i := range n {
			start := rapid.IntRange(1, 10_000).Draw(t, fmt.Sprintf("start%d", i))
			count := rapid.IntRange(0, 50).Draw(t, fmt.Sprintf("count%d", i))
			fmt.Fprintf(&body, "@@ -1 +%d,%d @@\n", start, count)
			if count > 0 {
				want = append(want, lineRange{start, start + count - 1})
			}
		}
		diff := "diff --git a/f.go b/f.go\n--- a/f.go\n+++ b/f.go\n" + body.String()
		if got := parseDiffRanges(diff); !reflect.DeepEqual(got, changedLines{"f.go": want}) {
			t.Fatalf("parseDiffRanges = %v, want %v", got, want)
		}
	})
}

func TestGoSourceTargets(t *testing.T) {
	cases := []struct {
		path                     string
		source, lint, complexity bool
	}{
		{"app/app.go", true, true, true},
		{"app/app_test.go", true, true, false},
		{"harness.go", true, false, false},
		{"tools/harness.go", true, true, true},
		{"app/testdata/x.go", false, false, false},
		{".worktrees/x/app.go", false, false, false},
		{"_scratch/app.go", false, false, false},
		{"vendor/m/m.go", false, false, false},
		{"README.md", false, false, false},
	}
	for _, c := range cases {
		got := [3]bool{isGoSource(c.path), isLintTarget(c.path), isComplexityTarget(c.path)}
		if want := [3]bool{c.source, c.lint, c.complexity}; got != want {
			t.Errorf("%s: source/lint/complexity = %v, want %v", c.path, got, want)
		}
	}
}

func TestPackageDirs(t *testing.T) {
	got := packageDirs([]string{"b/x.go", "a.go", "b/y_test.go", "b/c/z.go"})
	if want := []string{".", "./b", "./b/c"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("packageDirs = %v, want %v", got, want)
	}
}

func TestPorcelainPath(t *testing.T) {
	for line, want := range map[string]string{
		" M app/app.go":             "app/app.go",
		"?? new.go":                 "new.go",
		"R  old.go -> app/moved.go": "app/moved.go",
	} {
		if got := porcelainPath(line); got != want {
			t.Errorf("porcelainPath(%q) = %q, want %q", line, got, want)
		}
	}
}

// ── Complexity delta ──

const sampleLizardCSV = "NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end\n" +
	`3,1,66,3,4,"Run@97-100@app/a.go","app/a.go","Run","(s*Server)Run a int , b string",97,100` + "\n" +
	`2,1,13,1,2,"ok@4-5@app/a.go","app/a.go","ok","ok v int",4,5` + "\n" +
	`2,2,13,1,2,"ok@9-10@app/a.go","app/a.go","ok","ok v int",9,10` + "\n" +
	"garbage\n"

func TestParseLizardCSVKeysByLongNameAndNumbersRepeats(t *testing.T) {
	functions := parseLizardCSV(sampleLizardCSV)["app/a.go"]
	var keys []string
	for _, fn := range functions {
		keys = append(keys, fn.key)
	}
	if want := []string{"(s*Server)Run a int , b string", "ok v int", "ok v int#2"}; !reflect.DeepEqual(keys, want) {
		t.Fatalf("keys = %q, want %q", keys, want)
	}
	want := funcMetric{
		file: "app/a.go", key: "(s*Server)Run a int , b string", name: "Run",
		line: 97, end: 100, ccn: 1, args: 3, length: 4,
	}
	if functions[0] != want {
		t.Fatalf("first function = %+v, want %+v", functions[0], want)
	}
	if functions[2].ccn != 2 {
		t.Fatalf("repeat ccn = %d, want 2", functions[2].ccn)
	}
}

// busy is a function measured by lizard; key and name default to one function.
func busy(ccn, args, length, line int) funcMetric {
	return funcMetric{key: "busy v int", name: "busy", ccn: ccn, args: args, length: length, line: line}
}

func withKey(m funcMetric, key string) funcMetric {
	m.key = key
	return m
}

func deltaOf(now funcMetric, was ...funcMetric) []string {
	return complexityDelta(map[string][]funcMetric{"a.go": {now}}, map[string][]funcMetric{"a.go": was})
}

func TestComplexityDelta(t *testing.T) {
	cases := []struct {
		name string
		got  []string
		want []string
	}{
		{"new function over the limit", deltaOf(busy(17, 1, 5, 3)), []string{"a.go:3: busy CCN new→17 (limit 15)"}},
		{"worse than base", deltaOf(busy(17, 1, 5, 1), busy(14, 1, 5, 1)), []string{"a.go:1: busy CCN 14→17 (limit 15)"}},
		{"worse over an over-limit base", deltaOf(busy(21, 1, 5, 1), busy(20, 1, 5, 1)), []string{"a.go:1: busy CCN 20→21 (limit 15)"}},
		{"unchanged", deltaOf(busy(20, 1, 5, 1), busy(20, 1, 5, 1)), nil},
		{"better", deltaOf(busy(18, 1, 5, 1), busy(20, 1, 5, 1)), nil},
		{"at the limit", deltaOf(busy(15, 1, 5, 1)), nil},
		{"args and length", deltaOf(busy(1, 9, 101, 1)), []string{
			"a.go:1: busy args new→9 (limit 8)",
			"a.go:1: busy length new→101 (limit 100)",
		}},
		{"signature edit falls back to the unique name", deltaOf(withKey(busy(20, 1, 5, 1), "busy v int64"), busy(20, 1, 5, 1)), nil},
		{"anonymous function", deltaOf(funcMetric{key: " x int", ccn: 16, line: 7}), []string{"a.go:7: (anonymous) CCN new→16 (limit 15)"}},
	}
	for _, c := range cases {
		if !reflect.DeepEqual(c.got, c.want) {
			t.Errorf("%s: got %q, want %q", c.name, c.got, c.want)
		}
	}
}

func TestComplexityDeltaAmbiguousNameCountsAsNew(t *testing.T) {
	current := map[string][]funcMetric{"a.go": {
		withKey(busy(20, 1, 5, 1), "busy v int64"),
		withKey(busy(2, 1, 5, 9), "(s*S)busy"),
	}}
	base := map[string][]funcMetric{"a.go": {busy(20, 1, 5, 1)}}
	got := complexityDelta(current, base)
	if want := []string{"a.go:1: busy CCN new→20 (limit 15)"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

// ── Lint residue ──

func TestGolangciFindingsKeepChangedLinesAndCompileErrors(t *testing.T) {
	base := t.TempDir()
	for _, rel := range []string{"app/a.go", "app/new.go"} {
		if err := os.MkdirAll(filepath.Join(base, "app"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(base, rel), nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	scope := changedLines{"app/a.go": {{2, 3}}, "app/new.go": wholeFile}
	issue := func(linter, file string, line int, text string) map[string]any {
		return map[string]any{"FromLinter": linter, "Text": text,
			"Pos": map[string]any{"Filename": filepath.Join(base, file), "Line": line}}
	}
	report, err := json.Marshal(map[string]any{"Issues": []any{
		issue("unused", "app/a.go", 3, "func helper is unused"),
		issue("unused", "app/a.go", 9, "old"),
		issue("gocyclo", "app/a.go", 3, "cyclomatic complexity 16 of func `busy` is high (> 15)"),
		issue("revive", "app/new.go", 1, "exported: comment\nspans lines"),
		issue("typecheck", "other/b.go", 1, ": # scratch/other\nother/b.go:7:2: undefined: foo\nother/b.go:9:1: missing return"),
	}})
	if err != nil {
		t.Fatal(err)
	}
	got, err := golangciFindings(report, scope, base)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"app/a.go:3: unused: func helper is unused",
		"app/new.go:1: revive: exported: comment spans lines",
		"other/b.go:7: typecheck: undefined: foo",
		"other/b.go:9: typecheck: missing return",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestGolangciFindingsCleanAndUnreadable(t *testing.T) {
	if got, err := golangciFindings([]byte(`{"Issues":null}`), changedLines{}, "."); err != nil || got != nil {
		t.Fatalf("clean report: got %q, %v", got, err)
	}
	for _, report := range []string{"not json", `{"Issues":[{"Text":"x"}]}`} {
		if _, err := golangciFindings([]byte(report), changedLines{}, "."); err == nil {
			t.Errorf("%s: expected an error", report)
		}
	}
}

func TestRunTool(t *testing.T) {
	if _, err := runTool("nope", []string{"/nonexistent/harness-tool"}, []int{0}, ""); err == nil ||
		!strings.HasPrefix(err.Error(), "nope not runnable") {
		t.Fatalf("missing tool: got %v", err)
	}
	cmd := []string{"sh", "-c", `printf 'a\nboom\n' >&2; exit 4`}
	if _, err := runTool("sh", cmd, []int{0}, ""); err == nil || err.Error() != "sh exited 4: boom" {
		t.Fatalf("unexpected exit: got %v", err)
	}
	if out, err := runTool("sh", cmd, []int{4}, ""); err != nil || out != "" {
		t.Fatalf("accepted exit: got %q, %v", out, err)
	}
}

// ── Stop-hook verdict ──

func numbered(n int, format string) []string {
	lines := make([]string, n)
	for i := range lines {
		lines[i] = fmt.Sprintf(format, i+1)
	}
	return lines
}

func TestStopHookPayloadNamesFailedGatesAndCapsFindings(t *testing.T) {
	lines := strings.Split(stopHookPayload([]deltaResult{
		{gate: "Lint", findings: numbered(23, "a.go:%d: E1 x")},
		{gate: "Empty"},
		{gate: "Complexity", findings: []string{"b.go:1: busy CCN new→16 (limit 15)"}},
	}), "\n")
	if lines[0] != "stop-hook failed: Lint, Complexity" {
		t.Fatalf("header = %q", lines[0])
	}
	if len(lines) != 22 || lines[20] != "a.go:20: E1 x" {
		t.Fatalf("payload = %q", lines)
	}
	if want := "… +4 more — run `go run harness.go stop-hook --verbose`"; lines[21] != want {
		t.Fatalf("more line = %q, want %q", lines[21], want)
	}
}

func TestCapFindings(t *testing.T) {
	twenty := numbered(20, "a.go:%d: x")
	if got := capFindings(twenty); !reflect.DeepEqual(got, twenty) {
		t.Fatalf("twenty findings need no more line: %q", got)
	}
	thirty := numbered(30, "a.go:%d: x")
	verbose = true
	defer func() { verbose = false }()
	if got := capFindings(thirty); !reflect.DeepEqual(got, thirty) {
		t.Fatalf("--verbose lifts the cap: %q", got)
	}
}

func TestCleanResultsHaveNoPayload(t *testing.T) {
	if got := stopHookPayload([]deltaResult{{gate: "Lint"}, {gate: "Complexity", problem: "boom"}}); got != "" {
		t.Fatalf("payload = %q", got)
	}
}

func TestStopHookExit(t *testing.T) {
	digest := payloadDigest("P")
	active := map[string]any{"stop_hook_active": true}
	none := map[string]any{}
	cases := []struct {
		payload string
		failed  int
		event   map[string]any
		stored  string
		want    int
	}{
		{"", 0, none, "", 0},
		{"", 1, none, "", 1},
		{"P", 0, none, "", 2},
		{"P", 1, none, "", 2}, // a crashed gate never hides another gate's findings
		{"P", 0, active, "", 2},
		{"P", 0, none, digest, 2}, // a new turn blocks again on the same findings
		{"P", 0, active, digest, 1},
		{"Q", 0, active, digest, 2}, // the findings changed: block again
		{"P", 0, map[string]any{"stop_hook_active": "true"}, digest, 2},
	}
	for _, c := range cases {
		if got := stopHookExit(c.payload, c.failed, c.event, c.stored); got != c.want {
			t.Errorf("stopHookExit(%q, %d, %v, stored=%v) = %d, want %d",
				c.payload, c.failed, c.event, c.stored != "", got, c.want)
		}
	}
}

func TestLoopGuardKey(t *testing.T) {
	for prefix, want := range map[string]string{"": "root", "go": "go", "apps/my app": "apps-my-app"} {
		if got := loopGuardKey(prefix); got != want {
			t.Errorf("loopGuardKey(%q) = %q, want %q", prefix, got, want)
		}
	}
}

// ── Hook input ──

func TestHookTargetResolvesInsideTheProjectOnly(t *testing.T) {
	base := t.TempDir()
	for _, rel := range []string{"app/app.go", "app/data.txt", "testdata/tool.go"} {
		path := filepath.Join(base, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	event := func(filePath any) map[string]any {
		return map[string]any{"tool_input": map[string]any{"file_path": filePath}}
	}
	cases := []struct {
		event map[string]any
		want  string
	}{
		{event(filepath.Join(base, "app", "app.go")), "app/app.go"},
		{event("app/app.go"), "app/app.go"},
		{event("app/missing.go"), ""},
		{event("app/data.txt"), ""},
		{event("testdata/tool.go"), ""},
		{event("../elsewhere/app/app.go"), ""},
		{event(""), ""},
		{event(3.0), ""},
		{map[string]any{"tool_input": "app/app.go"}, ""},
		{map[string]any{}, ""},
	}
	for _, c := range cases {
		if got := hookTarget(c.event, base); got != c.want {
			t.Errorf("hookTarget(%v) = %q, want %q", c.event, got, c.want)
		}
	}
}

func TestParseHookEventToleratesBadInput(t *testing.T) {
	cases := map[string]map[string]any{
		`{"stop_hook_active": true}`: {"stop_hook_active": true},
		"":                           {},
		"not json":                   {},
		"[1, 2]":                     {},
		"null":                       {},
	}
	for text, want := range cases {
		if got := parseHookEvent(text); !reflect.DeepEqual(got, want) {
			t.Errorf("parseHookEvent(%q) = %v, want %v", text, got, want)
		}
	}
}

func TestReadWithDeadline(t *testing.T) {
	if text, complete := readWithDeadline(strings.NewReader(`{"a":1}`), time.Second); text != `{"a":1}` || !complete {
		t.Fatalf("closed input: got %q, %v", text, complete)
	}
	idle, writer := io.Pipe()
	defer writer.Close()
	start := time.Now()
	if text, complete := readWithDeadline(idle, 50*time.Millisecond); text != "" || complete {
		t.Fatalf("idle pipe: got %q, %v", text, complete)
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("idle pipe held the read for %s", elapsed)
	}
}

// ── Hook wiring ──

// inRoot points the runner at dir for one test.
func inRoot(t *testing.T, dir string) {
	t.Helper()
	previous := root
	root = dir
	t.Cleanup(func() { root = previous })
}

func writeJSON(t *testing.T, dir, rel string, value any) {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	writeText(t, dir, rel, data)
}

func writeText(t *testing.T, dir, rel string, data []byte) {
	t.Helper()
	path := filepath.Join(dir, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func readJSON(t *testing.T, path string) any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return value
}

// asJSON is value as encoding/json reads it back (numbers become float64).
func asJSON(t *testing.T, value any) any {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// TestHookWiringMatchesCommittedSettings installs every wiring into copies of
// the committed settings files: the install constants and the files agree when
// the parsed content does not change. Reinstalling is byte-identical.
func TestHookWiringMatchesCommittedSettings(t *testing.T) {
	template := root
	dir := t.TempDir()
	for _, rel := range []string{claudeSettings, codexHooks} {
		raw, err := os.ReadFile(filepath.Join(template, rel))
		if err != nil {
			t.Fatal(err)
		}
		writeText(t, dir, rel, raw)
	}
	inRoot(t, dir)
	for _, w := range hookWirings {
		installHook(w)
	}
	first := map[string]string{}
	for _, rel := range []string{claudeSettings, codexHooks} {
		if got, want := readJSON(t, filepath.Join(dir, rel)), readJSON(t, filepath.Join(template, rel)); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: installing changed the committed wiring:\ngot  %v\nwant %v", rel, got, want)
		}
		raw, _ := os.ReadFile(filepath.Join(dir, rel))
		first[rel] = string(raw)
	}
	for _, w := range hookWirings {
		installHook(w)
	}
	for rel, want := range first {
		if raw, _ := os.ReadFile(filepath.Join(dir, rel)); string(raw) != want {
			t.Errorf("%s: reinstalling is not a no-op", rel)
		}
	}
}

func TestInstallReplacesALegacyHandlerAndKeepsOthers(t *testing.T) {
	dir := t.TempDir()
	echo := map[string]any{"matcher": "Edit", "hooks": []any{map[string]any{"type": "command", "command": "echo hi"}}}
	writeJSON(t, dir, claudeSettings, map[string]any{"hooks": map[string]any{
		"Stop":        []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": "go run harness.go stop-hook"}}}},
		"PostToolUse": []any{echo},
	}})
	inRoot(t, dir)
	for range 2 {
		for _, w := range hookWirings[:2] {
			installHook(w)
		}
	}
	hooks := readJSON(t, filepath.Join(dir, claudeSettings)).(map[string]any)["hooks"].(map[string]any)
	wantStop := asJSON(t, []any{map[string]any{"hooks": []any{hookWirings[0].handler}}})
	if !reflect.DeepEqual(hooks["Stop"], wantStop) {
		t.Errorf("Stop = %v, want %v", hooks["Stop"], wantStop)
	}
	wantPost := asJSON(t, []any{echo, map[string]any{"matcher": "Edit|Write", "hooks": []any{hookWirings[1].handler}}})
	if !reflect.DeepEqual(hooks["PostToolUse"], wantPost) {
		t.Errorf("PostToolUse = %v, want %v", hooks["PostToolUse"], wantPost)
	}
}

func TestHookWiredFlagsMissingPostToolUse(t *testing.T) {
	dir := t.TempDir()
	writeJSON(t, dir, claudeSettings, map[string]any{"hooks": map[string]any{
		"Stop": []any{map[string]any{"hooks": []any{hookWirings[0].handler}}},
	}})
	writeText(t, dir, codexHooks, []byte("not json"))
	inRoot(t, dir)
	var got []bool
	for _, w := range hookWirings {
		got = append(got, hookWired(w))
	}
	if want := []bool{true, false, false}; !reflect.DeepEqual(got, want) {
		t.Fatalf("hookWired = %v, want %v (Claude Stop, Claude PostToolUse, Codex Stop)", got, want)
	}
}
