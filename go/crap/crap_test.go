package crap

import (
	"math"
	"testing"
)

func TestScore(t *testing.T) {
	tests := []struct {
		name string
		ccn  int
		cov  float64
		want float64
	}{
		{"full coverage collapses to ccn", 10, 1.0, 10.0},
		{"zero coverage adds ccn squared", 10, 0.0, 110.0},
		{"half coverage scales by 1/8", 10, 0.5, 22.5},
		{"trivial uncovered function", 1, 0.0, 2.0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Score(tt.ccn, tt.cov)
			if math.Abs(got-tt.want) > 1e-9 {
				t.Errorf("Score(%d, %v) = %v, want %v", tt.ccn, tt.cov, got, tt.want)
			}
		})
	}
}

func TestParseCoverProfile(t *testing.T) {
	input := "mode: count\n" +
		"src/foo.go:1.1,3.2 2 5\n" +
		"src/foo.go:5.1,5.20 1 0\n" +
		"src/bar.go:10.1,12.5 3 7\n"

	got := ParseCoverProfile(input)

	foo, ok := got["src/foo.go"]
	if !ok {
		t.Fatalf("expected src/foo.go in result, got keys: %v", keys(got))
	}
	for _, ln := range []int{1, 2, 3} {
		if foo[ln] != 5 {
			t.Errorf("src/foo.go line %d = %d, want 5", ln, foo[ln])
		}
	}
	if foo[5] != 0 {
		t.Errorf("src/foo.go line 5 = %d, want 0", foo[5])
	}
	if _, present := foo[5]; !present {
		t.Errorf("src/foo.go line 5 should be present (hit=0), not missing")
	}

	bar, ok := got["src/bar.go"]
	if !ok {
		t.Fatalf("expected src/bar.go in result, got keys: %v", keys(got))
	}
	for _, ln := range []int{10, 11, 12} {
		if bar[ln] != 7 {
			t.Errorf("src/bar.go line %d = %d, want 7", ln, bar[ln])
		}
	}
}

func keys(m map[string]map[int]int) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
