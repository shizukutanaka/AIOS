package plantracker

import (
	"strings"
	"testing"
)

// ─── Parse: 基本 ───────────────────────────────────────────

func TestParse_Empty(t *testing.T) {
	s := Parse("")
	if s.TotalTasks != 0 {
		t.Errorf("empty: TotalTasks should be 0")
	}
	if len(s.Issues) == 0 || s.Issues[0] != "empty" {
		t.Errorf("empty: should have 'empty' issue")
	}
}

func TestParse_BasicProgress(t *testing.T) {
	content := `## Phase 1
- [x] done task
- [x] another done
- [ ] not yet
`
	s := Parse(content)
	if s.TotalTasks != 3 || s.CompletedTasks != 2 {
		t.Errorf("got %d/%d, want 2/3", s.CompletedTasks, s.TotalTasks)
	}
	if s.ProgressPct != 66 {
		t.Errorf("progress: got %d, want 66", s.ProgressPct)
	}
}

func TestParse_MultiPhase(t *testing.T) {
	content := `## Phase 1
- [x] a
- [x] b

## Phase 2
- [ ] c
- [ ] d
`
	s := Parse(content)
	if len(s.Phases) != 2 {
		t.Fatalf("phases: got %d, want 2", len(s.Phases))
	}
	if !s.Phases[0].Done {
		t.Error("Phase 1 should be Done")
	}
	if s.Phases[1].Done {
		t.Error("Phase 2 should not be Done")
	}
	if s.CurrentPhase != "Phase 2" {
		t.Errorf("CurrentPhase: got %q, want Phase 2", s.CurrentPhase)
	}
}

// ─── Required sections (m's harness G1.P) ─────────────────

func TestParse_AllSectionsPresent(t *testing.T) {
	content := `# Plan

## 目的
背景の説明

## スコープ
- IN: foo
- OUT: bar

## フェーズ
- [x] phase a
- [ ] phase b

## リスクと対策
- risk x → 対策 y

## 完了定義
- [ ] DoD criterion 1
`
	s := Parse(content)
	if !s.HasPurpose {
		t.Error("should detect 目的")
	}
	if !s.HasScope {
		t.Error("should detect スコープ")
	}
	if !s.HasPhases {
		t.Error("should detect フェーズ")
	}
	if !s.HasRisks {
		t.Error("should detect リスク")
	}
	if !s.HasDoD {
		t.Error("should detect 完了定義")
	}
	if !s.IsHealthy {
		t.Error("all sections + tasks present should be healthy")
	}
}

func TestParse_EnglishSections(t *testing.T) {
	content := `## Purpose
text

## Scope
- IN
- OUT

## Phases
- [x] a

## Definition of Done
- [ ] criterion
`
	s := Parse(content)
	if !s.HasPurpose || !s.HasScope || !s.HasPhases || !s.HasDoD {
		t.Errorf("English sections should be detected: %+v", s)
	}
}

