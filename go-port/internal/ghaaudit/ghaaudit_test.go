package ghaaudit

import (
	"strings"
	"testing"
)

// ─── classifyRef ─────────────────────────────────────────────

func TestClassifyRef(t *testing.T) {
	cases := []struct {
		ref  string
		want refType
	}{
		{"11bd71901bbe5b1630ceea73d27597364c9af683", refSHA}, // 40-char SHA
		{"v4", refMutableTag},
		{"v4.2.2", refMutableTag},
		{"1.0.0", refMutableTag},
		{"main", refBranch},
		{"master", refBranch},
		{"HEAD", refBranch},
		{"dev", refBranch},
		{"abc1234", refMutableTag}, // short hex = mutable
		{"./local/action", refLocal},
		{"../foo", refLocal},
		{"", refUnknown},
		{"weird-tag-name", refUnknown},
	}
	for _, c := range cases {
		got := classifyRef(c.ref)
		if got != c.want {
			t.Errorf("classifyRef(%q) = %d, want %d", c.ref, got, c.want)
		}
	}
}

func TestExtractRef(t *testing.T) {
	cases := map[string]string{
		"actions/checkout@v4": "v4",
		"actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683":           "11bd71901bbe5b1630ceea73d27597364c9af683",
		"slsa-framework/slsa-github-generator/.github/workflows/x.yml@v2.0.0": "v2.0.0",
		"no-at-sign": "",
		"trailing@":  "",
		"":           "",
	}
	for in, want := range cases {
		if got := extractRef(in); got != want {
			t.Errorf("extractRef(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestIsHexLike(t *testing.T) {
	cases := map[string]bool{
		"abc123":   true,
		"ABCDEF":   true,
		"deadbeef": true,
		"main":     false,
		"v1":       false,
		"":         true, // empty hex is technically hex-like
	}
	for in, want := range cases {
		if got := isHexLike(in); got != want {
			t.Errorf("isHexLike(%q) = %v, want %v", in, got, want)
		}
	}
}

// ─── AuditFile: R1/R2 unpinned-uses / mutable-ref ────────────

func TestAuditFile_UnpinnedTagDetected(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5.1.0
`
	a := New()
	findings := a.AuditFile(".github/workflows/ci.yml", yaml)
	count := 0
	for _, f := range findings {
		if f.RuleID == "unpinned-uses" {
			count++
		}
	}
	if count != 2 {
		t.Errorf("expected 2 unpinned-uses, got %d: %+v", count, findings)
	}
}

func TestAuditFile_BranchRefDetected(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: {}
jobs:
  bad:
    runs-on: ubuntu-latest
    steps:
      - uses: some/action@main
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	found := false
	for _, f := range findings {
		if f.RuleID == "mutable-ref" && f.Severity == SeverityCritical {
			found = true
		}
	}
	if !found {
		t.Errorf("branch ref not flagged: %+v", findings)
	}
}

func TestAuditFile_SHAPinnedIsClean(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: {}
jobs:
  good:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	for _, f := range findings {
		if f.RuleID == "unpinned-uses" || f.RuleID == "mutable-ref" {
			t.Errorf("SHA-pinned uses should be clean, got: %+v", f)
		}
	}
}

func TestAuditFile_LocalActionIsClean(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: {}
jobs:
  local:
    runs-on: ubuntu-latest
    steps:
      - uses: ./local/action
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	for _, f := range findings {
		if f.RuleID == "unpinned-uses" || f.RuleID == "mutable-ref" {
			t.Errorf("local action should be clean: %+v", f)
		}
	}
}

// ─── R3 no-permissions ───────────────────────────────────────

func TestAuditFile_NoTopLevelPermissions(t *testing.T) {
	yaml := `name: ci
on: [push]
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	hasNoPerms := false
	for _, f := range findings {
		if f.RuleID == "no-permissions" {
			hasNoPerms = true
		}
	}
	if !hasNoPerms {
		t.Errorf("expected no-permissions finding")
	}
}

func TestAuditFile_HasTopLevelPermissions(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions:
  contents: read
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	for _, f := range findings {
		if f.RuleID == "no-permissions" {
			t.Errorf("should not flag no-permissions: %+v", f)
		}
	}
}

// ─── R4 write-all-perms ──────────────────────────────────────

func TestAuditFile_WriteAllPermissionsDetected(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: write-all
jobs:
  x:
    runs-on: ubuntu-latest
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	found := false
	for _, f := range findings {
		if f.RuleID == "write-all-perms" {
			found = true
		}
	}
	if !found {
		t.Errorf("write-all permissions not flagged")
	}
}

// ─── R5 dangerous-trigger ────────────────────────────────────

func TestAuditFile_PullRequestTargetDetected(t *testing.T) {
	yaml := `name: ci
on:
  pull_request_target:
    types: [opened]
permissions: {}
jobs:
  x:
    runs-on: ubuntu-latest
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	found := false
	for _, f := range findings {
		if f.RuleID == "dangerous-trigger" && f.Severity == SeverityCritical {
			found = true
		}
	}
	if !found {
		t.Errorf("pull_request_target not flagged as dangerous")
	}
}

func TestAuditFile_WorkflowRunDetected(t *testing.T) {
	yaml := `name: ci
on:
  workflow_run:
    workflows: ["build"]
permissions: {}
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	found := false
	for _, f := range findings {
		if f.RuleID == "dangerous-trigger" {
			found = true
		}
	}
	if !found {
		t.Errorf("workflow_run not flagged")
	}
}

// ─── R6 template-injection ───────────────────────────────────

func TestAuditFile_TemplateInjectionInRun(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: {}
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - run: echo "PR title is ${{ github.event.pull_request.title }}"
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	found := false
	for _, f := range findings {
		if f.RuleID == "template-injection" && f.Severity == SeverityCritical {
			found = true
		}
	}
	if !found {
		t.Errorf("template injection in run: not flagged: %+v", findings)
	}
}

// ─── R7 toJson-secrets ───────────────────────────────────────

func TestAuditFile_ToJsonSecretsDetected(t *testing.T) {
	yaml := `name: ci
on: [push]
permissions: {}
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - env:
          ALL_SECRETS: ${{ toJson(secrets) }}
        run: echo done
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	found := false
	for _, f := range findings {
		if f.RuleID == "tojson-secrets" {
			found = true
		}
	}
	if !found {
		t.Errorf("toJson(secrets) not flagged")
	}
}

// ─── 統合: 既存 yagura workflow (= 0 findings 期待) ─────────

func TestAuditFile_PerfectWorkflow_HasNoFindings(t *testing.T) {
	yaml := `name: ci
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          persist-credentials: false
      - uses: actions/setup-go@41dfa10bad2bb2ae585af6ee5bb4d7d973ad74ed
        with:
          go-version: '1.22'
      - env:
          BR: ${{ github.ref_name }}
        run: |
          echo "Building $BR"
          go test ./...
`
	findings := New().AuditFile(".github/workflows/ci.yml", yaml)
	if len(findings) > 0 {
		for _, f := range findings {
			t.Errorf("perfect workflow flagged: rule=%s line=%d snippet=%q",
				f.RuleID, f.Line, f.Snippet)
		}
	}
}

// ─── AuditDir + Summarize ────────────────────────────────────

func TestAuditDir_MultipleFiles(t *testing.T) {
	files := map[string]string{
		"a.yml": `on: [push]
permissions: {}
jobs:
  x:
    steps:
      - uses: actions/checkout@v4
`,
		"b.yml": `on: [push]
jobs:
  x:
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
`,
	}
	results := New().AuditDir(".", files)
	if len(results) != 2 {
		t.Errorf("expected 2 files, got %d", len(results))
	}
}

func TestSummarize(t *testing.T) {
	results := map[string][]Finding{
		"a.yml": {
			{RuleID: "unpinned-uses", Severity: SeverityHigh},
			{RuleID: "no-permissions", Severity: SeverityHigh},
		},
		"b.yml": {
			{RuleID: "tojson-secrets", Severity: SeverityCritical},
		},
	}
	s := Summarize(results)
	if s.TotalFiles != 2 {
		t.Errorf("TotalFiles: %d", s.TotalFiles)
	}
	if s.TotalFindings != 3 {
		t.Errorf("TotalFindings: %d", s.TotalFindings)
	}
	if s.BySeverity["CRITICAL"] != 1 || s.BySeverity["HIGH"] != 2 {
		t.Errorf("BySeverity: %+v", s.BySeverity)
	}
	if s.ByRule["unpinned-uses"] != 1 {
		t.Errorf("ByRule[unpinned-uses]: %d", s.ByRule["unpinned-uses"])
	}
}

// ─── ソートと finding 構造 ──────────────────────────────────

func TestSortFindings_SeverityDesc(t *testing.T) {
	fs := []Finding{
		{Severity: SeverityLow, Line: 1},
		{Severity: SeverityCritical, Line: 5},
		{Severity: SeverityHigh, Line: 3},
	}
	sortFindings(fs)
	if fs[0].Severity != SeverityCritical {
		t.Errorf("first should be CRITICAL: %+v", fs)
	}
	if fs[2].Severity != SeverityLow {
		t.Errorf("last should be LOW: %+v", fs)
	}
}

// ─── インデント計算 ────────────────────────────────────────

func TestIndentOf(t *testing.T) {
	cases := map[string]int{
		"":        0,
		"foo":     0,
		"  foo":   2,
		"    foo": 4,
		"\tfoo":   4, // tab = 4 spaces
		"\t  foo": 6,
		"      ":  6,
	}
	for in, want := range cases {
		if got := indentOf(in); got != want {
			t.Errorf("indentOf(%q) = %d, want %d", in, got, want)
		}
	}
}

// ─── inRunBlock の境界条件 ─────────────────────────────────

func TestInRunBlock_VariousPositions(t *testing.T) {
	lines := []string{
		"jobs:",                             // 1
		"  build:",                          // 2
		"    steps:",                        // 3
		"      - run: |",                    // 4 (run: 開始)
		"          echo hello",              // 5 (in run)
		"          echo world",              // 6 (in run)
		"      - uses: actions/checkout@v4", // 7 (not in run)
	}
	if !inRunBlock(lines, 5) {
		t.Error("line 5 should be inside run block")
	}
	if inRunBlock(lines, 7) {
		t.Error("line 7 should NOT be inside run block")
	}
	if inRunBlock(lines, 0) {
		t.Error("line 0 invalid")
	}
}

// ─── multi line snippet sanity ─────────────────────────────

func TestAuditFile_MultipleFindings_SortedBySeverity(t *testing.T) {
	yaml := `on:
  workflow_run:
    workflows: ["x"]
jobs:
  x:
    steps:
      - uses: actions/checkout@v4
`
	findings := New().AuditFile(".github/workflows/x.yml", yaml)
	if len(findings) < 2 {
		t.Fatalf("expected multiple findings, got %d", len(findings))
	}
	// CRITICAL が先頭
	if findings[0].Severity != SeverityCritical {
		t.Errorf("first should be CRITICAL: %v", findings)
	}
}

// ─── reTriggerKey ────────────────────────────────────────────

func TestReTriggerKey_Match(t *testing.T) {
	re := reTriggerKey("pull_request_target")
	cases := map[string]bool{
		"  pull_request_target:":          true,
		"  pull_request_target: ":         true,
		"pull_request_target:":            true,
		"# pull_request_target:":          false,
		"other_event_pull_request_target": false,
	}
	for in, want := range cases {
		got := re.MatchString(in)
		if got != want {
			t.Errorf("reTriggerKey match(%q) = %v, want %v", in, got, want)
		}
	}
}

// ─── 大きな入力 sanity ─────────────────────────────────────

func TestAuditFile_LargeInput_DoesNotCrash(t *testing.T) {
	// SHA-pinned checkout with persist-credentials: false → fully clean
	step := "    - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n      with:\n        persist-credentials: false\n"
	big := "permissions: {}\non: [push]\n" + strings.Repeat(step, 1000)
	findings := New().AuditFile("big.yml", big)
	if len(findings) != 0 {
		t.Errorf("large clean input flagged %d findings", len(findings))
	}
}

// ─── R8 secrets-inherit / R9 self-hosted-runner (zizmor parity) ──

func TestAuditFile_SecretsInherit(t *testing.T) {
	wf := "on: workflow_call\npermissions: {}\njobs:\n  call:\n    uses: ./.github/workflows/reusable.yml\n    secrets: inherit\n"
	findings := New().AuditFile("caller.yml", wf)
	if !hasRule(findings, "secrets-inherit") {
		t.Errorf("expected secrets-inherit finding, got %+v", findings)
	}
}

func TestAuditFile_SecretsInherit_ExplicitIsClean(t *testing.T) {
	// passing secrets explicitly must NOT trip secrets-inherit
	wf := "on: workflow_call\npermissions: {}\njobs:\n  call:\n    uses: ./.github/workflows/reusable.yml\n    secrets:\n      TOKEN: ${{ secrets.TOKEN }}\n"
	if hasRule(New().AuditFile("caller.yml", wf), "secrets-inherit") {
		t.Error("explicit per-secret passing should not be flagged")
	}
}

func TestAuditFile_SelfHostedRunner(t *testing.T) {
	for _, line := range []string{
		"    runs-on: self-hosted",
		"    runs-on: [self-hosted, linux, x64]",
	} {
		wf := "on: [push]\npermissions: {}\njobs:\n  b:\n" + line + "\n    steps:\n      - run: echo hi\n"
		findings := New().AuditFile("w.yml", wf)
		if !hasRule(findings, "self-hosted-runner") {
			t.Errorf("expected self-hosted-runner finding for %q, got %+v", line, findings)
		}
	}
}

func TestAuditFile_GitHubHostedRunnerIsClean(t *testing.T) {
	wf := "on: [push]\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
	if hasRule(New().AuditFile("w.yml", wf), "self-hosted-runner") {
		t.Error("ubuntu-latest must not be flagged as self-hosted")
	}
}

func hasRule(findings []Finding, ruleID string) bool {
	for _, f := range findings {
		if f.RuleID == ruleID {
			return true
		}
	}
	return false
}

// ─── R10 artipacked (zizmor parity) ──────────────────────────

func TestAuditFile_Artipacked_FlaggedWithoutPersistCredentials(t *testing.T) {
	wf := `on: [push]
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: go test ./...
`
	findings := New().AuditFile("ci.yml", wf)
	if !hasRule(findings, "artipacked") {
		t.Errorf("expected artipacked finding, got %+v", findings)
	}
}

func TestAuditFile_Artipacked_CleanWithPersistCredentialsFalse(t *testing.T) {
	wf := `on: [push]
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          persist-credentials: false
      - run: go test ./...
`
	if hasRule(New().AuditFile("ci.yml", wf), "artipacked") {
		t.Error("persist-credentials: false must clear artipacked")
	}
}

func TestAuditFile_Artipacked_NonCheckoutActionIsClean(t *testing.T) {
	wf := `on: [push]
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-go@0a12ed9d6a96ab950c8f026ed9f722034518b59e
      - run: go test ./...
`
	if hasRule(New().AuditFile("ci.yml", wf), "artipacked") {
		t.Error("non-checkout action must not be flagged as artipacked")
	}
}

func TestAuditFile_Artipacked_MultipleStepsOnlyCheckout(t *testing.T) {
	// Two checkout steps: one with, one without persist-credentials: false
	wf := `on: [push]
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          persist-credentials: false
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.event.pull_request.head.sha }}
`
	findings := New().AuditFile("ci.yml", wf)
	count := 0
	for _, f := range findings {
		if f.RuleID == "artipacked" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected exactly 1 artipacked finding (second checkout), got %d: %+v", count, findings)
	}
}

// ─── R11 envfile-injection (zizmor parity) ───────────────────

func TestAuditFile_EnvfileInjection_GithubEventToGITHUB_ENV(t *testing.T) {
	wf := `on: [pull_request]
permissions: {}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "TITLE=${{ github.event.pull_request.title }}" >> $GITHUB_ENV
`
	findings := New().AuditFile("ci.yml", wf)
	if !hasRule(findings, "envfile-injection") {
		t.Errorf("expected envfile-injection, got %+v", findings)
	}
	// Ensure it's CRITICAL
	for _, f := range findings {
		if f.RuleID == "envfile-injection" && f.Severity != SeverityCritical {
			t.Errorf("envfile-injection must be CRITICAL, got %s", f.Severity)
		}
	}
}

func TestAuditFile_EnvfileInjection_HeadRefToGITHUB_OUTPUT(t *testing.T) {
	wf := `on: [push]
permissions: {}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.head_ref }}" >> $GITHUB_OUTPUT
`
	if !hasRule(New().AuditFile("ci.yml", wf), "envfile-injection") {
		t.Error("github.head_ref >> $GITHUB_OUTPUT must be flagged")
	}
}

func TestAuditFile_EnvfileInjection_SecretsAreClean(t *testing.T) {
	// secrets.* is repo-owner controlled — should NOT be flagged
	wf := `on: [push]
