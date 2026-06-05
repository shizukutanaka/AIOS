// Package daemon implements the aiosd REST API server.
package daemon

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/shizukutanaka/aios/internal/runtime"
	"github.com/shizukutanaka/aios/internal/state"
)

// Server is the aiosd HTTP server.
type Server struct {
	store     *state.Store
	startTime time.Time
	mux       *http.ServeMux
}

// New creates a new daemon server.
func New(store *state.Store) *Server {
	s := &Server{
		store:     store,
		startTime: time.Now(),
		mux:       http.NewServeMux(),
	}
	s.routes()
	return s
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /v1/health", s.handleHealth)
	s.mux.HandleFunc("GET /v1/node", s.handleNode)
	s.mux.HandleFunc("GET /v1/runtime", s.handleRuntime)
	s.mux.HandleFunc("GET /v1/stacks", s.handleListStacks)
	s.mux.HandleFunc("GET /v1/recipes", s.handleListRecipes)
	s.mux.HandleFunc("GET /v1/metrics/psi", s.handlePSI)
	s.mux.HandleFunc("GET /v1/upgrade/plan", s.handleUpgradePlan)
}

// ServeHTTP implements http.Handler.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// CORS
	w.Header().Set("Access-Control-Allow-Origin", "*")
	if r.Method == "OPTIONS" {
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		w.WriteHeader(200)
		return
	}
	s.mux.ServeHTTP(w, r)
}

// ListenAndServe starts the server.
func (s *Server) ListenAndServe(addr string) error {
	srv := &http.Server{
		Addr:         addr,
		Handler:      s,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}
	log.Printf("aiosd listening on http://%s", addr)
	log.Printf("State dir: %s", s.store.Dir)
	return srv.ListenAndServe()
}

// ── Handlers ───────────────────────────────────────────

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	report := runtime.Detect()
	s.json(w, 200, map[string]interface{}{
		"status":            "ok",
		"initialized":       s.store.IsInitialized(),
		"profile":           report.Profile,
		"container_runtime": report.ContainerRT,
		"uptime_seconds":    time.Since(s.startTime).Seconds(),
	})
}

func (s *Server) handleNode(w http.ResponseWriter, r *http.Request) {
	node, _ := s.store.LoadNode()
	report := runtime.Detect()
	s.json(w, 200, map[string]interface{}{
		"node":   node,
		"system": report.System,
		"gpus":   report.GPUs,
		"npus":   report.NPUs,
		"issues": report.Issues,
	})
}

func (s *Server) handleRuntime(w http.ResponseWriter, r *http.Request) {
	report := runtime.Detect()
	s.json(w, 200, map[string]interface{}{
		"profile":           report.Profile,
		"container_runtime": report.ContainerRT,
		"ollama":            report.OllamaAvailable,
		"gpus":              report.GPUs,
		"npus":              report.NPUs,
		"recommendations":   report.Recommendations,
	})
}

func (s *Server) handleListStacks(w http.ResponseWriter, r *http.Request) {
	stacks, _ := s.store.LoadStacks()
	s.json(w, 200, map[string]interface{}{"stacks": stacks})
}

func (s *Server) handleListRecipes(w http.ResponseWriter, r *http.Request) {
	recipes := []map[string]interface{}{
		{"name": "local-chat", "services": 2, "gpu_required": false},
		{"name": "team-rag", "services": 3, "gpu_required": true},
		{"name": "local-gpu-chat", "services": 2, "gpu_required": true},
		{"name": "code-assist", "services": 2, "gpu_required": true},
		{"name": "whisper-stt", "services": 1, "gpu_required": true},
		{"name": "image-gen", "services": 1, "gpu_required": true},
		{"name": "embedding-only", "services": 1, "gpu_required": false},
		{"name": "bank-convert", "services": 1, "gpu_required": false},
	}
	s.json(w, 200, map[string]interface{}{"recipes": recipes})
}

func (s *Server) handlePSI(w http.ResponseWriter, r *http.Request) {
	psi := readPSI()
	s.json(w, 200, psi)
}

func (s *Server) handleUpgradePlan(w http.ResponseWriter, r *http.Request) {
	node, _ := s.store.LoadNode()
	stacks, _ := s.store.LoadStacks()
	s.json(w, 200, map[string]interface{}{
		"current_version": node.Version,
		"active_stacks":   len(stacks),
		"steps": []string{
			"snapshot_state", "drain_workloads", "stage_update",
			"apply_update", "verify_health", "restore_workloads",
		},
		"rollback": "bootc rollback",
	})
}

// ── Helpers ────────────────────────────────────────────

func (s *Server) json(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	enc.Encode(data)
}

// PSI reads Linux Pressure Stall Information.
type PSI struct {
	MemorySomeAvg10 float64 `json:"memory_some_avg10"`
	MemoryFullAvg10 float64 `json:"memory_full_avg10"`
	CPUSomeAvg10    float64 `json:"cpu_some_avg10"`
	IOSomeAvg10     float64 `json:"io_some_avg10"`
}

func readPSI() PSI {
	p := PSI{}
	for _, res := range []struct {
		file string
		some *float64
	}{
		{"/proc/pressure/memory", &p.MemorySomeAvg10},
		{"/proc/pressure/cpu", &p.CPUSomeAvg10},
		{"/proc/pressure/io", &p.IOSomeAvg10},
	} {
		data, err := os.ReadFile(res.file)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "some") {
				for _, field := range strings.Fields(line) {
					if strings.HasPrefix(field, "avg10=") {
						val, _ := strconv.ParseFloat(strings.TrimPrefix(field, "avg10="), 64)
						*res.some = val
					}
				}
			}
			if strings.HasPrefix(line, "full") && res.file == "/proc/pressure/memory" {
				for _, field := range strings.Fields(line) {
					if strings.HasPrefix(field, "avg10=") {
						val, _ := strconv.ParseFloat(strings.TrimPrefix(field, "avg10="), 64)
						p.MemoryFullAvg10 = val
					}
				}
			}
		}
	}
	return p
}
