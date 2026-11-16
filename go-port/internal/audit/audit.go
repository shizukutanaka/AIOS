// Package audit は Yagura の append-only audit log を実装する。
//
// 設計判断(ultrathink S0.3):
//   - JSON Lines (1 行 1 record) でローテーション可能・grep 可能
//   - 1 日 1 ファイル (YYYY-MM-DD.jsonl) で運用上扱いやすい
//   - O_APPEND モード書込で、過去レコードの改変は OS レベルで起きない
//   - 各 record に直前 record の SHA-256 を含む hash chain で改ざん検出
//   - file mode 0600(secret は載らないが、行動履歴は十分機微)
//   - 同期書込(fsync)。性能より「絶対に消えない」を優先
//
// 設計境界:
//   - このパッケージは local file のみ管理する
//   - yagura_state git repo への push、Sigstore 署名は別レイヤー(将来追加)
//   - つまり「改ざんを検出する」までが責務、「改ざんを防ぐ」は git remote で
package audit

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Record は監査ログの 1 エントリ。
type Record struct {
	// 自動付与フィールド
	Time     time.Time `json:"time"`
	Hash     string    `json:"hash,omitempty"`
	PrevHash string    `json:"prev_hash,omitempty"`
	Seq      uint64    `json:"seq"`

	// 呼出側設定
	Kind   string         `json:"kind"`
	Actor  string         `json:"actor,omitempty"`
	Target string         `json:"target,omitempty"`
	Fields map[string]any `json:"fields,omitempty"`
}

// Logger は append-only な audit log の書込を担う。
type Logger struct {
	mu          sync.Mutex
	dir         string
	file        *os.File
	filePath    string
	seq         uint64
	lastHash    string
	currentDate string
}

// New は Logger を生成する。dir が存在しなければ 0700 で作成し、
// 当日のログファイルを open(O_APPEND | O_CREATE)する。
// 既存ファイルがあれば末尾を読んで hash chain を継続する。
func New(dir string) (*Logger, error) {
	if dir == "" {
		return nil, errors.New("audit: dir is required")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("create audit dir: %w", err)
	}
	l := &Logger{
		dir:         dir,
		currentDate: time.Now().UTC().Format("2006-01-02"),
	}
	if err := l.openCurrent(); err != nil {
		return nil, err
	}
	return l, nil
}

func (l *Logger) openCurrent() error {
	path := filepath.Join(l.dir, l.currentDate+".jsonl")
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return fmt.Errorf("open audit file: %w", err)
	}
	l.file = f
	l.filePath = path

	prev, n, err := tailState(path)
	if err != nil {
		// 復元失敗 → degraded path で続行(初回や軽微な末尾異常)
		l.lastHash = ""
		l.seq = 0
		return nil
	}
	l.lastHash = prev
	l.seq = n
	return nil
}

// Append は 1 record を書込む。Time / Hash / PrevHash / Seq は自動付与される。
//
// 注: 全ての文字列フィールド(Kind, Actor, Target, Fields の string 値)は
// 書込前に valid UTF-8 に正規化される。invalid byte は U+FFFD で置換される。
// これは encoding/json の Marshal/Unmarshal 不変性を保証するため不可欠
// (invalid UTF-8 を Marshal すると \ufffd literal が書かれるが、Unmarshal
// 後の文字列は 3-byte UTF-8 になり、再 Marshal で異なるバイト列が出る)。
func (l *Logger) Append(r Record) error {
	if r.Kind == "" {
		return errors.New("audit: Kind is required")
	}
	r = normalizeUTF8(r)

	l.mu.Lock()
	defer l.mu.Unlock()

	// 日付切替判定(UTC)
	today := time.Now().UTC().Format("2006-01-02")
	if today != l.currentDate {
		if err := l.rotateLocked(today); err != nil {
			return err
		}
	}

	l.seq++
	r.Seq = l.seq
	r.Time = time.Now().UTC()
	r.PrevHash = l.lastHash

	hash, payload, err := computeHashAndPayload(r)
	if err != nil {
		return fmt.Errorf("compute hash: %w", err)
	}
	r.Hash = hash

	final, err := withHashField(payload, hash)
	if err != nil {
		return fmt.Errorf("attach hash: %w", err)
	}
	final = append(final, '\n')

	if _, err := l.file.Write(final); err != nil {
		return fmt.Errorf("write: %w", err)
	}
	if err := l.file.Sync(); err != nil {
		return fmt.Errorf("fsync: %w", err)
	}
	l.lastHash = hash
	return nil
}

