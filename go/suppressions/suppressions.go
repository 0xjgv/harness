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
	{"nolint", regexp.MustCompile(`//\s*nolint(?::([\w,\s]+))?`)},
	{"lint_ignore", regexp.MustCompile(`//\s*lint:ignore\s+(\S+)`)},
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

// Scan walks the given roots and collects suppressions from all .go files.
// Skips vendor/ and hidden directories.
func Scan(roots ...string) map[string][][]string {
	results := map[string][][]string{}
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
			for line := range strings.SplitSeq(string(data), "\n") {
				for _, m := range ParseLine(line) {
					results[m.Kind] = append(results[m.Kind], m.Rules)
				}
			}
			return nil
		})
	}
	return results
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
