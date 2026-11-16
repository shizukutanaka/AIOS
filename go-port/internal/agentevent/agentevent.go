// Package agentevent は、任意のコーディングエージェント(Claude Code / Gemini CLI /
// Codex / 汎用 / OpenTelemetry 形式)が発する lifecycle イベントを、ベンダー中立な
// canonical イベントへ決定論的に正規化する。
//
// 動機(Claude Code だけでなく汎用に): Yagura の governance / MCP サーフェスは元々
// エージェント非依存だが、observability の取り込み口だけが Claude Code の hook 形式に
// 結合していた(`/hooks/claude-code`)。各エージェントの hook 形式は異なる(Claude Code は
// 12+ events、Gemini CLI は 10 events、Codex は hook 無し)が、lifecycle の概念は共通
// (pre/post-tool, session start/stop)。
//
// 正規化先は **OpenTelemetry GenAI semantic conventions**(2026, semconv 1.40.0)という
// 業界標準: `gen_ai.operation.name`(execute_tool / invoke_agent / create_agent / chat)、
// `gen_ai.tool.name`、`gen_ai.agent.name`、`gen_ai.conversation.id`、`error.type`。
// 独自語彙を作らず標準語彙へ写すことで、OTel 対応の任意のツールと相互運用できる。
// LLM は呼ばず、フィールドの別名を吸収して決定論的に Event を返す。
package agentevent

import (
	"encoding/json"
	"strings"
)

// 既知の OTel gen_ai.operation.name 値。
const (
	// OpExecuteTool はツール実行操作(gen_ai.operation.name)。
	OpExecuteTool = "execute_tool"
	// OpInvokeAgent はエージェント呼出操作。
	OpInvokeAgent = "invoke_agent"
	// OpCreateAgent はエージェント生成操作。
	OpCreateAgent = "create_agent"
	// OpChat はチャット(推論)操作。
	OpChat = "chat"
)

// Phase は point イベントの位相(span ではなく hook ストリーム向け)。
const (
	// PhaseStart は操作開始の位相。
	PhaseStart = "start"
	// PhaseEnd は操作正常終了の位相。
	PhaseEnd = "end"
	// PhaseError は操作がエラーで終了した位相。
	PhaseError = "error"
)

// Event は正規化済みの canonical イベント。
type Event struct {
	Agent        string `json:"agent,omitempty"`       // 発生元エージェント(claude_code/gemini_cli/codex/...)
	Operation    string `json:"operation"`             // OTel gen_ai.operation.name 値
	Phase        string `json:"phase"`                 // start|end|error
	Tool         string `json:"tool,omitempty"`        // gen_ai.tool.name
	Session      string `json:"session,omitempty"`     // gen_ai.conversation.id
	ErrorType    string `json:"error_type,omitempty"`  // error.type
	DurationMs   int64  `json:"duration_ms,omitempty"` // gen_ai.client.operation.duration(ms 表現)
	Timestamp    string `json:"timestamp,omitempty"`   // RFC3339(渡されたまま)
	SourceFormat string `json:"source_format"`         // 検出した入力形式
}

// Normalize は raw イベント(map)を canonical Event に正規化する。
func Normalize(raw map[string]any) Event {
	e := Event{SourceFormat: detect(raw)}

	rawName := firstString(raw, "gen_ai.operation.name", "hook_event_name", "hookEventName",
		"event", "event_type", "type", "hook", "operation")
	e.Tool = firstString(raw, "gen_ai.tool.name", "tool_name", "toolName", "tool")
	e.Session = firstString(raw, "gen_ai.conversation.id", "conversation_id", "session_id",
		"sessionId", "session")
	e.Agent = firstString(raw, "agent", "gen_ai.agent.name", "gen_ai.provider.name", "provider")
	e.Timestamp = firstString(raw, "timestamp", "time", "ts", "@timestamp")
	e.ErrorType = extractError(raw)
	e.DurationMs = firstInt(raw, "duration_ms", "durationMs", "duration")

	if e.Agent == "" {
		e.Agent = inferAgent(raw, e.SourceFormat)
	}
	e.Phase = derivePhase(rawName, raw, e.ErrorType)
	e.Operation = deriveOperation(rawName, e.Tool)
	return e
}

// NormalizeJSON は raw JSON bytes を正規化する。
func NormalizeJSON(data []byte) (Event, error) {
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		return Event{}, err
	}
	return Normalize(raw), nil
}

