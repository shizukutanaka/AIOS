# aictl v1.7.0 レビュー — 長所・短所・改善案

> v1.7.0 公開時点（2026-07）の棚卸し。全項目、実コード・実セッション履歴に基づく
> （推測や一般論ではなく、ファイル参照・実際に起きた事象を根拠として記載）。
> 改善案は Opus/Sonnet 用指示書（`INSTRUCTIONS_OPUS.md` / `INSTRUCTIONS_SONNET.md`）
> と対応しており、各項目にどちらのモデル向きかを付記した。

## 長所（Strengths）

1. **検証規律が構造化されている** — `aictl gate`（compile + import + version +
   3433 tests + demo + docs + MCP + security）を変更のたびに二重実行する文化。
   決定性（同じ結果が2回出ること）まで確認する運用は同種 OSS でも稀。
2. **stdlib-only の徹底** — 外部 Python 依存ゼロを 190 パス超の改善を経ても維持。
   サプライチェーン面・可搬性の強い差別化要因。
3. **off-by-default / opt-in の一貫哲学** — v1.6.0→v1.7.0 の全 222 コミットが
   後方互換・既定無効。`trust_policy` / `guard_policy` / `route_knn_enabled` /
   `rerank_endpoint` 等すべて同型。アップグレードで挙動が変わらない。
4. **層状ルーティング完備** — rules（`score_complexity`）→ embedding-kNN
   （`route_tier_gated`、信頼度ゲート付きタイブレーカー）→ cascade（`run_cascade`）。
   2026 年のルーティング研究（RouteJudge 等）が収斂した3層構成をローカル・ゼロ依存で実装。
5. **検索品質のフルスタック** — dense+BM25 の RRF 融合、TEI 互換 reranker、
   埋め込みモデルの capability detection、劣化モード（ハッシュフォールバック）の
   正直な表示（`rag status` / `cache status`）。
6. **ガードの実トラフィック統合** — 手動ツールだった `core/guard.py` がプロキシの
   リクエスト/レスポンス両面に統合され、model-check には LRU 判定キャッシュ
   （guardrail-as-DoS-target、arXiv:2606.14517 対策）。
7. **確立された拡張パターン** — 設定追加は「dataclass フィールド → `load_config()` →
   `_dict_to_config` → `_validate_config`」の4点セット、CLI は `getattr(args, "x",
   False)` 規約、検証は CLI 層 + ライブラリ層の二重ガード。新規参加者（人間・モデル
   とも）が模倣しやすい。
8. **文書が実装と同期している** — `docs/IMPROVEMENTS.md` が バックログ兼状態台帳
   として毎パス更新され、各項目に実装パス番号・ファイル参照・意図的な未実装
   （documented gap）が明記される。

## 短所（Weaknesses）

> すべて本セッションで実際に露呈した事象。理論上の懸念は含めていない。

1. **バージョン文字列の分散** — v1.7.0 バンプで **20 ファイル**の編集が必要だった
   （`pyproject.toml` / `constants.py` / Go port 3箇所 / README / docs 3点 / Makefile /
   テストの現行バージョンピン 8 ファイル）。一括 sed はテストデータ文字列
   （snapshot fixture 等の任意の "1.6.0"）を巻き込む危険があり、1件ずつ文脈判定が
   必要だった。
2. **CLAUDE.md のカウント3重複** — テスト数・ファイル数が 3 箇所にあり毎パス手動更新。
   機械的だが忘れると gate の docs チェックとズレる。
3. **文書の陳腐化が起きた実績** — `RELEASE.md` が v1.5.0 のまま2世代放置。
   v1.6.0 リリースノートに「GitHub Actions CI」と記載があるが **CI は実在しない**
   （`.github/workflows/` が存在しない）。実装非同期の宣伝文が混入し得る。
4. **CI 不在** — gate はローカル実行のみ。PR #2（+42k 行）もサーバー側チェックゼロで
   マージされた。なお CI 追加自体も Claude GitHub App の `workflows` 権限不足で
   ブロックされた実績あり（権限付与が前提条件）。
5. **Go port の乖離** — 29 コマンド（Python は 80）。かつ本環境では cobra の
   チェックサム不一致で `go build` 検証が不能（バージョン文字列はテストが
   ソース文字列照合で担保、ビルドは未検証）。
6. **名前衝突ハザード** — `cmd/route.py` はローカルの `_load_config`（tier 設定）を
   持ち、グローバル `core.config.load_config` と衝突し得る。今回はエイリアス
   import で回避したが、同型のローカル設定関数が他コマンドにも散在する。
