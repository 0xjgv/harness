// Package suppressions scans Go source files for lint suppression comments
// (// nolint, // lint:ignore) and reports aggregate counts.
package suppressions

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Match is a single suppression directive found on a line.
type Match struct {
	Kind  string
	Rules []string
}

var patterns = []struct {
	kind    string
	pattern *regexp.Regexp
}{
	{kindNolint, regexp.MustCompile(`//\s*nolint(?::([\w,\s]+))?`)},
	{kindLintIgnore, regexp.MustCompile(`//\s*lint:ignore\s+(\S+)`)},
}

const (
	kindNolint     = "nolint"
	kindLintIgnore = "lint_ignore"
)

const (
	baselineFile              = ".harness-baseline"
	suppressionBaselinePrefix = "suppressions."
)

// Finding is a suppression directive plus its source location.
type Finding struct {
	Match
	Location string
}

// ParseLine returns all suppression matches found on a single line.
func ParseLine(line string) []Match {
	var out []Match
	for _, sp := range patterns {
		m := sp.pattern.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		var rules []string
		if len(m) > 1 && m[1] != "" {
			for r := range strings.SplitSeq(m[1], ",") {
				r = strings.TrimSpace(r)
				if r != "" {
					rules = append(rules, r)
				}
			}
		}
		out = append(out, Match{Kind: sp.kind, Rules: rules})
	}
	return out
}

// ScanFindings walks the given roots and collects suppressions from all .go files.
// Skips vendor/ and hidden directories.
func ScanFindings(roots ...string) []Finding {
	var findings []Finding
	for _, root := range roots {
		_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if d.IsDir() {
				name := d.Name()
				if name == "vendor" || (strings.HasPrefix(name, ".") && name != ".") {
					return fs.SkipDir
				}
				return nil
			}
			if !strings.HasSuffix(path, ".go") {
				return nil
			}
			data, err := os.ReadFile(path) //nolint:gosec // path is produced by filepath.WalkDir from caller-supplied roots; never user input
			if err != nil {
				return nil
			}
			lineNo := 0
			for line := range strings.SplitSeq(string(data), "\n") {
				lineNo++
				for _, m := range ParseLine(line) {
					findings = append(findings, Finding{
						Match:    m,
						Location: fmt.Sprintf("%s:%d", path, lineNo),
					})
				}
			}
			return nil
		})
	}
	return findings
}

// BucketByKind converts a finding list into the historical {kind: rules} shape.
func BucketByKind(findings []Finding) map[string][][]string {
	results := map[string][][]string{}
	for _, finding := range findings {
		results[finding.Kind] = append(results[finding.Kind], finding.Rules)
	}
	return results
}

// Scan walks the given roots and collects suppressions from all .go files.
// Skips vendor/ and hidden directories.
func Scan(roots ...string) map[string][][]string {
	return BucketByKind(ScanFindings(roots...))
}

// Counts returns baseline keys for the current suppression counts. Every
// known kind is present, so a kind that vanished from the tree is recorded
// as 0 and ratchets down instead of silently keeping its old floor.
func Counts(results map[string][][]string) map[string]int {
	counts := map[string]int{}
	for _, sp := range patterns {
		counts[suppressionBaselinePrefix+sp.kind] = 0
	}
	for kind, entries := range results {
		counts[suppressionBaselinePrefix+kind] = len(entries)
	}
	return counts
}

// ReadBaseline parses .harness-baseline from root. Missing files return ok=false.
func ReadBaseline(root string) (map[string]int, bool) {
	data, err := os.ReadFile(filepath.Join(root, baselineFile)) //nolint:gosec // root is the project root selected by the harness; the filename is fixed
	if err != nil {
		return nil, false
	}
	values := map[string]int{}
	for line := range strings.SplitSeq(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) != 2 {
			continue
		}
		value, err := strconv.Atoi(parts[1])
		if err != nil {
			continue
		}
		values[parts[0]] = value
	}
	return values, true
}

