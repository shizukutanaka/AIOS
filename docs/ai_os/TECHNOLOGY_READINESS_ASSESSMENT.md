# AI特化Linux OS 技術レディネス評価

- 作成日: 2026-04-09
- 文書種別: 技術成熟度評価 / 採用判断メモ
- 目的: AI特化Linux OSで採用を検討する技術要素について、2026-04-09時点での成熟度、製品採用可否、リスク、投入時期を判断する

## 1. 要旨

AI特化Linux OSで差別化になりそうな技術は多いが、すべてを v1 に入れると製品が不安定になる。  
本評価では、各技術を以下の3区分で整理する。

- `Ready Now`
  - v1 で採用可能
- `Pilot Now`
  - v1.5 以降で限定採用が妥当
- `Watch`
  - v2 以降の差別化候補だが、v1 主軸には置かない

この分類は、各公式ドキュメントの記述と、AI特化OSとしての実装難易度・依存関係・運用負荷を踏まえた製品設計上の推論である。

## 2. 総論

### 2.1 v1 の土台として十分成熟しているもの

- `bootc` 系の image-based update
- Podman / Quadlet
- cgroup v2
- PSI
- `systemd-oomd`
- Cosign
- OpenTelemetry Collector
- NVIDIA Container Toolkit
- ROCm コンテナ運用

### 2.2 面白いが、v1 で全面採用は早いもの

- OpenVINO NPU を軸にした NPU-first 戦略
- DAMON を使った積極的メモリ最適化
- GPU スライス QoS の全面自動化
- 小規模ノード自動拡張

### 2.3 強い差別化になるが、v2 以降が妥当なもの

- GPUDirect Storage を前提にした高速モデル展開
- CXL を前提にしたメモリ階層制御
- Confidential Containers を利用した attestation + secret release
- AI文脈継続をOS標準機能として扱うこと

## 3. 技術別評価

## 3.1 `bootc`

### 評価

- 区分: `Ready Now`
- 理由:
  - bootable OCI image を前提にしており、AI特化OSの配布モデルに相性が良い
  - 更新とロールバックの話が明確

### 製品への意味

- OS更新を「アプリ更新に近い体験」で扱える
- 金融や製造など、壊れにくい更新が求められる企業導入に向く

### リスク

- ブートローダ更新やディスク構成周辺は運用設計が必要

### 結論

- v1 採用

## 3.2 Podman / Quadlet

### 評価

- 区分: `Ready Now`
- 理由:
  - rootless
  - systemd 統合
  - Quadlet による宣言的起動

### 製品への意味

- Kubernetes を持ち込まなくても、ローカルや小規模ノード運用を整理しやすい

### リスク

- GPU runtime との接続部分はベンダー差分が残る

### 結論

- v1 採用

## 3.3 cgroup v2 / PSI / `systemd-oomd`

### 評価

- 区分: `Ready Now`
- 理由:
  - Linux標準の資源制御・圧力検知・OOM前制御として成熟している

### 製品への意味

- AIジョブの暴走やメモリ圧迫を「落ちる前に扱う」基盤になる
- 対話APIとバッチ処理の共存に効く

### リスク

- AI向けポリシーは製品側で定義が必要

### 結論

- v1 採用

## 3.4 Cosign

### 評価

- 区分: `Ready Now`
- 理由:
  - OCI artifact の署名・検証に現実的
  - v1 で最小 trust chain を作るのに十分

### 製品への意味

- モデル配布を単なるファイルコピーから、追跡可能な資産配布へ寄せられる

### リスク

- キー管理と運用フローを設計しないと形骸化する

### 結論

- v1 採用

## 3.5 OpenTelemetry Collector

### 評価

- 区分: `Ready Now`
- 理由:
  - ローカル可視化と外部監視接続の両方に使える

### 製品への意味

- AI向けOSを「監視可能なプロダクト」にしやすい

### リスク

- AI固有メトリクスの定義は自前で必要

### 結論

- v1 採用

## 3.6 NVIDIA Container Toolkit

### 評価

- 区分: `Ready Now`
- 理由:
  - NVIDIA GPU のコンテナ利用で事実上の標準

