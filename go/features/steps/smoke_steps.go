// Package steps holds godog step definitions for acceptance features.
//
// Add new step files here as scenarios grow; register them from
// InitializeScenario so the acceptance runner picks them up.
package steps

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"

	"github.com/cucumber/godog"

	"harness/suppressions"
)

const fakeGoWorkspace = `#!/bin/sh
set -eu

case $0 in
  */*) script_dir=${0%/*} ;;
  *) script_dir=. ;;
esac
root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
state=$root/.fake-workspace
logs=$root/.fake-logs
offline=${OFFLINE:-0}
if [ "$offline" = 1 ]; then
  proxy=off
else
  proxy=https://proxy.golang.org,direct
fi
export GOPROXY=$proxy
mkdir -p "$logs"
printf '%s\t%s\t%s\n' "$offline" "$proxy" "$*" >>"$logs/operations"

require_go_profile() {
  [ "$#" -eq 2 ] && [ "$2" = go ] || {
    printf '%s\n' 'fake workspace: expected the Go profile' >&2
    exit 64
  }
}

case ${1:-} in
  preflight)
    require_go_profile "$@"
    if [ -f "$root/.fake-unmanaged-hook" ]; then
      printf '%s\n' 'fake workspace: unmanaged hook' >&2
      exit 65
    fi
    ;;
  install)
    require_go_profile "$@"
    if [ "$offline" = 1 ] && [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'fake workspace: cold offline tool cache' >&2
      exit 66
    fi
    if [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'go tools' >>"$logs/downloads"
      mkdir -p "$state/cache"
      printf '%s\n' 'cached Go tools' >"$state/cache/tools"
    fi
    if [ ! -f "$state/tools/go" ]; then
      mkdir -p "$state/tools"
      printf '%s\n' 'managed Go profile' >"$state/tools/go"
    fi
    ;;
  exec)
    [ "${2:-}" = go ] && [ "${3:-}" = -- ] || {
      printf '%s\n' 'fake workspace: malformed managed exec' >&2
      exit 67
    }
    [ -f "$state/tools/go" ] || {
      printf '%s\n' 'fake workspace: tools are not installed' >&2
      exit 68
    }
    shift 3
    case $* in
      'env GOFLAGS=-mod=readonly go mod download')
        if [ "$offline" = 1 ] && [ ! -f "$state/cache/dependencies" ]; then
          printf '%s\n' 'fake workspace: cold offline dependency cache' >&2
          exit 69
        fi
        if [ ! -f "$state/cache/dependencies" ]; then
          printf '%s\n' 'go dependencies' >>"$logs/downloads"
          mkdir -p "$state/cache"
          printf '%s\n' 'cached readonly dependencies' >"$state/cache/dependencies"
        fi
        mkdir -p "$state/dependencies"
        if [ ! -f "$state/dependencies/readonly" ]; then
          printf '%s\n' 'readonly dependencies' >"$state/dependencies/readonly"
        fi
        ;;
      'go run -mod=readonly harness.go check')
        [ -f "$state/dependencies/readonly" ] || {
          printf '%s\n' 'fake workspace: checks ran before dependencies' >&2
          exit 70
        }
        ;;
      *)
        printf 'fake workspace: unexpected managed command: %s\n' "$*" >&2
        exit 71
        ;;
    esac
    ;;
  sync-skills)
    [ "$#" -eq 1 ] || exit 72
    [ -f "$state/tools/go" ] && [ -f "$state/dependencies/readonly" ] || exit 73
    if [ ! -f "$state/skills/harness/SKILL.md" ]; then
      mkdir -p "$state/skills/harness"
      printf '%s\n' 'managed harness skill' >"$state/skills/harness/SKILL.md"
    fi
    ;;
  install-hooks)
    [ "$#" -eq 1 ] || exit 74
    [ -f "$state/tools/go" ] && [ -f "$state/dependencies/readonly" ] || exit 75
    if [ ! -f "$state/hooks/pre-commit" ]; then
      mkdir -p "$state/hooks"
      printf '%s\n' '#!/bin/sh' 'exit 0' >"$state/hooks/pre-commit"
      chmod +x "$state/hooks/pre-commit"
    fi
    ;;
  verify)
    require_go_profile "$@"
    [ -f "$state/tools/go" ] || exit 76
    [ -f "$state/hooks/pre-commit" ] || exit 77
    [ -f "$state/skills/harness/SKILL.md" ] || exit 78
    ;;
  *)
    printf 'fake workspace: unexpected operation: %s\n' "${1:-}" >&2
    exit 79
    ;;
esac
`

