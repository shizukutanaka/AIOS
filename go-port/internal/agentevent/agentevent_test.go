package agentevent

import (
	"encoding/json"
	"testing"
)

func norm(t *testing.T, js string) Event {
	t.Helper()
	e, err := NormalizeJSON([]byte(js))
	if err != nil {
		t.Fatalf("normalize: %v", err)
	}
	return e
}

func TestClaudeCode_PreToolUse(t *testing.T) {
	e := norm(t, `{"hook_event_name":"PreToolUse","tool_name":"Bash","session_id":"abc","cwd":"/x"}`)
	if e.SourceFormat != "claude_code" || e.Agent != "claude_code" {
		t.Errorf("detect/agent: %+v", e)
	}
	if e.Operation != OpExecuteTool || e.Phase != PhaseStart || e.Tool != "Bash" || e.Session != "abc" {
		t.Errorf("unexpected: %+v", e)
	}
}

func TestClaudeCode_PostToolUseFailure(t *testing.T) {
	e := norm(t, `{"hook_event_name":"PostToolUseFailure","tool_name":"Edit","tool_error":"timeout"}`)
	if e.Operation != OpExecuteTool || e.Phase != PhaseError || e.ErrorType != "timeout" {
		t.Errorf("error phase not derived: %+v", e)
	}
}

func TestClaudeCode_StopAndSubagent(t *testing.T) {
	if e := norm(t, `{"hook_event_name":"Stop","session_id":"s"}`); e.Operation != OpInvokeAgent || e.Phase != PhaseEnd {
		t.Errorf("Stop: %+v", e)
	}
	if e := norm(t, `{"hook_event_name":"SubagentStop"}`); e.Operation != OpInvokeAgent || e.Phase != PhaseEnd {
		t.Errorf("SubagentStop: %+v", e)
	}
	if e := norm(t, `{"hook_event_name":"SessionStart"}`); e.Operation != OpInvokeAgent || e.Phase != PhaseStart {
		t.Errorf("SessionStart: %+v", e)
	}
}

func TestOTelFormat_PassThrough(t *testing.T) {
	e := norm(t, `{"gen_ai.operation.name":"execute_tool","gen_ai.tool.name":"search","gen_ai.conversation.id":"c1","gen_ai.agent.name":"my-agent"}`)
	if e.SourceFormat != "otel" {
		t.Errorf("should detect otel, got %s", e.SourceFormat)
	}
	if e.Operation != OpExecuteTool || e.Tool != "search" || e.Session != "c1" || e.Agent != "my-agent" {
		t.Errorf("otel mapping: %+v", e)
	}
}

func TestGenericAndExplicitAgent(t *testing.T) {
	// explicit agent (e.g. gemini_cli) + generic event names
	e := norm(t, `{"agent":"gemini_cli","event":"beforeToolCall","tool":"write_file","session":"g1"}`)
	if e.Agent != "gemini_cli" {
		t.Errorf("agent: %+v", e)
	}
	if e.Operation != OpExecuteTool || e.Phase != PhaseStart || e.Tool != "write_file" {
		t.Errorf("generic mapping: %+v", e)
	}
	// status-based error
	e = norm(t, `{"agent":"codex","type":"toolResult","tool":"shell","status":"failure"}`)
	if e.Phase != PhaseError {
		t.Errorf("status failure → error: %+v", e)
	}
}

func TestErrorNested(t *testing.T) {
	e := norm(t, `{"event":"tool.error","tool":"x","error":{"type":"rate_limit"}}`)
	if e.ErrorType != "rate_limit" || e.Phase != PhaseError {
		t.Errorf("nested error: %+v", e)
	}
}

func TestOTelOutput(t *testing.T) {
	e := norm(t, `{"hook_event_name":"PreToolUse","tool_name":"Bash","session_id":"s","duration_ms":2500}`)
	o := e.OTel()
	if o["gen_ai.operation.name"] != OpExecuteTool || o["gen_ai.tool.name"] != "Bash" {
		t.Errorf("otel out: %+v", o)
	}
	if o["gen_ai.client.operation.duration"] != 2.5 {
		t.Errorf("duration ms→s: %+v", o["gen_ai.client.operation.duration"])
	}
}

func TestDeterminism(t *testing.T) {
	js := `{"hook_event_name":"PostToolUse","tool_name":"Read","session_id":"z"}`
	a := norm(t, js)
	b := norm(t, js)
	ba, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	if string(ba) != string(bb) {
		t.Error("Normalize must be deterministic")
	}
}

