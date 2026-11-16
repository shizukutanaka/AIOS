package secretscan

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// ─── CompileRules ─────────────────────────────────────────────

func TestCompileRules_HappyPath(t *testing.T) {
	specs := []RuleSpec{
		{ID: "acme-token", Description: "ACME internal token", Severity: SeverityHigh,
			Pattern: `acme_[A-Za-z0-9]{16}`},
	}
	rules, err := CompileRules(specs)
	if err != nil {
		t.Fatalf("CompileRules: %v", err)
	}
	if len(rules) != 1 {
		t.Fatalf("expected 1 rule, got %d", len(rules))
	}
	if rules[0].ID != "acme-token" || rules[0].Regex == nil {
		t.Errorf("rule not compiled correctly: %+v", rules[0])
	}
}

func TestCompileRules_MissingID(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{Pattern: `foo`, Severity: SeverityLow}})
	if err == nil {
		t.Error("expected error for missing id")
	}
}

func TestCompileRules_MissingPattern(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Severity: SeverityLow}})
	if err == nil {
		t.Error("expected error for missing pattern")
	}
}

func TestCompileRules_BadRegex(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `[invalid`, Severity: SeverityLow}})
	if err == nil {
		t.Error("expected error for invalid regex")
	}
}

func TestCompileRules_DefaultSeverity(t *testing.T) {
	rules, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `foo`}})
	if err != nil {
		t.Fatalf("CompileRules: %v", err)
	}
	if rules[0].Severity != SeverityMedium {
		t.Errorf("empty severity should default to MEDIUM, got %q", rules[0].Severity)
	}
}

func TestCompileRules_InvalidSeverity(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `foo`, Severity: "BOGUS"}})
	if err == nil {
		t.Error("expected error for invalid severity")
	}
}

func TestCompileRules_PatternTooLong(t *testing.T) {
	long := make([]byte, maxPatternLen+1)
	for i := range long {
		long[i] = 'a'
	}
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: string(long), Severity: SeverityLow}})
	if err == nil {
		t.Error("expected error for over-long pattern")
	}
}

// TestCompileRules_NegativeEntropyMin rejects a negative entropy threshold.
// Shannon entropy is always ≥ 0 and the documented sentinel for "no filter"
// is 0 — a negative value (e.g. a "-4.0" typo for "4.0") is meaningless and
// would silently disable filtering (EntropyMin>0 guard never trips).
func TestCompileRules_NegativeEntropyMin(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `foo`, Severity: SeverityLow, EntropyMin: -1}})
	if err == nil {
		t.Error("expected error for negative entropy_min")
	}
}

// TestCompileRules_EntropyMinTooHigh rejects a threshold no string can reach.
// Shannon entropy in bits/char is capped at log2(256)=8.0; entropy_min>8.0
// can never pass, silently turning the rule into a dead no-op.
func TestCompileRules_EntropyMinTooHigh(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `foo`, Severity: SeverityLow, EntropyMin: 8.5}})
	if err == nil {
		t.Error("expected error for entropy_min > 8.0 (impossible to reach)")
	}
}

// TestCompileRules_NegativeCaptureIdx rejects a negative capture index. The
// sentinel for "use the full match" is 0; a negative value is meaningless.
func TestCompileRules_NegativeCaptureIdx(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `(foo)`, Severity: SeverityLow, CaptureIdx: -1}})
	if err == nil {
		t.Error("expected error for negative capture_idx")
	}
}

// TestCompileRules_CaptureIdxOutOfRange rejects a capture index the pattern
// can never satisfy. A pattern with 1 group can only address capture_idx 0..1;
// capture_idx 3 would silently fall back to full-match entropy (Scan's
// len(m)>=2*(idx+1) guard), misconfiguring the rule without any signal.
func TestCompileRules_CaptureIdxOutOfRange(t *testing.T) {
	_, err := CompileRules([]RuleSpec{{ID: "x", Pattern: `(foo)`, Severity: SeverityLow, CaptureIdx: 3}})
	if err == nil {
		t.Error("expected error for capture_idx exceeding pattern's group count")
	}
}