func (l *Logger) rotateLocked(newDate string) error {
	// openCurrent が成功するまで currentDate を確定しない。
	// 先に currentDate を書き換えると、open 失敗時(一時的な disk full / 権限)に
	// 以降の Append が today==currentDate と誤判定して再 rotate せず、閉じた
	// 旧ハンドルへ書き続けてプロセス再起動まで恒久的に失敗してしまう。
	prevDate := l.currentDate
	if l.file != nil {
		l.file.Sync()
		l.file.Close()
		l.file = nil
	}
	l.currentDate = newDate
	l.seq = 0
	l.lastHash = ""
	if err := l.openCurrent(); err != nil {
		// rollback: 次回 Append で再 rotate を試みられるよう旧日付に戻す。
		l.currentDate = prevDate
		return err
	}
	return nil
}

// Close は file を flush + close する。多重呼出は安全。
func (l *Logger) Close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.file == nil {
		return nil
	}
	if err := l.file.Sync(); err != nil {
		l.file.Close()
		l.file = nil
		return err
	}
	err := l.file.Close()
	l.file = nil
	return err
}

// CurrentFile は現在書込中のファイルパスを返す(テスト・診断用)。
func (l *Logger) CurrentFile() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.filePath
}

// ─── verification ────────────────────────────────────────────

// VerifyResult は単一ファイルの検証結果。
type VerifyResult struct {
	File         string
	TotalRecords int
	OK           bool
	FailedAtSeq  uint64
	Reason       string
}

// Verify は audit ディレクトリ内の全 *.jsonl を hash chain で検証する。
// 1 ファイル内で chain が壊れていれば該当ファイルが Failed と報告される。
// ディレクトリが存在しない場合は空の結果を返す(エラー扱いではない)。
// 戻り値は各ファイルの検証結果(ファイル名昇順=日付昇順)。
func Verify(dir string) ([]VerifyResult, error) {
	files, err := listJSONLFiles(dir)
	if err != nil {
		return nil, err
	}
	results := make([]VerifyResult, 0, len(files))
	for _, p := range files {
		results = append(results, verifyFile(p))
	}
	return results, nil
}

// listJSONLFiles は dir 内の *.jsonl をファイル名昇順(=日付昇順)で返す。
// ディレクトリ不在は空スライス(エラーではない)。Verify/Read で共有する。
func listJSONLFiles(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, fmt.Errorf("read dir: %w", err)
	}
	var files []string
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		files = append(files, filepath.Join(dir, e.Name()))
	}
	sort.Strings(files)
	return files, nil
}

// Read は dir 内の全 *.jsonl を日付順(ファイル名昇順=時系列)に読み、record を返す。
// kind != "" のときはその Kind の record だけを返す。整合性検証はしない(Verify が担う)。
// ディレクトリ不在は空スライス(エラーではない)。壊れた行に当たった場合は、そこまでに
// 読めた record と error を返す(best-effort 読出)。
func Read(dir, kind string) ([]Record, error) {
	files, err := listJSONLFiles(dir)
	if err != nil {
		return nil, err
	}
	var out []Record
	for _, p := range files {
		recs, rerr := decodeJSONLFile(p, kind)
		out = append(out, recs...)
		if rerr != nil {
			return out, rerr
		}
	}
	return out, nil
}

// decodeJSONLFile は 1 ファイルを JSONL decode し、kind でフィルタした record を
// 返す(kind=="" は無条件)。decode エラー時は、そこまでに読めた record と error を
// 返す(best-effort 読出)。
func decodeJSONLFile(path, kind string) ([]Record, error) {
	f, err := os.Open(path) //nolint:gosec
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()

	var out []Record
	dec := json.NewDecoder(f)
	for {
		var r Record
		if derr := dec.Decode(&r); derr != nil {
			if errors.Is(derr, io.EOF) {
				break
			}
			return out, fmt.Errorf("decode %s: %w", path, derr)
		}
		if kind == "" || r.Kind == kind {
			out = append(out, r)
		}
	}
	return out, nil
}