permissions: {}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "TOKEN=${{ secrets.MY_TOKEN }}" >> $GITHUB_ENV
`
	if hasRule(New().AuditFile("ci.yml", wf), "envfile-injection") {
		t.Error("secrets.* must not be flagged by envfile-injection")
	}
}

func TestAuditFile_EnvfileInjection_StepsOutputsAreClean(t *testing.T) {
	// steps.x.outputs.* is produced by trusted steps — not flagged
	wf := `on: [push]
permissions: {}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "VAL=${{ steps.build.outputs.digest }}" >> $GITHUB_OUTPUT
`
	if hasRule(New().AuditFile("ci.yml", wf), "envfile-injection") {
		t.Error("steps.*.outputs.* must not be flagged by envfile-injection")
	}
}

// ─── R12 bot-conditions (zizmor parity) ──────────────────────

func TestAuditFile_BotConditions_FlaggedActorEqualsBot(t *testing.T) {
	for _, line := range []string{
		`    if: github.actor == 'dependabot[bot]'`,
		`    if: github.actor == "renovate[bot]"`,
		`    if: 'dependabot[bot]' == github.actor`,
		`    if: github.actor != 'dependabot[bot]'`,
	} {
		wf := "on: pull_request_target\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n" + line + "\n    steps:\n      - run: echo hi\n"
		if !hasRule(New().AuditFile("w.yml", wf), "bot-conditions") {
			t.Errorf("expected bot-conditions finding for %q", line)
		}
	}
}

func TestAuditFile_BotConditions_PlainActorCheckIsClean(t *testing.T) {
	// Comparing github.actor to a human login (no [bot]) is not this rule.
	wf := "on: [push]\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    if: github.actor == 'octocat'\n    steps:\n      - run: echo hi\n"
	if hasRule(New().AuditFile("w.yml", wf), "bot-conditions") {
		t.Error("actor compared to a non-bot login must not trip bot-conditions")
	}
}

func TestAuditFile_BotConditions_PRUserLoginIsClean(t *testing.T) {
	// The recommended robust signal (pull_request.user.login) must not be flagged.
	wf := "on: pull_request_target\npermissions: {}\njobs:\n  b:\n    runs-on: ubuntu-latest\n    if: github.event.pull_request.user.login == 'dependabot[bot]'\n    steps:\n      - run: echo hi\n"
	if hasRule(New().AuditFile("w.yml", wf), "bot-conditions") {
		t.Error("pull_request.user.login is the robust signal; must not be flagged")
	}
}
