// Property-based tests for the pure helpers in this package.
//
// Worked example for the template's PBT convention: law-like behavior
// (formulas, parsers, round-trips) gets a property, not just examples.
// Examples pin known cases; properties pin the law.
package crap

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"pgregory.net/rapid"
)

func TestScoreFullCoverageCollapsesToCCN(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		ccn := rapid.IntRange(1, 100).Draw(t, "ccn")
		if got := Score(ccn, 1.0); got != float64(ccn) {
			t.Fatalf("Score(%d, 1.0) = %v, want %d", ccn, got, ccn)
		}
	})
}

func TestScoreBounds(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		ccn := rapid.IntRange(1, 100).Draw(t, "ccn")
		cov := rapid.Float64Range(0, 1).Draw(t, "cov")
		got := Score(ccn, cov)
		if got < float64(ccn) || got > float64(ccn*ccn+ccn) {
			t.Fatalf("Score(%d, %v) = %v, want within [%d, %d]", ccn, cov, got, ccn, ccn*ccn+ccn)
		}
	})
}

func TestScoreMoreCoverageNeverRaisesScore(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		ccn := rapid.IntRange(1, 100).Draw(t, "ccn")
		lo := rapid.Float64Range(0, 1).Draw(t, "lo")
		hi := rapid.Float64Range(lo, 1).Draw(t, "hi")
		if Score(ccn, lo) < Score(ccn, hi) {
			t.Fatalf("Score(%d, %v) < Score(%d, %v)", ccn, lo, ccn, hi)
		}
	})
}

func TestScoreMoreComplexityNeverLowersScore(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		lo := rapid.IntRange(1, 100).Draw(t, "lo")
		hi := rapid.IntRange(lo, 100).Draw(t, "hi")
		cov := rapid.Float64Range(0, 1).Draw(t, "cov")
		if Score(lo, cov) > Score(hi, cov) {
			t.Fatalf("Score(%d, %v) > Score(%d, %v)", lo, cov, hi, cov)
		}
	})
}

type coverBlock struct {
	path             string
	start, end, hits int
}

func coverBlockGen() *rapid.Generator[coverBlock] {
	return rapid.Custom(func(t *rapid.T) coverBlock {
		start := rapid.IntRange(1, 500).Draw(t, "start")
		return coverBlock{
			path:  rapid.StringMatching(`[a-z][a-z0-9/._-]{0,15}\.go`).Draw(t, "path"),
			start: start,
			end:   rapid.IntRange(start, start+20).Draw(t, "end"),
			hits:  rapid.IntRange(0, 1000).Draw(t, "hits"),
		}
	})
}

// renderProfile renders blocks in coverprofile format and builds the
// expected parse result per the documented spec: every line in
// [start, end] is marked; on overlap the maximum hit count wins.
func renderProfile(blocks []coverBlock) (string, map[string]map[int]int) {
	var sb strings.Builder
	sb.WriteString("mode: count\n")
	want := map[string]map[int]int{}
	for _, b := range blocks {
		fmt.Fprintf(&sb, "%s:%d.1,%d.2 1 %d\n", b.path, b.start, b.end, b.hits)
		fileMap := want[b.path]
		if fileMap == nil {
			fileMap = map[int]int{}
			want[b.path] = fileMap
		}
		for ln := b.start; ln <= b.end; ln++ {
			if prev, seen := fileMap[ln]; !seen || b.hits > prev {
				fileMap[ln] = b.hits
			}
		}
	}
	return sb.String(), want
}

func TestParseCoverProfileRoundTrip(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		blocks := rapid.SliceOfN(coverBlockGen(), 1, 8).Draw(t, "blocks")
		text, want := renderProfile(blocks)
		got := ParseCoverProfile(text)
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("ParseCoverProfile mismatch\n got: %v\nwant: %v", got, want)
		}
	})
}

func TestParseCoverProfileTotalOnArbitraryText(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		text := rapid.String().Draw(t, "text")
		for path, lines := range ParseCoverProfile(text) {
			if lines == nil {
				t.Fatalf("nil line map for %q", path)
			}
		}
	})
}

func TestParseCoverProfileSkipsMalformedLines(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		blocks := rapid.SliceOfN(coverBlockGen(), 1, 5).Draw(t, "blocks")
		garbage := rapid.SliceOfN(
			rapid.String().Filter(func(s string) bool {
				line := strings.TrimSpace(s)
				if line == "" || strings.ContainsAny(s, "\n") || strings.HasPrefix(line, "mode:") {
					return false
				}
				_, _, _, _, ok := parseCoverBlock(line)
				return !ok
			}),
			1, 5,
		).Draw(t, "garbage")

		clean, want := renderProfile(blocks)
		dirty := clean + strings.Join(garbage, "\n") + "\n"
		if got := ParseCoverProfile(dirty); !reflect.DeepEqual(got, want) {
			t.Fatalf("garbage lines changed the result\n got: %v\nwant: %v", got, want)
		}
	})
}
