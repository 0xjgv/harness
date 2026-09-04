package suppressions

import (
	"path/filepath"
	"strings"
)

// NormalizeChangedPath trims a leading "./" and, when prefix is non-empty,
// strips "prefix/" from a repo-root-relative git status/diff path. Paths
// outside prefix are returned unchanged — callers that need a strict
// subtree membership check use PorcelainChangedGoPath instead.
func NormalizeChangedPath(path, prefix string) string {
	normalized := strings.TrimPrefix(filepath.ToSlash(strings.TrimSpace(path)), "./")
	if prefix != "" && strings.HasPrefix(normalized, prefix+"/") {
		return strings.TrimPrefix(normalized, prefix+"/")
	}
	return normalized
}

// PorcelainChangedGoPath parses one line of `git status --porcelain` output
// and returns the .go file's path relative to prefix, or ok=false when the
// entry is deleted, not a .go file, or outside the prefix subtree.
//
// `git status --porcelain` always prints paths relative to the repo root,
// regardless of cwd — the subtree check is what stops a sibling template's
// change (e.g. "python/foo.go" in a monorepo checkout) from being mistaken
// for a change in this template. Rename lines ("R  old.go -> new.go")
// resolve to the new path.
func PorcelainChangedGoPath(line, prefix string) (string, bool) {
	if len(line) < 4 {
		return "", false
	}
	if strings.Contains(line[:2], "D") {
		return "", false
	}
	raw := line[3:]
	if idx := strings.LastIndex(raw, " -> "); idx >= 0 {
		raw = raw[idx+len(" -> "):]
	}
	trimmed := strings.TrimPrefix(filepath.ToSlash(strings.TrimSpace(raw)), "./")
	if prefix != "" && !strings.HasPrefix(trimmed, prefix+"/") {
		return "", false
	}
	normalized := NormalizeChangedPath(raw, prefix)
	if !strings.HasSuffix(normalized, ".go") {
		return "", false
	}
	return normalized, true
}

// IsGherkinGuardProductionPath reports whether a normalized (gitPrefix-
// stripped, "./"-trimmed) changed path counts as "production source" for
// the Gherkin-first guard: a non-test .go file, outside features/ (godog
// step definitions don't add new behavior on their own), excluding
// harness.go itself (it carries `//go:build ignore`, is not part of any
// package, and is the runner, not template output).
func IsGherkinGuardProductionPath(path string) bool {
	if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
		return false
	}
	if path == "harness.go" {
		return false
	}
	return path != "features" && !strings.HasPrefix(path, "features/")
}

// GherkinGuardResult is the Gherkin-first guard's decision, independent of
// how it is printed or enforced (warn vs. block) — see EvaluateGherkinGuard.
type GherkinGuardResult int

const (
	// GherkinGuardPass means there is nothing to flag — no production source
	// changed, a .feature changed alongside it, or the override is set.
	GherkinGuardPass GherkinGuardResult = iota
	// GherkinGuardSkip means the template has no .feature files anywhere yet.
	// Callers pass silently (no output at all) on this result — retrofitting
	// the harness into a repo with no acceptance suite must never block.
	GherkinGuardSkip
	// GherkinGuardTrigger means production source changed with no
	// accompanying .feature and no override — the caller should warn or block.
	GherkinGuardTrigger
)

// EvaluateGherkinGuard is the Gherkin-first guard's pure decision function:
// production source changed, no .feature changed alongside it, at least one
// .feature file exists somewhere in the template, and no override → Trigger.
// No .feature files anywhere in the template → Skip (checked first: an
// override or an accompanying .feature can't "unskip" a suite that doesn't
// exist). Otherwise → Pass.
func EvaluateGherkinGuard(hasFeatureFilesInRepo, hasProductionChange, hasFeatureChange, overrideSet bool) GherkinGuardResult {
	if !hasFeatureFilesInRepo {
		return GherkinGuardSkip
	}
	if overrideSet {
		return GherkinGuardPass
	}
	if hasProductionChange && !hasFeatureChange {
		return GherkinGuardTrigger
	}
	return GherkinGuardPass
}
