// Package ghaaudit implements static analysis of GitHub Actions workflow YAML
// files for security misconfigurations.
//
// 設計判断 (security spec S1.5):
//   - zizmor 風の audit を zero-dep で実装(ADR-0001 維持)
//   - YAML 完全パースは行わず、line-based prefix + regex で必要箇所を抽出
//     (GHA workflow YAML は十分構造化されているので line-based で実用充分)
//   - 検出ルール 12 種類は OWASP / zizmor / GitHub Actions security roadmap
//     から導出
//
// 検出ルール(2026 年現在の supply chain attack 例から逆算):
//   - R1 unpinned-uses    : tj-actions/changed-files 攻撃 (mar 2025)
//   - R2 mutable-ref      : Trivy-action 攻撃 (mar 2026, 75/76 tag 改竄)
//   - R3 no-permissions   : default GITHUB_TOKEN が write 権限を持つ
//   - R4 write-all-perms  : 過剰権限による blast radius 拡大
//   - R5 dangerous-trigger: pull_request_target / workflow_run の不正利用
//   - R6 template-inject  : nx 攻撃 (aug 2025) / spotbugs / Ultralytics
//   - R7 toJson-secrets   : 全 secret を env に渡す anti-pattern
//   - R8 secrets-inherit  : reusable workflow に全 secret を渡す over-provisioning
//   - R9 self-hosted-runner: 隔離困難な self-hosted runner
//   - R10 artipacked      : actions/checkout が Git 認証情報をディスクに残す (zizmor parity)
//   - R11 envfile-injection: ユーザー操作値を $GITHUB_ENV / $GITHUB_OUTPUT / $GITHUB_PATH へ流す
//   - R12 bot-conditions  : github.actor を bot login と比較する spoofable な gate (zizmor parity)
//
// 参考:
//   - https://github.com/zizmorcore/zizmor (Rust 実装、本実装の参考)
//   - https://docs.github.com/en/actions/reference/security/secure-use
package ghaaudit