### 製品への意味

- v1 の GPU プロファイルを確実に成立させる中心技術

### リスク

- ドライババージョンとの相性管理が必要

### 結論

- v1 採用

## 3.7 ROCm コンテナ運用

### 評価

- 区分: `Ready Now`
- 理由:
  - 公式に Docker/コンテナ運用が整理されている

### 製品への意味

- NVIDIA 一択ではない企業向けの現実的な選択肢になる

### リスク

- 実機差や対応GPU世代差を吸収する必要がある

### 結論

- v1 採用

## 3.8 OpenVINO NPU

### 評価

- 区分: `Pilot Now`
- 理由:
  - 公式に NPU device と制約が整理されている
  - ただし static shape 等の実運用制限が残る

### 製品への意味

- 低電力ローカルAIの差別化には有望

### リスク

- 対応機種とワークロードが限られる
- v1 主軸にするとサポート負荷が上がる

### 結論

- v1 は実験的
- v1.5 以降で限定採用

## 3.9 DAMON

### 評価

- 区分: `Pilot Now`
- 理由:
  - Linux標準の軽量メモリアクセス監視として強い
  - ただし AI 向け自動配置ロジックは製品側でまだ大きく作る必要がある

### 製品への意味

- 将来の Fabric Memory Orchestrator の中核候補

### リスク

- 監視から制御へ進むと誤制御リスクが増える

### 結論

- v1 では参考信号
- v1.5 以降で advisory mode

## 3.10 Multi-Gen LRU

### 評価

- 区分: `Pilot Now`
- 理由:
  - メモリ圧迫時のスラッシング抑制に有用
  - ただし AI 向けデータ種別制御とはまだ距離がある

### 製品への意味

- ローカルワークステーションの体感悪化を抑える補助機能になり得る

### リスク

- AI特化OSの差別化というより、基盤チューニング色が強い

### 結論

- v1 研究継続
- v1.5 で限定採用検討

## 3.11 CXL メモリ

### 評価

- 区分: `Watch`
- 理由:
  - Linux側の driver / memory device 基盤は整いつつある
  - ただし実機普及と製品運用ノウハウはまだ限定的

### 製品への意味

- 将来の「コールドな重みや文脈の逃がし先」として非常に魅力的

### リスク

- 実機前提の検証負荷が高い
- v1 に入れると仮説先行になりやすい

### 結論

- v2 差別化候補

## 3.12 GPUDirect Storage

### 評価

- 区分: `Watch`
- 理由:
  - 公式に direct path と導入手順が整理されている
  - ただしファイルシステム、`O_DIRECT`、ドライバ、CUDA、ストレージ経路の条件が多い

### 製品への意味

- 大規模モデルのロード時間短縮で大きな差別化になり得る

### リスク

- v1 では「やれば動く」より「条件が揃えば強い」に近い

### 結論

- v2 差別化候補

## 3.13 Confidential Containers

### 評価

- 区分: `Watch`
- 理由:
  - attestation、secret release、encrypted image まで含む強い仕組み
  - ただし Kubernetes / Kata / Trustee など依存が厚く、v1 ローカルOSへ直で入れるには重い

### 製品への意味

- 規制業界向けの最強差別化候補
- Attested Model Vault の土台になる

### リスク

- TEE、guest image、secret distribution、policy運用が一気に入ってくる

### 結論

- v2 以降
- v1 では概念整備と PoC のみ

## 3.14 Bottlerocket の API-driven / host containers

### 評価

- 区分: `Pilot Now`
- 理由:
  - API-driven OS と host containers の発想は非常に参考になる
  - ただし AWS / container orchestration 寄りで、そのままAIワークステーションへは向かない

### 製品への意味

- `aiosd` と `aictl` の設計に大きな示唆がある

### リスク

- shell-less host をそのまま真似るとローカル利用が窮屈になる

### 結論

- 設計思想を採用
- 実装そのものを依存しない

## 3.15 Flatcar update-engine / Ignition

### 評価

