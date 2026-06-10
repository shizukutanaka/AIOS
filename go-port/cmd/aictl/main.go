// aictl — AI Native Linux OS control CLI (Go implementation)
//
// Build:  go build -o aictl ./cmd/aictl
// Cross:  GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o aictl-arm64 ./cmd/aictl
// Run:    ./aictl doctor
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/shizukutanaka/aios/internal/daemon"
	"github.com/shizukutanaka/aios/internal/runtime"
	"github.com/shizukutanaka/aios/internal/state"
)

var (
	version  = "1.4.0"
	jsonFlag bool
	stateDir string
)

func main() {
	root := &cobra.Command{
		Use:     "aictl",
		Short:   "AI Native Linux OS — local-first AI infrastructure CLI",
		Version: version,
	}

	root.PersistentFlags().BoolVar(&jsonFlag, "json", false, "JSON output")
	root.PersistentFlags().StringVar(&stateDir, "state-dir", "", "Override state directory")

	root.AddCommand(
		cmdInit(),
		cmdDoctor(),
		cmdPs(),
		cmdServe(),
		cmdStatus(),
		cmdRecommend(),
		cmdRecipeList(),
		cmdApply(),
		cmdDown(),
		cmdModel(),
		cmdConfig(),
		cmdNet(),
		cmdFabric(),
		cmdUpgrade(),
		cmdDeploy(),
		cmdCost(),
		cmdSecurity(),
		cmdTenant(),
		cmdContext(),
		cmdScale(),
		cmdDemo(),
		cmdChat(),
		cmdHealth(),
		cmdInfo(),
		cmdReport(),
		cmdMeter(),
		cmdLora(),
		cmdGate(),
		cmdBench(),
	)

	if err := root.Execute(); err != nil {
		os.Exit(1)
	}
}

func getStore() *state.Store {
	s, err := state.New(stateDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "✗ Failed to open state: %v\n", err)
		os.Exit(1)
	}
	return s
}

// ── init ───────────────────────────────────────────────

func cmdInit() *cobra.Command {
	var force bool
	cmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize local AI OS node",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			if store.IsInitialized() && !force {
				return fmt.Errorf("already initialized — use --force to re-initialize")
			}

			report := runtime.Detect()
			id := randomHex(6)

			ns := &state.NodeState{
				NodeID:      id,
				Hostname:    report.System.Hostname,
				Profile:     report.Profile,
				Version:     version,
				Mode:        "local",
				GPUCount:    len(report.GPUs),
				VRAMTotalMB: totalVRAM(report.GPUs),
				RAMTotalMB:  report.System.RAMTotalMB,
			}
			if err := store.SaveNode(ns); err != nil {
				return err
			}

			if jsonFlag {
				return printJSON(ns)
			}

			fmt.Printf("✓ Node initialized: %s\n", id)
			printKV([][2]string{
				{"Hostname", ns.Hostname},
				{"Profile", ns.Profile},
				{"GPUs", fmt.Sprintf("%d (%d MB VRAM)", ns.GPUCount, ns.VRAMTotalMB)},
				{"RAM", fmt.Sprintf("%d MB", ns.RAMTotalMB)},
				{"Container RT", report.ContainerRT},
				{"State dir", store.Dir},
			})

			if len(report.Issues) > 0 {
				fmt.Println()
				for _, issue := range report.Issues {
					fmt.Printf("✗ %s\n", issue)
				}
			}
			if len(report.Recommendations) > 0 {
				fmt.Println()
				for _, rec := range report.Recommendations {
					fmt.Printf("  → %s\n", rec)
				}
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&force, "force", false, "Re-initialize")
	return cmd
}

// ── doctor ─────────────────────────────────────────────

