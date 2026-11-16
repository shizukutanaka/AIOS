// sink.go: 他パッケージから audit logger を依存注入で受け取るための小さな interface。
//
// 各パッケージ(scanner / mcp / main 等)は *Logger を直接 import せず、
// この Sink interface を受け取る。これにより:
//  1. 依存方向が常に「core → audit」となり循環参照しない
//  2. テスト時は no-op sink を渡せる
//  3. 将来 audit 先を git/Sigstore に拡張するときに呼出側を変えなくて済む
package audit

// Sink は audit log の書込先を抽象化する。
//
// 実装:
//   - *Logger(本番) — このパッケージの Logger
//   - NopSink(テスト) — 全 record を捨てる
type Sink interface {
	Append(r Record) error
}

// NopSink は audit を書込まない実装。テストや audit 無効化時に使う。
type NopSink struct{}

// Append は record を捨てる。常に nil を返す。
func (NopSink) Append(_ Record) error { return nil }
