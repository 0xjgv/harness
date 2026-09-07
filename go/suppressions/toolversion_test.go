package suppressions

import (
	"fmt"
	"testing"

	"pgregory.net/rapid"
)

func TestGolangciLintVersion(t *testing.T) {
	tests := []struct {
		name   string
		output string
		want   string
	}{
		{
			name:   "release banner",
			output: "golangci-lint has version 2.13.2 built with go1.27.0 from 27774aa on 2026-08-27T22:52:01Z\n",
			want:   "2.13.2",
		},
		{
			name:   "go install build keeps its tag prefix",
			output: "golangci-lint has version v2.13.2 built with go1.27.0 from (unknown) on (unknown)\n",
			want:   "v2.13.2",
		},
		{
			name:   "banner without the marker",
			output: "golangci-lint 2.13.2\n",
			want:   "",
		},
		{
			name:   "empty output",
			output: "",
			want:   "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := GolangciLintVersion(tt.output); got != tt.want {
				t.Errorf("GolangciLintVersion(%q) = %q, want %q", tt.output, got, tt.want)
			}
		})
	}
}

// TestGolangciLintVersionRoundTrip pins the law the examples above sample: any
// whitespace-free version in a golangci-lint banner comes back verbatim.
func TestGolangciLintVersionRoundTrip(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		version := rapid.StringMatching(`[vV]?[0-9]{1,3}(\.[0-9]{1,3}){0,3}(-[a-z0-9]{1,8})?`).Draw(t, "version")
		banner := fmt.Sprintf("golangci-lint has version %s built with go1.27.0 from abc1234 on 2026-01-01T00:00:00Z", version)
		if got := GolangciLintVersion(banner); got != version {
			t.Fatalf("GolangciLintVersion(%q) = %q, want %q", banner, got, version)
		}
	})
}