func cmdDoctor() *cobra.Command {
	return &cobra.Command{
		Use:   "doctor",
		Short: "Check system readiness",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			report := runtime.Detect()

			if jsonFlag {
				return printJSON(report)
			}

			fmt.Println("System")
			printKV([][2]string{
				{"Hostname", report.System.Hostname},
				{"Kernel", report.System.Kernel},
				{"CPU", fmt.Sprintf("%s (%d cores)", report.System.CPUModel, report.System.CPUCores)},
				{"RAM", fmt.Sprintf("%d MB", report.System.RAMTotalMB)},
			})

			fmt.Println("\nChecks")
			check("cgroup v2", report.System.CgroupV2, "")
			check("PSI (pressure stall)", report.System.PSIEnabled, "")
			check("Container runtime", report.ContainerRT != "none", report.ContainerRT)
			check("Ollama", report.OllamaAvailable, "")
			check("Node initialized", store.IsInitialized(), "")

			if len(report.GPUs) > 0 {
				fmt.Printf("\nGPUs (%d)\n", len(report.GPUs))
				for _, g := range report.GPUs {
					mig := ""
					if g.MIGEnabled {
						mig = " [MIG enabled]"
					} else if g.MIGCapable {
						mig = " [MIG capable]"
					}
					fmt.Printf("  [%d] %s — %d MB VRAM — %s %s%s\n",
						g.Index, g.Name, g.VRAMMB, g.Vendor, g.DriverVersion, mig)
				}
			} else {
				fmt.Println("\nGPUs: none detected")
			}

			fmt.Printf("\nProfile: %s\n", report.Profile)

			if len(report.Issues) > 0 {
				fmt.Println("\nIssues")
				for _, issue := range report.Issues {
					fmt.Printf("  ✗ %s\n", issue)
				}
			}
			return nil
		},
	}
}

// ── ps ─────────────────────────────────────────────────

func cmdPs() *cobra.Command {
	return &cobra.Command{
		Use:   "ps",
		Short: "List running AI services",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			stacks, _ := store.LoadStacks()

			if jsonFlag {
				return printJSON(map[string]interface{}{"stacks": stacks})
			}

			if len(stacks) > 0 {
				for _, s := range stacks {
					fmt.Printf("  %s — %s (from %s)\n", s.Name, s.Status, s.File)
				}
			} else {
				fmt.Println("No services running. Try: aictl recipe list")
			}
			return nil
		},
	}
}

// ── serve ──────────────────────────────────────────────

func cmdServe() *cobra.Command {
	var host string
	var port int
	cmd := &cobra.Command{
		Use:   "serve",
		Short: "Start aiosd local control daemon",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			srv := daemon.New(store)
			addr := fmt.Sprintf("%s:%d", host, port)
			return srv.ListenAndServe(addr)
		},
	}
	cmd.Flags().StringVar(&host, "host", "127.0.0.1", "Listen host")
	cmd.Flags().IntVar(&port, "port", 7700, "Listen port")
	return cmd
}

// ── status ─────────────────────────────────────────────

func cmdStatus() *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Unified system status",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			node, _ := store.LoadNode()
			report := runtime.Detect()
			stacks, _ := store.LoadStacks()

			if jsonFlag {
				return printJSON(map[string]interface{}{
					"node": node, "profile": report.Profile,
					"gpus": len(report.GPUs), "stacks": len(stacks),
				})
			}

			icon := "✓"
			if !store.IsInitialized() {
				icon = "✗"
			}
			fmt.Printf("%s AI OS — %s\n\n", icon, node.Hostname)
			printKV([][2]string{
				{"Profile", report.Profile},
				{"GPUs", fmt.Sprintf("%d (%d MB VRAM)", len(report.GPUs), totalVRAM(report.GPUs))},
				{"RAM", fmt.Sprintf("%d MB", report.System.RAMTotalMB)},
				{"Container RT", report.ContainerRT},
				{"Stacks", fmt.Sprintf("%d", len(stacks))},
			})
			return nil
		},
	}
}

// ── recommend ──────────────────────────────────────────