import (
	"bufio"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Severity は finding の重要度。
type Severity string

const (
	// SeverityCritical はワークフロー設定の即時修正が必要な最高深刻度の finding。
	SeverityCritical Severity = "CRITICAL"
	// SeverityHigh は高優先度で対処すべき deep security 問題の finding。
	SeverityHigh Severity = "HIGH"
	// SeverityMedium は対処を推奨するベストプラクティス違反の finding。
	SeverityMedium Severity = "MEDIUM"
	// SeverityLow は改善を検討すべき軽微な設定上の問題の finding。
	SeverityLow Severity = "LOW"
)

// Finding は単一の audit 結果。
type Finding struct {
	RuleID      string   `json:"rule_id"`
	Description string   `json:"description"`
	Severity    Severity `json:"severity"`
	File        string   `json:"file"`
	Line        int      `json:"line"`
	Snippet     string   `json:"snippet"`    // 検出された当該 line(改行除去)
	Suggestion  string   `json:"suggestion"` // 修正提案
}

// Auditor は GHA workflow YAML を走査して finding を返す。
type Auditor struct{}

// New は標準 Auditor を返す。
func New() *Auditor { return &Auditor{} }

// AuditFile は 1 ファイル(YAML 文字列)を audit する。
//
// filePath は finding の File フィールドに記録される(例: ".github/workflows/ci.yml")。
// 結果は severity 降順 → line 昇順でソート。
func (a *Auditor) AuditFile(filePath, content string) []Finding {
	var findings []Finding

	// 行単位で走査
	scanner := bufio.NewScanner(strings.NewReader(content))
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024) // 1MB max line
	lineNum := 0

	// workflow 全体メタ
	hasTopLevelPermissions := false
	allLines := []string{}

	for scanner.Scan() {
		lineNum++
		raw := scanner.Text()
		allLines = append(allLines, raw)
		stripped := strings.TrimSpace(raw)
		indent := indentOf(raw)

		// R3 no-permissions: 探索フラグ(top-level "permissions:")
		if indent == 0 && strings.HasPrefix(stripped, "permissions:") {
			hasTopLevelPermissions = true
		}

		// R4 write-all-perms: permissions: write-all
		if reWriteAll.MatchString(stripped) {
			findings = append(findings, Finding{
				RuleID:      "write-all-perms",
				Description: "Workflow grants GITHUB_TOKEN write-all permissions (excessive blast radius)",
				Severity:    SeverityHigh,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Replace with least-privilege per-scope grants: 'contents: read' etc.",
			})
		}

		// R5 dangerous-trigger: pull_request_target / workflow_run
		for _, danger := range dangerousTriggers {
			if reTriggerKey(danger).MatchString(stripped) {
				findings = append(findings, Finding{
					RuleID:      "dangerous-trigger",
					Description: fmt.Sprintf("Workflow uses %s trigger (runs with secrets even for external PRs)", danger),
					Severity:    SeverityCritical,
					File:        filePath,
					Line:        lineNum,
					Snippet:     stripped,
					Suggestion:  "Validate event.pull_request.head.sha and use environment protection rules.",
				})
			}
		}

		// R6 template-inject: ${{ github.event.*.title|body|head_ref }} in run:
		// R7 toJson-secrets: env: SECRETS: ${{ toJson(secrets) }}
		// これらは下で contextual に判定する

		// R1/R2: uses: の解析
		if m := reUses.FindStringSubmatch(raw); m != nil {
			full := m[1] // "owner/repo@ref"
			ref := extractRef(full)
			switch classifyRef(ref) {
			case refMutableTag:
				findings = append(findings, Finding{
					RuleID:      "unpinned-uses",
					Description: "Action reference is a mutable tag (can be force-moved by maintainer)",
					Severity:    SeverityHigh,
					File:        filePath,
					Line:        lineNum,
					Snippet:     stripped,
					Suggestion:  "Pin to a full-length commit SHA (40 hex chars). Use 'git ls-remote' to resolve.",
				})
			case refBranch:
				findings = append(findings, Finding{
					RuleID:      "mutable-ref",
					Description: "Action reference is a branch (changes on every push to that branch)",
					Severity:    SeverityCritical,
					File:        filePath,
					Line:        lineNum,
					Snippet:     stripped,
					Suggestion:  "Pin to a full-length commit SHA. Branches are unsafe — any push compromises every run.",
				})
			}
		}

		// R6 template-inject: ${{ github.* }} in run: blocks
		if reTemplateInjection.MatchString(raw) && inRunBlock(allLines, lineNum) {
			findings = append(findings, Finding{
				RuleID:      "template-injection",
				Description: "User-controlled github.* context interpolated directly into shell (script injection risk)",
				Severity:    SeverityCritical,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Assign to env: VAR: ${{ github.X }} first, then reference $VAR in shell.",
			})
		}

		// R7 toJson-secrets: ${{ toJson(secrets) }}
		if reToJsonSecrets.MatchString(raw) {
			findings = append(findings, Finding{
				RuleID:      "tojson-secrets",
				Description: "Exposes all secrets via toJson(secrets) — leaks credentials beyond intended scope",
				Severity:    SeverityCritical,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Pass only the specific secrets needed: env: TOKEN: ${{ secrets.MY_TOKEN }}.",
			})
		}

		// R8 secrets-inherit: reusable workflow を 'secrets: inherit' で呼ぶ(zizmor parity)
		if reSecretsInherit.MatchString(raw) {
			findings = append(findings, Finding{
				RuleID:      "secrets-inherit",
				Description: "Reusable workflow called with 'secrets: inherit' — passes every caller secret, far beyond what the callee needs",
				Severity:    SeverityHigh,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Pass only the needed secrets explicitly under 'secrets:' (e.g. TOKEN: ${{ secrets.TOKEN }}).",
			})
		}

		// R9 self-hosted-runner: self-hosted runner の利用(zizmor parity)
		if reSelfHosted.MatchString(raw) {
			findings = append(findings, Finding{
				RuleID:      "self-hosted-runner",
				Description: "Job runs on a self-hosted runner (hard to isolate; risky on public repos or with fork-PR triggers)",
				Severity:    SeverityMedium,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Prefer GitHub-hosted runners; if self-hosted is required, keep the repo private and avoid pull_request triggers.",
			})
		}

		// R11 envfile-injection: github.event.* / github.head_ref → >> $GITHUB_ENV/OUTPUT/PATH
		// ユーザー操作可能値を環境ファイルに書くと任意の環境変数・出力を inject できる(zizmor parity)
		if reEnvFileSink.MatchString(raw) && reUserControlledExpr.MatchString(raw) {
			findings = append(findings, Finding{
				RuleID:      "envfile-injection",
				Description: "User-controlled github context value written to an environment file (arbitrary env/output injection)",
				Severity:    SeverityCritical,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Sanitize or reject user-controlled values; never echo untrusted data directly into $GITHUB_ENV/$GITHUB_OUTPUT/$GITHUB_PATH.",
			})
		}

		// R12 bot-conditions: github.actor を bot login と比較する spoofable な gate(zizmor parity)
		if reBotCondition.MatchString(raw) {
			findings = append(findings, Finding{
				RuleID:      "bot-conditions",
				Description: "Security gate compares github.actor to a bot login — github.actor is spoofable, so this is an unreliable bot check",
				Severity:    SeverityMedium,
				File:        filePath,
				Line:        lineNum,
				Snippet:     stripped,
				Suggestion:  "Gate on a robust signal instead, e.g. github.event.pull_request.user.login or github.event.sender.type == 'Bot'.",
			})
		}
	}

	// R3 no-permissions: workflow に top-level permissions: 無し
	if !hasTopLevelPermissions {
		findings = append(findings, Finding{
			RuleID:      "no-permissions",
			Description: "Workflow lacks top-level 'permissions:' declaration (defaults to overly broad GITHUB_TOKEN scope)",
			Severity:    SeverityHigh,
			File:        filePath,
			Line:        1,
			Snippet:     "(workflow root)",
			Suggestion:  "Add 'permissions: {}' at workflow top-level and grant specific scopes per-job.",
		})
	}

	// R10 artipacked: actions/checkout without persist-credentials: false (post-scan, needs look-ahead)
	findings = append(findings, checkArtipacked(filePath, allLines)...)

	sortFindings(findings)
	return findings
}

