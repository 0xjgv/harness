package suppressions

import "testing"

const testHarnessGo = "harness.go"

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
		{"package source is production", "crap/crap.go", true},
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
