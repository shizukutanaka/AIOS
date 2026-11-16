// Package sessionsummary は、正規化済みエージェントイベント列(internal/agentevent)を
// セッション単位の構造化サマリへ決定論的に集約する。
//
// 着想(Hermes Desktop 参照): Hermes Desktop の目玉 UX は「ライブ tool activity と
// 構造化された tool-call サマリ」。Yagura は brain ではないが、その **kernel 側 deterministic
// 版**を提供できる — 任意エージェントの正規化イベント列から、tool 別呼出数・操作/位相内訳・
// エラー・tool 実行順・異常(連続エラー / ループ / 失敗多発 tool)を計算する。UI(Hermes 風でも
// Yagura dashboard でも)はこれを render するだけ。LLM は呼ばず、決定論的。
//
// agentevent.Normalize と組で、Claude Code / Gemini CLI / Codex / OTel いずれのセッションでも
// 同じサマリが得られる(エージェント非依存)。
package sessionsummary

import (
	"fmt"
	"sort"
	"strings"

	"github.com/shizukutanaka/aios/internal/agentevent"
)

const (
	maxSequence        = 50 // tool 実行順の出力上限
	loopThreshold      = 5  // 同一 tool が連続 N 回でループ疑い
	consecErrThreshold = 3  // 連続エラー N 回で異常
	failingMinCalls    = 3  // 失敗多発判定の最小呼出数
	failingRate        = 0.5
)

// ErrorItem は 1 件のエラー。
type ErrorItem struct {
	Tool      string `json:"tool,omitempty"`
	ErrorType string `json:"error_type,omitempty"`
	Agent     string `json:"agent,omitempty"`
}

// Summary はセッション活動の構造化サマリ。
type Summary struct {
	Events          int            `json:"events"`
	ToolInvocations int            `json:"tool_invocations"`
	DistinctTools   int            `json:"distinct_tools"`
	ByTool          map[string]int `json:"by_tool"`
	ByOperation     map[string]int `json:"by_operation"`
	ByPhase         map[string]int `json:"by_phase"`
	Agents          []string       `json:"agents"`
	Errors          []ErrorItem    `json:"errors,omitempty"`
	ErrorRate       float64        `json:"error_rate"`
	ToolSequence    []string       `json:"tool_sequence,omitempty"`
	SequenceTrunc   bool           `json:"sequence_truncated,omitempty"`
	DurationMsTotal int64          `json:"duration_ms_total,omitempty"`
	Anomalies       []string       `json:"anomalies,omitempty"`
	Summary         string         `json:"summary"`
}

// Summarize はイベント列(時系列順)を構造化サマリへ集約する。決定論的。
func Summarize(events []agentevent.Event) Summary {
	s := Summary{
		ByTool:      map[string]int{},
		ByOperation: map[string]int{},
		ByPhase:     map[string]int{},
		Events:      len(events),
	}
	agentSet := map[string]bool{}
	toolErrors := map[string]int{} // tool → error 数

	// 呼出数の基準: start イベントがあれば start、無ければ end/error で数える。
	var toolStarts int
	for _, e := range events {
		if e.Operation == agentevent.OpExecuteTool && e.Phase == agentevent.PhaseStart {
			toolStarts++
		}
	}
	countOnStart := toolStarts > 0

	var seq []string
	var errEvents int
	var consecErr, maxConsecErr int

	for _, e := range events {
		s.ByOperation[e.Operation]++
		s.ByPhase[e.Phase]++
		if e.Agent != "" {
			agentSet[e.Agent] = true
		}
		s.DurationMsTotal += e.DurationMs

		if e.Phase == agentevent.PhaseError {
			errEvents++
			consecErr++
			if consecErr > maxConsecErr {
				maxConsecErr = consecErr
			}
			s.Errors = append(s.Errors, ErrorItem{Tool: e.Tool, ErrorType: e.ErrorType, Agent: e.Agent})
			if e.Tool != "" {
				toolErrors[e.Tool]++
			}
		} else if e.Phase == agentevent.PhaseEnd {
			// 成功完了で連続エラー streak をリセット(start は中立で reset しない)。
			consecErr = 0
		}

		if e.Operation != agentevent.OpExecuteTool {
			continue
		}
		isInvocation := (countOnStart && e.Phase == agentevent.PhaseStart) ||
			(!countOnStart && (e.Phase == agentevent.PhaseEnd || e.Phase == agentevent.PhaseError))
		if isInvocation {
			s.ToolInvocations++
			tool := e.Tool
			if tool == "" {
				tool = "(unnamed)"
			}
			s.ByTool[tool]++
			if len(seq) < maxSequence {
				seq = append(seq, tool)
			} else {
				s.SequenceTrunc = true
			}
		}
	}

	s.DistinctTools = len(s.ByTool)
	s.ToolSequence = seq
	s.Agents = sortedKeys(agentSet)
	if s.ToolInvocations > 0 {
		s.ErrorRate = round2(float64(errEvents) / float64(s.ToolInvocations))
	}

	s.Anomalies = detectAnomalies(seq, toolErrors, s.ByTool, maxConsecErr, s.Agents)
	s.Summary = fmt.Sprintf("%d events, %d tool calls across %d tools, %d error(s) (rate %.0f%%)",
		s.Events, s.ToolInvocations, s.DistinctTools, errEvents, s.ErrorRate*100)
	return s
}

func detectAnomalies(seq []string, toolErrors, byTool map[string]int, maxConsecErr int, agents []string) []string {
	var out []string
	if maxConsecErr >= consecErrThreshold {
		out = append(out, fmt.Sprintf("%d consecutive errors", maxConsecErr))
	}
	// 連続同一 tool(ループ疑い)。
	if run, tool := longestRun(seq); run >= loopThreshold {
		out = append(out, fmt.Sprintf("possible loop: %q called %d times consecutively", tool, run))
	}
	// 複数エージェントによるセッション切替(handoff 信頼性の観測点)。
	if len(agents) > 1 {
		out = append(out, fmt.Sprintf("agent switch: %d distinct agents in session (%s)", len(agents), strings.Join(agents, ", ")))
	}
	// 失敗多発 tool。決定論のため tool 名昇順。
	var tools []string
	for t := range byTool {
		tools = append(tools, t)
	}
	sort.Strings(tools)
	for _, t := range tools {
		calls := byTool[t]
		errs := toolErrors[t]
		if calls >= failingMinCalls && float64(errs)/float64(calls) >= failingRate {
			out = append(out, fmt.Sprintf("tool %q failing (%d/%d)", t, errs, calls))
		}
	}
	return out
}

func longestRun(seq []string) (int, string) {
	best, bestTool := 0, ""
	cur, curTool := 0, ""
	for _, t := range seq {
		if t == curTool {
			cur++
		} else {
			cur, curTool = 1, t
		}
		if cur > best {
			best, bestTool = cur, curTool
		}
	}
	return best, bestTool
}

func sortedKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func round2(f float64) float64 {
	return float64(int(f*100+0.5)) / 100
}