const poisonGo = `#!/bin/sh
printf '%s\n' 'ambient go executed' >>"$POISON_GO_LOG"
exit 97
`

type commandResult struct {
	exitCode int
	output   string
}

type operationRecord struct {
	offline   string
	proxy     string
	operation string
}

type snapshotEntry struct {
	path    string
	mode    os.FileMode
	content string
	missing bool
}

// smokeWorld carries state across the steps of a single scenario.
type smokeWorld struct {
	dir        string
	count      int
	root       string
	makePath   string
	poisonBin  string
	operations string
	downloads  string
	poisonLog  string

	result        commandResult
	onlineResult  commandResult
	offlineResult commandResult

	onlineOperations  []operationRecord
	offlineOperations []operationRecord

	managedBefore       []snapshotEntry
	managedAfterOnline  []snapshotEntry
	managedAfterOffline []snapshotEntry
	mutationsBefore     []snapshotEntry
	downloadsBefore     string
}

func (w *smokeWorld) anEmptyDirectory() error {
	d, err := os.MkdirTemp("", "smoke")
	if err != nil {
		return err
	}
	w.dir = d
	return nil
}

func (w *smokeWorld) theSuppressionsScannerRuns() error {
	results := suppressions.Scan(w.dir)
	for _, v := range results {
		w.count += len(v)
	}
	return nil
}

func (w *smokeWorld) itReportsZeroSuppressions() error {
	if w.count != 0 {
		return fmt.Errorf("expected 0 suppressions, got %d", w.count)
	}
	return nil
}

func templateRoot() (string, error) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		return "", fmt.Errorf("cannot locate Go template root")
	}
	return filepath.Dir(filepath.Dir(filepath.Dir(file))), nil
}

func writeExecutable(path, content string) error {
	if err := os.WriteFile(path, []byte(content), 0o755); err != nil {
		return err
	}
	return os.Chmod(path, 0o755)
}

func copyFile(source, destination string) error {
	content, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	return os.WriteFile(destination, content, 0o600)
}

func (w *smokeWorld) newIsolatedRepository() error {
	root, err := os.MkdirTemp("", "go-workspace-")
	if err != nil {
		return err
	}
	w.root = root
	template, err := templateRoot()
	if err != nil {
		return err
	}
	if err := copyFile(filepath.Join(template, "Makefile"), filepath.Join(root, "Makefile")); err != nil {
		return err
	}
	harnessDir := filepath.Join(root, ".harness")
	if err := os.Mkdir(harnessDir, 0o755); err != nil {
		return err
	}
	if err := writeExecutable(filepath.Join(harnessDir, "workspace.sh"), fakeGoWorkspace); err != nil {
		return err
	}
	w.poisonBin = filepath.Join(root, ".poison-bin")
	if err := os.Mkdir(w.poisonBin, 0o755); err != nil {
		return err
	}
	if err := writeExecutable(filepath.Join(w.poisonBin, "go"), poisonGo); err != nil {
		return err
	}
	logs := filepath.Join(root, ".fake-logs")
	if err := os.Mkdir(logs, 0o755); err != nil {
		return err
	}
	w.operations = filepath.Join(logs, "operations")
	w.downloads = filepath.Join(logs, "downloads")
	w.poisonLog = filepath.Join(logs, "poison-go")
	for _, path := range []string{w.operations, w.downloads, w.poisonLog} {
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			return err
		}
	}
	w.makePath, err = exec.LookPath("make")
	if err != nil {
		return fmt.Errorf("make is required for workspace acceptance tests: %w", err)
	}
	if !filepath.IsAbs(w.makePath) {
		w.makePath, err = filepath.Abs(w.makePath)
		if err != nil {
			return err
		}
	}
	w.mutationsBefore, err = snapshotSubtrees(w.root, "hooks", "skills")
	if err != nil {
		return err
	}
	w.downloadsBefore, err = readFileString(w.downloads)
	return err
}

