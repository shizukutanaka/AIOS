// userconfig.go — custom rule loading from .yagura/secretscan.json (v0.36.0).
//
// 動機 (Roadmap #4 の完遂):
//   qualitycheck (RuleSpec/CompileRules) と aiverify (UserConfig) は既に
//   project 固有の custom rule をサポートしていたが、3 つ目の scanner である
//   secretscan には JSON からの custom rule loading が無かった。組織内の独自
//   token 形式(例: 社内 API key prefix)はデフォルト rule set では検出できない。
//   `.yagura/secretscan.json` で rule を追加 / 既存 rule を無効化できるようにする。
//
// JSON 形式:
//   {
//     "rules": [
//       {"id":"acme-token","description":"ACME internal token",
//        "pattern":"acme_[A-Za-z0-9]{16}","severity":"HIGH","entropy_min":3.0,"capture_idx":0}
//     ],
//     "disable": ["aws-access-key-id"]
//   }
//
// ADR-0001: stdlib のみ(encoding/json + regexp)。
package secretscan

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
)

// maxPatternLen は custom rule の regexp 長上限(暴走防止)。
const maxPatternLen = 1000

// maxEntropyBits は Shannon entropy (bits/char) の理論上限 = log2(256)。
// これを超える entropy_min はどんな文字列でも到達不能で、ルールを
// サイレントに dead no-op 化するため拒否する。
const maxEntropyBits = 8.0

// RuleSpec は JSON 入力用の rule(compiled regex を持たない)。
type RuleSpec struct {
	ID          string   `json:"id"`
	Description string   `json:"description,omitempty"`
	Pattern     string   `json:"pattern"`
	Severity    Severity `json:"severity,omitempty"`
	EntropyMin  float64  `json:"entropy_min,omitempty"`
	CaptureIdx  int      `json:"capture_idx,omitempty"`
}

// UserConfig は .yagura/secretscan.json のトップレベル。
type UserConfig struct {
	Rules   []RuleSpec `json:"rules,omitempty"`
	Disable []string   `json:"disable,omitempty"`
}

// CompileRules は RuleSpec 群を Rule へコンパイルする。決定論的(入力順保持)。
//   - id / pattern は必須。
//   - severity 未指定は MEDIUM。不正値はエラー。
//   - pattern は Go regexp(RE2、線形時間=ReDoS なし)で Compile。
func CompileRules(specs []RuleSpec) ([]Rule, error) {
	out := make([]Rule, 0, len(specs))
	for i, s := range specs {
		if strings.TrimSpace(s.ID) == "" {
			return nil, fmt.Errorf("secretscan: custom rule #%d: id is required", i)
		}
		if strings.TrimSpace(s.Pattern) == "" {
			return nil, fmt.Errorf("secretscan: custom rule %q: pattern is required", s.ID)
		}
		if len(s.Pattern) > maxPatternLen {
			return nil, fmt.Errorf("secretscan: custom rule %q: pattern too long (>%d)", s.ID, maxPatternLen)
		}
		sev := s.Severity
		if sev == "" {
			sev = SeverityMedium
		}
		if sev != SeverityCritical && sev != SeverityHigh && sev != SeverityMedium && sev != SeverityLow {
			return nil, fmt.Errorf("secretscan: custom rule %q: invalid severity %q (CRITICAL/HIGH/MEDIUM/LOW)", s.ID, sev)
		}
		re, err := regexp.Compile(s.Pattern)
		if err != nil {
			return nil, fmt.Errorf("secretscan: custom rule %q: invalid pattern: %w", s.ID, err)
		}
		// entropy_min / capture_idx は Scan の guard で範囲外だと黙って無視
		// (→ filter 無効化 or full-match へ fallback)されるため、ここで
		// 到達不能な値を拒否し、サイレントな misconfig を防ぐ。
		if s.EntropyMin < 0 {
			return nil, fmt.Errorf("secretscan: custom rule %q: entropy_min must be >= 0 (0 = no filter)", s.ID)
		}
		if s.EntropyMin > maxEntropyBits {
			return nil, fmt.Errorf("secretscan: custom rule %q: entropy_min %.2f exceeds max Shannon entropy %.1f (rule could never fire)", s.ID, s.EntropyMin, maxEntropyBits)
		}
		if s.CaptureIdx < 0 {
			return nil, fmt.Errorf("secretscan: custom rule %q: capture_idx must be >= 0 (0 = full match)", s.ID)
		}
		if s.CaptureIdx > re.NumSubexp() {
			return nil, fmt.Errorf("secretscan: custom rule %q: capture_idx %d exceeds pattern's %d capture group(s)", s.ID, s.CaptureIdx, re.NumSubexp())
		}
		out = append(out, Rule{
			ID:          s.ID,
			Description: s.Description,
			Severity:    sev,
			Regex:       re,
			EntropyMin:  s.EntropyMin,
			CaptureIdx:  s.CaptureIdx,
		})
	}
	return out, nil
}

// LoadUserConfig は path の JSON ファイルを読み込む。
// ファイルが存在しない場合はエラーを返す(呼出側で ErrNotExist を無視可能)。
func LoadUserConfig(path string) (*UserConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("secretscan: read user config %s: %w", path, err)
	}
	var cfg UserConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("secretscan: parse user config %s: %w", path, err)
	}
	return &cfg, nil
}

// Apply は base rule set に対して:
//  1. cfg.Disable に含まれる rule を除外する
//  2. cfg.Rules をコンパイルして末尾に追加する
//
// 戻り値は新しい slice。base は変更されない。
func (cfg *UserConfig) Apply(base []Rule) ([]Rule, error) {
	disableSet := make(map[string]bool, len(cfg.Disable))
	for _, id := range cfg.Disable {
		disableSet[id] = true
	}
	out := make([]Rule, 0, len(base)+len(cfg.Rules))
	for _, r := range base {
		if !disableSet[r.ID] {
			out = append(out, r)
		}
	}
	custom, err := CompileRules(cfg.Rules)
	if err != nil {
		return nil, err
	}
	out = append(out, custom...)
	return out, nil
}
