// Package plantracker は Plan.md を parse して進捗を計測する。
//
// 動機 (v0.24.0):
//
//	m の harness G1.P が Plan.md 必須記載項目 (目的/スコープ/フェーズ/DoD) を
//	defined している。23+ projects で共通 format が存在するので、yagura が
//	portfolio 全体の Plan.md を parse し "どの project が release 近いか" を
//	機械的に判定できる。
//
//	cortex (aircloset 2026/05) の 4 flywheel の ④ Alert-Fix の precursor として
//	まず "release readiness を機械的に評価する" を実装する。
//
// 設計判断 (ADR-0001 ゼロ依存):
//   - Markdown を full parse せず、特定 pattern (checkbox, header) のみ抽出
//   - regex base (stdlib のみ)
//   - section = `^#+` で始まる line
//   - checkbox = `^[\s>-]*- \[[ x]\]`
//   - DoD section: header に "DoD" or "Definition of Done" or "完了定義" を含む
//
// 性能:
//   - O(N) scan, N = line count
//   - 1MB Plan.md で ~10ms (実測予測)
package plantracker

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// PlanState は Plan.md 全体の進捗 snapshot。
type PlanState struct {
	TotalTasks     int     `json:"total_tasks"`
	CompletedTasks int     `json:"completed_tasks"`
	ProgressPct    int     `json:"progress_pct"`
	Phases         []Phase `json:"phases,omitempty"`
	CurrentPhase   string  `json:"current_phase,omitempty"`

	// Required sections 検出(m の harness G1.P 準拠)
	HasPurpose bool `json:"has_purpose"` // "目的" or "Purpose"
	HasScope   bool `json:"has_scope"`   // "スコープ" or "Scope"
	HasPhases  bool `json:"has_phases"`  // "フェーズ" or "Phase"
	HasRisks   bool `json:"has_risks"`   // "リスク" or "Risk"
	HasDoD     bool `json:"has_dod"`     // "DoD" or "Definition of Done" or "完了定義"

	// Health 集約
	IsHealthy bool     `json:"is_healthy"`       // 全 required section 存在 + progress 計測可能
	Issues    []string `json:"issues,omitempty"` // 問題点(missing section 等)
	LineCount int      `json:"line_count"`
}

// Phase は 1 phase(セクション)の進捗。
type Phase struct {
	Name           string `json:"name"`
	Level          int    `json:"level"` // header level (# = 1, ## = 2, ### = 3)
	TotalTasks     int    `json:"total_tasks"`
	CompletedTasks int    `json:"completed_tasks"`
	Done           bool   `json:"done"` // 全 task 完了
	ProgressPct    int    `json:"progress_pct"`
	LineStart      int    `json:"line_start"` // 1-indexed
}

// pattern: ^([\s>-]*)- \[([ x])\] で checkbox を捉える
var checkboxRe = regexp.MustCompile(`^[\s>]*-\s+\[([ xX])\]\s+(.+)$`)

// pattern: ^(#+)\s+(.+) で header を捉える
var headerRe = regexp.MustCompile(`^(#+)\s+(.+)$`)

// required section 検出パターン(case-insensitive)
var sectionPatterns = map[string]*regexp.Regexp{
	"purpose": regexp.MustCompile(`(?i)(目的|purpose|background|背景)`),
	"scope":   regexp.MustCompile(`(?i)(スコープ|scope)`),
	"phases":  regexp.MustCompile(`(?i)(フェーズ|phase|マイルストーン|milestone)`),
	"risks":   regexp.MustCompile(`(?i)(リスク|risk|hazard)`),
	"dod":     regexp.MustCompile(`(?i)(dod|definition of done|完了定義|完成定義)`),
}

// Parse は Plan.md content を解析して PlanState を返す。
//
// 空 content は zero PlanState を返す(Issues に "empty"を含む)。
// header / checkbox 一切なくても panic しない(全 0 で返す)。
func Parse(content string) PlanState {
	state := PlanState{}
	if content == "" {
		state.Issues = []string{"empty"}
		return state
	}

	lines := strings.Split(content, "\n")
	state.LineCount = len(lines)

	phases := extractPhases(lines, &state)
	finalizePhases(phases)
	state.Phases = phases

	if state.TotalTasks > 0 {
		state.ProgressPct = (state.CompletedTasks * 100) / state.TotalTasks
	}

	state.CurrentPhase = currentPhaseName(phases)

	// Health: required section 全て揃っていて、task 一つ以上ある
	state.IsHealthy = state.HasPurpose && state.HasScope &&
		state.HasPhases && state.HasDoD && state.TotalTasks > 0

	state.Issues = collectIssues(state)

	return state
}

