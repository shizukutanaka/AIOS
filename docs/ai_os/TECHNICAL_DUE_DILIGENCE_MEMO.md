# AI特化Linux OS 技術DDメモ

- 作成日: 2026-04-09
- 文書種別: 技術デューデリジェンスメモ
- 目的: 買収・投資・社内新規事業判断のため、技術的な強み、弱み、実現可能性、主要リスクを簡潔に整理する

## 1. 投資仮説

本製品は、「AIを動かせるLinux」ではなく、「AI運用の面倒をOSに押し込む」ことに価値がある。

差別化の核は以下にある。

- AI向けに壊れにくい更新
- ローカル1台から始められる内蔵オーケストレーション
- モデル供給の信頼連鎖
- 将来の CXL / Attestation / GPUDirect Storage を受け止める拡張余地

## 2. 技術的に評価すべき強み

### 2.1 市場の隙間を突いている

- Kubernetes や大規模MLOps基盤ほど重くない
- 一方で普通の Ubuntu + Docker より運用一貫性が高い
- 「1台で価値が出る」ため、PoCの立ち上がりが速い

### 2.2 既存技術の組み合わせ方が良い

- `bootc`
- Podman / Quadlet
- cgroup v2 / PSI / `systemd-oomd`
- Cosign
- OTel

これらは単体では既存技術だが、「AI専用OS」として結合すると製品価値が出る。

### 2.3 将来差別化の伸びしろがある

- Fabric Memory
- Attested Model Vault
- QoS Slice Broker
- Context Continuity
- Direct-to-Accelerator Streaming

これらは現時点では未成熟でも、製品ストーリーとして連続性がある。

## 3. 技術的な弱み

### 3.1 v1 はまだ moat が薄い

- v1 の中心は「組み合わせの巧さ」であり、強い独占技術ではない
- 買う側から見ると「内製でも作れるのでは」という疑念が残る

### 3.2 GPU依存が強い

- v1 は NVIDIA / AMD GPU 前提で成立させるのが現実的
- NPU や CXL が入るのはまだ先

### 3.3 高度差別化は実装負荷が高い

- Attestation
- GPUDirect Storage
- CXL-aware placement
- 文脈継続

このあたりは v2 以降でないと、製品の安定性を損ねやすい

## 4. 買い手視点の評価ポイント

### 4.1 買う価値がある理由

- 企業内PoCを横展開しやすい
- 既存クラウド/オンプレ/GPU事業に接続しやすい
- セキュアなローカルAI基盤という説明がしやすい
- 差別化機能のロードマップが明快

### 4.2 買わない理由になりうる点

- v1 だけでは、まだ「よくまとまった基盤」止まりに見える可能性
- 実コードより文書の比率が高いと、実現力が読みにくい
- 強い運用導線と最小実装が無いと、机上構想に見えやすい

## 5. 技術リスク

### 5.1 高リスク

- CXL 前提のメモリ戦略
- Attestation / confidential guest の標準化
- cross-node context resume

### 5.2 中リスク

- ROCm 実機差分
- OpenVINO NPU 制約
- GPUDirect Storage の環境依存

### 5.3 低リスク

- Podman / Quadlet ベースのローカル運用
- image-based update
- Cosign 検証
- OTel 収集

## 6. 直近で価値を証明するために必要なもの

買収候補として説得力を出すには、以下が必要である。

- `aictl init`
- `aictl doctor`
- `aictl recipe run local-chat`
- `aictl apply -f stack.json`
- `aictl ps`
- `aictl upgrade plan`

つまり、文書だけでなく「触れる最小体験」が必要である。

## 7. 推奨判断

### 技術としての結論

- 続ける価値は十分ある
- ただし差別化技術を v1 に詰め込まず、土台を先に成立させるべき

### 買収価値としての結論

- v1 の最小実装が存在し、PoCで1台導入から価値が見えれば、有望な買収対象になり得る
- 特に GPU 事業者、クラウド事業者、大手エンタープライズソフト企業との相性が良い

## 8. 次のアクション

- `aictl` 最小プロトタイプの実装
- `Stack` schema の実コード化
- ローカル状態管理と profile detection の実装
- signed model policy の最小実装
- demo 用の `local-chat` recipe 実行導線整備

## 9. 参考にした主な一次情報

- [bootc](https://bootc-dev.github.io/bootc/)
- [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [Bottlerocket API-driven](https://bottlerocket.dev/en/os/1.52.x/concepts/api-driven/)
- [Flatcar update and reboot strategies](https://www.flatcar.org/docs/latest/setup/releases/update-strategies/)
- [SLE Micro transactional-update](https://documentation.suse.com/sle-micro/6.1/html/Micro-transactional-updates/index.html)
- [Confidential Containers overview](https://confidentialcontainers.org/docs/overview/)
- [GPUDirect Storage overview](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html)

