package suppressions

import (
	"slices"
	"testing"
)

// Package paths repeat across the table below; naming them keeps goconst
// quiet without hiding what each case is about.
const (
	crapGo          = "crap/crap.go"
	crapPkg         = "./crap"
	suppressionsPkg = "./suppressions"
)

func TestMutationPackages(t *testing.T) {
	cases := []struct {
		name    string
		changed []string
		want    []string
	}{
		{"package dir per changed file", []string{"suppressions/suppressions.go"}, []string{suppressionsPkg}},
		{
			"deduplicates and sorts",
			[]string{"suppressions/gitstatus.go", crapGo, "suppressions/suppressions.go"},
			[]string{crapPkg, suppressionsPkg},
		},
		{"test files select their package too", []string{"crap/crap_test.go"}, []string{crapPkg}},
		{"non-go paths carry no mutants", []string{"README.md", "features/smoke.feature"}, nil},
		{"harness.go belongs to no package", []string{"harness.go"}, nil},
		{"a root-level source maps to the module root", []string{"main.go"}, []string{"."}},
		{"leading ./ is not a separate package", []string{"./" + crapGo, crapGo}, []string{crapPkg}},
		{"nothing changed", nil, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := MutationPackages(tc.changed)
			if !slices.Equal(got, tc.want) {
				t.Fatalf("MutationPackages(%v) = %v, want %v", tc.changed, got, tc.want)
			}
		})
	}
}

func TestMutationScore(t *testing.T) {
	cases := []struct {
		name         string
		killed, live int
		want         int
		wantOK       bool
	}{
		// gremlins reported test_efficacy 75.60975609756098 for these counts.
		{"rounds to the nearest percent", 31, 10, 76, true},
		{"every mutant killed", 4, 0, 100, true},
		{"every mutant survived", 0, 3, 0, true},
		{"nothing ran is not a zero score", 0, 0, 0, false},
		{"rounds half away from zero", 1, 2, 33, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := MutationScore(tc.killed, tc.live)
			if got != tc.want || ok != tc.wantOK {
				t.Fatalf("MutationScore(%d, %d) = (%d, %t), want (%d, %t)",
					tc.killed, tc.live, got, ok, tc.want, tc.wantOK)
			}
		})
	}
}
