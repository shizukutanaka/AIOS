package sessionsummary

import (
	"encoding/json"
	"testing"

	"github.com/shizukutanaka/aios/internal/agentevent"
)

func ev(agent, op, phase, tool, errType string) agentevent.Event {
	return agentevent.Event{Agent: agent, Operation: op, Phase: phase, Tool: tool, ErrorType: errType}
}

func TestSummarize_Basic(t *testing.T) {
	events := []agentevent.Event{
		ev("claude_code", agentevent.OpExecuteTool, agentevent.PhaseStart, "Bash", ""),
		ev("claude_code", agentevent.OpExecuteTool, agentevent.PhaseEnd, "Bash", ""),
		ev("claude_code", agentevent.OpExecuteTool, agentevent.PhaseStart, "Read", ""),
		ev("claude_code", agentevent.OpExecuteTool, agentevent.PhaseError, "Read", "timeout"),
		ev("claude_code", agentevent.OpInvokeAgent, agentevent.PhaseEnd, "", ""),
	}
	s := Summarize(events)
	if s.Events != 5 {
		t.Errorf("events = %d, want 5", s.Events)
	}
	// invocations counted on start (2 starts: Bash, Read)
	if s.ToolInvocations != 2 || s.DistinctTools != 2 {
		t.Errorf("invocations/distinct: %d/%d", s.ToolInvocations, s.DistinctTools)
	}
	if s.ByTool["Bash"] != 1 || s.ByTool["Read"] != 1 {
		t.Errorf("by_tool: %+v", s.ByTool)
	}
	if len(s.Errors) != 1 || s.Errors[0].Tool != "Read" || s.Errors[0].ErrorType != "timeout" {
		t.Errorf("errors: %+v", s.Errors)
	}
	if s.ErrorRate != 0.5 { // 1 error / 2 invocations
		t.Errorf("error_rate = %v, want 0.5", s.ErrorRate)
	}
	if len(s.Agents) != 1 || s.Agents[0] != "claude_code" {
		t.Errorf("agents: %+v", s.Agents)
	}
}

func TestSummarize_CountOnEndWhenNoStarts(t *testing.T) {
	// only end/error events (some agents emit only post-hooks)
	events := []agentevent.Event{
		ev("gemini_cli", agentevent.OpExecuteTool, agentevent.PhaseEnd, "write_file", ""),
		ev("gemini_cli", agentevent.OpExecuteTool, agentevent.PhaseError, "shell", "exit1"),
	}
	s := Summarize(events)
	if s.ToolInvocations != 2 {
		t.Errorf("should count ends when no starts: %d", s.ToolInvocations)
	}
}

func TestSummarize_ConsecutiveErrors(t *testing.T) {
	var events []agentevent.Event
	for i := 0; i < 3; i++ {
		events = append(events, ev("a", agentevent.OpExecuteTool, agentevent.PhaseStart, "T", ""))
		events = append(events, ev("a", agentevent.OpExecuteTool, agentevent.PhaseError, "T", "boom"))
	}
	s := Summarize(events)
	if !hasAnomaly(s, "consecutive errors") {
		t.Errorf("expected consecutive-errors anomaly: %+v", s.Anomalies)
	}
	if !hasAnomaly(s, "failing") {
		t.Errorf("expected failing-tool anomaly: %+v", s.Anomalies)
	}
}

func TestSummarize_LoopDetection(t *testing.T) {
	var events []agentevent.Event
	for i := 0; i < 6; i++ {
		events = append(events, ev("a", agentevent.OpExecuteTool, agentevent.PhaseStart, "Glob", ""))
		events = append(events, ev("a", agentevent.OpExecuteTool, agentevent.PhaseEnd, "Glob", ""))
	}
	s := Summarize(events)
	if !hasAnomaly(s, "possible loop") {
		t.Errorf("expected loop anomaly: %+v", s.Anomalies)
	}
}

func TestSummarize_Empty(t *testing.T) {
	s := Summarize(nil)
	if s.Events != 0 || s.ToolInvocations != 0 || s.Summary == "" {
		t.Errorf("empty summary: %+v", s)
	}
	// ToolInvocations==0 must leave ErrorRate at 0, not NaN. NaN would make the
	// summary fail json.Marshal, breaking yagura_session_summary for an idle
	// session with no tool calls.
	if s.ErrorRate != 0 {
		t.Errorf("empty session ErrorRate = %v, want 0 (not NaN)", s.ErrorRate)
	}
	if _, err := json.Marshal(s); err != nil {
		t.Errorf("empty summary must be JSON-marshalable (no NaN/Inf): %v", err)
	}
}

func TestSummarize_Deterministic(t *testing.T) {
	events := []agentevent.Event{
		ev("z", agentevent.OpExecuteTool, agentevent.PhaseStart, "B", ""),
		ev("a", agentevent.OpExecuteTool, agentevent.PhaseStart, "A", ""),
	}
	a := Summarize(events)
	b := Summarize(events)
	ba, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	if string(ba) != string(bb) {
		t.Error("Summarize must be deterministic")
	}
	// agents sorted
	if a.Agents[0] != "a" || a.Agents[1] != "z" {
		t.Errorf("agents not sorted: %+v", a.Agents)
	}
}

func hasAnomaly(s Summary, sub string) bool {
	for _, a := range s.Anomalies {
		if contains(a, sub) {
			return true
		}
	}
	return false
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// ─── agent-switch anomaly (v0.43.0) ──────────────────────────────────────

func TestSummarize_AgentSwitch_Flagged(t *testing.T) {
	events := []agentevent.Event{
		ev("claude-code", agentevent.OpExecuteTool, agentevent.PhaseStart, "Bash", ""),
		ev("windsurf", agentevent.OpExecuteTool, agentevent.PhaseStart, "Read", ""),
		ev("windsurf", agentevent.OpExecuteTool, agentevent.PhaseEnd, "Read", ""),
	}
	s := Summarize(events)
	if !hasAnomaly(s, "agent switch") {
		t.Errorf("expected 'agent switch' anomaly for 2 distinct agents, got anomalies: %v", s.Anomalies)
	}
}

func TestSummarize_SingleAgent_NoSwitch(t *testing.T) {
	events := []agentevent.Event{
		ev("claude-code", agentevent.OpExecuteTool, agentevent.PhaseStart, "Bash", ""),
		ev("claude-code", agentevent.OpExecuteTool, agentevent.PhaseEnd, "Bash", ""),
	}
	s := Summarize(events)
	if hasAnomaly(s, "agent switch") {
		t.Errorf("single agent must not flag agent-switch anomaly, got anomalies: %v", s.Anomalies)
	}
}