func (w *smokeWorld) runMake(target string, offline bool) (commandResult, error) {
	offlineValue := "0"
	if offline {
		offlineValue = "1"
	}
	environment := append([]string{}, os.Environ()...)
	environment = append(environment,
		"PATH="+w.poisonBin+string(os.PathListSeparator)+os.Getenv("PATH"),
		"OFFLINE="+offlineValue,
		"POISON_GO_LOG="+w.poisonLog,
	)
	//nolint:gosec // acceptance invokes an absolute make with a fixed target.
	command := exec.Command(w.makePath, "--no-print-directory", target)
	command.Dir = w.root
	command.Env = environment
	output, err := command.CombinedOutput()
	result := commandResult{output: string(output)}
	if err == nil {
		return result, nil
	}
	var exitError *exec.ExitError
	if errors.As(err, &exitError) {
		result.exitCode = exitError.ExitCode()
		return result, nil
	}
	return commandResult{}, fmt.Errorf("run make %s: %w", target, err)
}

func readFileString(path string) (string, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(content), nil
}

func readOperationRecords(path string) ([]operationRecord, error) {
	content, err := readFileString(path)
	if err != nil {
		return nil, err
	}
	trimmed := strings.TrimSuffix(content, "\n")
	if trimmed == "" {
		return nil, nil
	}
	records := make([]operationRecord, 0)
	for _, line := range strings.Split(trimmed, "\n") {
		fields := strings.SplitN(line, "\t", 3)
		if len(fields) != 3 {
			return nil, fmt.Errorf("malformed operation record %q", line)
		}
		records = append(records, operationRecord{
			offline: fields[0], proxy: fields[1], operation: fields[2],
		})
	}
	return records, nil
}

func snapshotSubtrees(root string, names ...string) ([]snapshotEntry, error) {
	state := filepath.Join(root, ".fake-workspace")
	entries := make([]snapshotEntry, 0)
	for _, name := range names {
		path := filepath.Join(state, name)
		if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
			entries = append(entries, snapshotEntry{path: name, missing: true})
			continue
		} else if err != nil {
			return nil, err
		}
		if err := filepath.Walk(path, func(current string, info os.FileInfo, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			relative, err := filepath.Rel(state, current)
			if err != nil {
				return err
			}
			entry := snapshotEntry{path: filepath.ToSlash(relative), mode: info.Mode().Perm()}
			if !info.IsDir() {
				content, err := os.ReadFile(current)
				if err != nil {
					return err
				}
				entry.content = string(content)
			}
			entries = append(entries, entry)
			return nil
		}); err != nil {
			return nil, err
		}
	}
	return entries, nil
}

func snapshotManaged(root string) ([]snapshotEntry, error) {
	return snapshotSubtrees(root, "cache", "tools", "dependencies", "hooks", "skills")
}

func (w *smokeWorld) assertPoisonUnused() error {
	content, err := readFileString(w.poisonLog)
	if err != nil {
		return err
	}
	if content != "" {
		return fmt.Errorf("workspace invoked poisoned ambient go: %s", content)
	}
	return nil
}

func commandSucceeded(result commandResult) error {
	if result.exitCode != 0 {
		return fmt.Errorf("expected workspace success, got %d\n--- output ---\n%s",
			result.exitCode, result.output)
	}
	return nil
}

func (w *smokeWorld) isolatedCleanGoRepository() error {
	return w.newIsolatedRepository()
}

func (w *smokeWorld) runGoWorkspaceOnline() error {
	result, err := w.runMake("workspace", false)
	w.result = result
	return err
}

func (w *smokeWorld) workspaceCommandSucceeds() error {
	if err := commandSucceeded(w.result); err != nil {
		return err
	}
	return w.assertPoisonUnused()
}

func (w *smokeWorld) workspaceOperationsRunInOrder(table *godog.Table) error {
	records, err := readOperationRecords(w.operations)
	if err != nil {
		return err
	}
	if len(table.Rows) == 0 || len(table.Rows[0].Cells) != 1 ||
		table.Rows[0].Cells[0].Value != "operation" {
		return fmt.Errorf("workspace operation table must have one operation column")
	}
	expected := make([]string, 0, len(table.Rows)-1)
	for _, row := range table.Rows[1:] {
		if len(row.Cells) != 1 {
			return fmt.Errorf("workspace operation row must have one cell")
		}
		expected = append(expected, row.Cells[0].Value)
	}
	actual := make([]string, 0, len(records))
	for _, record := range records {
		if record.offline != "0" || record.proxy != "https://proxy.golang.org,direct" {
			return fmt.Errorf("online workspace policy differs: %+v", record)
		}
		actual = append(actual, record.operation)
	}
	if !reflect.DeepEqual(actual, expected) {
		return fmt.Errorf("expected operations %q, got %q", expected, actual)
	}
	return nil
}