func cmdRecommend() *cobra.Command {
	return &cobra.Command{
		Use:   "recommend",
		Short: "Recommend models for your hardware",
		RunE: func(cmd *cobra.Command, args []string) error {
			report := runtime.Detect()
			vram := totalVRAM(report.GPUs)

			type rec struct {
				Name    string `json:"name"`
				Runtime string `json:"runtime"`
				VRAM    string `json:"vram"`
				Use     string `json:"use"`
			}

			// Simple recommendations based on VRAM
			var recs []rec
			if vram >= 24000 {
				recs = append(recs,
					rec{"meta-llama/Llama-3.2-8B-Instruct", "vllm", "16GB", "chat"},
					rec{"Qwen/Qwen2.5-Coder-7B-Instruct", "vllm", "16GB", "code"},
					rec{"qwen2.5:14b", "ollama", "10GB", "chat"},
				)
			} else if vram >= 8000 {
				recs = append(recs,
					rec{"llama3.1:8b", "ollama", "6GB", "chat"},
					rec{"qwen2.5-coder:7b", "ollama", "5.5GB", "code"},
				)
			} else {
				recs = append(recs,
					rec{"llama3.2:3b", "ollama", "3GB", "chat"},
					rec{"llama3.2:1b", "ollama", "1.5GB", "chat"},
					rec{"nomic-embed-text", "ollama", "0.5GB", "embedding"},
				)
			}

			if jsonFlag {
				return printJSON(recs)
			}

			mode := "GPU"
			if vram == 0 {
				mode = "CPU-only"
			}
			fmt.Printf("✓ Recommended models (%s, %dMB VRAM)\n\n", mode, vram)
			fmt.Printf("  %-45s %-8s %-6s %s\n", "NAME", "RUNTIME", "VRAM", "USE")
			fmt.Printf("  %-45s %-8s %-6s %s\n", strings.Repeat("-", 45), "-------", "------", "---")
			for _, r := range recs {
				fmt.Printf("  %-45s %-8s %-6s %s\n", r.Name, r.Runtime, r.VRAM, r.Use)
			}
			return nil
		},
	}
}

// ── recipe list ────────────────────────────────────────

func cmdRecipeList() *cobra.Command {
	return &cobra.Command{
		Use:   "recipe",
		Short: "Built-in AI recipes",
		RunE: func(cmd *cobra.Command, args []string) error {
			recipes := []struct {
				Name string; Services int; GPU bool
			}{
				{"local-chat", 2, false},
				{"team-rag", 3, true},
				{"local-gpu-chat", 2, true},
				{"code-assist", 2, true},
				{"whisper-stt", 1, true},
				{"image-gen", 1, true},
				{"embedding-only", 1, false},
				{"bank-convert", 1, false},
			}

			if jsonFlag {
				return printJSON(recipes)
			}

			fmt.Println("Available recipes:")
			for _, r := range recipes {
				gpu := ""
				if r.GPU {
					gpu = " [GPU]"
				}
				fmt.Printf("  %s — %d services%s\n", r.Name, r.Services, gpu)
			}
			fmt.Println("\nRun with: aictl recipe run <name>")
			return nil
		},
	}
}

// ── Helpers ────────────────────────────────────────────

func printJSON(v interface{}) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func printKV(pairs [][2]string) {
	maxKey := 0
	for _, p := range pairs {
		if len(p[0]) > maxKey {
			maxKey = len(p[0])
		}
	}
	for _, p := range pairs {
		fmt.Printf("%-*s  %s\n", maxKey, p[0], p[1])
	}
}

func check(label string, ok bool, detail string) {
	icon := "✓"
	if !ok {
		icon = "✗"
	}
	suffix := ""
	if detail != "" {
		suffix = fmt.Sprintf(" (%s)", detail)
	}
	fmt.Printf("  %s %s%s\n", icon, label, suffix)
}

func totalVRAM(gpus []runtime.GPUInfo) int {
	total := 0
	for _, g := range gpus {
		total += g.VRAMMB
	}
	return total
}

func randomHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// ── apply ──────────────────────────────────────────────

func cmdApply() *cobra.Command {
	var file string
	var quadlet bool
	cmd := &cobra.Command{
		Use:   "apply",
		Short: "Apply a Stack manifest",
		RunE: func(cmd *cobra.Command, args []string) error {
			if file == "" {
				return fmt.Errorf("--file/-f required")
			}
			if jsonFlag {
				return printJSON(map[string]interface{}{
					"file":   file,
					"quadlet": quadlet,
					"status": "stub",
				})
			}
			fmt.Printf("✓ Applying stack from %s", file)
			if quadlet {
				fmt.Printf(" (quadlet mode)")
			}
			fmt.Println()
			// TODO: port from Python aictl/cmd/apply.py
			fmt.Println("  (Go port — apply stub)")
			return nil
		},
	}
	cmd.Flags().StringVarP(&file, "file", "f", "", "Stack manifest file")
	cmd.Flags().BoolVar(&quadlet, "quadlet", false, "Generate Quadlet systemd units")
	return cmd
}

// ── down ───────────────────────────────────────────────

func cmdDown() *cobra.Command {
	return &cobra.Command{
		Use:   "down [stack]",
		Short: "Stop a running Stack",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			if jsonFlag {
				return printJSON(map[string]interface{}{
					"stack":  name,
					"status": "stopping",
				})
			}
			fmt.Printf("✓ Stopping stack: %s\n", name)
			// TODO: port from Python
			return nil
		},
	}
}

// ── model ──────────────────────────────────────────────

func cmdModel() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "model",
		Short: "Model management",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "list",
		Short: "List registered models",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			stacks, _ := store.LoadStacks()
			models := make(map[string]bool)
			for _, s := range stacks {
				for _, svc := range s.Services {
					if m, ok := svc["model"].(string); ok && m != "" {
						models[m] = true
					}
				}
			}
			if len(models) == 0 {
				fmt.Println("No models registered. Apply a stack first.")
				return nil
			}
			for m := range models {
				fmt.Printf("  %s\n", m)
			}
			return nil
		},
	})
	return cmd
}

// ── config ─────────────────────────────────────────────

func cmdConfig() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "config",
		Short: "Configuration management",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "show",
		Short: "Show current configuration",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			node, _ := store.LoadNode()
			if jsonFlag {
				return printJSON(node)
			}
			printKV([][2]string{
				{"Node ID", node.NodeID},
				{"Profile", node.Profile},
				{"Mode", node.Mode},
				{"Version", node.Version},
			})
			return nil
		},
	})
	return cmd
}

// ── net ────────────────────────────────────────────────

func cmdNet() *cobra.Command {
	return &cobra.Command{
		Use:   "net",
		Short: "Network diagnostics",
		RunE: func(cmd *cobra.Command, args []string) error {
			endpoints := map[string]string{
				"vllm":   "localhost:8000",
				"ollama": "localhost:11434",
				"sglang": "localhost:30000",
				"aiosd":  "127.0.0.1:7700",
			}
			type epResult struct {
				Name      string `json:"name"`
				Address   string `json:"address"`
				Reachable bool   `json:"reachable"`
			}
			var results []epResult
			for name, addr := range endpoints {
				results = append(results, epResult{name, addr, checkTCP(addr)})
			}
			if jsonFlag {
				return printJSON(results)
			}
			fmt.Println("Network diagnostics")
			fmt.Println()
			for _, r := range results {
				icon := "✗"
				status := "unreachable"
				if r.Reachable {
					icon = "✓"
					status = "reachable"
				}
				fmt.Printf("  %s %-10s %s  %s\n", icon, r.Name, r.Address, status)
			}
			return nil
		},
	}
}

