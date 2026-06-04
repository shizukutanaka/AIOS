package daemon

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/shizukutanaka/aios/internal/state"
)

func newTestServer(t *testing.T) *Server {
	t.Helper()
	tmp := t.TempDir()
	store, err := state.New(tmp)
	if err != nil {
		t.Fatalf("state.New: %v", err)
	}
	return New(store)
}

func TestHealthEndpoint(t *testing.T) {
	srv := newTestServer(t)
	req := httptest.NewRequest("GET", "/v1/health", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if body["status"] != "ok" {
		t.Fatalf("expected status ok, got %v", body["status"])
	}
}

func TestNodeEndpoint(t *testing.T) {
	srv := newTestServer(t)
	req := httptest.NewRequest("GET", "/v1/node", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var body map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &body)
	if _, ok := body["system"]; !ok {
		t.Fatal("response should contain 'system' key")
	}
}

func TestRecipesEndpoint(t *testing.T) {
	srv := newTestServer(t)
	req := httptest.NewRequest("GET", "/v1/recipes", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestPSIEndpoint(t *testing.T) {
	srv := newTestServer(t)
	req := httptest.NewRequest("GET", "/v1/metrics/psi", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestNotFound(t *testing.T) {
	srv := newTestServer(t)
	req := httptest.NewRequest("GET", "/v1/nonexistent", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestContentTypeJSON(t *testing.T) {
	srv := newTestServer(t)
	req := httptest.NewRequest("GET", "/v1/health", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	ct := w.Header().Get("Content-Type")
	if ct != "application/json" {
		t.Fatalf("expected application/json, got %s", ct)
	}
}