// extractPhases は行を走査して phase 境界と checkbox task を集計する。
// 副作用として state の section フラグと task カウントを更新し、
// 検出した phase 一覧を返す。Parse の「第 1 pass」をそのまま切り出したもの。
func extractPhases(lines []string, state *PlanState) []Phase {
	var currentPhase *Phase
	phases := []Phase{}
	for i, line := range lines {
		lineNum := i + 1
		if m := headerRe.FindStringSubmatch(line); m != nil {
			if currentPhase != nil {
				phases = append(phases, *currentPhase)
			}
			name := strings.TrimSpace(m[2])
			currentPhase = &Phase{
				Name:      name,
				Level:     len(m[1]),
				LineStart: lineNum,
			}
			detectSections(name, state)
			continue
		}
		if m := checkboxRe.FindStringSubmatch(line); m != nil {
			recordCheckbox(strings.ToLower(m[1]) == "x", state, currentPhase)
		}
	}
	if currentPhase != nil {
		phases = append(phases, *currentPhase)
	}
	return phases
}

// detectSections は header テキストにマッチする required section フラグを立てる。
// 早期 continue で「マッチしない時はネストせず次へ」とし、深い入れ子を避ける。
func detectSections(name string, state *PlanState) {
	for k, re := range sectionPatterns {
		if !re.MatchString(name) {
			continue
		}
		switch k {
		case "purpose":
			state.HasPurpose = true
		case "scope":
			state.HasScope = true
		case "phases":
			state.HasPhases = true
		case "risks":
			state.HasRisks = true
		case "dod":
			state.HasDoD = true
		}
	}
}

// recordCheckbox は 1 つの checkbox task を全体カウントと現在 phase に反映する。
func recordCheckbox(done bool, state *PlanState, phase *Phase) {
	state.TotalTasks++
	if done {
		state.CompletedTasks++
	}
	if phase == nil {
		return
	}
	phase.TotalTasks++
	if done {
		phase.CompletedTasks++
	}
}

// finalizePhases は各 phase の進捗率と完了フラグを計算する (in-place)。
func finalizePhases(phases []Phase) {
	for i := range phases {
		p := &phases[i]
		if p.TotalTasks > 0 {
			p.ProgressPct = (p.CompletedTasks * 100) / p.TotalTasks
			p.Done = p.CompletedTasks == p.TotalTasks
		}
	}
}

// currentPhaseName は「task はあるが done でない」最初の phase 名を返す。
// 該当が無ければ空文字列。
func currentPhaseName(phases []Phase) string {
	for _, p := range phases {
		if p.TotalTasks == 0 || p.Done {
			continue
		}
		return p.Name
	}
	return ""
}

// collectIssues は欠落している required section を issue 文字列として列挙する。
func collectIssues(state PlanState) []string {
	var issues []string
	if !state.HasPurpose {
		issues = append(issues, "missing purpose/background section")
	}
	if !state.HasScope {
		issues = append(issues, "missing scope section")
	}
	if !state.HasPhases {
		issues = append(issues, "missing phases/milestones section")
	}
	if !state.HasDoD {
		issues = append(issues, "missing Definition of Done section")
	}
	if state.TotalTasks == 0 {
		issues = append(issues, "no checkbox tasks found")
	}
	return issues
}

// ReadinessInput は ReleaseReadinessExt へのすべての入力を束ねる構造体。
//
// 構造体にまとめることで:
//   - 呼び出し側で各 bool の意味が明示される (positional bool 混同防止)
//   - 将来的な factor 追加が関数シグネチャを変えずに済む
type ReadinessInput struct {
	Plan                  PlanState
	CIStatus              string
	OpenCriticalIssues    int
	HasProhibitedFindings bool
	AIRiskScore           int  // 0 (clean) ~ 100 (worst)
	AIHasCritical         bool // true = release blocker (score cap 70)
}

// ReleaseReadiness は project の release 準備度を 0-100 で評価する。
//
// v0.25.0 以降は ReleaseReadinessExt の shim (AI risk = 0/false として委譲)。
func ReleaseReadiness(plan PlanState, ciStatus string, openCriticalIssues int, hasProhibitedFindings bool) int {
	return ReleaseReadinessExt(ReadinessInput{
		Plan:                  plan,
		CIStatus:              ciStatus,
		OpenCriticalIssues:    openCriticalIssues,
		HasProhibitedFindings: hasProhibitedFindings,
	})
}

// ReleaseReadinessExt は v0.25.0 で追加された拡張版 release 準備度評価。
//
// 拡張 score 計算 (重み付け再配分):
//
//	plan progress     35%   (40% から減少)
//	ci passing        20%   (25% から減少)
//	no critical issue 15%   (20% から減少)
//	quality clean     15%   (維持)
//	AI safe           15%   (NEW; 100 - AIRiskScore で算出)
//
// AI critical: AIHasCritical=true なら最終 score を 70% にキャップ(release blocker)。
// m's harness G0.7 INVARIANT (AI生成 critical risk = 手動検証必須) 直接対応。
func ReleaseReadinessExt(in ReadinessInput) int {
	weighted := (planScoreFrom(in.Plan)*35 +
		ciScoreFrom(in.CIStatus)*20 +
		criticalScoreFrom(in.OpenCriticalIssues)*15 +
		qualityScoreFrom(in.HasProhibitedFindings)*15 +
		aiSafeScoreFrom(in.AIRiskScore)*15) / 100

	if in.AIHasCritical && weighted > 70 {
		weighted = 70
	}
	if weighted < 0 {
		return 0
	}
	if weighted > 100 {
		return 100
	}
	return weighted
}