func TestParse_MissingSections(t *testing.T) {
	content := `## Phase 1
- [ ] a task
`
	s := Parse(content)
	if s.IsHealthy {
		t.Error("missing required sections should not be healthy")
	}
	expectedIssues := []string{
		"missing purpose",
		"missing scope",
		"missing Definition of Done",
	}
	for _, want := range expectedIssues {
		found := false
		for _, got := range s.Issues {
			if strings.Contains(got, want) {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("expected issue containing %q, got: %v", want, s.Issues)
		}
	}
}

// ─── Checkbox edge cases ─────────────────────────────

func TestParse_CapitalXAlsoCounts(t *testing.T) {
	content := `- [X] uppercase X
- [x] lowercase x
- [ ] empty
`
	s := Parse(content)
	if s.CompletedTasks != 2 {
		t.Errorf("X and x both count: got %d, want 2", s.CompletedTasks)
	}
}

func TestParse_NestedCheckboxes(t *testing.T) {
	content := `- [ ] top
  - [x] nested done
  - [ ] nested undone
`
	s := Parse(content)
	if s.TotalTasks != 3 {
		t.Errorf("nested checkboxes count: got %d, want 3", s.TotalTasks)
	}
}

func TestParse_IgnoresNonCheckboxLines(t *testing.T) {
	content := `# Plan
text without checkbox
- a regular bullet
- another item
text [x] not a checkbox
- [ ] real checkbox
`
	s := Parse(content)
	if s.TotalTasks != 1 {
		t.Errorf("only real checkbox counted: got %d, want 1", s.TotalTasks)
	}
}

// ─── CurrentPhase ────────────────────────────────────

func TestParse_CurrentPhaseIsFirstUnfinished(t *testing.T) {
	content := `## Done Phase
- [x] done

## In Progress
- [x] mid done
- [ ] mid undone

## Future
- [ ] future
`
	s := Parse(content)
	if s.CurrentPhase != "In Progress" {
		t.Errorf("CurrentPhase: got %q, want 'In Progress'", s.CurrentPhase)
	}
}

func TestParse_NoTasksMeansNoCurrentPhase(t *testing.T) {
	content := `## A
text
## B
- [x] all done`
	s := Parse(content)
	if s.CurrentPhase != "" {
		t.Errorf("CurrentPhase should be empty (all done): got %q", s.CurrentPhase)
	}
}

// ─── ReleaseReadiness ───────────────────────────────

func TestReleaseReadiness_AllGreen(t *testing.T) {
	plan := PlanState{
		TotalTasks: 10, CompletedTasks: 10, ProgressPct: 100,
		HasPurpose: true, HasScope: true, HasPhases: true, HasDoD: true,
		IsHealthy: true,
	}
	score := ReleaseReadiness(plan, "passing", 0, false)
	if score != 100 {
		t.Errorf("all green: got %d, want 100", score)
	}
}

func TestReleaseReadiness_AllRed(t *testing.T) {
	plan := PlanState{
		TotalTasks: 10, CompletedTasks: 0, ProgressPct: 0,
		IsHealthy: false,
	}
	// 新 weights (v0.25): 35/20/15/15/15
	// All red but ai-safe is 100 → score = 15×100/100 = 15
	// AI も red にすると全 0 → 0
	score := ReleaseReadinessExt(ReadinessInput{Plan: plan, CIStatus: "failing", OpenCriticalIssues: 10, HasProhibitedFindings: true, AIRiskScore: 100})
	if score != 0 {
		t.Errorf("all red incl AI: got %d, want 0", score)
	}
	// 旧 API (AI 不問) は ai_safe=100 のまま → 15
	scoreOld := ReleaseReadiness(plan, "failing", 10, true)
	if scoreOld != 15 {
		t.Errorf("old API all red (ai_safe=100): got %d, want 15", scoreOld)
	}
}

func TestReleaseReadiness_PartialGreen(t *testing.T) {
	plan := PlanState{TotalTasks: 10, CompletedTasks: 5, ProgressPct: 50,
		HasPurpose: true, HasScope: true, HasPhases: true, HasDoD: true,
		IsHealthy: true}
	// 新 weights: 50×35 + 100×20 + 100×15 + 100×15 + 100×15 = 1750+2000+1500+1500+1500 = 8250/100 = 82
	score := ReleaseReadiness(plan, "passing", 0, false)
	if score != 82 {
		t.Errorf("partial green: got %d, want 82", score)
	}
}

func TestReleaseReadiness_UnhealthyPlanCapsAt80(t *testing.T) {
	plan := PlanState{TotalTasks: 10, CompletedTasks: 10, ProgressPct: 100,
		IsHealthy: false}
	// plan score 100 → capped to 80 → 35×80 + 20×100 + 15×100 + 15×100 + 15×100
	//                 = 2800+2000+1500+1500+1500 = 9300/100 = 93
	score := ReleaseReadiness(plan, "passing", 0, false)
	if score != 93 {
		t.Errorf("unhealthy plan capped: got %d, want 93", score)
	}
}

func TestReleaseReadiness_UnknownCIScores50(t *testing.T) {
	plan := PlanState{ProgressPct: 100, IsHealthy: true,
		HasPurpose: true, HasScope: true, HasPhases: true, HasDoD: true,
		TotalTasks: 1, CompletedTasks: 1}
	// 100×35 + 50×20 + 100×15 + 100×15 + 100×15 = 3500+1000+1500+1500+1500 = 9000/100 = 90
	score := ReleaseReadiness(plan, "unknown", 0, false)
	if score != 90 {
		t.Errorf("unknown CI: got %d, want 90", score)
	}
}

// ─── v0.25.0 拡張: AI risk factor ──────────────────────

func TestReleaseReadinessExt_AICriticalCapsAt70(t *testing.T) {
	plan := PlanState{TotalTasks: 1, CompletedTasks: 1, ProgressPct: 100,
		HasPurpose: true, HasScope: true, HasPhases: true, HasDoD: true,
		IsHealthy: true}
	// 全要素 green でも aiHasCritical=true なら 70 にキャップ
	score := ReleaseReadinessExt(ReadinessInput{Plan: plan, CIStatus: "passing", AIRiskScore: 50, AIHasCritical: true})
	if score != 70 {
		t.Errorf("AI critical should cap at 70: got %d", score)
	}
}

func TestReleaseReadinessExt_AIRiskReducesScore(t *testing.T) {
	plan := PlanState{TotalTasks: 1, CompletedTasks: 1, ProgressPct: 100,
		HasPurpose: true, HasScope: true, HasPhases: true, HasDoD: true,
		IsHealthy: true}
	// AI clean: 100 → 100
	clean := ReleaseReadinessExt(ReadinessInput{Plan: plan, CIStatus: "passing"})
	// AI risky: aiRisk=80 → aiSafe=20 → 35+20+15+15 + 15×20/100 = 85 + 3 = 88
	risky := ReleaseReadinessExt(ReadinessInput{Plan: plan, CIStatus: "passing", AIRiskScore: 80})
	if clean <= risky {
		t.Errorf("AI clean (%d) should beat AI risky (%d)", clean, risky)
	}
}

func TestReleaseReadinessExt_BackwardCompatViaOldAPI(t *testing.T) {
	plan := PlanState{TotalTasks: 1, CompletedTasks: 1, ProgressPct: 100,
		HasPurpose: true, HasScope: true, HasPhases: true, HasDoD: true,
		IsHealthy: true}
	// 旧 API は ext (aiRisk=0, aiHasCritical=false) を呼ぶ → AllGreen 相当
	if ReleaseReadiness(plan, "passing", 0, false) != 100 {
		t.Error("old API should equal AllGreen via Ext")
	}
}

// ─── Rank ────────────────────────────────────────────

func TestRank_DescendingOrder(t *testing.T) {
	items := []RankedProject{
		{Slug: "a", Readiness: 50, PlanProgressPct: 50},
		{Slug: "b", Readiness: 90, PlanProgressPct: 80},
		{Slug: "c", Readiness: 70, PlanProgressPct: 60},
	}
	out := Rank(items)
	if out[0].Slug != "b" || out[1].Slug != "c" || out[2].Slug != "a" {
		t.Errorf("rank order: %v", []string{out[0].Slug, out[1].Slug, out[2].Slug})
	}
}

func TestRank_TieBreakByPlanProgress(t *testing.T) {
	items := []RankedProject{
		{Slug: "a", Readiness: 80, PlanProgressPct: 50},
		{Slug: "b", Readiness: 80, PlanProgressPct: 90},
	}
	out := Rank(items)
	if out[0].Slug != "b" {
		t.Errorf("tie: b should win on progress; got %s first", out[0].Slug)
	}
}

func TestRank_TieBreakBySlugAlphabetical(t *testing.T) {
	items := []RankedProject{
		{Slug: "zeta", Readiness: 80, PlanProgressPct: 50},
		{Slug: "alpha", Readiness: 80, PlanProgressPct: 50},
	}
	out := Rank(items)
	if out[0].Slug != "alpha" {
		t.Errorf("alphabetical tie break: got %s first", out[0].Slug)
	}
}

// ─── Summary ─────────────────────────────────────────

func TestSummary_Format(t *testing.T) {
	s := PlanState{TotalTasks: 10, CompletedTasks: 7, ProgressPct: 70,
		CurrentPhase: "Implementation"}
	got := s.Summary()
	if !strings.Contains(got, "70%") {
		t.Errorf("summary should include 70%%: %s", got)
	}
	if !strings.Contains(got, "Implementation") {
		t.Errorf("summary should include phase: %s", got)
	}
}

func TestSummary_NoTasks(t *testing.T) {
	s := PlanState{}
	got := s.Summary()
	if !strings.Contains(got, "no tasks") {
		t.Errorf("no tasks summary: %s", got)
	}
}

// ─── 大規模 plan の性能 ───────────────────────────

func TestParse_LargePlanPerformance(t *testing.T) {
	// 1000 phases × 5 tasks = 5000 tasks の Plan
	var sb strings.Builder
	sb.WriteString("# Mega Plan\n## 目的\nx\n## スコープ\ny\n## フェーズ\n## 完了定義\n")
	for i := 0; i < 1000; i++ {
		sb.WriteString("### Phase ")
		sb.WriteString(strings.Repeat("x", 1))
		sb.WriteString("\n")
		for j := 0; j < 5; j++ {
			if j%2 == 0 {
				sb.WriteString("- [x] task\n")
			} else {
				sb.WriteString("- [ ] task\n")
			}
		}
	}
	s := Parse(sb.String())
	if s.TotalTasks != 5000 {
		t.Errorf("large plan: got %d tasks, want 5000", s.TotalTasks)
	}
}

// ─── Fuzz tests (v0.28) ───────────────────────────────────

// FuzzParse は任意の string を Plan.md として parse して panic しないことを確認。
//
// 期待: malformed input (binary, 巨大行, 特殊文字, unbalanced markdown) で
// Parse / ReleaseReadiness / Rank が panic / 無限ループしない。
func FuzzParse(f *testing.F) {
	seeds := []string{
		"",
		"## 目的\n- [x] x",
		"# x\n## scope\n- [ ] y\n## DoD\n- [x] z",
		strings.Repeat("- [ ] x\n", 1000),
		"# 標 \x00 binary",
		"## A\n## B\n## C\n## D\n## E",
		"- [-] invalid checkbox",
		"## phase\n  - [x] nested\n    - [ ] deeper",
	}
	for _, s := range seeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, content string) {
		state := Parse(content)
		// 不変量チェック
		if state.CompletedTasks > state.TotalTasks {
			t.Errorf("completed > total: %d > %d", state.CompletedTasks, state.TotalTasks)
		}
		if state.ProgressPct < 0 || state.ProgressPct > 100 {
			t.Errorf("progress out of range: %d", state.ProgressPct)
		}
		// ReleaseReadiness も panic しないこと
		score := ReleaseReadiness(state, "passing", 0, false)
		if score < 0 || score > 100 {
			t.Errorf("readiness out of range: %d", score)
		}
		// Summary も panic しないこと
		_ = state.Summary()
	})
}

