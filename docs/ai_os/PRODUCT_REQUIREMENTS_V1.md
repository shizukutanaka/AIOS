# AI特化Linux OS v1 製品要求仕様書

- 文書名: AI特化Linux OS v1 製品要求仕様書
- 版数: v1.0-draft
- 作成日: 2026-04-09
- 文書種別: PRD / v1 要求定義
- 対象読者: プロダクト、基盤開発、SRE、セキュリティ、M&A / 事業責任者

## 1. 目的

本書は、AI特化Linux OSの v1 で実装すべき機能を定義する。

v1 のテーマは以下の3つとする。

- 1台で数分以内に使い始められること
- 更新しても壊れにくいこと
- GPU を使うローカル推論基盤として現実に運用できること

本書の判断は、既存OS比較と一次情報に基づく製品設計上の推論である。

関連文書:

- [AI向けOS 比較調査メモ](docs\ai_os\OS_COMPARATIVE_RESEARCH.md)
- [AI向けOS 意思決定マトリクスとロードマップ](docs\ai_os\OS_DECISION_MATRIX_AND_ROADMAP.md)
- [AI特化Linux OS ローカル運用・内蔵オーケストレーション仕様](docs\ai_os\LOCAL_ORCHESTRATION_SPEC.md)

## 2. v1 の対象ユーザー

### 2.1 主要ユーザー

- ローカルGPUワークステーションでLLMを使いたい開発者
- 小規模部門向けに社内AI実行基盤を作りたいインフラ担当
- セキュアなローカルAI実行環境を求める企業のPoC担当

### 2.2 二次ユーザー

- モデル配布と更新の監査を求めるセキュリティ担当
- 生成AIの部門標準化を検討する事業責任者

## 3. v1 で解く課題

現在のローカルAI環境には以下の痛みがある。

- ドライバ、CUDA、ROCm、Python、推論サーバーが分離しており壊れやすい
- 更新で環境が壊れる
- コンテナはあってもローカル運用が人間向けに整理されていない
- モデル配布の信頼性と監査性が弱い
- AI向けメトリクスがOS標準では見えない

v1 は、これらをすべて解くのではなく、まず「壊れにくいローカルAI基盤」を成立させる。

## 4. v1 の成功条件

### 4.1 製品条件

- 単一ノードにインストールして、初回セットアップ後 10 分以内にローカル推論APIを起動できること
- OS更新後にロールバック可能であること
- GPU 対応プロファイルで、署名済みコンテナおよび署名済みモデルを検証できること
- 1ファイルの `Stack` マニフェストでサービス起動、停止、再適用ができること

### 4.2 事業条件

- 企業PoC導入時に「Kubernetesなし」「1台から導入可能」と説明できること
- 買収検討で差別化要素として `壊れにくい更新` `信頼連鎖` `AI専用運用モデル` を示せること

## 5. v1 の非目標

以下は v1 の非目標とする。

- 大規模分散学習
- Kubernetes 完全互換
- 汎用デスクトップOSとしての完成度
- OS横断の汎用VRAMデデュープ
- 全ベンダーNPUの完全最適化
- 高度なAttestation前提の鍵解放

## 6. v1 の設計原則

- Single-node first
- Image-based updates
- Local-first orchestration
- Container-first AI runtime
- Signed artifacts by default
- AI metrics as first-class signals
- Host minimalism

## 7. v1 の基盤選定

以下を v1 の推奨基盤として採用する。

### 7.1 OS更新モデル

- `bootc` 系の image-based / transactional update を採用する
- 理由:
  - 公式にトランザクショナル更新とロールバックを想定している
  - bootable OCI image として扱える

### 7.2 コンテナ実行基盤

- Podman を標準採用する
- Quadlet を宣言的サービス起動の基本形式とする
- 理由:
  - daemonless
  - rootless
  - systemd と自然につながる
  - Quadlet により K8s なしでも宣言的運用が可能

### 7.3 資源制御

- Linux cgroup v2 を必須とする
- PSI と `systemd-oomd` を採用する
- 理由:
  - AIワークロードのメモリ圧力を早期に検知しやすい
  - 単なるOOMではなく、事前縮退がしやすい

### 7.4 観測性

- OpenTelemetry Collector を標準採用する
- 理由:
  - 外部監視接続とローカル可視化の両方に使える

### 7.5 署名・供給網

- Cosign ベースの署名検証を採用する
- 理由:
  - OCI artifact への署名と検証が現実的
  - 将来の model vault / attestation に接続しやすい

## 8. v1 対応ハードウェア範囲

### 8.1 必須サポート

- NVIDIA GPU
- AMD GPU
- CPU-only fallback

### 8.2 限定サポート

- Intel NPU は実験的扱い
- 理由:
  - OpenVINO NPU は Linux 側の前提条件が限定的
  - static shape 制約など実運用制限が残る

### 8.3 v1 の結論

v1 は GPU 中心で成立させる。

## 9. 機能要求

## 9.1 インストール / 初期化

- 単一コマンドでローカル初期化できること
- ハードウェア検出とプロファイル推定を自動実行すること
- モデルキャッシュ、ログ、状態保存先を自動作成すること
- Local Console を `127.0.0.1` に標準公開すること

