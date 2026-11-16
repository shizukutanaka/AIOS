// watch.go — repository watch & agent-record commands.
//
// The internal packages behind these subcommands (secretscan, ghaaudit,
// sbom, audit, plantracker, agentevent, sessionsummary) were imported
// from Yagura (github.com/shizukutanaka/Yagura, v0.119.0) when that
// repository was consolidated into AIOS. They are stdlib-only and keep
// their original tests. They are registered as subcommands of the
// existing `security` and `report` commands so the top-level command
// count documented for the Go port is unchanged.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/shizukutanaka/aios/internal/agentevent"
	"github.com/shizukutanaka/aios/internal/audit"
	"github.com/shizukutanaka/aios/internal/ghaaudit"
	"github.com/shizukutanaka/aios/internal/plantracker"
	"github.com/shizukutanaka/aios/internal/sbom"
	"github.com/shizukutanaka/aios/internal/secretscan"
	"github.com/shizukutanaka/aios/internal/sessionsummary"
)

// ── security secrets ────────────────────────────────────

func cmdSecuritySecrets() *cobra.Command {
	return &cobra.Command{
		Use:          "secrets [path...]",
		Short:        "Scan files for leaked secrets (imported from Yagura)",
		SilenceUsage: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			paths := args
			if len(paths) == 0 {
				paths = []string{"."}
			}
			var items []secretscan.ScanItem
			for _, p := range paths {
				found, err := collectTextFiles(p)
				if err != nil {
					return err
				}
				items = append(items, found...)
			}
			result := secretscan.New().ScanBatch(items)
			if jsonFlag {
				if err := printJSON(result); err != nil {
					return err
				}
			} else {
				for _, src := range result.SourceOrder {
					for _, f := range result.BySource[src] {
						fmt.Println("  " + secretscan.FormatSummary(f))
					}
				}
				fmt.Printf("\nScanned %d file(s): %d potential secret(s)\n", len(items), result.Total)
			}
			if result.Total > 0 {
				return fmt.Errorf("found %d potential secret(s)", result.Total)
			}
			return nil
		},
	}
}

// collectTextFiles reads one file, or walks a directory, returning
// scannable text files. VCS internals, oversized files and files that
// look binary are skipped.
func collectTextFiles(root string) ([]secretscan.ScanItem, error) {
	info, err := os.Stat(root)
	if err != nil {
		return nil, err
	}
	var items []secretscan.ScanItem
	add := func(path string) {
		data, err := os.ReadFile(path)
		if err != nil || len(data) > 1<<20 {
			return
		}
		if strings.ContainsRune(string(data), '\x00') {
			return // binary
		}
		items = append(items, secretscan.ScanItem{Source: path, Text: string(data)})
	}
	if !info.IsDir() {
		add(root)
		return items, nil
	}
	err = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if d.Name() == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		add(path)
		return nil
	})
	return items, err
}

// ── security gha ────────────────────────────────────────

func cmdSecurityGHA() *cobra.Command {
	return &cobra.Command{
		Use:          "gha [workflow-dir]",
		Short:        "Audit GitHub Actions workflows (imported from Yagura)",
		Args:         cobra.MaximumNArgs(1),
		SilenceUsage: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			dir := ".github/workflows"
			if len(args) == 1 {
				dir = args[0]
			}
			entries, err := os.ReadDir(dir)
			if err != nil {
				return err
			}
			files := map[string]string{}
			for _, e := range entries {
				name := e.Name()
				if e.IsDir() || !(strings.HasSuffix(name, ".yml") || strings.HasSuffix(name, ".yaml")) {
					continue
				}
				data, err := os.ReadFile(filepath.Join(dir, name))
				if err != nil {
					return err
				}
				files[name] = string(data)
			}
			results := ghaaudit.New().AuditDir(dir, files)
			summary := ghaaudit.Summarize(results)
			if jsonFlag {
				return printJSON(map[string]interface{}{
					"findings": results,
					"summary":  summary,
				})
			}
			names := make([]string, 0, len(results))
			for name := range results {
				names = append(names, name)
			}
			sort.Strings(names)
			for _, name := range names {
				for _, f := range results[name] {
					fmt.Printf("  %s: [%s] %s (line %d)\n", name, f.Severity, f.Description, f.Line)
				}
			}
			fmt.Printf("\nAudited %d workflow file(s): %d finding(s)\n", summary.TotalFiles, summary.TotalFindings)
			for _, sev := range []ghaaudit.Severity{ghaaudit.SeverityCritical, ghaaudit.SeverityHigh} {
				if summary.BySeverity[string(sev)] > 0 {
					return fmt.Errorf("found %d %s finding(s)", summary.BySeverity[string(sev)], sev)
				}
			}
			return nil
		},
	}
}

// ── security sbom ───────────────────────────────────────

func cmdSecuritySBOM() *cobra.Command {
	return &cobra.Command{
		Use:          "sbom",
		Short:        "Print a CycloneDX SBOM for this binary (imported from Yagura)",
		SilenceUsage: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			bom, err := sbom.New().Generate("github.com/shizukutanaka/aios/go-port", version)
			if err != nil {
				return err
			}
			if jsonFlag {
				data, err := bom.JSON()
				if err != nil {
					return err
				}
				fmt.Println(string(data))
				return nil
			}
			s := bom.Summarize()
			fmt.Printf("SBOM for %s %s (CycloneDX %s): %d component(s)\n", s.Application, s.Version, s.SpecVersion, s.TotalComponents)
			return nil
		},
	}
}

