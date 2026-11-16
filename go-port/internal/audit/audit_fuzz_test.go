package audit

import (
	"os"
	"path/filepath"
	"testing"
)

// FuzzVerify は任意の JSONL 入力に対して Verify が panic しないことを確認する。
//
// この fuzz は意図的に破損したファイルや invalid JSON を放り込んで、
// hash chain 検証ロジックが gracefully fail することを保証する。
func FuzzVerify(f *testing.F) {
	f.Add([]byte(""))
	f.Add([]byte("{}"))
	f.Add([]byte(`{"seq":1,"hash":"abc","prev_hash":"","event":"test","ts":"2026-01-01T00:00:00Z"}`))
	f.Add([]byte("garbage data\nnot-json\n"))
	f.Add([]byte(`{"seq":1}` + "\n" + `{"seq":2}`))
	f.Add([]byte(`{"seq":1,"hash":"","prev_hash":"","event":"a"}`))

	f.Fuzz(func(t *testing.T, content []byte) {
		dir := t.TempDir()
		path := filepath.Join(dir, "2026-01-01.jsonl")
		if err := os.WriteFile(path, content, 0600); err != nil {
			t.Fatal(err)
		}

		// Property: Verify はどんな入力でも panic しない
		results, err := Verify(dir)
		_ = err // エラーは許容(破損データなので)

		// Property: 結果は nil でなければ valid 構造体
		for _, r := range results {
			if r.File == "" {
				t.Error("empty File field")
			}
			if r.TotalRecords < 0 {
				t.Errorf("negative TotalRecords: %d", r.TotalRecords)
			}
		}
	})
}