受入基準:

- `aictl init` 実行後、状態ファイルと既定ディレクトリが生成される
- GPU が見つかれば対応プロファイルが選択される

## 9.2 ローカル診断

- 現在の runtime、GPU、メモリ、ディスク、主要ツール有無を表示すること
- OSが不足している要素を案内できること

受入基準:

- `aictl doctor` で人間可読な準備状況を返す

## 9.3 ローカル推論サービス

- OpenAI互換の localhost API を起動できること
- 署名済みモデルのみ許可するポリシーを適用可能であること
- モデルバンドルの名前で起動できること

受入基準:

- `Stack` 適用後に localhost endpoint が返る

## 9.4 `Stack` マニフェスト

- `Stack` 1ファイルで推論サービス群を定義できること
- `apply` `ps` `down` が可能であること
- JSON/TOML を標準対応とし、YAML は任意対応とする

受入基準:

- 1ファイルで gateway + inference + embedding の起動意図を保存できる

## 9.5 レシピ

- 代表テンプレートを同梱すること
- 例:
  - `local-chat`
  - `team-rag`
  - `private-coding-assistant`

受入基準:

- `aictl recipe list` と `aictl recipe run` が動く

## 9.6 モデル供給の最小信頼連鎖

- Cosign によるコンテナ署名検証を実施できること
- モデルバンドルに digest と signer policy を持てること
- 検証結果を記録できること

受入基準:

- 署名未検証または digest 不一致時に起動拒否できる

## 9.7 資源制御

- cgroup v2 を使ってワークロードごとに制御可能であること
- PSI と `systemd-oomd` を使った圧力監視を有効にできること
- 高優先度の対話サービスと低優先度ジョブを区別できること

受入基準:

- 低優先度ジョブが対話APIを圧迫した際、警告または縮退動作を発火できる

## 9.8 観測性

- 主要メトリクスを Local Console と CLI で確認できること
- 最低限、以下を表示すること
  - CPU
  - RAM
  - VRAM
  - tokens/sec
  - model load time
  - queue depth

受入基準:

- 最低1つのスタックで上記メトリクスが確認できる

## 9.9 更新とロールバック

- OS更新をダウンロードと適用に分離できること
- 前版への復帰手段を持つこと
- 更新計画を出力できること

受入基準:

- `upgrade plan` 相当の計画出力が可能
- 更新後の失敗時に復帰フローが定義されている

## 10. 非機能要求

### 10.1 性能

- 単一ノードでローカル推論が常識的な応答速度で動くこと
- モデルロード時間が可視化されること
- ホスト常駐コストが低いこと

### 10.2 安定性

- OS更新とワークロード障害が分離されること
- 状態破損時に初期化と再適用で復旧可能であること

### 10.3 セキュリティ

- rootless を標準とする
- loopback bind を既定とする
- 署名検証を標準ポリシーにできること

### 10.4 保守性

- `Stack` は git 管理しやすいテキスト形式であること
- Quadlet や systemd と接続可能な内部モデルであること

## 11. v1 で見送るもの

- Attested Model Vault の完全版
- Fabric Memory Orchestrator の自動再配置
- Cross-node context resume
- NPU 専用の製品最適化
- 大規模クラスタ制御面

## 12. リリースゲート

v1 を出荷可能とする条件は以下とする。

- 単一ノード初期化
- 少なくとも1つのGPUプロファイルでの成功
- ローカルOpenAI互換API起動
- `Stack` による起動 / 停止 / 再適用
- 署名済みartifact 検証
- 更新とロールバックの手順確認
- 基本メトリクス可視化

## 13. 主要リスク

- GPUドライバ差分が大きい
- Podman / systemd / GPU runtime の組み合わせで相性問題が起きうる
- YAML依存を強くすると初期導入性が落ちる
- NPUを早く取り込むと v1 の安定性が落ちる

## 14. リスク低減策

- v1 は NVIDIA と AMD GPU を主軸にする
- マニフェストは JSON/TOML 標準にする
- Quadlet と systemd に寄せてローカル運用の実装を小さく保つ
- 署名と検証はまず「拒否できる」最小機能から入れる

## 15. 参考にした主な一次情報

- [bootc upgrade and rollback](https://bootc-dev.github.io/bootc/upgrades.html)
- [bootc install / bootable OCI image](https://bootc-dev.github.io/bootc/bootc-install.html)
- [Podman overview](https://docs.podman.io/)
- [podman quadlet](https://docs.podman.io/en/latest/markdown/podman-quadlet.1.html)
- [podman systemd unit / Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [Linux cgroup v2](https://docs.kernel.org/5.10/admin-guide/cgroup-v2.html)
- [Linux PSI](https://docs.kernel.org/accounting/psi.html)
- [systemd-oomd](https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html)
- [Sigstore Cosign signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)
- [Sigstore Cosign verify](https://docs.sigstore.dev/cosign/verifying/verify/)
- [OpenTelemetry Collector install](https://opentelemetry.io/docs/collector/installation/)
- [NVIDIA Container Toolkit install](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.18.0/install-guide.html)
- [ROCm Docker containers](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/how-to/docker.html)
- [OpenVINO NPU device](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)