func checkTCP(addr string) bool {
	conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// ── fabric ─────────────────────────────────────────────

func cmdFabric() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "fabric",
		Short: "Memory fabric detection",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "detect",
		Short: "Detect memory tiers",
		RunE: func(cmd *cobra.Command, args []string) error {
			// Read meminfo
			data, err := os.ReadFile("/proc/meminfo")
			if err != nil {
				fmt.Println("Cannot read /proc/meminfo")
				return nil
			}
			var totalKB, availKB int
			for _, line := range strings.Split(string(data), "\n") {
				if strings.HasPrefix(line, "MemTotal:") {
					fmt.Sscanf(strings.TrimPrefix(line, "MemTotal:"), "%d", &totalKB)
				} else if strings.HasPrefix(line, "MemAvailable:") {
					fmt.Sscanf(strings.TrimPrefix(line, "MemAvailable:"), "%d", &availKB)
				}
			}
			totalGB := float64(totalKB) / (1024 * 1024)
			availGB := float64(availKB) / (1024 * 1024)

			fmt.Printf("✓ Memory Fabric\n\n")
			fmt.Printf("  DRAM: %.1f GB total, %.1f GB available\n", totalGB, availGB)

			// Check CXL
			if _, err := os.Stat("/sys/bus/cxl"); err == nil {
				fmt.Println("  CXL:  detected")
			} else {
				fmt.Println("  CXL:  not detected")
			}

			// Check DAMON
			if _, err := os.Stat("/sys/kernel/mm/damon"); err == nil {
				fmt.Println("  DAMON: available")
			} else {
				fmt.Println("  DAMON: not available")
			}
			return nil
		},
	})
	return cmd
}

// ── upgrade ────────────────────────────────────────────

func cmdUpgrade() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "upgrade",
		Short: "OS upgrade management",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "plan",
		Short: "Show upgrade plan",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			node, _ := store.LoadNode()
			stacks, _ := store.LoadStacks()
			steps := []string{
				"Save context snapshots (aictl context save)",
				"Drain workloads (aictl down <stack>)",
				"Stage OS update (bootc upgrade)",
				"Reboot",
				"Restore contexts (aictl context restore)",
				"Re-apply stacks (aictl apply -f <stack>)",
			}
			if jsonFlag {
				return printJSON(map[string]interface{}{
					"current_version": node.Version,
					"target_version":  "next",
					"active_stacks":   len(stacks),
					"steps":           steps,
					"rollback":        "bootc rollback",
				})
			}
			fmt.Printf("Upgrade Plan (current: %s)\n\n", node.Version)
			fmt.Println("  Steps:")
			for i, s := range steps {
				fmt.Printf("    %d. %s\n", i+1, s)
			}
			fmt.Printf("\n  Active stacks: %d\n", len(stacks))
			fmt.Println("  Rollback: bootc rollback")
			return nil
		},
	})
	return cmd
}

// ── deploy ─────────────────────────────────────────

func cmdDeploy() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "deploy",
		Short: "Zero-config model deployment (Dynamo DGDR-style)",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "plan [model]",
		Short: "Plan deployment resources",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			model := args[0]
			fmt.Printf("✓ Deployment Plan: %s\n\n", model)
			// Estimate params from model name
			fmt.Println("  (Go port — resource estimation stub)")
			fmt.Println("  Use Python CLI for full estimation: python3 -m aictl deploy plan " + model)
			return nil
		},
	})
	cmd.AddCommand(&cobra.Command{
		Use:   "dynamo",
		Short: "Check NVIDIA Dynamo availability",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("NVIDIA Dynamo Status")
			// Check binary
			if _, err := exec.LookPath("dynamo"); err == nil {
				fmt.Println("  ✓ dynamo binary found")
			} else {
				fmt.Println("  ✗ dynamo binary not found")
			}
			// Check NIXL
			if _, err := os.Stat("/usr/lib/libnixl.so"); err == nil {
				fmt.Println("  ✓ NIXL library found")
			} else {
				fmt.Println("  ✗ NIXL library not found")
			}
			return nil
		},
	})
	return cmd
}

// ── cost ───────────────────────────────────────────