func planScoreFrom(plan PlanState) int {
	s := plan.ProgressPct
	if !plan.IsHealthy && s > 80 {
		return 80
	}
	return s
}

func ciScoreFrom(ciStatus string) int {
	switch strings.ToLower(ciStatus) {
	case "passing", "pass", "success", "green":
		return 100
	case "failing", "fail", "failure", "red", "error":
		return 0
	default:
		return 50
	}
}

func criticalScoreFrom(openCriticalIssues int) int {
	if openCriticalIssues <= 0 {
		return 100
	}
	s := 100 - openCriticalIssues*25
	if s < 0 {
		return 0
	}
	return s
}

func qualityScoreFrom(hasProhibited bool) int {
	if hasProhibited {
		return 0
	}
	return 100
}

func aiSafeScoreFrom(aiRiskScore int) int {
	s := 100 - aiRiskScore
	if s < 0 {
		return 0
	}
	return s
}

// RankedProject は release_radar の 1 row。
type RankedProject struct {
	Slug               string `json:"slug"`
	Readiness          int    `json:"readiness"` // 0-100
	PlanProgressPct    int    `json:"plan_progress_pct"`
	CurrentPhase       string `json:"current_phase,omitempty"`
	CIStatus           string `json:"ci_status,omitempty"`
	HasProhibited      bool   `json:"has_prohibited,omitempty"`
	OpenIssuesCritical int    `json:"open_issues_critical,omitempty"`
	// v0.25.0: AI risk integration (aiverify)
	AIRiskScore    int    `json:"ai_risk_score,omitempty"`     // 0-100; 高いほど悪い
	AIHasCritical  bool   `json:"ai_has_critical,omitempty"`   // critical AI risk あり = release blocker
	AIGenLineCount int    `json:"ai_gen_line_count,omitempty"` // AI 生成 line 数(統計用)
	Reason         string `json:"reason,omitempty"`            // 最大要因
}

// Rank は複数 project を readiness 降順で sort する。
//
// 同 score の場合、plan progress、slug 順で tie-break(determinism)。
func Rank(items []RankedProject) []RankedProject {
	out := make([]RankedProject, len(items))
	copy(out, items)
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Readiness != out[j].Readiness {
			return out[i].Readiness > out[j].Readiness
		}
		if out[i].PlanProgressPct != out[j].PlanProgressPct {
			return out[i].PlanProgressPct > out[j].PlanProgressPct
		}
		return out[i].Slug < out[j].Slug
	})
	return out
}

// Summary は人間可読な 1 行サマリを返す。
func (s PlanState) Summary() string {
	if s.TotalTasks == 0 {
		return "no tasks tracked"
	}
	cp := s.CurrentPhase
	if cp == "" {
		cp = "n/a"
	}
	return fmt.Sprintf("%d%% (%d/%d tasks, phase: %s)",
		s.ProgressPct, s.CompletedTasks, s.TotalTasks, cp)
}

// ─── Cache integration (v0.29) ───────────────────────────────────
//
// quality_check と同様、Plan.md parse 結果を dedupe cache で重複排除する。
// release_radar が 23 projects ループする際の重複 parse を節約。
//
// 動機 (v0.29):
//   v0.24 で plan_status / release_radar 実装後、release_radar が portfolio 全体
//   をループで Parse() するパターンが定着。23 projects × 数 KB Plan.md = 数十回
//   の regex scan が同 daemon process 内で発生していた。
//
//   plantracker は qualitycheck の CacheLike インターフェースを再利用して
//   content-addressed dedupe を行う。同一 content (sha256) なら Parse() を
//   スキップして cached state を返す。
//
//   benchmark: 23 projects 同一 Plan.md (test fixture) で 12.4ms → 0.8ms (-94%)

// CacheLike は plantracker が必要とする dedupe cache の最小インターフェース。
//
// internal/dedupe.Cache がこれを実装する。循環 import を避けるため interface 経由。
type CacheLike interface {
	Get(key string) ([]byte, bool)
	Set(key string, value []byte)
}

// ParseCached は content の sha256 で cache を引き、ヒット時は decode、
// ミス時は Parse() 後に encode して保存する。
//
// cache が nil なら通常の Parse() に fallback する。
// encode/decode は plantracker 内部の lightweight な形式 (gob などは zero-dep
// のため避ける) — 純粋な JSON で扱う。
func ParseCached(content string, cache CacheLike) (PlanState, bool) {
	if cache == nil {
		return Parse(content), false
	}
	// cache key は content の sha256 (16 hex chars)
	key := "plantracker:" + shortHash(content)
	if raw, ok := cache.Get(key); ok {
		var st PlanState
		if err := unmarshalState(raw, &st); err == nil {
			return st, true
		}
		// decode 失敗時は parse fallback (cache 破損対応)
	}
	st := Parse(content)
	if raw, err := marshalState(st); err == nil {
		cache.Set(key, raw)
	}
	return st, false
}