// BaselineFloor returns the committed floor for a ratcheted metric, or
// ok=false when there is none — no .harness-baseline at all, or a file that
// does not carry this key. Every gate reading a floor must then run
// report-only: retrofitting the harness into an existing repo has to be
// green on day one, and a floor of 0 inferred from a missing number is not a
// floor, it is a demand that the repo already be perfect.
func BaselineFloor(root, key string) (int, bool) {
	baseline, ok := ReadBaseline(root)
	if !ok {
		return 0, false
	}
	value, ok := baseline[key]
	return value, ok
}

// Measurement is a ratcheted metric's value, or the reason there isn't one.
// Three states, deliberately not collapsed into an optional int:
//   - Measured: Value holds the number, including a legitimate 0.
//   - Unavailable: the metric does not apply to this repo (no tests, no app
//     sources). The baseline key is dropped, and the gate goes report-only.
//   - Error: the measuring tool ran and failed. WriteBaseline aborts and
//     writes nothing: a floor recorded from a broken run is worse than no
//     floor, because every downstream gate trusts it.
//
// Build one with Measured, Unavailable, or Failed.
type Measurement struct {
	Value       int
	Measured    bool
	Unavailable string
	Error       string
}

// Measured is a metric that was measured, including a legitimate 0.
func Measured(value int) Measurement { return Measurement{Value: value, Measured: true} }

// Unavailable is a metric that does not apply to this repo.
func Unavailable(reason string) Measurement { return Measurement{Unavailable: reason} }

// Failed is a metric whose measuring tool ran and failed.
func Failed(reason string) Measurement { return Measurement{Error: reason} }

// Measurer pairs a baseline key with the function that measures it. The
// harness keeps a table of these; WriteBaseline rewrites every key in the
// table and carries every other key through untouched.
type Measurer struct {
	Key     string
	Measure func() Measurement
}

// KeyMeasurement is one measured baseline key, in measurement order.
type KeyMeasurement struct {
	Key string
	Measurement
}

// MeasureRatcheted measures every key in order, stopping at the first one that
// failed. Sequential and short-circuiting on purpose: a broken tool usually
// breaks the metrics after it too (CRAP re-runs the coverage suite), so the
// first failure is the one worth reporting, and the later ones would only be
// noise.
func MeasureRatcheted(measurers []Measurer) []KeyMeasurement {
	var measured []KeyMeasurement
	for _, m := range measurers {
		result := m.Measure()
		measured = append(measured, KeyMeasurement{Key: m.Key, Measurement: result})
		if result.Error != "" {
			break
		}
	}
	return measured
}

// MeasurementError reports the metric WriteBaseline could not measure. Nothing
// was written.
type MeasurementError struct {
	Key    string
	Reason string
}

func (e *MeasurementError) Error() string {
	return fmt.Sprintf("could not measure %s: %s", e.Key, e.Reason)
}

// WriteBaseline merges measured floors over the existing baseline; unknown
// keys are preserved. Returns the baseline as written.
//
// Every key the harness measures is rewritten (so a metric that improved
// ratchets down); every key it does not recognise is carried through
// untouched. A metric that does not apply here has its key *removed*, never
// carried forward: the shipped template's own numbers must not survive into
// an adopting repo's first baseline. A metric that could not be measured
// aborts the whole write with a *MeasurementError — see Measurement.
func WriteBaseline(root string, results map[string][][]string, measurers []Measurer) (map[string]int, error) {
	baseline, _ := ReadBaseline(root)
	if baseline == nil {
		baseline = map[string]int{}
	}
	for key, count := range Counts(results) {
		baseline[key] = count
	}

	for _, m := range MeasureRatcheted(measurers) {
		if m.Error != "" {
			return nil, &MeasurementError{Key: m.Key, Reason: m.Error}
		}
		if !m.Measured {
			if _, had := baseline[m.Key]; had {
				fmt.Printf("  ⚠ %s: dropped — %s\n", m.Key, m.Unavailable)
			}
			delete(baseline, m.Key)
			continue
		}
		baseline[m.Key] = m.Value
	}

	if err := os.WriteFile(filepath.Join(root, baselineFile), []byte(formatBaseline(baseline)), 0o600); err != nil {
		return nil, err
	}
	return baseline, nil
}