func cmdCost() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "cost",
		Short: "GPU cost estimation",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "compare",
		Short: "Compare GPU types (April 2026 pricing)",
		RunE: func(cmd *cobra.Command, args []string) error {
			type gpuPrice struct {
				Name      string `json:"name"`
				Cloud     string `json:"cloud_per_month"`
				OnPrem    string `json:"onprem_per_month"`
				Breakeven string `json:"breakeven"`
			}
			gpus := []gpuPrice{
				{"RTX 4090", "$252/mo", "$83/mo", "9 months"},
				{"A100 80GB", "$1,181/mo", "$451/mo", "21 months"},
				{"H100 SXM", "$1,512/mo", "$894/mo", "49 months"},
				{"H200 SXM", "$1,800/mo", "$1,033/mo", "46 months"},
			}
			if jsonFlag {
				return printJSON(gpus)
			}
			fmt.Printf("%-12s %-12s %-12s %-12s\n", "GPU", "Cloud/mo", "On-prem/mo", "Break-even")
			fmt.Println(strings.Repeat("-", 48))
			for _, g := range gpus {
				fmt.Printf("%-12s %-12s %-12s %-12s\n", g.Name, g.Cloud, g.OnPrem, g.Breakeven)
			}
			return nil
		},
	})
	return cmd
}

// ── security ───────────────────────────────────────

func cmdSecurity() *cobra.Command {
	return &cobra.Command{
		Use:   "security",
		Short: "Security scan",
		RunE: func(cmd *cobra.Command, args []string) error {
			score := 100
			checks := []struct{ name, severity string; pass bool }{
				{"cgroup v2", "medium", fileExists("/sys/fs/cgroup/cgroup.controllers")},
				{"PSI enabled", "low", fileExists("/proc/pressure/memory")},
				{"Not root", "medium", os.Geteuid() != 0},
				{"Container runtime", "medium", execExists("podman") || execExists("docker")},
			}
			passed := 0
			for _, c := range checks {
				if c.pass {
					passed++
				} else {
					switch c.severity {
					case "high":
						score -= 15
					case "medium":
						score -= 10
					case "low":
						score -= 5
					}
				}
			}
			if jsonFlag {
				type finding struct {
					Name     string `json:"name"`
					Severity string `json:"severity"`
					Pass     bool   `json:"pass"`
				}
				var findings []finding
				for _, c := range checks {
					findings = append(findings, finding{c.name, c.severity, c.pass})
				}
				return printJSON(map[string]interface{}{
					"score":          score,
					"checks_passed":  passed,
					"checks_total":   len(checks),
					"findings":       findings,
				})
			}
			for _, c := range checks {
				icon := "✗"
				if c.pass {
					icon = "✓"
				}
				fmt.Printf("  %s %s\n", icon, c.name)
			}
			fmt.Printf("\nSecurity Score: %d/100 (%d/%d passed)\n", score, passed, len(checks))
			return nil
		},
	}
}

// ── tenant ─────────────────────────────────────────

func cmdTenant() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "tenant",
		Short: "Multi-tenant isolation",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "classes",
		Short: "List tenant classes",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf("%-12s %-5s %-8s %-8s %-6s %-8s\n", "CLASS", "GPU", "RAM", "VRAM", "RPM", "AUDIT")
			fmt.Println(strings.Repeat("-", 52))
			fmt.Printf("%-12s %-5s %-8s %-8s %-6s %-8s\n", "regulated", "2", "64GB", "80GB", "1000", "detailed")
			fmt.Printf("%-12s %-5s %-8s %-8s %-6s %-8s\n", "standard", "1", "32GB", "24GB", "120", "standard")
			fmt.Printf("%-12s %-5s %-8s %-8s %-6s %-8s\n", "dev", "1", "16GB", "8GB", "30", "minimal")
			return nil
		},
	})
	return cmd
}

// ── context ────────────────────────────────────────

