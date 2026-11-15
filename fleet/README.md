# fleet/ — ローカルマルチエージェント運用層（agent-fleet から統合）

このディレクトリは、旧 `agent-fleet` リポジトリの中核だった「ローカルで複数の
AI エージェントを組織として運用する」自動化を、AIOS（aictl）の一層として
取り込んだものです。旧リポジトリは統合後にアーカイブされます。

> 位置づけ: aictl 本体（Python パッケージ）とは独立した **bash 運用層** です。
> aictl のライブラリ/CLI の振る舞いには影響しません。

## 構成

| ファイル | 役割 |
|---|---|
| `router3.sh` | ボス（上司 AI）。判断のみを行い、実作業は部下 AI（Ollama / Claude / Hermes 等）へ委譲。成果物を git diff と検証オラクルで判定し、不備は差し戻す |
| `parallel_scheduler.sh` | router3 の STEP 2 から委譲される依存グラフ・並列実行スケジューラ |
| `boss_trigger_guard.sh` | 同一トリガーの連続再投入を遮断するサーキットブレーカー |
| `ethics_guard.sh` | 人間の決定ログに基づく倫理 HALT（stop_signal 連携） |
| `verify_artifacts.py` | 成果物検証: 期待ファイルの実在・中身をチェック（自己申告を信用しない） |
| `fleet_loop.sh` | 内ループ常駐デーモン。`goals.md` の未完了目標を順に router3 へ委譲し、オラクルで完了判定。連続失敗は人間へエスカレーション |
| `goal_complete.sh` | 目標の `done_when:` オラクルの抽出/実行（完了判定の単一実装） |
| `fleet_status.sh` | フリートの死活・モデル基盤・VRAM・未コミット変更の状態表示 |
| `detect_failures.sh` | 失敗シグネチャ検知ループ。失敗クラスを学習ファイルへ構造化追記し、サーキットブレーカーと連携 |
| `guard_*.sh`（7件） | 証拠整合性の監視層（L35–L41）。詳細は `GUARDS.md` |
| `gh_bulk_health.sh` / `gh_repo_health_pr.sh` | 全リポジトリへ .gitignore / SECURITY.md の衛生 PR を一括作成 |
| `gh_bulk_merge.sh` | 全リポジトリの open PR 一括マージ（結果を分類して報告） |
| `DESIGN.md` | 組織設計の蒸留（監視チェーン、監視者を誰が監視するか、教訓、ボスの4ルール） |
| `GUARDS.md` | ガード層の蒸留（7層の考え方と自己検証方法） |

## 前提（prerequisites）

- **実行環境**: Windows 11 + Git Bash（MSYS）を主対象に書かれています
  （`cygpath` によるパス正規化、UTF-8 コードページ固定を含む）。
  Linux/macOS の bash でも `cygpath` 不在時はフォールバックしますが、
  実運用の検証対象は Windows Git Bash です。
- **モデル基盤**: Ollama（`http://localhost:11434`、メイン）、必要に応じて
  LM Studio / Claude Code / Hermes 等の部下バックエンド。
- **GitHub 操作**: `gh` CLI（認証済みであること）。トークンは環境/gh 認証から
  取得し、スクリプト内に直書きされた資格情報はありません。
- **配置**: スクリプトはこのディレクトリに同居していることを前提に、
  自分の置き場所を `FLEET_HOME` として解決します
  （`FLEET_HOME` 環境変数で上書き可能。旧リポジトリでは `~/agent-fleet` 固定でした）。
- **状態ファイル**: `goals.md`（目標キュー）、`stop_signal.txt`（サーキット
  ブレーカー）、`learnings.md`（学習ループ）等は実行時に `FLEET_HOME` 配下へ
  生成される運用物であり、リポジトリには含まれません。

## 使い方（最小）

```bash
# ボスに単発タスクを渡す
bash fleet/router3.sh "<タスク指示>" [workdir]

# 目標キューの常駐実行
bash fleet/fleet_loop.sh

# 状態確認
bash fleet/fleet_status.sh

# ガードの自己検証（オフライン・決定的）
bash fleet/guard_trust_boundary.sh --self-test
```

## 持ち込まなかったもの（旧 agent-fleet の残り）

旧リポジトリの追跡ファイル908件の大半は、製品ではなく一個人の運用記録でした。
Ring → Orange 統合の先例に倣い、再利用できる自動化だけを移し、以下は移していません。

- **運用ログ・アーカイブ**: `logs/` 配下596件（実行ログ、tar.gz アーカイブ198件等）。
  なお旧ログの一部には GitHub OAuth トークン形式の文字列が残存していたため、
  ログ類は理由を問わず一切持ち込んでいません（持ち込み対象は全件、秘密
  パターン走査で一致ゼロを確認済み）。
- **状態ファイル・台帳・生成レポート**: コスト台帳、組織状態フラグ、
  健全性レポート等の実行時生成物。
- **ガードの残り約38件と集約ランナー**: 考え方と中核7層（L35–L41）を
  `GUARDS.md` と代表スクリプトとして蒸留しました。個別ガードの全量は
  アーカイブされる旧リポジトリに残ります。
- **外ループ（fleet_outer.sh）等の PC 固有運用**: 特定マシンの cron ジョブ ID や
  パスに密結合した自己修復・常駐運用は、設計の考え方のみ `DESIGN.md` に残し、
  実装は持ち込んでいません。

## 出自

- 移行元: `shizukutanaka/agent-fleet`（統合後にアーカイブ）
- 移行方針: ユーザー決定（2026-11-15）。再利用可能な自動化のみを移し、
  個人的運用物は移さない。