// formatBaseline serialises a baseline as sorted `key value` lines. Sorted so a
// regenerated file diffs cleanly against the committed one.
func formatBaseline(baseline map[string]int) string {
	keys := make([]string, 0, len(baseline))
	for key := range baseline {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	lines := make([]string, 0, len(keys))
	for _, key := range keys {
		lines = append(lines, fmt.Sprintf("%s %d", key, baseline[key]))
	}
	return strings.Join(lines, "\n") + "\n"
}

// CheckBaseline compares current findings to .harness-baseline.
func CheckBaseline(root string, findings []Finding, noExit bool, updateCommand string, printMissingReport bool) bool {
	results := BucketByKind(findings)
	current := Counts(results)
	baseline, ok := ReadBaseline(root)
	if !ok {
		if printMissingReport {
			PrintReport(results)
		}
		fmt.Printf("  ⚠ Suppressions are report-only: no %s found\n", baselineFile)
		fmt.Printf("  ↳ fix: run `%s` to start ratcheting\n", updateCommand)
		return true
	}

	total := 0
	for _, count := range current {
		total += count
	}
	baselineTotal := 0
	for key, count := range baseline {
		if strings.HasPrefix(key, suppressionBaselinePrefix) {
			baselineTotal += count
		}
	}
	var grown []string
	for key, count := range current {
		if count > baseline[key] {
			grown = append(grown, key)
		}
	}
	sort.Strings(grown)
	if len(grown) == 0 {
		suffix := ""
		if total < baselineTotal {
			suffix = fmt.Sprintf(" — run `%s` to ratchet down", updateCommand)
		}
		fmt.Printf("  ✓ Suppressions: %d (baseline %d)%s\n", total, baselineTotal, suffix)
		return true
	}

	locations := map[string][]string{}
	for _, finding := range findings {
		locations[finding.Kind] = append(locations[finding.Kind], finding.Location)
	}
	fmt.Printf("  ✗ Suppressions grew: %d (baseline %d)\n", total, baselineTotal)
	for _, key := range grown {
		kind := strings.TrimPrefix(key, suppressionBaselinePrefix)
		fmt.Printf("    %s: %d > %d\n", kind, current[key], baseline[key])
		limit := min(len(locations[kind]), 10)
		for _, location := range locations[kind][:limit] {
			fmt.Printf("      %s\n", location)
		}
	}
	fmt.Printf("  ↳ fix: fix it, or with human sign-off: `%s`\n", updateCommand)
	if !noExit {
		os.Exit(1)
	}
	return false
}

// PrintReport writes a formatted report of counts to stdout.
func PrintReport(results map[string][][]string) {
	total := 0
	for _, v := range results {
		total += len(v)
	}
	fmt.Println("\n=== Suppressions ===")
	fmt.Println()
	fmt.Printf("Suppressions: %d total\n", total)
	if total == 0 {
		return
	}
	kinds := make([]string, 0, len(results))
	for k := range results {
		kinds = append(kinds, k)
	}
	sort.Strings(kinds)
	for _, kind := range kinds {
		entries := results[kind]
		fmt.Printf("  %s: %d\n", kind, len(entries))
		ruleCounts := map[string]int{}
		for _, rules := range entries {
			for _, r := range rules {
				ruleCounts[r]++
			}
		}
		type ruleCount struct {
			rule  string
			count int
		}
		sorted := make([]ruleCount, 0, len(ruleCounts))
		for r, c := range ruleCounts {
			sorted = append(sorted, ruleCount{r, c})
		}
		sort.Slice(sorted, func(i, j int) bool {
			if sorted[i].count != sorted[j].count {
				return sorted[i].count > sorted[j].count
			}
			return sorted[i].rule < sorted[j].rule
		})
		limit := min(len(sorted), 10)
		for _, rc := range sorted[:limit] {
			fmt.Printf("    %s: %d\n", rc.rule, rc.count)
		}
	}
}
