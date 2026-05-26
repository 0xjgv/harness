// Package crap provides pure helpers for the CRAP (Change Risk Anti-Patterns)
// gate: a numeric score combining cyclomatic complexity with test coverage,
// and a parser for Go's coverprofile text format.
//
// The functions here are kept pure (no I/O, no globals) so they can be unit
// tested directly — harness.go carries `//go:build ignore` and is therefore
// not part of any testable package.
package crap

import (
	"math"
	"strconv"
	"strings"
)

// Score returns the CRAP value for a function with the given cyclomatic
// complexity (ccn) and coverage fraction (cov, in [0, 1]).
//
// Formula: ccn² × (1-cov)³ + ccn.
//
// At full coverage the score collapses to ccn; at zero coverage it grows
// cubically with the uncovered fraction, so an untested complex function
// dominates the report.
func Score(ccn int, cov float64) float64 {
	return float64(ccn*ccn)*math.Pow(1-cov, 3) + float64(ccn)
}

// ParseCoverProfile parses Go's coverprofile text format into a nested map
// keyed by file path and then by 1-indexed source line, with the maximum
// hit count seen for any block covering that line.
//
// Input format (produced by `go test -coverprofile=...`):
//
//	mode: count
//	path/file.go:startLine.startCol,endLine.endCol numStmts hits
//	...
//
// Every line in [startLine, endLine] inclusive is marked with the block's
// hit count. When multiple blocks overlap a line, the maximum hit count
// wins — a line is "covered" if any block covering it ran.
//
// Malformed lines are silently skipped: the coverprofile is machine-written
// but downstream consumers should not crash on a truncated file.
func ParseCoverProfile(text string) map[string]map[int]int {
	result := map[string]map[int]int{}
	first := true
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if first {
			first = false
			if strings.HasPrefix(line, "mode:") {
				continue
			}
		}
		path, startLine, endLine, hits, ok := parseCoverBlock(line)
		if !ok {
			continue
		}
		fileMap, exists := result[path]
		if !exists {
			fileMap = map[int]int{}
			result[path] = fileMap
		}
		for ln := startLine; ln <= endLine; ln++ {
			if prev, seen := fileMap[ln]; !seen || hits > prev {
				fileMap[ln] = hits
			}
		}
	}
	return result
}

// parseCoverBlock parses one coverprofile block line into its components.
// Returns ok=false for any malformed input — the caller skips the line.
func parseCoverBlock(line string) (path string, startLine, endLine, hits int, ok bool) {
	colon := strings.LastIndex(line, ":")
	if colon < 0 {
		return
	}
	fields := strings.Fields(line[colon+1:])
	if len(fields) != 3 {
		return
	}
	comma := strings.Index(fields[0], ",")
	if comma < 0 {
		return
	}
	startLine, ok1 := parseLineNumber(fields[0][:comma])
	endLine, ok2 := parseLineNumber(fields[0][comma+1:])
	h, errHits := strconv.Atoi(fields[2])
	if !ok1 || !ok2 || errHits != nil || endLine < startLine {
		return path, 0, 0, 0, false
	}
	return line[:colon], startLine, endLine, h, true
}

// parseLineNumber extracts the line number from a "line.col" token.
func parseLineNumber(token string) (int, bool) {
	dot := strings.Index(token, ".")
	if dot < 0 {
		return 0, false
	}
	n, err := strconv.Atoi(token[:dot])
	if err != nil {
		return 0, false
	}
	return n, true
}
