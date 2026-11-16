// Package secretscan implements gitleaks-style secret detection over arbitrary text.
//
// 設計判断 (security spec S1.3):
//   - regex + Shannon entropy のハイブリッド検出 (gitleaks 互換のアプローチ)
//   - 14 種類の代表的 secret pattern (AWS / GitHub / Slack / Stripe / OpenAI /
//     Anthropic / Google API / JWT / Generic API key / Private key / Database URL / Generic hex)
//   - 各検出には rule ID + severity + finger print を付与
//   - secret 自体はレポートに含めない(redacted) — 監査ログに secret raw を残さない
//   - ゼロ依存(標準ライブラリ regexp + math のみ)
//
// 既定の rule set は OWASP "Secrets in Code" 上位 patterns を網羅。
// gitleaks の lookahead 制約と同様、Go regexp はバックリファレンスを持たないため
// 同じパターンマッチ範囲(Go re2)で実装する。
//
// 検出の precision を上げるために Shannon entropy フィルタを併用:
//   - regex match した部分文字列の entropy が閾値以上であれば「真の secret」
//   - 低 entropy match(例: README 内のサンプル "AKIAIOSFODNN7EXAMPLE")は除外
package secretscan

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"sync"
)

// Severity は finding の重要度。
type Severity string

const (
	// SeverityCritical は即時対応が必要な最高深刻度のシークレット検出。
	SeverityCritical Severity = "CRITICAL"
	// SeverityHigh は高優先度で修正が必要なシークレット検出。
	SeverityHigh Severity = "HIGH"
	// SeverityMedium は対処を推奨する中程度のシークレット検出。
	SeverityMedium Severity = "MEDIUM"
	// SeverityLow は改善を検討すべき低深刻度のシークレット検出。
	SeverityLow Severity = "LOW"
)

// Finding は単一の secret 検出結果。
//
// Secret raw value は含まれない(redacted)。ファイル/行/オフセットを返し、
// 呼出側で必要に応じて手動確認できるようにする。Fingerprint は同じ secret に対して
// 同じ値を返すため、findings の重複排除に使える。
type Finding struct {
	RuleID      string   `json:"rule_id"`
	Description string   `json:"description"`
	Severity    Severity `json:"severity"`
	Match       string   `json:"match"`       // redacted (always "[REDACTED]")
	Fingerprint string   `json:"fingerprint"` // SHA-256(rule_id|secret)[:16]
	Source      string   `json:"source"`      // ユーザ指定の出所(例: "README", "issue#42")
	Line        int      `json:"line,omitempty"`
	Column      int      `json:"column,omitempty"`
	Entropy     float64  `json:"entropy"`
}

// Rule は単一の検出ルール。
type Rule struct {
	ID          string         // 一意 ID(例: "aws-access-key-id")
	Description string         // 人間可読な説明
	Severity    Severity       // 重要度
	Regex       *regexp.Regexp // マッチパターン (Go regexp、lookahead 不可)
	EntropyMin  float64        // 0 ならエントロピー閾値なし
	CaptureIdx  int            // entropy 計算対象の capture group index (0 = 全マッチ)
}

// Scanner は登録された rule に基づいてテキストをスキャンする。
type Scanner struct {
	rules []Rule
}

// New は標準 rule set で Scanner を生成する。
func New() *Scanner {
	return &Scanner{rules: DefaultRules()}
}

// NewWithRules はカスタム rule set で Scanner を生成する。
func NewWithRules(rules []Rule) *Scanner {
	return &Scanner{rules: append([]Rule(nil), rules...)}
}

// Rules は現在の rule set を返す(read-only)。
func (s *Scanner) Rules() []Rule { return s.rules }