func TestBadJSON(t *testing.T) {
	if _, err := NormalizeJSON([]byte(`{`)); err == nil {
		t.Error("expected error for bad json")
	}
}

func TestUnknownAndChatDefault(t *testing.T) {
	// no recognizable fields → unknown format, chat default operation
	e := norm(t, `{"foo":"bar"}`)
	if e.SourceFormat != "unknown" || e.Agent != "unknown" || e.Operation != OpChat {
		t.Errorf("unknown/chat default: %+v", e)
	}
	// explicit create_agent passthrough
	if e := norm(t, `{"operation":"create_agent","agent":"x"}`); e.Operation != OpCreateAgent {
		t.Errorf("create_agent passthrough: %+v", e)
	}
}

func TestAgentInferenceFromOTelWithCwd(t *testing.T) {
	// gen_ai.* present (otel) but also looks like a Claude Code hook payload
	e := norm(t, `{"gen_ai.tool.name":"Bash","cwd":"/x","tool_name":"Bash"}`)
	if e.SourceFormat != "otel" {
		t.Errorf("format: %+v", e)
	}
	if e.Agent != "claude_code" {
		t.Errorf("agent inference via cwd+tool_name: %+v", e)
	}
}

func TestTimestampPassthrough(t *testing.T) {
	e := norm(t, `{"event":"chat","time":"2026-06-08T00:00:00Z"}`)
	if e.Timestamp != "2026-06-08T00:00:00Z" {
		t.Errorf("timestamp: %+v", e)
	}
}

func TestExplicitPhase(t *testing.T) {
	e := norm(t, `{"event":"something","phase":"start","tool":"t"}`)
	if e.Phase != "start" {
		t.Errorf("explicit phase should win: %+v", e)
	}
}

func TestOTel_WithErrorType(t *testing.T) {
	// OTel() must include error.type when ErrorType is set.
	e := norm(t, `{"hook_event_name":"PostToolUseFailure","tool_name":"Bash","tool_error":"timeout"}`)
	o := e.OTel()
	if _, ok := o["error.type"]; !ok {
		t.Errorf("OTel should include error.type for non-empty ErrorType, got %+v", o)
	}
	if o["error.type"] != "timeout" {
		t.Errorf("error.type: got %v, want timeout", o["error.type"])
	}
}

func TestDetect_CamelCaseHookEventName(t *testing.T) {
	// hookEventName (camelCase) must also be recognized as claude_code format.
	e := norm(t, `{"hookEventName":"PreToolUse","tool_name":"Bash"}`)
	if e.SourceFormat != "claude_code" {
		t.Errorf("camelCase hookEventName: expected claude_code, got %s", e.SourceFormat)
	}
}

func TestInferAgent_OtelWithCwdNoToolName(t *testing.T) {
	// OTel format + cwd but no tool_name → not inferred as claude_code → returns format.
	e := norm(t, `{"gen_ai.operation.name":"execute_tool","cwd":"/home/user"}`)
	if e.SourceFormat != "otel" {
		t.Errorf("format: %s", e.SourceFormat)
	}
	// cwd present but no tool_name → inferAgent returns "otel" (not claude_code)
	if e.Agent != "otel" {
		t.Errorf("agent inference without tool_name: got %q, want otel", e.Agent)
	}
}

func TestDeriveOperation_MessageKeyword(t *testing.T) {
	// Event name containing "message" → OpChat.
	e := norm(t, `{"event":"user_message","agent":"gemini_cli"}`)
	if e.Operation != OpChat {
		t.Errorf("message keyword → OpChat, got %q", e.Operation)
	}
}

func TestFirstInt_Int64AndInt(t *testing.T) {
	m := map[string]any{"x": int64(42), "y": int(7)}
	if got := firstInt(m, "x"); got != 42 {
		t.Errorf("int64 branch: got %d, want 42", got)
	}
	if got := firstInt(m, "y"); got != 7 {
		t.Errorf("int branch: got %d, want 7", got)
	}
}

func TestExtractError_StringValue(t *testing.T) {
	// "error" field is a plain string.
	e := norm(t, `{"event":"tool.fail","error":"connection reset"}`)
	if e.ErrorType != "connection reset" {
		t.Errorf("string error field: got %q, want connection reset", e.ErrorType)
	}
}