func (w *smokeWorld) isolatedWarmGoRepository() error {
	if err := w.newIsolatedRepository(); err != nil {
		return err
	}
	result, err := w.runMake("workspace", false)
	if err != nil {
		return err
	}
	if err := commandSucceeded(result); err != nil {
		return err
	}
	if err := w.assertPoisonUnused(); err != nil {
		return err
	}
	w.managedBefore, err = snapshotManaged(w.root)
	if err != nil {
		return err
	}
	w.downloadsBefore, err = readFileString(w.downloads)
	if err != nil {
		return err
	}
	return os.WriteFile(w.operations, nil, 0o600)
}

func (w *smokeWorld) rerunGoWorkspaceOnlineAndBootstrapOffline() error {
	var err error
	w.onlineResult, err = w.runMake("workspace", false)
	if err != nil {
		return err
	}
	w.onlineOperations, err = readOperationRecords(w.operations)
	if err != nil {
		return err
	}
	w.managedAfterOnline, err = snapshotManaged(w.root)
	if err != nil {
		return err
	}
	if err := os.WriteFile(w.operations, nil, 0o600); err != nil {
		return err
	}
	w.offlineResult, err = w.runMake("bootstrap", true)
	if err != nil {
		return err
	}
	w.offlineOperations, err = readOperationRecords(w.operations)
	if err != nil {
		return err
	}
	w.managedAfterOffline, err = snapshotManaged(w.root)
	return err
}

func (w *smokeWorld) bothWorkspaceCommandsSucceed() error {
	if err := commandSucceeded(w.onlineResult); err != nil {
		return err
	}
	if err := commandSucceeded(w.offlineResult); err != nil {
		return err
	}
	return w.assertPoisonUnused()
}

func countOperation(records []operationRecord, offline, operation string) int {
	count := 0
	for _, record := range records {
		if record.offline == offline && record.operation == operation {
			count++
		}
	}
	return count
}

func (w *smokeWorld) dependenciesUse(command string) error {
	operation := "exec go -- " + command
	if countOperation(w.onlineOperations, "0", operation) != 1 {
		return fmt.Errorf("online dependencies did not use %q: %+v", command, w.onlineOperations)
	}
	if countOperation(w.offlineOperations, "1", operation) != 1 {
		return fmt.Errorf("offline dependencies did not use %q: %+v", command, w.offlineOperations)
	}
	return nil
}

func (w *smokeWorld) offlineDependencyRestorationDisablesProxy() error {
	const dependencyOperation = "exec go -- env GOFLAGS=-mod=readonly go mod download"
	for _, record := range w.offlineOperations {
		if record.operation == dependencyOperation && record.proxy == "off" {
			return nil
		}
	}
	return fmt.Errorf("offline dependency restore did not force GOPROXY=off: %+v",
		w.offlineOperations)
}

func (w *smokeWorld) managedWorkspaceSnapshotUnchanged() error {
	if !reflect.DeepEqual(w.managedBefore, w.managedAfterOnline) {
		return fmt.Errorf("online rerun changed managed workspace state")
	}
	if !reflect.DeepEqual(w.managedBefore, w.managedAfterOffline) {
		return fmt.Errorf("offline bootstrap changed managed workspace state")
	}
	downloads, err := readFileString(w.downloads)
	if err != nil {
		return err
	}
	if downloads != w.downloadsBefore {
		return fmt.Errorf("warm rerun downloaded tools or dependencies")
	}
	return nil
}

func (w *smokeWorld) isolatedColdOfflineGoRepository() error {
	return w.newIsolatedRepository()
}

func (w *smokeWorld) runGoWorkspaceOffline() error {
	result, err := w.runMake("workspace", true)
	w.result = result
	return err
}

func (w *smokeWorld) workspaceFailsDuringToolInstallation() error {
	if w.result.exitCode == 0 {
		return fmt.Errorf("expected cold offline workspace failure")
	}
	records, err := readOperationRecords(w.operations)
	if err != nil {
		return err
	}
	expected := []operationRecord{
		{offline: "1", proxy: "off", operation: "preflight go"},
		{offline: "1", proxy: "off", operation: "install go"},
	}
	if !reflect.DeepEqual(records, expected) {
		return fmt.Errorf("cold offline operations differ: %+v", records)
	}
	if !strings.Contains(w.result.output, "cold offline tool cache") {
		return fmt.Errorf("cold offline failure did not come from tool installation: %s",
			w.result.output)
	}
	return w.assertPoisonUnused()
}

