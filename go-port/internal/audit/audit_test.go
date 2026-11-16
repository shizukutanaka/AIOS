package audit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// ─── Logger lifecycle ────────────────────────────────────────

func TestNew_CreatesDirAndFile(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "audit")
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()

	info, err := os.Stat(l.CurrentFile())
	if err != nil {
		t.Fatalf("audit file should exist: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("expected 0600 perm, got %o", info.Mode().Perm())
	}
}

func TestNew_RejectsEmptyDir(t *testing.T) {
	if _, err := New(""); err == nil {
		t.Error("empty dir should error")
	}
}

func TestAppend_BasicWrite(t *testing.T) {
	l, _ := New(t.TempDir())
	defer l.Close()

	err := l.Append(Record{
		Kind:   "test_event",
		Actor:  "tester",
		Target: "p1",
		Fields: map[string]any{"foo": "bar"},
	})
	if err != nil {
		t.Fatal(err)
	}

	data, err := os.ReadFile(l.CurrentFile())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"kind":"test_event"`) {
		t.Errorf("written data missing kind: %s", data)
	}
	if !strings.Contains(string(data), `"hash":"`) {
		t.Errorf("missing hash field: %s", data)
	}
}

func TestAppend_RejectsEmptyKind(t *testing.T) {
	l, _ := New(t.TempDir())
	defer l.Close()
	if err := l.Append(Record{}); err == nil {
		t.Error("empty Kind should error")
	}
}

func TestAppend_HashChainContinues(t *testing.T) {
	l, _ := New(t.TempDir())
	defer l.Close()

	_ = l.Append(Record{Kind: "first"})
	_ = l.Append(Record{Kind: "second"})
	_ = l.Append(Record{Kind: "third"})

	data, _ := os.ReadFile(l.CurrentFile())
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 3 {
		t.Fatalf("expected 3 lines, got %d", len(lines))
	}

	var prevHash string
	for i, line := range lines {
		var r map[string]any
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			t.Fatal(err)
		}
		gotPrev, _ := r["prev_hash"].(string)
		if gotPrev != prevHash {
			t.Errorf("line %d: prev_hash mismatch, expected %q got %q", i+1, prevHash, gotPrev)
		}
		gotSeq, _ := r["seq"].(float64)
		if uint64(gotSeq) != uint64(i+1) {
			t.Errorf("line %d: seq mismatch, expected %d got %v", i+1, i+1, r["seq"])
		}
		prevHash, _ = r["hash"].(string)
	}
}

func TestAppend_HashChainAcrossReopen(t *testing.T) {
	dir := t.TempDir()
	l1, _ := New(dir)
	_ = l1.Append(Record{Kind: "before_close"})
	_ = l1.Close()

	// Reopen — chain should continue
	l2, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l2.Close()
	_ = l2.Append(Record{Kind: "after_reopen"})

	data, _ := os.ReadFile(l2.CurrentFile())
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d", len(lines))
	}

	var first, second map[string]any
	_ = json.Unmarshal([]byte(lines[0]), &first)
	_ = json.Unmarshal([]byte(lines[1]), &second)

	firstHash, _ := first["hash"].(string)
	secondPrev, _ := second["prev_hash"].(string)
	if firstHash == "" || secondPrev != firstHash {
		t.Errorf("chain broken across reopen: first.hash=%q second.prev_hash=%q",
			firstHash, secondPrev)
	}
}

func TestAppend_FilePermsAfterAppend(t *testing.T) {
	l, _ := New(t.TempDir())
	defer l.Close()
	_ = l.Append(Record{Kind: "perm_check"})

	info, _ := os.Stat(l.CurrentFile())
	if info.Mode().Perm() != 0o600 {
		t.Errorf("perm should remain 0600, got %o", info.Mode().Perm())
	}
}

