package secretscan_test

import (
	"fmt"

	"github.com/shizukutanaka/aios/internal/secretscan"
)

// ExampleNew は単一テキストの secret scan の基本的な使い方を示す。
func ExampleNew() {
	s := secretscan.New()
	findings := s.Scan(`AWS_KEY=AKIAIOSFODNN7EXAMPLE not-a-real-secret`, "config.example")

	fmt.Println("findings:", len(findings))
	if len(findings) > 0 {
		fmt.Println("rule:", findings[0].RuleID)
		fmt.Println("severity:", findings[0].Severity)
		fmt.Println("match:", findings[0].Match) // always [REDACTED]
	}
	// Output:
	// findings: 1
	// rule: aws-access-key-id
	// severity: CRITICAL
	// match: [REDACTED]
}

// ExampleScanner_ScanBatch は複数 source の並列スキャンを示す。
func ExampleScanner_ScanBatch() {
	s := secretscan.New()
	items := []secretscan.ScanItem{
		{Source: "readme", Text: "Plain README."},
		{Source: "config", Text: "token=ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"},
	}
	result := s.ScanBatch(items)

	fmt.Println("total findings:", result.Total)
	fmt.Println("by_severity[CRITICAL]:", result.BySeverity["CRITICAL"])
	// Output:
	// total findings: 1
	// by_severity[CRITICAL]: 1
}

// ExampleShannonEntropy は文字列のエントロピー(ランダム性指標)を示す。
//
// 高エントロピー(> 4.0)の文字列は secret である可能性が高く、
// 低エントロピー(< 3.0)の文字列は通常の単語である可能性が高い。
func ExampleShannonEntropy() {
	low := secretscan.ShannonEntropy("aaaaaaaaaa")
	high := secretscan.ShannonEntropy("aB3$kL9@mN")
	fmt.Printf("low: %.2f\n", low)
	fmt.Printf("high: %.2f\n", high)
	// Output:
	// low: 0.00
	// high: 3.32
}