// checkArtipacked は allLines を走査して actions/checkout ステップのうち
// persist-credentials: false が設定されていないものを検出する。
//
// zizmor artipacked parity: Git 認証情報が以降のステップ全体でディスクに残るため、
// サプライチェーン攻撃や悪意ある後続アクションに悪用されうる。
func checkArtipacked(filePath string, lines []string) []Finding {
	var findings []Finding
	for i, line := range lines {
		m := reUses.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		if !reCheckoutAction.MatchString(m[1]) {
			continue
		}
		lineNum := i + 1
		checkoutIndent := indentOf(line)
		found := false
		// Look ahead up to 40 lines for persist-credentials: false within this step.
		for j := i + 1; j < len(lines) && j < i+40; j++ {
			l := lines[j]
			s := strings.TrimSpace(l)
			if s == "" || strings.HasPrefix(s, "#") {
				continue
			}
			lIndent := indentOf(l)
			// Reached a new step/key at the same or shallower indent — stop.
			if lIndent <= checkoutIndent {
				break
			}
			if rePersistCredentialsFalse.MatchString(s) {
				found = true
				break
			}
		}
		if !found {
			findings = append(findings, Finding{
				RuleID:      "artipacked",
				Description: "actions/checkout without 'persist-credentials: false' — Git credentials remain on disk for all subsequent steps",
				Severity:    SeverityMedium,
				File:        filePath,
				Line:        lineNum,
				Snippet:     strings.TrimSpace(line),
				Suggestion:  "Add 'persist-credentials: false' under 'with:' unless Git auth is intentionally needed in later steps.",
			})
		}
	}
	return findings
}

// AuditDir はディレクトリ内の全 *.yml / *.yaml ファイルを audit する。
// 戻り値は file path をキーとした finding map。
func (a *Auditor) AuditDir(dir string, files map[string]string) map[string][]Finding {
	out := make(map[string][]Finding, len(files))
	for path, content := range files {
		out[path] = a.AuditFile(path, content)
	}
	return out
}

// ─── regex / 補助 ───────────────────────────────────────────

