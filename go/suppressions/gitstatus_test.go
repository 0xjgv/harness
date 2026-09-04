package suppressions

import "testing"

const (
	testHarnessGo  = "harness.go"
	testCrapSource = "crap/crap.go"
	testCrapPkg    = "./crap/..."
)

func TestNormalizeChangedPath(t *testing.T) {
	tests := []struct {
		name   string
		path   string
		prefix string
		want   string
	}{
		{"strips module prefix", "go/crap/foo.go", "go", "crap/foo.go"},
		{"no prefix returns as-is", testHarnessGo, "", testHarnessGo},
		{"leading ./ trimmed", "./go/harness.go", "go", testHarnessGo},
		{"path outside prefix unchanged", "python/x.go", "go", "python/x.go"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := NormalizeChangedPath(tt.path, tt.prefix); got != tt.want {
				t.Errorf("NormalizeChangedPath(%q, %q) = %q, want %q", tt.path, tt.prefix, got, tt.want)
			}
		})
	}
}

func TestPorcelainChangedGoPath(t *testing.T) {
	tests := []struct {
		name   string
		line   string
		prefix string
		want   string
		wantOk bool
	}{
		{"modified file within prefix", " M go/harness.go", "go", testHarnessGo, true},
		{"untracked file within prefix", "?? go/crap/new.go", "go", "crap/new.go", true},
		{"sibling template rejected", "?? python/x.go", "go", "", false},
		{"rename yields new path", "R  old.go -> new.go", "", "new.go", true},
		{"rename within prefix yields new path", "R  go/old.go -> go/new.go", "go", "new.go", true},
		{"deleted in index skipped", "D  go/foo.go", "go", "", false},
		{"deleted in worktree skipped", " D go/foo.go", "go", "", false},
		{"non-go file skipped", "?? go/README.md", "go", "", false},
		{"short line skipped", "M", "go", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := PorcelainChangedGoPath(tt.line, tt.prefix)
			if got != tt.want || ok != tt.wantOk {
				t.Errorf("PorcelainChangedGoPath(%q, %q) = (%q, %v), want (%q, %v)",
					tt.line, tt.prefix, got, ok, tt.want, tt.wantOk)
			}
		})
	}
}

func TestIsGherkinGuardProductionPath(t *testing.T) {
	tests := []struct {
		name string
		path string
		want bool
	}{
		{"package source is production", testCrapSource, true},
		{"harness.go itself is excluded", testHarnessGo, false},
		{"test file is excluded", "suppressions/gitstatus_test.go", false},
		{"feature file itself is not go", "features/smoke.feature", false},
		{"step definitions under features/ are excluded", "features/steps/smoke_steps.go", false},
		{"features dir sentinel excluded", "features", false},
		{"non-go file is excluded", "README.md", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsGherkinGuardProductionPath(tt.path); got != tt.want {
				t.Errorf("IsGherkinGuardProductionPath(%q) = %v, want %v", tt.path, got, tt.want)
			}
		})
	}
}

func TestEvaluateGherkinGuard(t *testing.T) {
	tests := []struct {
		name                  string
		hasFeatureFilesInRepo bool
		hasProductionChange   bool
		hasFeatureChange      bool
		overrideSet           bool
		want                  GherkinGuardResult
	}{
		{"production change with no .feature triggers", true, true, false, false, GherkinGuardTrigger},
		{"production change alongside a .feature passes", true, true, true, false, GherkinGuardPass},
		{"no production change passes", true, false, false, false, GherkinGuardPass},
		{"no .feature files anywhere skips", false, true, false, false, GherkinGuardSkip},
		{"no .feature files anywhere skips even with override", false, true, false, true, GherkinGuardSkip},
		{"override passes despite trigger conditions", true, true, false, true, GherkinGuardPass},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := EvaluateGherkinGuard(tt.hasFeatureFilesInRepo, tt.hasProductionChange, tt.hasFeatureChange, tt.overrideSet)
			if got != tt.want {
				t.Errorf("EvaluateGherkinGuard(%v, %v, %v, %v) = %v, want %v",
					tt.hasFeatureFilesInRepo, tt.hasProductionChange, tt.hasFeatureChange, tt.overrideSet, got, tt.want)
			}
		})
	}
}

func TestPackagesForChangedGoFiles(t *testing.T) {
	tested := func(dirs ...string) func(string) bool {
		set := map[string]bool{}
		for _, d := range dirs {
			set[d] = true
		}
		return func(dir string) bool { return set[dir] }
	}
	tests := []struct {
		name         string
		files        []string
		hasTests     func(string) bool
		wantPkgs     []string
		wantUntested []string
	}{
		{
			name:     "one package per changed directory, deduped",
			files:    []string{testCrapSource, "crap/crap_test.go", "suppressions/gitstatus.go"},
			hasTests: tested("crap", "suppressions"),
			wantPkgs: []string{testCrapPkg, "./suppressions/..."},
		},
		{
			name:         "a changed package with no tests is reported, not run",
			files:        []string{testCrapSource, "features/steps/steps.go"},
			hasTests:     tested("crap"),
			wantPkgs:     []string{testCrapPkg},
			wantUntested: []string{"features/steps"},
		},
		{
			name:     "harness.go maps to no package",
			files:    []string{testHarnessGo},
			hasTests: tested("."),
		},
		{
			name:     "a root-package file scopes to the root package alone",
			files:    []string{"main.go"},
			hasTests: tested("."),
			wantPkgs: []string{"."},
		},
		{
			name:     "non-go paths are ignored and ./ prefixes trimmed",
			files:    []string{"README.md", "./" + testCrapSource},
			hasTests: tested("crap"),
			wantPkgs: []string{testCrapPkg},
		},
		{
			name:     "an empty change set yields no packages",
			hasTests: tested("crap"),
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			pkgs, untested := PackagesForChangedGoFiles(tt.files, tt.hasTests)
			if !equalStrings(pkgs, tt.wantPkgs) {
				t.Errorf("packages = %v, want %v", pkgs, tt.wantPkgs)
			}
			if !equalStrings(untested, tt.wantUntested) {
				t.Errorf("untested = %v, want %v", untested, tt.wantUntested)
			}
		})
	}
}

func equalStrings(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