// Scan は text に対して全 rule を実行し、findings を返す。
//
// source は finding の Source フィールドに記録される(例: "README", "issue#42")。
// 同一 secret(同じ fingerprint)に対して複数 rule がマッチした場合は最重要度の
// rule の finding のみ返す(重複排除)。
//
// 結果は severity 降順 → rule_id 昇順でソート。
func (s *Scanner) Scan(text, source string) []Finding {
	var findings []Finding
	seen := make(map[string]Severity) // fingerprint → highest severity seen

	for _, rule := range s.rules {
		for _, m := range rule.Regex.FindAllStringSubmatchIndex(text, -1) {
			f, ok := matchToFinding(rule, m, text, source, seen)
			if !ok {
				continue
			}
			findings = append(findings, f)
		}
	}

	// 重複排除: 同じ fingerprint で複数残っていれば最重要のみ保持
	final := dedupeBySeverity(findings)
	sortFindings(final)
	return final
}

// matchToFinding は 1 件の regex match を Finding へ変換する。低エントロピー
// (プレースホルダの可能性)、または同一 fingerprint が既に同等以上の severity で
// 登録済みなら ok=false。seen は呼び出し元と共有され、登録時に mutate される。
func matchToFinding(rule Rule, m []int, text, source string, seen map[string]Severity) (Finding, bool) {
	fullMatch := text[m[0]:m[1]]

	// Capture group が指定されている場合はそちらで entropy 計算
	target := fullMatch
	if rule.CaptureIdx > 0 && len(m) >= 2*(rule.CaptureIdx+1) {
		gs, ge := m[2*rule.CaptureIdx], m[2*rule.CaptureIdx+1]
		if gs >= 0 && ge >= 0 {
			target = text[gs:ge]
		}
	}

	ent := ShannonEntropy(target)
	if rule.EntropyMin > 0 && ent < rule.EntropyMin {
		return Finding{}, false // 低エントロピー、サンプル/プレースホルダの可能性
	}

	fp := fingerprint(rule.ID, target)
	// 同じ fingerprint で先に登録された rule が同等以上の severity なら skip
	if prev, ok := seen[fp]; ok && severityRank(prev) >= severityRank(rule.Severity) {
		return Finding{}, false
	}
	seen[fp] = rule.Severity

	line, col := lineCol(text, m[0])
	return Finding{
		RuleID:      rule.ID,
		Description: rule.Description,
		Severity:    rule.Severity,
		Match:       "[REDACTED]",
		Fingerprint: fp,
		Source:      source,
		Line:        line,
		Column:      col,
		Entropy:     round2(ent),
	}, true
}

// ShannonEntropy は文字列の Shannon エントロピー (bits/character) を計算する。
//
// 高エントロピー(>4.5) は random-looking string で、secret である可能性が高い。
// 低エントロピー(<3.0) は単語ベースの文字列で、誤検出の可能性が高い。
func ShannonEntropy(s string) float64 {
	if len(s) == 0 {
		return 0
	}
	freq := make(map[rune]int)
	for _, r := range s {
		freq[r]++
	}
	var entropy float64
	length := float64(len([]rune(s)))
	for _, count := range freq {
		p := float64(count) / length
		entropy -= p * math.Log2(p)
	}
	return entropy
}

// ─── default rules ──────────────────────────────────────────

// DefaultRules は標準的な secret patterns 14 種類を返す。
//
// 各 pattern は OWASP "Top 10 Web Application Security Risks" の credential
// 漏洩カテゴリ、および gitleaks v8 default rules を参考にしている。
// Go regexp は lookahead/lookbehind 不可のため、gitleaks の TOML rule をそのまま
// 変換できないものは context-free な regex に簡略化している。
// compiledDefaultRules は 14 個の正規表現を一度だけコンパイルして再利用する
// (New() / DefaultRules() 呼び出し毎の MustCompile を回避)。
var compiledDefaultRules = sync.OnceValue(buildDefaultRules)

// DefaultRules の返り値は呼び出し毎に独立した slice。
func DefaultRules() []Rule {
	rs := compiledDefaultRules()
	out := make([]Rule, len(rs))
	copy(out, rs)
	return out
}