- 区分: `Pilot Now`
- 理由:
  - passive partition への更新、reboot manager、Ignition first-boot が整理されている

### 製品への意味

- 「壊れにくい更新」と「初期構成自動化」の実践例として有益

### リスク

- そのまま採用するより、bootc 側に寄せた方が一貫性が高い

### 結論

- 発想を採用
- bootc 方向を優先

## 3.16 SUSE `transactional-update`

### 評価

- 区分: `Pilot Now`
- 理由:
  - snapshot-based update と rollback の考え方は成熟している
  - ただし AI 向けには package mutation より image mutation の方が相性が良い

### 製品への意味

- recovery-aware OS の設計参考になる

### リスク

- v1 は package mutation より image update を軸にした方が製品が単純になる

### 結論

- 参考採用
- 中核方式にはしない

## 4. v1 技術スタック推奨

### 中核

- `bootc`
- Podman
- Quadlet
- cgroup v2
- PSI
- `systemd-oomd`
- Cosign
- OpenTelemetry Collector

### GPU

- NVIDIA Container Toolkit
- ROCm container support

### 補助

- JSON/TOML ベースの `Stack` マニフェスト
- `aictl` / `aiosd`

## 5. v1.5 技術スタック候補

- DAMON advisory mode
- OpenVINO NPU 限定対応
- small-cluster join
- degraded mode automation

## 6. v2 技術スタック候補

- Confidential Containers 連携
- Trustee / attestation policy
- CXL-aware placement
- GPUDirect Storage accelerated model loading

## 7. 最終判断

2026-04-09 時点で最も現実的なのは、「v1 は bootc + Podman/Quadlet + cgroup/PSI + Cosign + GPU runtime」で確実に成立させ、その上に v1.5 と v2 で DAMON、NPU、CXL、Confidential Computing を段階的に乗せる戦略である。

差別化したい気持ちが強いほど、v1 に重い先端技術を入れたくなる。しかし、実際に勝つ製品は「壊れにくく、始めやすく、次の差別化へつなげやすい」土台を先に作ったものになる。

## 8. 参考にした主な一次情報

- [bootc](https://bootc-dev.github.io/bootc/)
- [bootc upgrades and rollback](https://bootc-dev.github.io/bootc/upgrades.html)
- [Podman systemd unit / Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [Sigstore Cosign verify](https://docs.sigstore.dev/cosign/verifying/verify/)
- [OpenTelemetry Collector install](https://opentelemetry.io/docs/collector/installation/)
- [NVIDIA Container Toolkit install](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.18.0/install-guide.html)
- [ROCm Docker containers](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/how-to/docker.html)
- [OpenVINO NPU device](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)
- [Linux DAMON](https://docs.kernel.org/6.14/admin-guide/mm/damon/index.html)
- [Linux DAMON sysfs usage](https://docs.kernel.org/6.16/admin-guide/mm/damon/usage.html)
- [Linux Multi-Gen LRU](https://docs.kernel.org/admin-guide/mm/multigen_lru.html)
- [Linux CXL](https://docs.kernel.org/5.17/driver-api/cxl/memory-devices.html)
- [GPUDirect Storage overview](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html)
- [Confidential Containers overview](https://confidentialcontainers.org/docs/overview/)
- [Confidential Containers design overview](https://confidentialcontainers.org/docs/architecture/design-overview/)
- [Confidential Containers policies](https://confidentialcontainers.org/docs/attestation/policies/)
- [Bottlerocket API-driven](https://bottlerocket.dev/en/os/1.52.x/concepts/api-driven/)
- [Bottlerocket host containers](https://bottlerocket.dev/en/os/1.51.x/concepts/host-containers/)
- [Bottlerocket updates](https://bottlerocket.dev/en/os/1.37.x/concepts/updating-bottlerocket/)
- [Flatcar update and reboot strategies](https://www.flatcar.org/docs/latest/setup/releases/update-strategies/)
- [Flatcar Ignition](https://www.flatcar.org/docs/latest/provisioning/ignition/)
- [SLE Micro transactional-update](https://documentation.suse.com/sle-micro/6.1/html/Micro-transactional-updates/index.html)

