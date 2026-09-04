package suppressions

import (
	"math"
	"path"
	"sort"
	"strings"
)

// MutationPackages maps a change set of .go paths to the gremlins targets
// that cover them: one deduplicated, sorted package directory per changed
// file, in the `./dir` form gremlins takes as its single path argument.
//
// Two exclusions, both because gremlins mutates compiled packages:
//   - non-.go paths carry no mutants;
//   - `harness.go` carries `//go:build ignore`, so it belongs to no package
//     (the same reason IsGherkinGuardProductionPath skips it).
//
// A changed file at the module root maps to `.`, which gremlins reads as the
// whole module subtree, not just the root package — a scoped run that starts
// there is as wide as the module. This template has no root-level package
// source, so the case only arises in an adopting repo.
func MutationPackages(changed []string) []string {
	seen := map[string]bool{}
	for _, p := range changed {
		normalized := strings.TrimPrefix(path.Clean(strings.TrimSpace(p)), "./")
		if !strings.HasSuffix(normalized, ".go") || normalized == "harness.go" {
			continue
		}
		dir := path.Dir(normalized)
		if dir == "." {
			seen["."] = true
			continue
		}
		seen["./"+dir] = true
	}
	targets := make([]string, 0, len(seen))
	for target := range seen {
		targets = append(targets, target)
	}
	sort.Strings(targets)
	return targets
}

// MutationScore is the percentage of mutants a test suite killed, rounded to
// an integer: killed / (killed + lived). Mutants that were never run — not
// covered, not viable, skipped — are excluded from both sides, so the score
// measures the tests that exist rather than the code they never reach. This
// is exactly gremlins' own `test_efficacy` (and deliberately not its
// `mutations_coverage`), summed across targets so several scoped runs
// aggregate into one number instead of averaging percentages.
//
// ok=false when nothing ran: there is no score to compare against a floor,
// and the gate must go report-only rather than record a 0 it would then
// enforce.
func MutationScore(killed, lived int) (int, bool) {
	total := killed + lived
	if total <= 0 {
		return 0, false
	}
	return int(math.Round(100 * float64(killed) / float64(total))), true
}
