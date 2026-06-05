// Package state manages node state, stacks, and model registry.
// State directory: ~/.aios/ (or $AIOS_STATE_DIR)
//   state.json   — node metadata
//   stacks.json  — applied stacks
//   config.json  — persistent config
//   models.db    — SQLite model registry
package state

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// NodeState represents the local node's configuration and hardware profile.
type NodeState struct {
	NodeID        string  `json:"node_id"`
	Hostname      string  `json:"hostname"`
	InitializedAt float64 `json:"initialized_at"`
	Profile       string  `json:"profile"`
	Version       string  `json:"version"`
	Mode          string  `json:"mode"` // local | cluster
	GPUCount      int     `json:"gpu_count"`
	VRAMTotalMB   int     `json:"vram_total_mb"`
	RAMTotalMB    int     `json:"ram_total_mb"`
}

// StackEntry represents an applied stack.
type StackEntry struct {
	Name      string                   `json:"name"`
	File      string                   `json:"file"`
	AppliedAt float64                  `json:"applied_at"`
	Status    string                   `json:"status"`
	Services  []map[string]interface{} `json:"services"`
}

// Store manages persistent state on disk.
type Store struct {
	Dir string
}

// DefaultDir returns the default state directory.
func DefaultDir() string {
	if d := os.Getenv("AIOS_STATE_DIR"); d != "" {
		return d
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".aios")
}

// New creates a state store, ensuring the directory exists.
func New(dir string) (*Store, error) {
	if dir == "" {
		dir = DefaultDir()
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, err
	}
	return &Store{Dir: dir}, nil
}

// IsInitialized returns true if state.json exists.
func (s *Store) IsInitialized() bool {
	_, err := os.Stat(filepath.Join(s.Dir, "state.json"))
	return err == nil
}

// SaveNode writes node state to state.json.
func (s *Store) SaveNode(ns *NodeState) error {
	if ns.InitializedAt == 0 {
		ns.InitializedAt = float64(time.Now().Unix())
	}
	data, err := json.MarshalIndent(ns, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(s.Dir, "state.json"), data, 0644)
}

// LoadNode reads node state from state.json.
func (s *Store) LoadNode() (*NodeState, error) {
	data, err := os.ReadFile(filepath.Join(s.Dir, "state.json"))
	if err != nil {
		return &NodeState{}, nil // Return empty if not found
	}
	ns := &NodeState{}
	if err := json.Unmarshal(data, ns); err != nil {
		return &NodeState{}, err
	}
	return ns, nil
}

// SaveStacks writes the stack list to stacks.json.
func (s *Store) SaveStacks(stacks []StackEntry) error {
	data, err := json.MarshalIndent(stacks, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(s.Dir, "stacks.json"), data, 0644)
}

// LoadStacks reads the stack list from stacks.json.
func (s *Store) LoadStacks() ([]StackEntry, error) {
	data, err := os.ReadFile(filepath.Join(s.Dir, "stacks.json"))
	if err != nil {
		return nil, nil
	}
	var stacks []StackEntry
	if err := json.Unmarshal(data, &stacks); err != nil {
		return nil, err
	}
	return stacks, nil
}