// OTel は Event を OpenTelemetry GenAI semconv の attribute bag に変換する。
func (e Event) OTel() map[string]any {
	m := map[string]any{"gen_ai.operation.name": e.Operation}
	if e.Tool != "" {
		m["gen_ai.tool.name"] = e.Tool
	}
	if e.Agent != "" {
		m["gen_ai.agent.name"] = e.Agent
	}
	if e.Session != "" {
		m["gen_ai.conversation.id"] = e.Session
	}
	if e.ErrorType != "" {
		m["error.type"] = e.ErrorType
	}
	if e.DurationMs > 0 {
		// OTel の duration metric は秒(double)。ms から換算。
		m["gen_ai.client.operation.duration"] = float64(e.DurationMs) / 1000.0
	}
	return m
}

// detect は入力形式を推定する。
func detect(raw map[string]any) string {
	for k := range raw {
		if strings.HasPrefix(k, "gen_ai.") {
			return "otel"
		}
	}
	if _, ok := raw["hook_event_name"]; ok {
		return "claude_code"
	}
	if _, ok := raw["hookEventName"]; ok {
		return "claude_code"
	}
	if a := firstString(raw, "agent", "provider"); a != "" {
		return strings.ToLower(strings.ReplaceAll(a, " ", "_"))
	}
	if firstString(raw, "operation", "event", "type", "hook") != "" {
		return "generic"
	}
	return "unknown"
}

// inferAgent は agent 名が無い場合に形式から推定する。
func inferAgent(raw map[string]any, format string) string {
	switch format {
	case "claude_code":
		return "claude_code"
	case "otel", "generic", "unknown":
		// cwd + hook 形式は Claude Code の手掛かりだが断定はしない。
		if _, ok := raw["cwd"]; ok {
			if _, ok2 := raw["tool_name"]; ok2 {
				return "claude_code"
			}
		}
		return format
	default:
		return format
	}
}

// derivePhase は event 名/状態/エラーから位相を決める(error > start > end の優先)。
func derivePhase(rawName string, raw map[string]any, errType string) string {
	n := strings.ToLower(rawName)
	status := strings.ToLower(firstString(raw, "status", "result", "outcome"))
	if errType != "" || strings.Contains(n, "fail") || strings.Contains(n, "error") ||
		status == "error" || status == "failure" || status == "failed" {
		return PhaseError
	}
	if p := strings.ToLower(firstString(raw, "phase")); p != "" {
		return p
	}
	switch {
	case strings.Contains(n, "pre") || strings.Contains(n, "before") ||
		strings.Contains(n, "start") || strings.Contains(n, "submit") || strings.Contains(n, "begin"):
		return PhaseStart
	case strings.Contains(n, "post") || strings.Contains(n, "after") ||
		strings.Contains(n, "stop") || strings.Contains(n, "end") || strings.Contains(n, "complete"):
		return PhaseEnd
	}
	return PhaseEnd
}

// deriveOperation は event 名/tool 有無から OTel operation 値へ写す。
func deriveOperation(rawName, tool string) string {
	n := strings.ToLower(rawName)
	switch n {
	case OpExecuteTool, OpInvokeAgent, OpCreateAgent, OpChat:
		return n
	}
	switch {
	case strings.Contains(n, "tool"):
		return OpExecuteTool
	case strings.Contains(n, "subagent") || strings.Contains(n, "agent"):
		return OpInvokeAgent
	case strings.Contains(n, "prompt") || strings.Contains(n, "message") ||
		strings.Contains(n, "chat") || strings.Contains(n, "user"):
		return OpChat
	case strings.Contains(n, "session") || strings.Contains(n, "stop") || strings.Contains(n, "start"):
		return OpInvokeAgent
	}
	if tool != "" {
		return OpExecuteTool
	}
	return OpChat
}

// ─── helpers ─────────────────────────────────────────────────

func firstString(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := m[k]; ok {
			if s, ok := v.(string); ok && s != "" {
				return s
			}
		}
	}
	return ""
}

func firstInt(m map[string]any, keys ...string) int64 {
	for _, k := range keys {
		if v, ok := m[k]; ok {
			switch n := v.(type) {
			case float64:
				return int64(n)
			case int64:
				return n
			case int:
				return int64(n)
			}
		}
	}
	return 0
}

// extractError は error.type(literal)/ nested error.type / error 文字列 / tool_error を吸収。
func extractError(m map[string]any) string {
	if s := firstString(m, "error.type", "errorType", "tool_error", "error_message"); s != "" {
		return s
	}
	if v, ok := m["error"]; ok {
		switch e := v.(type) {
		case string:
			if e != "" {
				return e
			}
		case map[string]any:
			if s := firstString(e, "type", "code", "message"); s != "" {
				return s
			}
		}
	}
	return ""
}