var (
	// `uses: owner/repo@ref` (optional quotes)
	reUses = regexp.MustCompile(`^\s*-?\s*uses:\s*["']?([^"'\s]+)["']?`)

	// `permissions: write-all`
	reWriteAll = regexp.MustCompile(`^\s*permissions:\s*write-all\b`)

	// `${{ github.event.*. title | body | head_ref... }}` を run: 内で展開
	// 危険な context fields(攻撃面が大きい)
	reTemplateInjection = regexp.MustCompile(`\$\{\{\s*github\.(event\.[a-zA-Z_]+\.(?:title|body|head_ref|description|name)|head_ref)`)

	// `${{ toJson(secrets) }}` or `${{ secrets.*\.\* }}` 全 secret 渡し
	reToJsonSecrets = regexp.MustCompile(`\$\{\{\s*toJson\(\s*secrets\s*\)\s*\}\}`)

	// `secrets: inherit` — reusable workflow 呼出で caller の全 secret を渡す
	// (zizmor: secrets-inherit / overprovisioned-secrets。blast radius が過大)
	reSecretsInherit = regexp.MustCompile(`^\s*secrets:\s*inherit\s*$`)

	// `runs-on: ... self-hosted ...` — self-hosted runner(zizmor: self-hosted-runner。
	// 隔離が難しく public repo / fork-PR では危険)。inline 形式(scalar / flow list)を検出。
	reSelfHosted = regexp.MustCompile(`^\s*runs-on:\s*.*\bself-hosted\b`)

	// R10 artipacked: actions/checkout が persist-credentials: false なし
	// (zizmor parity) — Git 認証情報(GIT_CONFIG / .git/config)がステップ終了後も
	// ディスクに残り、後続の悪意あるアクションが利用可能になる。
	reCheckoutAction          = regexp.MustCompile(`^actions/checkout@`)
	rePersistCredentialsFalse = regexp.MustCompile(`^\s*persist-credentials:\s*false\s*$`)

	// R11 envfile-injection: ユーザー操作可能な github.event.* / github.head_ref を
	// >> $GITHUB_ENV / $GITHUB_OUTPUT / $GITHUB_PATH へ書き込む
	// → 任意の環境変数・出力値を inject 可能(CRITICAL)。
	// secrets.* は repo owner 管理なので対象外(false positive 削減)。
	reEnvFileSink        = regexp.MustCompile(`>>\s*\$GITHUB_(ENV|OUTPUT|PATH)\b`)
	reUserControlledExpr = regexp.MustCompile(`\$\{\{\s*github\.(event\.[a-zA-Z_]+|head_ref\b)`)

	// R12 bot-conditions: `github.actor` を bot login(`...[bot]`)と比較する
	// security gate(zizmor parity)。github.actor は spoof 可能なため、bot 判定の
	// 信頼できる根拠にならない。両方の順序(actor 左/右)+ ==/!= を検出。
	reBotCondition = regexp.MustCompile(`github\.actor\s*[=!]=\s*['"][^'"]*\[bot\]['"]|['"][^'"]*\[bot\]['"]\s*[=!]=\s*github\.actor`)
)

// reTriggerKey は trigger 名を YAML key として検出する正規表現を返す。
// 例: "pull_request_target" → /^\s*pull_request_target:\s*$/m
func reTriggerKey(name string) *regexp.Regexp {
	// `name:` (top-level or nested under `on:`) で識別。
	// false positive を避けるため "name:" のみ (= で始まらない)。
	return regexp.MustCompile(`^\s*` + regexp.QuoteMeta(name) + `(?:\s*:|:)`)
}

var dangerousTriggers = []string{
	"pull_request_target",
	"workflow_run",
}

// refType は uses: の ref 部分の種類。
type refType int

const (
	refUnknown    refType = iota
	refSHA                // 40-char hex (immutable)
	refMutableTag         // semver tag (v1, v1.2, v1.2.3) etc.
	refBranch             // main, master, dev, HEAD etc. (highly mutable)
	refLocal              // ./actions/foo (same repo, no SHA needed)
)

var reSHA40 = regexp.MustCompile(`^[0-9a-f]{40}$`)
var reSemverTag = regexp.MustCompile(`^v?\d+(\.\d+){0,2}([-+][\w.]+)?$`)
var commonBranches = map[string]bool{
	"main":    true,
	"master":  true,
	"dev":     true,
	"develop": true,
	"HEAD":    true,
	"trunk":   true,
}

