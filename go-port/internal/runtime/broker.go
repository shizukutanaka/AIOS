// Package runtime implements hardware detection and profile selection.
package runtime

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

// GPUInfo describes a detected GPU.
type GPUInfo struct {
	Index         int    `json:"index"`
	Name          string `json:"name"`
	Vendor        string `json:"vendor"`
	VRAMMB        int    `json:"vram_mb"`
	DriverVersion string `json:"driver_version"`
	ComputeCap    string `json:"compute_cap"`
	MIGCapable    bool   `json:"mig_capable"`
	MIGEnabled    bool   `json:"mig_enabled"`
}

// NPUInfo describes a detected NPU.
type NPUInfo struct {
	Name          string `json:"name"`
	Vendor        string `json:"vendor"`
	DriverLoaded  bool   `json:"driver_loaded"`
	Runtime       string `json:"runtime"`
}

// SystemInfo describes the host system.
type SystemInfo struct {
	Hostname   string  `json:"hostname"`
	CPUModel   string  `json:"cpu_model"`
	CPUCores   int     `json:"cpu_cores"`
	RAMTotalMB int     `json:"ram_total_mb"`
	SwapMB     int     `json:"swap_total_mb"`
	DiskFreeGB float64 `json:"disk_free_gb"`
	Kernel     string  `json:"kernel"`
	CgroupV2   bool    `json:"cgroup_v2"`
	PSIEnabled bool    `json:"psi_enabled"`
}

// Report is the full hardware detection result.
type Report struct {
	System          SystemInfo `json:"system"`
	GPUs            []GPUInfo  `json:"gpus"`
	NPUs            []NPUInfo  `json:"npus"`
	Profile         string     `json:"profile"`
	ContainerRT     string     `json:"container_runtime"`
	OllamaAvailable bool       `json:"ollama_available"`
	Issues          []string   `json:"issues"`
	Recommendations []string   `json:"recommendations"`
}

// Detect runs full hardware detection.
func Detect() *Report {
	r := &Report{}
	r.System = detectSystem()
	r.GPUs = append(detectNVIDIA(), detectAMD()...)
	r.NPUs = detectNPUs()
	r.Profile = selectProfile(r.GPUs, r.NPUs)
	r.ContainerRT = detectContainerRT()
	r.OllamaAvailable = commandExists("ollama")

	// Issues
	if !r.System.CgroupV2 {
		r.Issues = append(r.Issues, "cgroup v2 not detected")
	}
	if !r.System.PSIEnabled {
		r.Issues = append(r.Issues, "PSI not enabled")
	}
	if r.ContainerRT == "none" {
		r.Issues = append(r.Issues, "No container runtime found")
		r.Recommendations = append(r.Recommendations, "Install podman: sudo dnf install podman")
	}
	if len(r.GPUs) == 0 {
		r.Recommendations = append(r.Recommendations, "No GPU detected — CPU-only mode")
	}

	return r
}

func detectSystem() SystemInfo {
	si := SystemInfo{
		CPUCores: runtime.NumCPU(),
	}

	// Hostname
	si.Hostname, _ = os.Hostname()

	// Kernel
	if data, err := os.ReadFile("/proc/version"); err == nil {
		parts := strings.Fields(string(data))
		if len(parts) >= 3 {
			si.Kernel = parts[2]
		}
	}

	// CPU model
	if f, err := os.Open("/proc/cpuinfo"); err == nil {
		defer f.Close()
		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			line := scanner.Text()
			if strings.HasPrefix(line, "model name") {
				parts := strings.SplitN(line, ":", 2)
				if len(parts) == 2 {
					si.CPUModel = strings.TrimSpace(parts[1])
					break
				}
			}
		}
	}

	// RAM + Swap
	if f, err := os.Open("/proc/meminfo"); err == nil {
		defer f.Close()
		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			line := scanner.Text()
			if strings.HasPrefix(line, "MemTotal:") {
				si.RAMTotalMB = parseMeminfoKB(line) / 1024
			} else if strings.HasPrefix(line, "SwapTotal:") {
				si.SwapMB = parseMeminfoKB(line) / 1024
			}
		}
	}

	// Disk free
	// Use syscall.Statfs on Linux
	si.DiskFreeGB = getDiskFreeGB("/")

	// cgroup v2
	_, err := os.Stat("/sys/fs/cgroup/cgroup.controllers")
	si.CgroupV2 = err == nil

	// PSI
	_, err = os.Stat("/proc/pressure/memory")
	si.PSIEnabled = err == nil

	return si
}