func verifyFile(path string) VerifyResult {
	res := VerifyResult{File: path}
	f, err := os.Open(path) //nolint:gosec
	if err != nil {
		res.Reason = "open failed: " + err.Error()
		return res
	}
	defer f.Close()

	dec := json.NewDecoder(f)
	var prevHash string
	var expectedSeq uint64

	for {
		var raw map[string]any
		if err := dec.Decode(&raw); err != nil {
			if errors.Is(err, io.EOF) {
				break
			}
			res.Reason = "decode failed: " + err.Error()
			res.FailedAtSeq = expectedSeq + 1
			return res
		}
		res.TotalRecords++
		expectedSeq++

		seqVal, _ := raw["seq"].(float64)
		if uint64(seqVal) != expectedSeq {
			res.Reason = fmt.Sprintf("seq mismatch: expected %d, got %v", expectedSeq, raw["seq"])
			res.FailedAtSeq = expectedSeq
			return res
		}

		gotPrev, _ := raw["prev_hash"].(string)
		if gotPrev != prevHash {
			res.Reason = fmt.Sprintf("prev_hash mismatch at seq=%d", expectedSeq)
			res.FailedAtSeq = expectedSeq
			return res
		}

		recordedHash, _ := raw["hash"].(string)
		delete(raw, "hash")
		recomputed, err := hashMap(raw)
		if err != nil {
			res.Reason = "recompute hash failed: " + err.Error()
			res.FailedAtSeq = expectedSeq
			return res
		}
		if recomputed != recordedHash {
			res.Reason = fmt.Sprintf("hash mismatch at seq=%d", expectedSeq)
			res.FailedAtSeq = expectedSeq
			return res
		}
		prevHash = recordedHash
	}
	res.OK = true
	return res
}

// ─── internal helpers ────────────────────────────────────────

// normalizeUTF8 は Record 内の全文字列フィールドを valid UTF-8 に変換する。
// invalid UTF-8 byte は U+FFFD (REPLACEMENT CHARACTER) に置換される。
//
// 理由: encoding/json は invalid UTF-8 を Marshal すると \ufffd という
// literal escape sequence を書くが、Unmarshal で読戻すと U+FFFD の rune
// (3 bytes UTF-8) として復元される。再 Marshal すると 3-byte UTF-8 が
// そのまま書かれるため、元の Marshal 結果と一致しなくなる。hash chain は
// バイト列一致に依存するため、これは致命的。
//
// この関数を Append 入口で 1 回適用することで、書込まれる payload と
// Verify で再計算される payload が常に一致することを保証する。
func normalizeUTF8(r Record) Record {
	r.Kind = strings.ToValidUTF8(r.Kind, "\uFFFD")
	r.Actor = strings.ToValidUTF8(r.Actor, "\uFFFD")
	r.Target = strings.ToValidUTF8(r.Target, "\uFFFD")
	if len(r.Fields) > 0 {
		clean := make(map[string]any, len(r.Fields))
		for k, v := range r.Fields {
			k = strings.ToValidUTF8(k, "\uFFFD")
			if s, ok := v.(string); ok {
				clean[k] = strings.ToValidUTF8(s, "\uFFFD")
			} else {
				clean[k] = v
			}
		}
		r.Fields = clean
	}
	return r
}

func computeHashAndPayload(r Record) (hash string, payload []byte, err error) {
	r.Hash = ""
	payload, err = json.Marshal(r)
	if err != nil {
		return "", nil, err
	}
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:]), payload, nil
}

func withHashField(payload []byte, hash string) ([]byte, error) {
	if len(payload) < 2 || payload[0] != '{' || payload[len(payload)-1] != '}' {
		return nil, errors.New("invalid payload shape")
	}
	if len(payload) == 2 {
		return []byte(fmt.Sprintf(`{"hash":%q}`, hash)), nil
	}
	inner := payload[1 : len(payload)-1]
	return []byte(fmt.Sprintf(`{%s,"hash":%q}`, inner, hash)), nil
}