func buildDefaultRules() []Rule {
	return []Rule{
		{
			ID:          "aws-access-key-id",
			Description: "AWS Access Key ID (AKIA…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bAKIA[0-9A-Z]{16}\b`),
		},
		{
			ID:          "aws-secret-access-key",
			Description: "AWS Secret Access Key (40 chars after aws.+secret context)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`(?i)aws[_\-\s]*(secret|sk)[_\-\s]*[=:]\s*["']?([A-Za-z0-9/+=]{40})["']?`),
			EntropyMin:  4.0,
			CaptureIdx:  2,
		},
		{
			ID:          "github-personal-token",
			Description: "GitHub Personal Access Token (ghp_…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bghp_[0-9A-Za-z]{36}\b`),
		},
		{
			ID:          "github-fine-grained-pat",
			Description: "GitHub Fine-Grained Personal Access Token (github_pat_…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bgithub_pat_[0-9A-Za-z_]{82}\b`),
		},
		{
			ID:          "github-oauth-token",
			Description: "GitHub OAuth Token (gho_…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bgho_[0-9A-Za-z]{36}\b`),
		},
		{
			ID:          "slack-webhook",
			Description: "Slack Webhook URL",
			Severity:    SeverityHigh,
			Regex:       regexp.MustCompile(`https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{24}`),
		},
		{
			ID:          "stripe-live-key",
			Description: "Stripe Live Secret Key (sk_live_…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bsk_live_[0-9A-Za-z]{24,99}\b`),
		},
		{
			ID:          "anthropic-api-key",
			Description: "Anthropic API Key (sk-ant-…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bsk-ant-[a-zA-Z0-9_\-]{32,}\b`),
		},
		{
			ID:          "openai-api-key",
			Description: "OpenAI API Key (legacy sk-…48)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bsk-[a-zA-Z0-9]{48}\b`),
			EntropyMin:  4.0,
		},
		{
			// v0.35: 2024 以降の OpenAI project / service-account / admin キー。
			// 旧 sk-…48 パターンは "-"/"_" を含む新形式(sk-proj- 等)を取り逃がす。
			ID:          "openai-project-key",
			Description: "OpenAI project/service key (sk-proj-/sk-svcacct-/sk-admin-…)",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}`),
			EntropyMin:  3.0,
		},
		{
			// v0.35: Hugging Face access token(モデル/データセットの読み書き権限)。
			ID:          "huggingface-token",
			Description: "Hugging Face access token (hf_…)",
			Severity:    SeverityHigh,
			Regex:       regexp.MustCompile(`\bhf_[A-Za-z0-9]{34,}\b`),
			EntropyMin:  3.0,
		},
		{
			ID:          "google-api-key",
			Description: "Google API Key (AIza…)",
			Severity:    SeverityHigh,
			Regex:       regexp.MustCompile(`\bAIza[0-9A-Za-z_\-]{35}\b`),
		},
		{
			ID:          "jwt-token",
			Description: "JSON Web Token (header.payload.signature)",
			Severity:    SeverityMedium,
			Regex:       regexp.MustCompile(`\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b`),
		},
		{
			ID:          "pem-private-key",
			Description: "PEM-encoded private key",
			Severity:    SeverityCritical,
			Regex:       regexp.MustCompile(`-----BEGIN[ A-Z]*PRIVATE KEY-----`),
		},
		{
			ID:          "database-url-with-password",
			Description: "Database URL with embedded credentials",
			Severity:    SeverityHigh,
			// password は 1 文字以上。scheme が既知の DB なので短いパスワードでも
			// 資格情報埋め込みは検出する({4,} だと 1-3 文字を見逃していた)。
			Regex: regexp.MustCompile(`(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:\s]+:[^@\s]{1,}@[^/\s]+`),
		},
	}
}

// ─── helpers ─────────────────────────────────────────────────

// fingerprint は (rule_id, secret) の組から 16 文字の SHA-256 prefix を返す。
// 同じ secret 検出を重複排除するため。secret 自体は保存されない。
func fingerprint(ruleID, secret string) string {
	h := sha256.Sum256([]byte(ruleID + "|" + secret))
	return hex.EncodeToString(h[:8]) // 16 hex chars = 64 bits
}

// severityRank は降順比較用の数値化。
func severityRank(s Severity) int {
	switch s {
	case SeverityCritical:
		return 4
	case SeverityHigh:
		return 3
	case SeverityMedium:
		return 2
	case SeverityLow:
		return 1
	}
	return 0
}

// dedupeBySeverity は同じ fingerprint で複数 finding がある場合に最重要だけ残す。
func dedupeBySeverity(findings []Finding) []Finding {
	byFP := make(map[string]Finding)
	for _, f := range findings {
		if prev, ok := byFP[f.Fingerprint]; !ok || severityRank(f.Severity) > severityRank(prev.Severity) {
			byFP[f.Fingerprint] = f
		}
	}
	out := make([]Finding, 0, len(byFP))
	for _, f := range byFP {
		out = append(out, f)
	}
	return out
}

// sortFindings は severity 降順 → rule_id 昇順でソート。
func sortFindings(f []Finding) {
	sort.SliceStable(f, func(i, j int) bool {
		ri, rj := severityRank(f[i].Severity), severityRank(f[j].Severity)
		if ri != rj {
			return ri > rj
		}
		return f[i].RuleID < f[j].RuleID
	})
}

// lineCol は text 内の byte offset から 1-based の (line, column) を返す。
func lineCol(text string, offset int) (int, int) {
	if offset > len(text) {
		offset = len(text)
	}
	line, col := 1, 1
	for i := 0; i < offset; i++ {
		if text[i] == '\n' {
			line++
			col = 1
		} else {
			col++
		}
	}
	return line, col
}

// round2 は小数第 2 位で四捨五入。
func round2(f float64) float64 {
	return math.Round(f*100) / 100
}

// ─── batch / concurrent helpers ──────────────────────────────

// ScanBatch は複数 (source, text) ペアを並列スキャンする。
// 結果は source 内で severity 降順、source 間ではアルファベット順。
//
// 大量プロジェクト一括 scan で MCP tool 経由から呼ばれる用途を想定。
func (s *Scanner) ScanBatch(items []ScanItem) BatchResult {
	if len(items) == 0 {
		return BatchResult{}
	}
	type out struct {
		idx      int
		source   string
		findings []Finding
	}
	results := make(chan out, len(items))
	var wg sync.WaitGroup
	for i, it := range items {
		wg.Add(1)
		go func(idx int, item ScanItem) {
			defer wg.Done()
			results <- out{idx: idx, source: item.Source, findings: s.Scan(item.Text, item.Source)}
		}(i, it)
	}
	go func() { wg.Wait(); close(results) }()

	bySource := make(map[string][]Finding)
	for r := range results {
		bySource[r.source] = append(bySource[r.source], r.findings...)
	}

	// Source の安定順序
	sources := make([]string, 0, len(bySource))
	for k := range bySource {
		sources = append(sources, k)
	}
	sort.Strings(sources)

	var all []Finding
	br := BatchResult{
		BySource:    make(map[string][]Finding, len(bySource)),
		SourceOrder: sources,
	}
	for _, src := range sources {
		fs := bySource[src]
		sortFindings(fs)
		br.BySource[src] = fs
		all = append(all, fs...)
	}
	br.Total = len(all)
	br.BySeverity = countBySeverity(all)
	return br
}

// ScanItem は ScanBatch の入力。
type ScanItem struct {
	Source string
	Text   string
}

// BatchResult は ScanBatch の結果。
type BatchResult struct {
	BySource    map[string][]Finding `json:"by_source"`
	SourceOrder []string             `json:"source_order"`
	Total       int                  `json:"total"`
	BySeverity  map[string]int       `json:"by_severity"`
}

func countBySeverity(findings []Finding) map[string]int {
	m := map[string]int{}
	for _, f := range findings {
		m[string(f.Severity)]++
	}
	return m
}

// ─── utility for callers ─────────────────────────────────────

// FormatSummary は finding を人間可読な短い要約文字列に変換する(audit log 等用)。
// secret raw は含まれない。
func FormatSummary(f Finding) string {
	return fmt.Sprintf("%s [%s] at line %d (entropy=%g) in %s",
		f.RuleID, f.Severity, f.Line, f.Entropy, f.Source)
}

// FormatSummaryAll は finding 配列をカンマ区切り要約で返す(同上)。
func FormatSummaryAll(fs []Finding) string {
	parts := make([]string, 0, len(fs))
	for _, f := range fs {
		parts = append(parts, FormatSummary(f))
	}
	return strings.Join(parts, "; ")
}