7. **リリース工程が手動** — タグ・Release 作成の自動化がなく、権限制約下の
   セッションでは Release オブジェクト作成が不可能だった（v1.7.0 の Release ページは
   ユーザー操作待ち）。

## 改善案（Proposals）

優先度順。各項目に担当モデルの目安を付記（詳細は各指示書）。

| # | 提案 | 根拠 | 担当 |
|---|------|------|------|
| 1 | **バージョン単一情報源化**: `constants.AICTL_VERSION` を唯一の源とし、テストのピンを定数参照+正規表現形式検証に置換、Go port は生成 or テスト同期のみに | 短所1 | Sonnet |
| 2 | **CI 追加**: gate（--skip-demo）+ suite を GitHub Actions 化。前提: App への `workflows` 権限付与 | 短所3,4 | Opus（設計）→ Sonnet（YAML 保守） |
| 3 | **live fair-share スケジューラ**（IMPROVEMENTS.md 項目 M 残り）: governor/router に per-tenant 概念を導入し、Pass 190 の advisory を実制御に昇格。VTC 式カウンタ or DRR。要 opt-in 設計 | 項目 M | Opus |
| 4 | **G-4: クロスリクエスト injection 文脈**: セッション単位の finding 履歴永続化。データモデル設計から必要 | 項目 G | Opus |
| 5 | **N-3: MCP セッション永続化** + **MCP Tasks 拡張**（2026-07-28 final spec 確定後） | 項目 N | Opus |
| 6 | **rolling-window fairness**: `tco fairshare` の全期間累計を窓付きに（metering に窓カウンタ追加が必要と Pass 190 で文書化済み） | fairness.py 記載 | Sonnet |
| 7 | **tenant-tagged prefix stats**: `PrefixRouteTracker` に entity 次元を追加し fairness×locality ブレンドの前提を作る | fairness.py 記載 | Opus |
| 8 | **CHANGELOG 台帳の自動整合**: gate の docs チェックを現行バージョン動的参照に（今回 `"v1.7.0"` リテラルに更新したが、次のバンプでまた手動） | 短所2 | Sonnet |
| 9 | **Go port パリティ計画**: 全 80 コマンド移植ではなく「サーバー系のみ Go」等の方針決定を先に | 短所5 | Opus（方針）|
| 10 | **リリース手順書**: App 権限（`contents`/`workflows`）の要件を README/RELEASE.md に明記し、権限がある環境でのタグ+Release 自動化スクリプトを用意 | 短所7 | Sonnet |

## 関連文書

- 作業指示: `docs/INSTRUCTIONS_OPUS.md`（設計・研究パス用）/
  `docs/INSTRUCTIONS_SONNET.md`（実装・機械作業パス用）
- バックログ台帳: `docs/IMPROVEMENTS.md`（項目 A–S、状態つき）
- プロジェクト規約: リポジトリ直下 `CLAUDE.md`


---

## 訂正 (Pass 209): 「可観測性コマンドが過剰」は測定で否定された

上記の「過剰」表で最も強く主張したのは可観測性コマンドの重複だったが、実装を
測定した結果 **この主張は誤りだった**。

- `info` / `top` / `ps` はハードウェア・エンジンのプリミティブを**一度も直接
  呼んでいない**（直接呼び出し 0 件）。既に委譲している。
- 直接呼ぶのは `status` / `health` / `dash` の 3 つだけで、呼び先は
  `full_detect()` と `discover_engines()` という**既に単一の共有実装**。
- つまり「3つのコマンドが2つの共有関数を使っている」だけであり、重複実装は
  存在しない。差異は提示方法（要約 / 詳細 / 複合パネル）だけで、これは
  `ls` と `ls -l` と `top` の違いと同種の正当な UI 選択。

**コマンド数を数えただけで「過剰」と結論したのが誤り**だった。表面の数は
実装の重複を意味しない。Pass 201 で「チャンク境界分割の穴」が存在しないと
判明したのと同じ種類の訂正であり、同じ教訓が当てはまる:
**ギャップを文書化することと、ギャップの存在を確認することは別物**。

### 検証したが行動しなかったこと

`full_detect()` は GPU マシンでは `nvidia-smi` を起動するためキャッシュが
有用な可能性があるが、この環境での実測は 1ms（GPU 非搭載のため即座に失敗）。
**測定できないハードウェアを想定した最適化は推測であり、行わない。**
`dash --watch` は5秒間隔なので、仮に 200ms かかっても実用上の問題は小さい。
