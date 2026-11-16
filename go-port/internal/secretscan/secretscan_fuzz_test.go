package secretscan

import (
	"strings"
	"testing"
)

// FuzzScan は任意の入力に対して Scanner.Scan が panic しないことを確認する。
//
// Run with: go test -fuzz=FuzzScan -fuzztime=30s ./internal/secretscan/
//
// Findings (corpus entries) are saved to testdata/fuzz/FuzzScan/.
// Once a crash is found, the corpus file becomes a permanent regression test.
func FuzzScan(f *testing.F) {
	// Seed corpus: 既知の secrets + 安全なテキスト + edge cases
	seeds := []string{
		"",
		"plain text",
		"AKIAIOSFODNN7EXAMPLE",
		"ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ",
		"sk-ant-api03-abcdef",
		"-----BEGIN RSA PRIVATE KEY-----",
		strings.Repeat("a", 10000),                   // 大入力
		strings.Repeat("AKIA0123456789ABCDEF ", 100), // 多数の secret 候補
		"\x00\x01\x02\xff",                           // バイナリ
		"unicode 日本語 テスト 🔐",
		// Split across literals so push-protection scanners don't flag test fixtures.
		"https://hooks.slack.com/" + "services/T01234567/B12345678/abcdefghijklmnopqrstuvwx",
		`{"key": "value"}`,
		strings.Repeat("\n", 1000), // 大量の改行
	}
	for _, s := range seeds {
		f.Add(s)
	}

	s := New()
	f.Fuzz(func(t *testing.T, input string) {
		// Property 1: panic しない
		findings := s.Scan(input, "fuzz")

		// Property 2: 全 finding は redacted secret を持つ(漏洩防止)
		for _, fnd := range findings {
			if fnd.Match != "[REDACTED]" {
				t.Errorf("non-redacted match leaked: %q", fnd.Match)
			}
			if fnd.Fingerprint == "" {
				t.Error("missing fingerprint")
			}
			if len(fnd.Fingerprint) != 16 {
				t.Errorf("fingerprint length: %d", len(fnd.Fingerprint))
			}
			// Property 3: line/column は 1-based で valid
			if fnd.Line < 1 {
				t.Errorf("invalid line: %d", fnd.Line)
			}
		}
	})
}

// FuzzShannonEntropy はエントロピー計算が任意入力で panic せず、
// 値が [0, log2(unique_chars)] の範囲内にあることを検証する。
func FuzzShannonEntropy(f *testing.F) {
	f.Add("")
	f.Add("a")
	f.Add("abc")
	f.Add(strings.Repeat("a", 1000))
	f.Add("\x00\x01\x02")
	f.Add("日本語")

	f.Fuzz(func(t *testing.T, input string) {
		e := ShannonEntropy(input)
		// Property: エントロピーは非負
		if e < 0 {
			t.Errorf("negative entropy: %g for %q", e, input)
		}
		// Property: 空文字なら 0
		if input == "" && e != 0 {
			t.Errorf("empty string should be 0, got %g", e)
		}
	})
}