// ─── ParseCached (v0.29) ─────────────────────────────────────────

type fakeCache struct {
	store  map[string][]byte
	hits   int
	misses int
}

func newFakeCache() *fakeCache {
	return &fakeCache{store: map[string][]byte{}}
}

func (c *fakeCache) Get(key string) ([]byte, bool) {
	v, ok := c.store[key]
	if ok {
		c.hits++
	} else {
		c.misses++
	}
	return v, ok
}
func (c *fakeCache) Set(key string, value []byte) {
	c.store[key] = value
}

func TestParseCached_NilCacheFallsBack(t *testing.T) {
	content := "## 目的\n- [x] x\n## スコープ\n## フェーズ\n## 完了定義\n- [x] y"
	st, hit := ParseCached(content, nil)
	if hit {
		t.Error("nil cache must not report hit")
	}
	if st.TotalTasks == 0 {
		t.Error("expected non-zero tasks")
	}
}

func TestParseCached_FirstCallMissesAndPopulates(t *testing.T) {
	cache := newFakeCache()
	content := "## 目的\n- [x] a\n## スコープ\n## フェーズ\n## 完了定義"
	st, hit := ParseCached(content, cache)
	if hit {
		t.Error("first call must be a miss")
	}
	if cache.hits != 0 || cache.misses != 1 {
		t.Errorf("stats: hits=%d misses=%d", cache.hits, cache.misses)
	}
	if len(cache.store) != 1 {
		t.Errorf("cache should have 1 entry, got %d", len(cache.store))
	}
	if st.CompletedTasks != 1 {
		t.Errorf("expected 1 completed, got %d", st.CompletedTasks)
	}
}