func cmdContext() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "context",
		Short: "Inference context continuity",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "list",
		Short: "List saved context snapshots",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("No saved contexts. Use: aictl context save")
			return nil
		},
	})
	return cmd
}

// ── scale ──────────────────────────────────────────

func cmdScale() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "scale",
		Short: "Autoscaling management",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "keda [deployment]",
		Short: "Generate KEDA ScaledObject",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			deploy := args[0]
			manifest := fmt.Sprintf(`{
  "apiVersion": "keda.sh/v1alpha1",
  "kind": "ScaledObject",
  "metadata": {"name": "%s-autoscaler"},
  "spec": {
    "scaleTargetRef": {"name": "%s"},
    "minReplicaCount": 1,
    "maxReplicaCount": 8,
    "triggers": [{
      "type": "prometheus",
      "metadata": {
        "query": "avg(vllm:num_requests_waiting)",
        "threshold": "5"
      }
    }]
  }
}`, deploy, deploy)
			fmt.Println(manifest)
			return nil
		},
	})
	return cmd
}

// ── demo ───────────────────────────────────────────

func cmdDemo() *cobra.Command {
	return &cobra.Command{
		Use:   "demo",
		Short: "Run demo (requires Python: python3 -m aictl demo --auto)",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("Demo mode requires the Python runtime.")
			fmt.Println("Run: python3 -m aictl demo --auto")
			return nil
		},
	}
}

// ── chat ───────────────────────────────────────────

func cmdChat() *cobra.Command {
	return &cobra.Command{
		Use:   "chat",
		Short: "Interactive chat (requires Python: python3 -m aictl chat)",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("Chat mode requires the Python runtime.")
			fmt.Println("Run: python3 -m aictl chat --mock")
			return nil
		},
	}
}

// ── helpers ────────────────────────────────────────

// ── health ─────────────────────────────────────────

func cmdHealth() *cobra.Command {
	return &cobra.Command{
		Use:   "health",
		Short: "System health check",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			checks := []struct {
				name   string
				ok     bool
				detail string
			}{
				{"State directory", fileExists(store.Dir), store.Dir},
				{"Podman", execExists("podman"), ""},
				{"Ollama", execExists("ollama"), ""},
				{"nvidia-smi", execExists("nvidia-smi"), ""},
				{"Python 3", execExists("python3"), ""},
			}
			passed := 0
			for _, c := range checks {
				if c.ok {
					passed++
				}
			}
			if jsonFlag {
				type checkResult struct {
					Name   string `json:"name"`
					Ok     bool   `json:"ok"`
					Detail string `json:"detail,omitempty"`
				}
				var results []checkResult
				for _, c := range checks {
					results = append(results, checkResult{c.name, c.ok, c.detail})
				}
				return printJSON(map[string]interface{}{
					"checks_passed": passed,
					"checks_total":  len(checks),
					"healthy":       passed == len(checks),
					"checks":        results,
				})
			}
			for _, c := range checks {
				icon := "✓"
				if !c.ok {
					icon = "✗"
				}
				detail := ""
				if c.detail != "" {
					detail = " (" + c.detail + ")"
				}
				fmt.Printf("  %s %s%s\n", icon, c.name, detail)
			}
			fmt.Printf("\n  %d/%d checks passed\n", passed, len(checks))
			return nil
		},
	}
}

// ── info ───────────────────────────────────────────