func hashMap(m map[string]any) (string, error) {
	raw, err := json.Marshal(m)
	if err != nil {
		return "", err
	}
	var r Record
	if err := json.Unmarshal(raw, &r); err != nil {
		return "", err
	}
	r.Hash = ""
	payload, err := json.Marshal(r)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:]), nil
}

// tailState はファイル末尾を読んで最終 hash と seq を返す。
// ファイル不在 or 空なら ("", 0, nil) を返す(エラーではない)。
func tailState(path string) (lastHash string, lastSeq uint64, err error) {
	f, err := os.Open(path) //nolint:gosec
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", 0, nil
		}
		return "", 0, err
	}
	defer f.Close()

	st, err := f.Stat()
	if err != nil {
		return "", 0, err
	}
	size := st.Size()
	if size == 0 {
		return "", 0, nil
	}

	// 末尾 8KB を読む(典型 record は 200-500 bytes、8KB あれば最終 1 行が確実に取れる)
	readLen := int64(8192)
	if size < readLen {
		readLen = size
	}
	if _, err := f.Seek(size-readLen, io.SeekStart); err != nil {
		return "", 0, err
	}
	buf := make([]byte, readLen)
	n, err := io.ReadFull(f, buf)
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) {
		return "", 0, err
	}
	buf = buf[:n]

	// 末尾改行を削って最後の行を取る
	for len(buf) > 0 && buf[len(buf)-1] == '\n' {
		buf = buf[:len(buf)-1]
	}
	idx := strings.LastIndexByte(string(buf), '\n')
	var lastLine []byte
	if idx >= 0 {
		lastLine = buf[idx+1:]
	} else if size <= readLen {
		// ファイル全体が 1 行
		lastLine = buf
	} else {
		// 8KB に 1 行も収まっていない → degraded
		return "", 0, errors.New("last record exceeds 8KB tail buffer")
	}

	var rec map[string]any
	if err := json.Unmarshal(lastLine, &rec); err != nil {
		return "", 0, err
	}
	h, _ := rec["hash"].(string)
	seqVal, _ := rec["seq"].(float64)
	return h, uint64(seqVal), nil
}

// ─── retention ───────────────────────────────────────────────

// Prune は指定日数より古い audit ファイルを削除する。
// keepDays <= 0 は何もしない(無制限保持)。
//
// 削除前にファイル名(YYYY-MM-DD)から日付を parse し、
// 当該日付 + keepDays日 < 今日 のものを対象にする。
// parse できないファイルは保持する(safety first)。
//
// 戻り値は削除したファイル数。
//
// 設計判断:
//   - 当日ファイルは絶対に削除しない(現在書込中)
//   - parse 不能ファイルは温存(操作員が手動で対応する想定)
//   - 削除自体を audit log に記録するため、Append を 1 回呼ぶ責任は呼出側
//     (ここで Append すると自己参照ループになる可能性があり危険)
func Prune(dir string, keepDays int) (int, error) {
	if keepDays <= 0 {
		return 0, nil
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return 0, nil
		}
		return 0, fmt.Errorf("read dir: %w", err)
	}
	cutoff := time.Now().UTC().AddDate(0, 0, -keepDays)
	cutoffStr := cutoff.Format("2006-01-02")

	deleted := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		// ファイル名: YYYY-MM-DD.jsonl
		dateStr := strings.TrimSuffix(e.Name(), ".jsonl")
		if _, err := time.Parse("2006-01-02", dateStr); err != nil {
			// 不明な命名のファイルは保持
			continue
		}
		if dateStr >= cutoffStr {
			// cutoff 以降は保持(string 比較が ISO 8601 で日付比較として正しい)
			continue
		}
		fullPath := filepath.Join(dir, e.Name())
		if err := os.Remove(fullPath); err != nil {
			return deleted, fmt.Errorf("remove %s: %w", e.Name(), err)
		}
		deleted++
	}
	return deleted, nil
}