func detectNVIDIA() []GPUInfo {
	if !commandExists("nvidia-smi") {
		return nil
	}

	out, err := exec.Command("nvidia-smi",
		"--query-gpu=index,name,memory.total,driver_version,compute_cap,mig.mode.current",
		"--format=csv,noheader,nounits",
	).Output()
	if err != nil {
		return nil
	}

	var gpus []GPUInfo
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		parts := strings.Split(line, ",")
		if len(parts) < 5 {
			continue
		}
		for i := range parts {
			parts[i] = strings.TrimSpace(parts[i])
		}
		idx, _ := strconv.Atoi(parts[0])
		vram, _ := strconv.Atoi(parts[2])
		migStr := ""
		if len(parts) > 5 {
			migStr = strings.ToLower(parts[5])
		}

		name := parts[1]
		gpus = append(gpus, GPUInfo{
			Index:         idx,
			Name:          name,
			Vendor:        "nvidia",
			VRAMMB:        vram,
			DriverVersion: parts[3],
			ComputeCap:    parts[4],
			MIGCapable:    strings.Contains(name, "A100") || strings.Contains(name, "H100") || strings.Contains(name, "H200"),
			MIGEnabled:    migStr == "enabled",
		})
	}
	return gpus
}

func detectAMD() []GPUInfo {
	if !commandExists("rocm-smi") {
		return nil
	}
	// Simplified: detect via lspci
	out, err := exec.Command("lspci", "-nn").Output()
	if err != nil {
		return nil
	}
	var gpus []GPUInfo
	idx := 0
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, "VGA") && (strings.Contains(line, "AMD") || strings.Contains(line, "ATI")) {
			gpus = append(gpus, GPUInfo{
				Index:  idx,
				Name:   "AMD GPU",
				Vendor: "amd",
			})
			idx++
		}
	}
	return gpus
}

func detectNPUs() []NPUInfo {
	var npus []NPUInfo
	if _, err := os.Stat("/dev/accel/accel0"); err == nil {
		npus = append(npus, NPUInfo{"Intel NPU", "intel", true, "openvino"})
	}
	if _, err := os.Stat("/dev/amdxdna"); err == nil {
		npus = append(npus, NPUInfo{"AMD XDNA NPU", "amd", true, "xdna"})
	}
	return npus
}

func selectProfile(gpus []GPUInfo, npus []NPUInfo) string {
	if len(gpus) == 0 && len(npus) == 0 {
		return "cpu-only"
	}

	var best *GPUInfo
	for i := range gpus {
		if best == nil || gpus[i].VRAMMB > best.VRAMMB {
			best = &gpus[i]
		}
	}

	if best != nil {
		arch := inferArch(best)
		vram := best.VRAMMB / 1024
		return fmt.Sprintf("%s-%s-%dgb", best.Vendor, arch, vram)
	}

	if len(npus) > 0 {
		return fmt.Sprintf("%s-npu-%s", npus[0].Vendor, npus[0].Runtime)
	}
	return "cpu-only"
}

func inferArch(g *GPUInfo) string {
	name := strings.ToLower(g.Name)
	switch {
	case strings.Contains(name, "h100") || strings.Contains(name, "h200"):
		return "hopper"
	case strings.Contains(name, "4090") || strings.Contains(name, "4080") || strings.Contains(name, "l40"):
		return "ada"
	case strings.Contains(name, "a100") || strings.Contains(name, "3090") || strings.Contains(name, "3080"):
		return "ampere"
	case strings.Contains(name, "mi300"):
		return "cdna"
	case strings.Contains(name, "7900") || strings.Contains(name, "7800"):
		return "rdna3"
	default:
		return "gpu"
	}
}

func detectContainerRT() string {
	if commandExists("podman") {
		return "podman"
	}
	if commandExists("docker") {
		return "docker"
	}
	return "none"
}

// DetectGPUs returns all detected GPUs (NVIDIA + AMD).
func DetectGPUs() []GPUInfo {
	return append(detectNVIDIA(), detectAMD()...)
}

// DetectProfile returns the hardware profile string for the current system.
func DetectProfile() string {
	return selectProfile(DetectGPUs(), detectNPUs())
}

// DetectContainerRuntime returns the container runtime in use ("podman", "docker", or "none").
func DetectContainerRuntime() string {
	return detectContainerRT()
}

// DetectOllama returns true if the ollama binary is present.
func DetectOllama() bool {
	return commandExists("ollama")
}

// DetectSystem returns system information for the current host.
func DetectSystem() SystemInfo {
	return detectSystem()
}

func commandExists(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

func parseMeminfoKB(line string) int {
	fields := strings.Fields(line)
	if len(fields) >= 2 {
		v, _ := strconv.Atoi(fields[1])
		return v
	}
	return 0
}

func getDiskFreeGB(path string) float64 {
	// Use os.Stat approach — cross-platform fallback
	// On Linux, could use syscall.Statfs
	return 0 // TODO: implement with syscall
}
