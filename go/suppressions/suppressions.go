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

// Counts returns baseline keys for the current suppression counts.
func Counts(results map[string][][]string) map[string]int {
	counts := map[string]int{}
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

// WriteBaseline writes current suppression counts while preserving coverage.min.
func WriteBaseline(root string, results map[string][][]string) error {
	existing, _ := ReadBaseline(root)
	coverageMin := 0
	if existing != nil {
		coverageMin = existing["coverage.min"]
	}
	counts := Counts(results)
	keys := make([]string, 0, len(counts))
	for key := range counts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var lines []string
	for _, key := range keys {
		lines = append(lines, fmt.Sprintf("%s %d", key, counts[key]))
	}
	lines = append(lines, fmt.Sprintf("coverage.min %d", coverageMin))
	return os.WriteFile(filepath.Join(root, baselineFile), []byte(strings.Join(lines, "\n")+"\n"), 0o600)
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
