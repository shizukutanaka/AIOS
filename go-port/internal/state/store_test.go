package state

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultDir(t *testing.T) {
	dir := DefaultDir()
	if dir == "" {
		t.Fatal("DefaultDir returned empty")
	}
}

func TestNewStore(t *testing.T) {
	tmp := t.TempDir()
	s, err := New(tmp)
	if err != nil {
		t.Fatalf("New failed: %v", err)
	}
	if s.Dir != tmp {
		t.Fatalf("Dir mismatch: got %s, want %s", s.Dir, tmp)
	}
}

func TestIsInitialized(t *testing.T) {
	tmp := t.TempDir()
	s, _ := New(tmp)

	if s.IsInitialized() {
		t.Fatal("Should not be initialized")
	}

	// Create state.json
	os.WriteFile(filepath.Join(tmp, "state.json"), []byte(`{}`), 0644)
	if !s.IsInitialized() {
		t.Fatal("Should be initialized after state.json created")
	}
}

func TestSaveAndLoadNode(t *testing.T) {
	tmp := t.TempDir()
	s, _ := New(tmp)

	ns := &NodeState{
		NodeID:      "test-123",
		Hostname:    "myhost",
		Profile:     "cpu-only",
		Version:     "1.5.0",
		GPUCount:    0,
		RAMTotalMB:  16384,
	}

	if err := s.SaveNode(ns); err != nil {
		t.Fatalf("SaveNode failed: %v", err)
	}

	loaded, err := s.LoadNode()
	if err != nil {
		t.Fatalf("LoadNode failed: %v", err)
	}

	if loaded.NodeID != "test-123" {
		t.Fatalf("NodeID mismatch: got %s", loaded.NodeID)
	}
	if loaded.Hostname != "myhost" {
		t.Fatalf("Hostname mismatch: got %s", loaded.Hostname)
	}
	if loaded.InitializedAt == 0 {
		t.Fatal("InitializedAt should be set")
	}
}

func TestSaveAndLoadStacks(t *testing.T) {
	tmp := t.TempDir()
	s, _ := New(tmp)

	stacks := []StackEntry{
		{Name: "test-stack", File: "test.json", Status: "running"},
	}

	if err := s.SaveStacks(stacks); err != nil {
		t.Fatalf("SaveStacks failed: %v", err)
	}

	loaded, err := s.LoadStacks()
	if err != nil {
		t.Fatalf("LoadStacks failed: %v", err)
	}

	if len(loaded) != 1 {
		t.Fatalf("Expected 1 stack, got %d", len(loaded))
	}
	if loaded[0].Name != "test-stack" {
		t.Fatalf("Name mismatch: got %s", loaded[0].Name)
	}
}

func TestLoadStacksEmpty(t *testing.T) {
	tmp := t.TempDir()
	s, _ := New(tmp)

	stacks, err := s.LoadStacks()
	if err != nil {
		t.Fatalf("LoadStacks on empty dir failed: %v", err)
	}
	if stacks != nil {
		t.Fatalf("Expected nil, got %v", stacks)
	}
}