// classifyRef は ref を 5 種類に分類する。
//
// SHA: 検証は git ls-remote ベースでなく形式のみ(40-char hex)。
// 偽 SHA を入れた場合は GitHub 側で reject されるため、形式チェックで十分。
func classifyRef(ref string) refType {
	if ref == "" {
		return refUnknown
	}
	if strings.HasPrefix(ref, "./") || strings.HasPrefix(ref, "../") {
		return refLocal
	}
	if reSHA40.MatchString(ref) {
		return refSHA
	}
	if commonBranches[ref] {
		return refBranch
	}
	if reSemverTag.MatchString(ref) {
		return refMutableTag
	}
	// 短い hex (例: "abc1234")も mutable 扱い(force-push 可能)
	if len(ref) >= 7 && len(ref) < 40 && isHexLike(ref) {
		return refMutableTag
	}
	return refUnknown
}

// extractRef は "owner/repo@ref" から "ref" を取り出す。
// reusable workflow 形式 "owner/repo/.github/workflows/x.yml@ref" にも対応。
func extractRef(s string) string {
	i := strings.LastIndex(s, "@")
	if i < 0 || i == len(s)-1 {
		return ""
	}
	return s[i+1:]
}

func isHexLike(s string) bool {
	for _, c := range s {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return true
}

// indentOf は行の先頭スペース数を返す(タブは 4 として扱う)。
func indentOf(line string) int {
	n := 0
	for _, c := range line {
		switch c {
		case ' ':
			n++
		case '\t':
			n += 4
		default:
			return n
		}
	}
	return n
}

// inRunBlock は lineNum (1-based) の行が run: ブロックの中にあるかどうか判定する。
//
// 判定ロジック:
//
//  1. 現在の行自体に "run:" / "- run:" がある場合(単一行 run)→ true
//     例: `- run: echo "${{ github.event.pull_request.title }}"`
//     この場合 run: と template injection が同じ行に共存する。
//
//  2. 直前 50 行以内に "run:" / "- run:" があり、そのインデントより
//     深い位置にいる場合(複数行 run: |)→ true
//     例:
//     - run: |
//     echo "${{ github.event.pull_request.title }}"
//
// 注: 完全に正確ではない(YAML 構造によっては false positive あり)が、
// template injection は run: 内が圧倒的に多いので実用充分。
func inRunBlock(lines []string, lineNum int) bool {
	if lineNum < 1 || lineNum > len(lines) {
		return false
	}
	raw := lines[lineNum-1]
	stripped := strings.TrimSpace(raw)

	// (1) 現在の行が単一行 run: の場合
	if strings.HasPrefix(stripped, "run:") || strings.HasPrefix(stripped, "- run:") {
		return true
	}

	// (2) 直前を遡って "run:" を探す(複数行 run: | block)
	currentIndent := indentOf(raw)
	for i := lineNum - 2; i >= 0 && i >= lineNum-50; i-- {
		s := strings.TrimSpace(lines[i])
		if strings.HasPrefix(s, "run:") || strings.HasPrefix(s, "- run:") {
			runIndent := indentOf(lines[i])
			// run: ブロックの中はそれより深いインデント
			return currentIndent > runIndent
		}
	}
	return false
}

// ─── ソート + 統計 ─────────────────────────────────────────

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

func sortFindings(fs []Finding) {
	sort.SliceStable(fs, func(i, j int) bool {
		ri, rj := severityRank(fs[i].Severity), severityRank(fs[j].Severity)
		if ri != rj {
			return ri > rj
		}
		if fs[i].File != fs[j].File {
			return fs[i].File < fs[j].File
		}
		return fs[i].Line < fs[j].Line
	})
}

// Summary は AuditDir 結果の人間向け要約。
type Summary struct {
	TotalFiles    int            `json:"total_files"`
	TotalFindings int            `json:"total_findings"`
	BySeverity    map[string]int `json:"by_severity"`
	ByRule        map[string]int `json:"by_rule"`
}

// Summarize は file→findings map から Summary を生成する。
func Summarize(results map[string][]Finding) Summary {
	s := Summary{
		TotalFiles: len(results),
		BySeverity: map[string]int{},
		ByRule:     map[string]int{},
	}
	for _, findings := range results {
		for _, f := range findings {
			s.TotalFindings++
			s.BySeverity[string(f.Severity)]++
			s.ByRule[f.RuleID]++
		}
	}
	return s
}