// TestCompileRules_ValidEntropyAndCapture confirms in-range numeric fields are
// accepted (guards against over-rejection by the validation above).
func TestCompileRules_ValidEntropyAndCapture(t *testing.T) {
	rules, err := CompileRules([]RuleSpec{
		{ID: "x", Pattern: `key=([A-Za-z0-9]+)`, Severity: SeverityHigh, EntropyMin: 3.5, CaptureIdx: 1},
	})
	if err != nil {
		t.Fatalf("valid entropy_min/capture_idx rejected: %v", err)
	}
	if rules[0].EntropyMin != 3.5 || rules[0].CaptureIdx != 1 {
		t.Errorf("numeric fields not preserved: %+v", rules[0])
	}
}

// ─── LoadUserConfig + Apply ──────────────────────────────────

func TestLoadUserConfig_HappyPath(t *testing.T) {
	cfg := &UserConfig{
		Rules:   []RuleSpec{{ID: "acme", Pattern: `acme_[a-z0-9]{8}`, Severity: SeverityHigh}},
		Disable: []string{"aws-access-key-id"},
	}
	raw, _ := json.Marshal(cfg)
	f := filepath.Join(t.TempDir(), "secretscan.json")
	_ = os.WriteFile(f, raw, 0o644)

	got, err := LoadUserConfig(f)
	if err != nil {
		t.Fatalf("LoadUserConfig: %v", err)
	}
	if len(got.Rules) != 1 || got.Rules[0].ID != "acme" {
		t.Errorf("rules: %+v", got.Rules)
	}
	if len(got.Disable) != 1 || got.Disable[0] != "aws-access-key-id" {
		t.Errorf("disable: %v", got.Disable)
	}
}

func TestLoadUserConfig_MissingFile(t *testing.T) {
	_, err := LoadUserConfig(filepath.Join(t.TempDir(), "nope.json"))
	if err == nil {
		t.Error("expected error for missing file")
	}
}

func TestLoadUserConfig_Malformed(t *testing.T) {
	f := filepath.Join(t.TempDir(), "bad.json")
	_ = os.WriteFile(f, []byte("{nope"), 0o644)
	_, err := LoadUserConfig(f)
	if err == nil {
		t.Error("expected error for malformed JSON")
	}
}

func TestApply_AddsAndDisables(t *testing.T) {
	cfg := &UserConfig{
		Rules:   []RuleSpec{{ID: "acme-token", Pattern: `acme_[A-Za-z0-9]{16}`, Severity: SeverityHigh}},
		Disable: []string{"aws-access-key-id"},
	}
	merged, err := cfg.Apply(DefaultRules())
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	var hasCustom, hasDisabled bool
	for _, r := range merged {
		if r.ID == "acme-token" {
			hasCustom = true
		}
		if r.ID == "aws-access-key-id" {
			hasDisabled = true
		}
	}
	if !hasCustom {
		t.Error("custom rule 'acme-token' missing after Apply")
	}
	if hasDisabled {
		t.Error("disabled rule 'aws-access-key-id' still present after Apply")
	}
}

func TestApply_EmptyConfigPreservesDefaults(t *testing.T) {
	cfg := &UserConfig{}
	merged, err := cfg.Apply(DefaultRules())
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if len(merged) != len(DefaultRules()) {
		t.Errorf("empty config should preserve defaults: want %d got %d", len(DefaultRules()), len(merged))
	}
}

// TestApply_CustomRuleDetects verifies the merged rule actually fires.
func TestApply_CustomRuleDetects(t *testing.T) {
	cfg := &UserConfig{
		Rules: []RuleSpec{{ID: "acme-token", Pattern: `acme_[A-Za-z0-9]{16}`, Severity: SeverityHigh}},
	}
	rules, err := cfg.Apply(DefaultRules())
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	sc := NewWithRules(rules)
	findings := sc.Scan("token = acme_abcd1234efgh5678", "test")
	var found bool
	for _, f := range findings {
		if f.RuleID == "acme-token" {
			found = true
		}
	}
	if !found {
		t.Errorf("custom rule 'acme-token' did not fire; findings: %+v", findings)
	}
}