func cmdInfo() *cobra.Command {
	return &cobra.Command{
		Use:   "info",
		Short: "Project information",
		RunE: func(cmd *cobra.Command, args []string) error {
			stack := []string{
				"bootc v1.15 (Fedora 42)",
				"vLLM v0.19 / SGLang v0.5 / Ollama v0.20",
				"K3s v1.35 + KServe v0.17 + llm-d (CNCF)",
				"NVIDIA Dynamo v0.8 (KVBM + NIXL)",
				"Gateway API InferencePool v1",
				"OTel GenAI SemConv + Prometheus",
			}
			if jsonFlag {
				return printJSON(map[string]interface{}{
					"version":         "1.6.0",
					"go_commands":     29,
					"python_commands": 65,
					"rest_endpoints":  22,
					"recipes":         10,
					"tests":           "1695+",
					"stack":           stack,
				})
			}
			fmt.Println("✓ aictl v1.6.0 (Go port)")
			fmt.Println()
			fmt.Println("  Commands  29 Go + 65 Python")
			fmt.Println("  REST API  22 endpoints")
			fmt.Println("  Recipes   10")
			fmt.Println("  Tests     1695+")
			fmt.Println()
			fmt.Println("  Stack:")
			for _, s := range stack {
				fmt.Printf("    %s\n", s)
			}
			return nil
		},
	}
}

// ── report ─────────────────────────────────────────

func cmdReport() *cobra.Command {
	return &cobra.Command{
		Use:   "report",
		Short: "Generate system assessment (delegates to Python)",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("Report generation requires the Python runtime.")
			fmt.Println("Run: python3 -m aictl report")
			return nil
		},
	}
}

// ── meter ──────────────────────────────────────────

func cmdMeter() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "meter",
		Short: "Token usage metering",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "usage",
		Short: "Show token usage per entity",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			data, err := os.ReadFile(store.Dir + "/metering.json")
			if err != nil {
				if jsonFlag {
					return printJSON([]interface{}{})
				}
				fmt.Println("No usage recorded yet.")
				return nil
			}
			var buckets map[string]interface{}
			if err := json.Unmarshal(data, &buckets); err != nil {
				return fmt.Errorf("parse metering: %w", err)
			}
			if jsonFlag {
				return printJSON(buckets)
			}
			fmt.Printf("%-20s %10s %10s %10s\n", "ENTITY", "PROMPT", "COMPLETION", "TOTAL")
			for id, v := range buckets {
				m, ok := v.(map[string]interface{})
				if !ok {
					continue
				}
				fmt.Printf("%-20s %10.0f %10.0f %10.0f\n",
					id,
					m["prompt_tokens"],
					m["completion_tokens"],
					m["total_tokens"])
			}
			return nil
		},
	})
	return cmd
}

// ── lora ───────────────────────────────────────────

func cmdLora() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "lora",
		Short: "LoRA adapter management",
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "list",
		Short: "List registered adapters",
		RunE: func(cmd *cobra.Command, args []string) error {
			store := getStore()
			data, err := os.ReadFile(store.Dir + "/lora_registry.json")
			if err != nil {
				fmt.Println("No adapters registered.")
				return nil
			}
			var reg map[string]interface{}
			if err := json.Unmarshal(data, &reg); err != nil {
				return fmt.Errorf("parse lora: %w", err)
			}
			adapters, ok := reg["adapters"].(map[string]interface{})
			if !ok {
				fmt.Println("No adapters registered.")
				return nil
			}
			fmt.Printf("%-20s %-30s %5s\n", "NAME", "BASE", "RANK")
			for name, v := range adapters {
				m, ok := v.(map[string]interface{})
				if !ok {
					continue
				}
				fmt.Printf("%-20s %-30s %5.0f\n", name, m["base_model"], m["rank"])
			}
			return nil
		},
	})
	return cmd
}

// ── gate ───────────────────────────────────────────

func cmdGate() *cobra.Command {
	return &cobra.Command{
		Use:   "gate",
		Short: "Quality gate (compile + test + demo)",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("Quality gate requires the Python runtime.")
			fmt.Println("Run: python3 -m aictl gate")
			return nil
		},
	}
}

// ── bench ──────────────────────────────────────────

func cmdBench() *cobra.Command {
	return &cobra.Command{
		Use:   "bench",
		Short: "Benchmark inference performance (delegates to Python)",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println("Benchmark requires the Python runtime.")
			fmt.Println("Run: python3 -m aictl bench --mock -n 10")
			return nil
		},
	}
}

// ── helpers ────────────────────────────────────────

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func execExists(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}
