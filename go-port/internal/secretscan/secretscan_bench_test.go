package secretscan

import (
	"strings"
	"testing"
)

var smallText = `# README
Welcome to my project.
Some sample API call:
  curl -H "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ" \
       https://api.github.com/user
That's it.`

var largeText = strings.Repeat(smallText, 100)

func BenchmarkScan_Small(b *testing.B) {
	s := New()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = s.Scan(smallText, "readme")
	}
}

func BenchmarkScan_Large(b *testing.B) {
	s := New()
	b.SetBytes(int64(len(largeText)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = s.Scan(largeText, "readme")
	}
}

func BenchmarkScanBatch_10Sources(b *testing.B) {
	s := New()
	items := make([]ScanItem, 10)
	for i := range items {
		items[i] = ScanItem{Source: "source", Text: smallText}
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = s.ScanBatch(items)
	}
}

func BenchmarkShannonEntropy(b *testing.B) {
	s := "aB3$kL9@mN2&pQ5^xZ8vW7rS"
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = ShannonEntropy(s)
	}
}