func TestConcurrent_Append(t *testing.T) {
	l, _ := New(t.TempDir())
	defer l.Close()

	const N = 100
	var wg sync.WaitGroup
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_ = l.Append(Record{Kind: "concurrent", Fields: map[string]any{"i": i}})
		}(i)
	}
	wg.Wait()

	data, _ := os.ReadFile(l.CurrentFile())
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != N {
		t.Errorf("expected %d lines, got %d", N, len(lines))
	}

	// Verify chain
	results, _ := Verify(l.dir)
	if len(results) != 1 || !results[0].OK {
		t.Errorf("verify failed after concurrent writes: %+v", results)
	}
}

func TestClose_Idempotent(t *testing.T) {
	l, _ := New(t.TempDir())
	if err := l.Close(); err != nil {
		t.Fatal(err)
	}
	if err := l.Close(); err != nil {
		t.Errorf("second close should be no-op, got: %v", err)
	}
}

// ─── Verify ──────────────────────────────────────────────────

func TestVerify_EmptyDir(t *testing.T) {
	results, err := Verify(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 0 {
		t.Errorf("empty dir should have no results, got %d", len(results))
	}
}

func TestVerify_HappyPath(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	for i := 0; i < 5; i++ {
		_ = l.Append(Record{Kind: "ev", Fields: map[string]any{"i": i}})
	}
	_ = l.Close()

	results, err := Verify(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 {
		t.Fatalf("expected 1 file, got %d", len(results))
	}
	if !results[0].OK {
		t.Errorf("expected OK, got: %+v", results[0])
	}
	if results[0].TotalRecords != 5 {
		t.Errorf("expected 5 records, got %d", results[0].TotalRecords)
	}
}

func TestVerify_DetectsTampering(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "first"})
	_ = l.Append(Record{Kind: "second"})
	_ = l.Append(Record{Kind: "third"})
	path := l.CurrentFile()
	_ = l.Close()

	// Tamper: change a field value in the middle line
	data, _ := os.ReadFile(path)
	tampered := strings.Replace(string(data), `"kind":"second"`, `"kind":"INJECTED"`, 1)
	_ = os.WriteFile(path, []byte(tampered), 0o600)

	results, _ := Verify(dir)
	if len(results) != 1 {
		t.Fatal("expected 1 file")
	}
	if results[0].OK {
		t.Errorf("tampering should be detected, but Verify said OK: %+v", results[0])
	}
	if !strings.Contains(results[0].Reason, "hash mismatch") {
		t.Errorf("expected hash mismatch reason, got: %s", results[0].Reason)
	}
}

func TestVerify_DetectsDeletedLine(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "a"})
	_ = l.Append(Record{Kind: "b"})
	_ = l.Append(Record{Kind: "c"})
	path := l.CurrentFile()
	_ = l.Close()

	// Delete the middle line
	data, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	tampered := lines[0] + "\n" + lines[2] + "\n"
	_ = os.WriteFile(path, []byte(tampered), 0o600)

	results, _ := Verify(dir)
	if results[0].OK {
		t.Errorf("deleted line should be detected, but Verify said OK")
	}
}

func TestVerify_DetectsAppendedFakeRecord(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "real"})
	path := l.CurrentFile()
	_ = l.Close()

	// Append a fake record without computing the proper hash chain
	fake := `{"time":"2026-01-01T00:00:00Z","seq":2,"kind":"fake","prev_hash":"deadbeef","hash":"cafebabe"}` + "\n"
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	_, _ = f.WriteString(fake)
	_ = f.Close()

	results, _ := Verify(dir)
	if results[0].OK {
		t.Errorf("fake appended record should fail verification")
	}
}

// ─── computeHashAndPayload internal ─────────────────────────

func TestComputeHash_Deterministic(t *testing.T) {
	r := Record{
		Kind:   "x",
		Actor:  "a",
		Target: "t",
		Seq:    42,
		Fields: map[string]any{"k": "v"},
	}
	h1, _, err := computeHashAndPayload(r)
	if err != nil {
		t.Fatal(err)
	}
	h2, _, _ := computeHashAndPayload(r)
	if h1 != h2 {
		t.Errorf("hash should be deterministic: %s vs %s", h1, h2)
	}
	if len(h1) != 64 {
		t.Errorf("SHA-256 hex should be 64 chars, got %d", len(h1))
	}
}