func (w *smokeWorld) hooksAndSkillsUnchanged() error {
	after, err := snapshotSubtrees(w.root, "hooks", "skills")
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(after, w.mutationsBefore) {
		return fmt.Errorf("hooks or skills changed before workspace convergence")
	}
	return nil
}

func (w *smokeWorld) isolatedGoRepositoryWithUnmanagedHook() error {
	if err := w.newIsolatedRepository(); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.root, ".fake-unmanaged-hook"), []byte("unmanaged\n"), 0o600)
}

func (w *smokeWorld) workspaceFailsDuringPreflight() error {
	if w.result.exitCode == 0 {
		return fmt.Errorf("expected unmanaged-hook preflight failure")
	}
	records, err := readOperationRecords(w.operations)
	if err != nil {
		return err
	}
	expected := []operationRecord{{
		offline: "0", proxy: "https://proxy.golang.org,direct", operation: "preflight go",
	}}
	if !reflect.DeepEqual(records, expected) {
		return fmt.Errorf("unmanaged-hook operations differ: %+v", records)
	}
	if !strings.Contains(w.result.output, "unmanaged hook") {
		return fmt.Errorf("preflight failure did not identify the unmanaged hook: %s",
			w.result.output)
	}
	return w.assertPoisonUnused()
}

func (w *smokeWorld) noToolsDownloaded() error {
	downloads, err := readFileString(w.downloads)
	if err != nil {
		return err
	}
	if downloads != w.downloadsBefore {
		return fmt.Errorf("workspace downloaded before preflight succeeded")
	}
	return nil
}

// InitializeScenario registers step definitions with a fresh world per scenario.
func InitializeScenario(sc *godog.ScenarioContext) {
	w := &smokeWorld{}
	sc.Before(func(ctx context.Context, _ *godog.Scenario) (context.Context, error) {
		*w = smokeWorld{}
		return ctx, nil
	})
	sc.After(func(ctx context.Context, _ *godog.Scenario, _ error) (context.Context, error) {
		if w.root != "" {
			_ = os.RemoveAll(w.root)
		}
		if w.dir != "" {
			_ = os.RemoveAll(w.dir)
		}
		return ctx, nil
	})
	sc.Step(`^an empty directory$`, w.anEmptyDirectory)
	sc.Step(`^the suppressions scanner runs$`, w.theSuppressionsScannerRuns)
	sc.Step(`^it reports zero suppressions$`, w.itReportsZeroSuppressions)
	sc.Step(`^an isolated clean Go template repository$`, w.isolatedCleanGoRepository)
	sc.Step(`^I run the Go workspace target online$`, w.runGoWorkspaceOnline)
	sc.Step(`^the workspace command succeeds$`, w.workspaceCommandSucceeds)
	sc.Step(`^the workspace operations run in order:?$`, w.workspaceOperationsRunInOrder)
	sc.Step(`^an isolated warm Go template repository$`, w.isolatedWarmGoRepository)
	sc.Step(`^I rerun the Go workspace target online and bootstrap offline$`,
		w.rerunGoWorkspaceOnlineAndBootstrapOffline)
	sc.Step(`^both workspace commands succeed$`, w.bothWorkspaceCommandsSucceed)
	sc.Step(`^dependencies use "([^"]+)"$`, w.dependenciesUse)
	sc.Step(`^offline dependency restoration disables the Go proxy$`,
		w.offlineDependencyRestorationDisablesProxy)
	sc.Step(`^the managed workspace snapshot is unchanged$`, w.managedWorkspaceSnapshotUnchanged)
	sc.Step(`^an isolated cold offline Go template repository$`,
		w.isolatedColdOfflineGoRepository)
	sc.Step(`^I run the Go workspace target offline$`, w.runGoWorkspaceOffline)
	sc.Step(`^the workspace command fails during tool installation$`,
		w.workspaceFailsDuringToolInstallation)
	sc.Step(`^neither hooks nor skills are modified$`, w.hooksAndSkillsUnchanged)
	sc.Step(`^an isolated Go template repository with an unmanaged hook$`,
		w.isolatedGoRepositoryWithUnmanagedHook)
	sc.Step(`^the workspace command fails during preflight$`, w.workspaceFailsDuringPreflight)
	sc.Step(`^no tools are downloaded$`, w.noToolsDownloaded)
}
