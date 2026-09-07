package suppressions

import "regexp"

// golangciLintVersionRe matches the version banner golangci-lint prints:
// "golangci-lint has version 2.13.2 built with go1.27.0 from 27774aa on …".
var golangciLintVersionRe = regexp.MustCompile(`has version (\S+)`)

// GolangciLintVersion extracts the version from `golangci-lint version` output.
// Returns "" when the banner does not match, so a caller stays silent instead of
// guessing on an unknown output format.
//
// It lives here, not in harness.go: the runner carries `//go:build ignore` and
// cannot be unit-tested, so its testable helpers belong in a real package.
func GolangciLintVersion(output string) string {
	m := golangciLintVersionRe.FindStringSubmatch(output)
	if m == nil {
		return ""
	}
	return m[1]
}
