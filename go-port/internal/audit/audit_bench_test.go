package audit

import (
	"testing"
)

// BenchmarkAppend は 1 record の append + fsync スループットを測る。
// SSD では 1-5 ms / op を期待。
func BenchmarkAppend(b *testing.B) {
	l, err := New(b.TempDir())
	if err != nil {
		b.Fatal(err)
	}
	defer l.Close()

	rec := Record{
		Kind:   "bench_event",
		Actor:  "bench",
		Target: "target",
		Fields: map[string]any{"i": 0},
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rec.Fields["i"] = i
		if err := l.Append(rec); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkVerify は 1000 record のファイルに対する verify の所要時間を測る。
// chain 全体を再計算するので O(N)、SHA-256 だけが律速。
func BenchmarkVerify(b *testing.B) {
	dir := b.TempDir()
	l, _ := New(dir)
	for i := 0; i < 1000; i++ {
		_ = l.Append(Record{Kind: "bench", Fields: map[string]any{"i": i}})
	}
	_ = l.Close()

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		results, err := Verify(dir)
		if err != nil {
			b.Fatal(err)
		}
		if !results[0].OK {
			b.Fatal("verify failed")
		}
	}
}

// BenchmarkComputeHash は SHA-256 + JSON marshal 単体のスループット。
func BenchmarkComputeHash(b *testing.B) {
	r := Record{
		Kind:   "bench",
		Actor:  "actor",
		Target: "target",
		Seq:    1,
		Fields: map[string]any{"k": "v", "n": 42},
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _, err := computeHashAndPayload(r)
		if err != nil {
			b.Fatal(err)
		}
	}
}
