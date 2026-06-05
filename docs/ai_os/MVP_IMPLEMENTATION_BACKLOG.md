# AI特化Linux OS MVP 実装バックログ

- 文書名: AI特化Linux OS MVP 実装バックログ
- 版数: v1.0-draft
- 作成日: 2026-04-09
- 文書種別: 実装計画 / WBS

## 1. 目的

本書は、v1 製品要求仕様を実装可能な単位へ分解したものである。

関連文書:

- [AI特化Linux OS v1 製品要求仕様書](docs\ai_os\PRODUCT_REQUIREMENTS_V1.md)
- [AI向けOS 意思決定マトリクスとロードマップ](docs\ai_os\OS_DECISION_MATRIX_AND_ROADMAP.md)

## 2. 実装方針

- まず「単一ノードで動く最小縦切り」を成立させる
- その後に信頼連鎖、観測性、更新性を固める
- 早い段階で demo 可能な slice を用意する

## 3. マイルストーン

### M0 基盤整理

ゴール:

- リポジトリ構成、命名、最小CLI雛形を固める

成果物:

- `aictl` CLI skeleton
- 状態保存ディレクトリ
- ドキュメント索引

完了条件:

- `init`, `doctor`, `ps` が最低限動く

### M1 単一ノード初期化

ゴール:

- 1台でローカルAI基盤を起動できる

成果物:

- ノード初期化
- GPU/CPUプロファイル判定
- ローカル状態ストア
- Local Console のプレースホルダ

完了条件:

- `aictl init`
- `aictl doctor`
- 初期ディレクトリ自動生成

### M2 `Stack` / レシピ

ゴール:

- 宣言ファイル1つでサービス起動意図を扱える

成果物:

- `Stack` schema
- `apply / down / ps`
- built-in recipe
- JSON/TOML 対応

完了条件:

- `local-chat` と `team-rag` の2つを起動意図として扱える

### M3 Runtime Broker

ゴール:

- GPU runtime をOS側に近い位置で検出し、利用可能状態を返せる

成果物:

- NVIDIA runtime 検出
- AMD runtime 検出
- CPU fallback
- profile selection

完了条件:

- `doctor` に runtime 利用可否が出る
- `apply` 時に profile 選択結果が出る

### M4 署名検証

ゴール:

- 最小限の artifact trust chain を持つ

成果物:

- model bundle metadata
- digest verification
- signer policy placeholder
- 起動拒否ロジック

完了条件:

- digest mismatch の bundle を拒否できる

### M5 観測性

ゴール:

- AI向け主要メトリクスを取れる骨格を持つ

成果物:

- OTel Collector integration plan
- local metrics schema
- tokens/sec / model load time / queue depth / VRAM usage model
- Local Console metrics view mock

完了条件:

- 少なくとも1つの Stack でメトリクスの集約形式が定義される

### M6 更新 / ロールバック

ゴール:

- image-based update と rollback の運用手順を製品に取り込む

成果物:

- update plan
- staged update
- rollback procedure
- recovery runbook

完了条件:

- 計画出力と復帰手順が確認できる

## 4. エピック別バックログ

## E1 CLI / 状態管理

タスク:

- `aictl` のコマンド体系を確定する
- `.aios/state.json` のスキーマを定義する
- 状態ディレクトリ生成を実装する
- 文字列出力と JSON 出力の方針を決める

依存:

- なし

受入基準:

- 単一ノード状態が永続化される

## E2 ハードウェア検出

タスク:

- `nvidia-smi` 検出
- `rocm-smi` 検出
- Podman / Docker / Ollama 存在確認
- CPU/メモリ/ディスクの収集

依存:

- E1

受入基準:

- `doctor` が実機情報を要約して返す

## E3 Stack 解析

タスク:

- `Stack` schema 定義
- JSON/TOML ローダ
- YAML 任意対応
- validation エラー設計

依存:

- E1

受入基準:

- `apply -f` で manifest を読み込める

## E4 Recipe システム

タスク:

- recipe catalog
- render
- run
- local defaults の注入

依存:

- E3

受入基準:

- `recipe list` と `recipe run local-chat` が成立する

## E5 ローカルオーケストレーション

タスク:

- Stack -> internal service model 変換
- endpoint 付与
- degraded mode 判定
- `ps` / `down` 実装

依存:

- E3
- E4

受入基準:

- Stack 適用後にサービス一覧と endpoint を見られる

## E6 Runtime Broker

タスク:

- NVIDIA toolkit 前提条件を文書化
- AMD ROCm container 前提条件を文書化
- profile selection 戦略を定義
- CPU fallback を明示する

依存:

- E2

受入基準:

- profile が自動選択される

## E7 Trust Chain

タスク:

- ModelBundle schema
- digest verify
- signer identity policy
- verification result logging

依存:

- E3

受入基準:

- 未検証 bundle を拒否できる

## E8 Metrics / Local Console

タスク:

- メトリクス用内部スキーマ
- OTel export 設計
- Local Console の表示要件
- tokens/sec 等の収集インターフェース

依存:

- E5
- E6

受入基準:

- 主要メトリクスの型と表示先が定義される

## E9 アップグレード

タスク:

- update plan schema
- staged apply / rollback runbook
- health check hooks
- failure recovery notes

依存:

- E1

受入基準:

- `upgrade plan` で計画を生成できる

## 5. カットライン

以下はMVP成立のために必須とする。

- E1
- E2
- E3
- E4
- E5
- E6
- E9

以下はベータ前にほしいが、MVPからは外せる。

- E7
- E8

ただし、買収候補として見せるなら E7 の「最小署名検証」はできるだけMVPに入れるべきである。

## 6. 最初のデモシナリオ

### Demo A

目的:

- 1台のGPUワークステーションで、AI特化OSの価値を最短で見せる

流れ:

1. `aictl doctor`
2. `aictl init`
3. `aictl recipe run local-chat`
4. `aictl ps`
5. localhost endpoint を表示
6. `aictl upgrade plan --target-version x.y.z`

価値:

- セットアップ容易性
- ローカル運用性
- 更新の壊れにくさ

### Demo B

目的:

- 小規模企業導入の姿を見せる

流れ:

1. `team-rag` Stack を適用
2. signed model policy を有効化
3. inference / embedding / retriever の構成表示
4. QoS や文脈継続が将来乗る場所を説明

## 7. 実装順

以下の順が最もリスクが低い。

1. CLI / 状態管理
2. ハードウェア検出
3. Stack / Recipe
4. ローカルオーケストレーション
5. Runtime Broker
6. アップグレード計画
7. 最小Trust Chain
8. 観測性

## 8. 依存する主な外部技術

- bootc
- Podman
- Quadlet
- systemd
- cgroup v2
- PSI
- systemd-oomd
- Cosign
- OpenTelemetry Collector
- NVIDIA Container Toolkit
- ROCm container support

## 9. リスクと切り戻し戦略

### リスク

- GPU 実機が無いと profile 周りの検証が遅れる
- YAML依存を強めると実装の足場がぶれる
- update / rollback を早く作りすぎると基盤変更コストが高い

### 切り戻し

- まずは JSON/TOML のみで完結させる
- GPU 実機がなくても doctor / profile mock を進められる構造にする
- update 系は plan / runbook 先行で実装する

## 10. 参考にした主な一次情報

- [bootc upgrades and rollback](https://bootc-dev.github.io/bootc/upgrades.html)
- [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-quadlet.1.html)
- [Podman systemd unit / Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [NVIDIA Container Toolkit install](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.18.0/install-guide.html)
- [ROCm Docker containers](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/how-to/docker.html)
- [Linux DAMON](https://docs.kernel.org/admin-guide/mm/damon/)
- [OpenTelemetry Collector install](https://opentelemetry.io/docs/collector/installation/)
- [Sigstore Cosign verify](https://docs.sigstore.dev/cosign/verifying/verify/)

