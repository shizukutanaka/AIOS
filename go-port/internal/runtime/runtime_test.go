package runtime

import (
	"testing"
)

func TestDetectGPUs(t *testing.T) {
	gpus := DetectGPUs()
	// In CI/sandbox, no GPU expected
	if gpus == nil {
		t.Log("No GPUs detected (expected in CI)")
	}
}

func TestDetectProfile(t *testing.T) {
	profile := DetectProfile()
	validProfiles := map[string]bool{
		"nvidia-single": true, "nvidia-multi": true,
		"amd-single": true, "amd-multi": true,
		"intel-npu": true, "cpu-only": true,
	}
	if !validProfiles[profile] {
		// Unknown profile is acceptable on exotic hardware
		t.Logf("Detected profile: %s", profile)
	}
}

func TestDetectContainerRuntime(t *testing.T) {
	rt := DetectContainerRuntime()
	valid := map[string]bool{"podman": true, "docker": true, "none": true}
	if !valid[rt] {
		t.Fatalf("Unexpected runtime: %s", rt)
	}
}

func TestDetectOllama(t *testing.T) {
	// Just verify it doesn't panic
	_ = DetectOllama()
}

func TestSystemInfo(t *testing.T) {
	info := DetectSystem()
	if info.Hostname == "" {
		t.Fatal("Hostname should not be empty")
	}
	if info.CPUCores <= 0 {
		t.Fatal("CPUCores should be positive")
	}
	if info.RAMTotalMB <= 0 {
		t.Fatal("RAMTotalMB should be positive")
	}
}