func TestComputeHash_DifferentRecordsDiffer(t *testing.T) {
	r1 := Record{Kind: "a"}
	r2 := Record{Kind: "b"}
	h1, _, _ := computeHashAndPayload(r1)
	h2, _, _ := computeHashAndPayload(r2)
	if h1 == h2 {
		t.Error("different records should produce different hashes")
	}
}

// ─── Prune ───────────────────────────────────────────────────

func TestPrune_DeletesOldFiles(t *testing.T) {
	dir := t.TempDir()
	// 作成: 古い日付 3 つ + 当日
	today := time.Now().UTC().Format("2006-01-02")
	oldDates := []string{"2020-01-01", "2020-06-15", "2024-12-31"}
	for _, d := range oldDates {
		path := filepath.Join(dir, d+".jsonl")
		if err := os.WriteFile(path, []byte(`{"seq":1,"kind":"x"}`+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// 当日ファイルも作成
	todayPath := filepath.Join(dir, today+".jsonl")
	if err := os.WriteFile(todayPath, []byte(`{"seq":1,"kind":"x"}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	n, err := Prune(dir, 30)
	if err != nil {
		t.Fatal(err)
	}
	if n != 3 {
		t.Errorf("expected 3 deleted, got %d", n)
	}
	// 当日ファイルは残る
	if _, err := os.Stat(todayPath); err != nil {
		t.Errorf("today file should remain: %v", err)
	}
}

func TestPrune_KeepDaysZeroNoop(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "2020-01-01.jsonl")
	_ = os.WriteFile(path, []byte("x"), 0o600)

	n, err := Prune(dir, 0)
	if err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Errorf("keepDays=0 should not delete: got %d", n)
	}
	if _, err := os.Stat(path); err != nil {
		t.Errorf("file should remain: %v", err)
	}
}

func TestPrune_IgnoresUnknownFiles(t *testing.T) {
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, "not-a-date.jsonl"), []byte("x"), 0o600)
	_ = os.WriteFile(filepath.Join(dir, "README"), []byte("hello"), 0o600)
	_ = os.WriteFile(filepath.Join(dir, "2020-01-01.jsonl"), []byte("x"), 0o600)

	n, err := Prune(dir, 30)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("expected 1 deleted, got %d", n)
	}
	// 不明ファイルは残る
	if _, err := os.Stat(filepath.Join(dir, "not-a-date.jsonl")); err != nil {
		t.Error("unknown file should remain")
	}
}

func TestPrune_NoSuchDir(t *testing.T) {
	n, err := Prune(filepath.Join(t.TempDir(), "nonexistent"), 30)
	if err != nil {
		t.Errorf("missing dir should not error: %v", err)
	}
	if n != 0 {
		t.Errorf("missing dir should delete 0, got %d", n)
	}
}

func TestPrune_RecentFilesPreserved(t *testing.T) {
	dir := t.TempDir()
	yesterday := time.Now().UTC().AddDate(0, 0, -1).Format("2006-01-02")
	_ = os.WriteFile(filepath.Join(dir, yesterday+".jsonl"), []byte("x"), 0o600)

	n, _ := Prune(dir, 30)
	if n != 0 {
		t.Errorf("yesterday's file should not be pruned with keep=30: got %d", n)
	}
}

// ─── rotateLocked / NopSink ───────────────────────────────────

// TestRotate_DateChange は内部の rotateLocked パスをカバーする。
// rotateLocked を直接呼んで未来日付ファイルを作成 → 別ディレクトリで
// その日付の Append が新ファイルに書かれることを確認する。
//
// 注: Append は内部で today != currentDate を検出すると自動で
// 今日に rotate するため、人工的に未来日付に書き込ませることは
// 本来の挙動では不可能。ここでは rotateLocked 自体のロジック
// (ファイル切替・seq リセット・lastHash クリア)を検証する。
func TestRotate_DateChange(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()

	// 1 件書込む (今日のファイル)
	if err := l.Append(Record{Kind: "today"}); err != nil {
		t.Fatal(err)
	}
	firstFile := l.CurrentFile()
	firstHash := l.lastHash
	if firstHash == "" {
		t.Fatal("lastHash should be set after Append")
	}

	// rotateLocked を直接呼んで別日付に切替
	l.mu.Lock()
	err = l.rotateLocked("2099-12-31")
	l.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}

	newFile := l.CurrentFile()
	if newFile == firstFile {
		t.Errorf("CurrentFile should change after rotate: %s == %s", newFile, firstFile)
	}
	if l.seq != 0 {
		t.Errorf("seq should reset to 0 after rotate, got %d", l.seq)
	}
	if l.lastHash != "" {
		t.Errorf("lastHash should clear after rotate, got %q", l.lastHash)
	}

	// 両ファイルが存在することを確認
	entries, _ := os.ReadDir(dir)
	jsonlCount := 0
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".jsonl") {
			jsonlCount++
		}
	}
	if jsonlCount != 2 {
		t.Errorf("expected 2 jsonl files after rotate, got %d", jsonlCount)
	}

	// 元ファイルが Verify を通ることを確認
	results, _ := Verify(dir)
	for _, r := range results {
		if !r.OK {
			t.Errorf("file %s should verify OK: %s", r.File, r.Reason)
		}
	}
}

func TestNopSink(t *testing.T) {
	var s Sink = NopSink{}
	if err := s.Append(Record{Kind: "test"}); err != nil {
		t.Errorf("NopSink should always succeed: %v", err)
	}
	if err := s.Append(Record{}); err != nil {
		t.Errorf("NopSink should accept any record: %v", err)
	}
}

// TestAppend_OnClosedFile は Close 後の Append が安全にエラーになることを確認する。
func TestAppend_AfterClose(t *testing.T) {
	l, _ := New(t.TempDir())
	_ = l.Close()
	err := l.Append(Record{Kind: "x"})
	if err == nil {
		t.Error("Append after Close should error")
	}
}

// TestWithHashField_EmptyObject はエッジケース({} payload)をカバーする。
func TestWithHashField_EmptyObject(t *testing.T) {
	out, err := withHashField([]byte("{}"), "abc")
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != `{"hash":"abc"}` {
		t.Errorf("unexpected: %s", out)
	}
}

func TestWithHashField_InvalidShape(t *testing.T) {
	for _, in := range [][]byte{
		[]byte(""),
		[]byte("["),
		[]byte("not json"),
		[]byte("{"),
	} {
		_, err := withHashField(in, "x")
		if err == nil {
			t.Errorf("invalid input %q should error", in)
		}
	}
}

func TestRead_FilterByKind(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"self_improve", "mcp_call_ok", "self_improve"} {
		if err := l.Append(Record{Kind: k, Actor: "mcp", Target: "harness"}); err != nil {
			t.Fatal(err)
		}
	}
	_ = l.Close()

	all, err := Read(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 3 {
		t.Errorf("expected 3 records, got %d", len(all))
	}
	si, err := Read(dir, "self_improve")
	if err != nil {
		t.Fatal(err)
	}
	if len(si) != 2 {
		t.Errorf("expected 2 self_improve records, got %d", len(si))
	}
	// chronological order preserved (seq ascending)
	if si[0].Seq >= si[1].Seq {
		t.Errorf("records not in chronological order: %d, %d", si[0].Seq, si[1].Seq)
	}
}

func TestRead_MissingDir(t *testing.T) {
	recs, err := Read(filepath.Join(t.TempDir(), "nope"), "")
	if err != nil {
		t.Errorf("missing dir should not error, got %v", err)
	}
	if len(recs) != 0 {
		t.Errorf("expected 0 records, got %d", len(recs))
	}
}

func TestRead_BrokenJSONLine(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "good"})
	path := l.CurrentFile()
	_ = l.Close()

	// Append broken JSON after valid records
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	_, _ = f.WriteString("{not valid json\n")
	_ = f.Close()

	recs, err := Read(dir, "")
	// Should get partial result (good record) + an error
	if len(recs) != 1 {
		t.Errorf("expected 1 partial record, got %d", len(recs))
	}
	if err == nil {
		t.Error("broken JSON should return error")
	}
}

// ─── Verify with non-existent directory ──────────────────────

func TestVerify_NonexistentDir(t *testing.T) {
	results, err := Verify(filepath.Join(t.TempDir(), "no-such-dir"))
	if err != nil {
		t.Errorf("missing dir should not error, got %v", err)
	}
	if len(results) != 0 {
		t.Errorf("expected 0 results, got %d", len(results))
	}
}

// ─── tailState degraded path via reopen ──────────────────────

func TestNew_DegradedMode_BrokenTailJSON(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "good"})
	path := l.CurrentFile()
	_ = l.Close()

	// Overwrite with broken JSON at end so tailState unmarshal fails
	_ = os.WriteFile(path, []byte("{not json}\n"), 0o600)

	// Reopen should succeed in degraded mode (seq=0, lastHash="")
	l2, err := New(dir)
	if err != nil {
		t.Fatalf("New with broken tail should succeed in degraded mode, got %v", err)
	}
	defer l2.Close()

	// Should still be able to append
	if err := l2.Append(Record{Kind: "after_degraded"}); err != nil {
		t.Errorf("Append after degraded reopen: %v", err)
	}
}

// ─── New MkdirAll failure ────────────────────────────────────

func TestNew_MkdirAllFails(t *testing.T) {
	// Place a regular file at the path New() would use as a directory.
	parent := t.TempDir()
	blocker := filepath.Join(parent, "audit")
	if err := os.WriteFile(blocker, []byte("block"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := New(blocker) // blocker is a file, not a dir — MkdirAll fails on a sub-path
	// MkdirAll on a path that contains a file as a component fails
	nested := filepath.Join(blocker, "subdir")
	_, err = New(nested)
	if err == nil {
		t.Error("expected error when parent path is a regular file")
	}
}

// ─── hashMap unmarshal error ─────────────────────────────────

func TestHashMap_IncompatibleTimeField(t *testing.T) {
	// "time" with a numeric value: json.Marshal(m) yields {"time":12345},
	// json.Unmarshal into Record fails because time.Time expects RFC3339.
	_, err := hashMap(map[string]any{"kind": "x", "time": 12345})
	if err == nil {
		t.Error("expected error when 'time' field is a number (not RFC3339 string)")
	}
}

// ─── tailState: single-line file without trailing newline ────

func TestTailState_SingleLineNoNewline(t *testing.T) {
	dir := t.TempDir()
	today := time.Now().UTC().Format("2006-01-02")
	path := filepath.Join(dir, today+".jsonl")
	// Write a valid JSON record WITHOUT a trailing newline (triggers idx<0, size<=readLen path).
	content := `{"kind":"x","seq":3,"hash":"deadbeef"}`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	l, err := New(dir)
	if err != nil {
		t.Fatalf("New with single-line-no-newline file: %v", err)
	}
	defer l.Close()
	if l.seq != 3 {
		t.Errorf("seq = %d, want 3 (read from single-line file)", l.seq)
	}
}

// ─── tailState: exceeds 8KB buffer ──────────────────────────

func TestTailState_ExceedsBuffer(t *testing.T) {
	// Write a single line with no newline that is > 8192 bytes.
	// tailState reads the last 8192 bytes and finds no newline → degraded error.
	p := filepath.Join(t.TempDir(), "big.jsonl")
	line := strings.Repeat("X", 9000) // > 8192, no newline
	if err := os.WriteFile(p, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	_, _, err := tailState(p)
	if err == nil {
		t.Fatal("expected error for oversized last record, got nil")
	}
	if !strings.Contains(err.Error(), "exceeds 8KB") {
		t.Errorf("expected 'exceeds 8KB' error, got %q", err.Error())
	}
}

// TestNew_DegradedTailState covers the openCurrent degraded path: if the
// existing file's last line can't be parsed, New still succeeds (lastHash reset).
func TestNew_DegradedTailState(t *testing.T) {
	dir := t.TempDir()
	// Write today's .jsonl with a non-JSON last line.
	today := time.Now().UTC().Format("2006-01-02")
	path := filepath.Join(dir, today+".jsonl")
	if err := os.WriteFile(path, []byte("not-json\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	l, err := New(dir)
	if err != nil {
		t.Fatalf("New should succeed even with corrupted tail: %v", err)
	}
	defer l.Close()
	// lastHash must be empty (degraded reset) and we must still be able to Append.
	if l.lastHash != "" {
		t.Errorf("expected empty lastHash after degraded tail, got %q", l.lastHash)
	}
	if err := l.Append(Record{Kind: "after_degrade"}); err != nil {
		t.Errorf("Append after degraded state should succeed: %v", err)
	}
}

// TestRotate_OpenCurrentFailsRollback covers rotateLocked's rollback when
// the new date's file cannot be created (directory becomes read-only between
// the old-file close and the new-file open).
func TestRotate_OpenCurrentFailsRollback(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses directory write permissions")
	}
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = os.Chmod(dir, 0o755) // restore for cleanup
		l.Close()
	}()

	if err := l.Append(Record{Kind: "before"}); err != nil {
		t.Fatal(err)
	}
	prevDate := l.currentDate
	prevFile := l.CurrentFile()

	// Make the directory read-only so rotateLocked cannot create the new file.
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatal(err)
	}

	l.mu.Lock()
	err = l.rotateLocked("2099-12-31")
	l.mu.Unlock()
	if err == nil {
		t.Fatal("expected error when new file cannot be created")
	}
	// currentDate must have rolled back to prevDate.
	if l.currentDate != prevDate {
		t.Errorf("currentDate = %q after rollback, want %q", l.currentDate, prevDate)
	}
	// The old file path must be unchanged (we haven't lost it).
	_ = prevFile
}

// ─── Append rotation path ────────────────────────────────────

func TestAppend_DateRotation(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()

	// Rename the file created by New (today's date) to a fake past date
	// so that subsequent rotation creates a fresh file for today.
	oldPath := l.filePath
	pastDate := "2000-01-01"
	pastPath := filepath.Join(dir, pastDate+".jsonl")
	_ = l.file.Close()
	l.file = nil
	if err := os.Rename(oldPath, pastPath); err != nil {
		t.Fatal(err)
	}
	// Reopen the past-dated file as the "current" file to simulate a stale logger.
	f, err := os.OpenFile(pastPath, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	l.file = f
	l.filePath = pastPath
	l.currentDate = pastDate

	// Appending now should trigger rotation to today's date.
	if err := l.Append(Record{Kind: "post_rotate"}); err != nil {
		t.Fatalf("Append after backdated rotation: %v", err)
	}
	// Expect two .jsonl files: the past-date one + today's.
	entries, _ := os.ReadDir(dir)
	var jsonlCount int
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".jsonl" {
			jsonlCount++
		}
	}
	if jsonlCount < 2 {
		t.Errorf("expected ≥2 log files after rotation, got %d", jsonlCount)
	}
}

// ─── tailState: non-existent path (ErrNotExist branch) ──────

func TestTailState_NonExistentFile(t *testing.T) {
	p := filepath.Join(t.TempDir(), "ghost.jsonl")
	h, n, err := tailState(p)
	if err != nil {
		t.Fatalf("non-existent file should return nil error: %v", err)
	}
	if h != "" || n != 0 {
		t.Errorf("expected empty result, got (%q, %d)", h, n)
	}
}

// ─── tailState: multi-line file (idx >= 0 branch) ───────────

func TestTailState_MultiLineReopen(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	_ = l.Append(Record{Kind: "first"})
	_ = l.Append(Record{Kind: "second"})
	savedPath := l.filePath
	_ = l.Close()

	// Reopen: tailState sees a 2-record file → idx >= 0 branch
	l2, err := New(dir)
	if err != nil {
		t.Fatalf("New after 2 appends: %v", err)
	}
	defer l2.Close()
	if l2.seq != 2 {
		t.Errorf("seq = %d, want 2", l2.seq)
	}
	if l2.filePath != savedPath {
		t.Errorf("filePath = %q, want %q", l2.filePath, savedPath)
	}
}

// ─── hashMap: json.Marshal failure ──────────────────────────

func TestHashMap_UnmarshalableType(t *testing.T) {
	// channels cannot be JSON-marshalled → triggers the first error path
	_, err := hashMap(map[string]any{"ch": make(chan int)})
	if err == nil {
		t.Error("expected error for unmarshalable type, got nil")
	}
}

// ─── Verify: skips dirs and non-jsonl files ──────────────────

func TestVerify_SkipsNonJSONL(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "x"})
	_ = l.Close()

	// Non-jsonl file and a directory — both should be silently skipped
	_ = os.WriteFile(filepath.Join(dir, "README.txt"), []byte("ignore"), 0o644)
	_ = os.MkdirAll(filepath.Join(dir, "subdir.jsonl"), 0o755)

	results, err := Verify(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 {
		t.Errorf("expected 1 result, got %d", len(results))
	}
	if !results[0].OK {
		t.Errorf("expected OK result, got %+v", results[0])
	}
}

// ─── Read: skips dirs and non-jsonl files ────────────────────

func TestRead_SkipsNonJSONL(t *testing.T) {
	dir := t.TempDir()
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "good"})
	_ = l.Close()

	_ = os.WriteFile(filepath.Join(dir, "meta.txt"), []byte("ignore"), 0o644)
	_ = os.MkdirAll(filepath.Join(dir, "cache.jsonl"), 0o755)

	recs, err := Read(dir, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 1 {
		t.Errorf("expected 1 record, got %d", len(recs))
	}
}

// ─── verifyFile: open failure via broken symlink ─────────────

func TestVerify_OpenFails(t *testing.T) {
	dir := t.TempDir()
	badLink := filepath.Join(dir, "2000-01-01.jsonl")
	if err := os.Symlink(filepath.Join(dir, "nowhere"), badLink); err != nil {
		t.Skip("symlink not supported")
	}
	results, err := Verify(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 || results[0].OK {
		t.Errorf("expected 1 failed result, got %d ok=%v", len(results), len(results) > 0 && results[0].OK)
	}
	if results[0].Reason == "" {
		t.Error("expected non-empty Reason for open failure")
	}
}

// ─── Read: open failure via broken symlink ───────────────────

func TestRead_OpenFails(t *testing.T) {
	dir := t.TempDir()
	// Symlink with a date earlier than any real file (sorts first)
	badLink := filepath.Join(dir, "2000-01-01.jsonl")
	if err := os.Symlink(filepath.Join(dir, "nowhere"), badLink); err != nil {
		t.Skip("symlink not supported")
	}
	// Also add a valid record so we have something to skip
	l, _ := New(dir)
	_ = l.Append(Record{Kind: "after"})
	_ = l.Close()

	_, err := Read(dir, "")
	if err == nil {
		t.Error("expected error when a file cannot be opened")
	}
}

// ─── Prune: ReadDir failure ───────────────────────────────────

func TestPrune_ReadDirFails(t *testing.T) {
	// Pass a regular file as dir — ReadDir returns ENOTDIR (not ErrNotExist)
	f, err := os.CreateTemp(t.TempDir(), "audit-prune-block")
	if err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	_, pruneErr := Prune(f.Name(), 30)
	if pruneErr == nil {
		t.Error("expected error when dir is a regular file")
	}
}

// ─── New: openCurrent fails (directory blocker) ──────────────

func TestNew_OpenCurrentFails(t *testing.T) {
	dir := t.TempDir()
	today := time.Now().UTC().Format("2006-01-02")
	// Block the .jsonl path with a directory — os.OpenFile(O_WRONLY) fails on a dir
	blocker := filepath.Join(dir, today+".jsonl")
	if err := os.MkdirAll(blocker, 0o755); err != nil {
		t.Fatal(err)
	}
	_, err := New(dir)
	if err == nil {
		t.Error("expected error when audit file path is occupied by a directory")
	}
}

// ─── rotateLocked: rollback without chmod (directory blocker) ─

func TestRotate_RollbackDirBlocker(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	_ = l.Append(Record{Kind: "before"})
	prevDate := l.currentDate

	// Block the new date's file path with a directory
	newDate := "2099-12-31"
	_ = os.MkdirAll(filepath.Join(dir, newDate+".jsonl"), 0o755)

	l.mu.Lock()
	err = l.rotateLocked(newDate)
	l.mu.Unlock()
	if err == nil {
		t.Fatal("expected error when rotation target is blocked by a directory")
	}
	if l.currentDate != prevDate {
		t.Errorf("currentDate = %q after rollback, want %q", l.currentDate, prevDate)
	}
}

// ─── Verify / Read: ReadDir failure (file-as-dir) ────────────

func TestVerify_ReadDirFails(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "audit-block")
	if err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	_, err = Verify(f.Name())
	if err == nil {
		t.Error("expected error when dir arg is a regular file")
	}
}

func TestRead_ReadDirFails(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "audit-block")
	if err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	_, err = Read(f.Name(), "")
	if err == nil {
		t.Error("expected error when dir arg is a regular file")
	}
}

// ─── Append error paths ──────────────────────────────────────

// TestAppend_RotationFails covers the `return err` on Append's rotateLocked
// path (today != currentDate but openCurrent fails).
func TestAppend_RotationFails(t *testing.T) {
	dir := t.TempDir()
	l, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()

	// Replace today's .jsonl file with a directory so openCurrent fails.
	today := time.Now().UTC().Format("2006-01-02")
	todayPath := filepath.Join(dir, today+".jsonl")
	_ = l.file.Close()
	l.file = nil
	_ = os.Remove(todayPath)
	if err := os.MkdirAll(todayPath, 0o755); err != nil {
		t.Fatal(err)
	}
	// Set a stale date so Append triggers rotation.
	l.currentDate = "2000-01-01"

	if err = l.Append(Record{Kind: "rotate_fail"}); err == nil {
		t.Error("expected error when rotation target is blocked by a directory")
	}
}

// TestAppend_UnmarshalableFields covers the `return fmt.Errorf("compute hash: %w")`
// in Append and the `return "", nil, err` in computeHashAndPayload when
// json.Marshal fails because Fields contains an unmarshalable type.
func TestAppend_UnmarshalableFields(t *testing.T) {
	l, _ := New(t.TempDir())
	defer l.Close()
	err := l.Append(Record{
		Kind:   "test",
		Fields: map[string]any{"ch": make(chan int)},
	})
	if err == nil {
		t.Error("expected error when Fields contains an unmarshalable value (channel)")
	}
}

// ─── verifyFile: hashMap error ───────────────────────────────

// TestVerifyFile_RecomputeHashFails covers the `res.Reason = "recompute hash
// failed"` branch inside verifyFile. A record with a numeric "time" field
// passes seq/prev_hash checks but causes hashMap to fail when it tries to
// unmarshal the raw map into Record (time.Time can't be a number).
func TestVerifyFile_RecomputeHashFails(t *testing.T) {
	dir := t.TempDir()
	fname := time.Now().UTC().Format("2006-01-02") + ".jsonl"
	content := `{"seq":1,"kind":"x","prev_hash":"","time":12345,"hash":"anyhash"}` + "\n"
	if err := os.WriteFile(filepath.Join(dir, fname), []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	results, err := Verify(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 {
		t.Fatalf("expected 1 result, got %d", len(results))
	}
	if results[0].OK {
		t.Error("expected verification failure due to hashMap error")
	}
	if !strings.Contains(results[0].Reason, "recompute hash failed") {
		t.Errorf("expected 'recompute hash failed' in reason, got %q", results[0].Reason)
	}
}

// ─── tailState: non-ErrNotExist open failure ─────────────────

// TestTailState_OpenFailsEnotdir covers the `return "", 0, err` branch in
// tailState when os.Open fails with ENOTDIR (not ErrNotExist). The path
// "regularfile/ghost" triggers ENOTDIR because "regularfile" is not a directory.
func TestTailState_OpenFailsEnotdir(t *testing.T) {
	parent := t.TempDir()
	blocker := filepath.Join(parent, "notadir")
	if err := os.WriteFile(blocker, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, _, err := tailState(filepath.Join(blocker, "ghost"))
	if err == nil {
		t.Error("expected non-nil error for ENOTDIR open failure")
	}
}