// ── security chain ──────────────────────────────────────

func cmdSecurityChain() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "chain",
		Short: "Hash-chained audit log (imported from Yagura)",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:          "verify [dir]",
			Short:        "Verify the hash chain of an audit log directory",
			Args:         cobra.MaximumNArgs(1),
			SilenceUsage: true,
			RunE: func(cmd *cobra.Command, args []string) error {
				dir := "."
				if len(args) == 1 {
					dir = args[0]
				}
				results, err := audit.Verify(dir)
				if err != nil {
					return err
				}
				broken := 0
				for _, r := range results {
					status := "✓"
					if !r.OK {
						status = "✗ " + r.Reason
						broken++
					}
					fmt.Printf("  %s %s (%d records)\n", status, r.File, r.TotalRecords)
				}
				if broken > 0 {
					return fmt.Errorf("audit chain broken in %d file(s)", broken)
				}
				return nil
			},
		},
		&cobra.Command{
			Use:          "append <dir> <kind> [target]",
			Short:        "Append a record to an audit log directory",
			Args:         cobra.RangeArgs(2, 3),
			SilenceUsage: true,
			RunE: func(cmd *cobra.Command, args []string) error {
				logger, err := audit.New(args[0])
				if err != nil {
					return err
				}
				defer logger.Close()
				rec := audit.Record{Kind: args[1], Actor: "aictl"}
				if len(args) == 3 {
					rec.Target = args[2]
				}
				if err := logger.Append(rec); err != nil {
					return err
				}
				fmt.Printf("Appended %q to %s\n", args[1], logger.CurrentFile())
				return nil
			},
		},
		&cobra.Command{
			Use:          "list [dir] [kind]",
			Short:        "List records in an audit log directory",
			Args:         cobra.MaximumNArgs(2),
			SilenceUsage: true,
			RunE: func(cmd *cobra.Command, args []string) error {
				dir, kind := ".", ""
				if len(args) >= 1 {
					dir = args[0]
				}
				if len(args) == 2 {
					kind = args[1]
				}
				records, err := audit.Read(dir, kind)
				if err != nil {
					return err
				}
				if jsonFlag {
					return printJSON(records)
				}
				for _, r := range records {
					fmt.Printf("  #%d %s %s %s\n", r.Seq, r.Time.Format("2006-01-02T15:04:05Z07:00"), r.Kind, r.Target)
				}
				fmt.Printf("\n%d record(s)\n", len(records))
				return nil
			},
		},
	)
	return cmd
}

// ── report plan ─────────────────────────────────────────

func cmdReportPlan() *cobra.Command {
	return &cobra.Command{
		Use:          "plan <file>",
		Short:        "Measure progress of a Plan.md file (imported from Yagura)",
		Args:         cobra.ExactArgs(1),
		SilenceUsage: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			data, err := os.ReadFile(args[0])
			if err != nil {
				return err
			}
			state := plantracker.Parse(string(data))
			if jsonFlag {
				return printJSON(state)
			}
			fmt.Printf("Progress: %d/%d tasks (%d%%)\n", state.CompletedTasks, state.TotalTasks, state.ProgressPct)
			if state.CurrentPhase != "" {
				fmt.Printf("Current phase: %s\n", state.CurrentPhase)
			}
			for _, p := range state.Phases {
				fmt.Printf("  - %s: %d/%d\n", p.Name, p.CompletedTasks, p.TotalTasks)
			}
			if !state.IsHealthy {
				for _, issue := range state.Issues {
					fmt.Printf("  ! %s\n", issue)
				}
			}
			return nil
		},
	}
}

// ── report sessions ─────────────────────────────────────

func cmdReportSessions() *cobra.Command {
	return &cobra.Command{
		Use:          "sessions <file>",
		Short:        "Summarize agent session events from a JSONL file (imported from Yagura)",
		Args:         cobra.ExactArgs(1),
		SilenceUsage: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			data, err := os.ReadFile(args[0])
			if err != nil {
				return err
			}
			var events []agentevent.Event
			skipped := 0
			for _, line := range strings.Split(string(data), "\n") {
				line = strings.TrimSpace(line)
				if line == "" {
					continue
				}
				ev, err := agentevent.NormalizeJSON([]byte(line))
				if err != nil {
					skipped++
					continue
				}
				events = append(events, ev)
			}
			summary := sessionsummary.Summarize(events)
			if jsonFlag {
				return printJSON(summary)
			}
			fmt.Println(summary.Summary)
			fmt.Printf("Events: %d, tool invocations: %d, error rate: %.1f%%\n",
				summary.Events, summary.ToolInvocations, summary.ErrorRate*100)
			for _, a := range summary.Anomalies {
				fmt.Printf("  ! %s\n", a)
			}
			if skipped > 0 {
				fmt.Printf("  (%d line(s) skipped: unparseable)\n", skipped)
			}
			return nil
		},
	}
}