func TestParseCached_SecondCallHits(t *testing.T) {
	cache := newFakeCache()
	content := "## 目的\n- [x] a\n## スコープ\n## フェーズ\n## 完了定義"
	first, _ := ParseCached(content, cache)
	second, hit := ParseCached(content, cache)
	if !hit {
		t.Error("second call should hit cache")
	}
	if first.CompletedTasks != second.CompletedTasks ||
		first.ProgressPct != second.ProgressPct {
		t.Errorf("cached state diverged: first=%+v second=%+v", first, second)
	}
}

func TestParseCached_DifferentContentMisses(t *testing.T) {
	cache := newFakeCache()
	ParseCached("## 目的\n- [x] a", cache)
	ParseCached("## 目的\n- [ ] b", cache)
	if cache.hits != 0 {
		t.Errorf("different content should yield no hits, got %d hits", cache.hits)
	}
	if len(cache.store) != 2 {
		t.Errorf("cache should have 2 entries, got %d", len(cache.store))
	}
}

func TestShortHash_StableForSameInput(t *testing.T) {
	if shortHash("hello") != shortHash("hello") {
		t.Error("hash unstable")
	}
}

func TestShortHash_LengthIs16Chars(t *testing.T) {
	if len(shortHash("x")) != 16 {
		t.Errorf("expected 16 hex chars, got %d", len(shortHash("x")))
	}
}

func TestParseCached_CorruptCacheValueFallsBackToParse(t *testing.T) {
	cache := newFakeCache()
	content := "## 目的\n- [x] a\n## スコープ\n## フェーズ\n## 完了定義"
	// 壊れた JSON を inject
	cache.store["plantracker:"+shortHash(content)] = []byte(`{not json`)
	st, _ := ParseCached(content, cache)
	// fallback parse 結果が返るので fields は埋まる
	if st.CompletedTasks != 1 {
		t.Errorf("fallback parse should still work, got %+v", st)
	}
}
