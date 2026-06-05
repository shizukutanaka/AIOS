# AI向けOS 意思決定マトリクスとロードマップ

- 作成日: 2026-04-09
- 目的: 既存OS比較から、AI特化Linux OSで採用・不採用・先送りにすべき要素を決め、段階導入順を定義する

## 1. 結論

2026年4月9日時点の一次情報と既存OS比較から見て、AI特化Linux OSの勝ち筋は次の組み合わせにある。

- Ubuntu Server の現実的な互換性
- Ubuntu Core、Silverblue、CoreOS、bootc の壊れにくい更新
- NixOS の再現性と宣言性
- Talos の API 駆動と最小構成
- ChromeOS の Verified Boot と復旧導線
- macOS のメモリ階層を単純に見せる発想
- Windows ML の実行プロバイダ自動接続の発想

ただし、これらをそのまま足し算すると製品は重く複雑になる。よって、v1 では「企業価値に直結するものだけ採る」「AI以外の便利機能は切る」が必要である。

以下の採否判断は、公開ドキュメントの記述とそこからの製品設計上の推論に基づく。

## 2. 必須要素の優先度

## 2.1 P0

P0 は、v1 に入らないと製品の核が弱くなる要素である。

- Immutable / atomic update
- Rollback
- cgroup v2
- PSI
- OOM 事前制御
- OCI ベースのOS更新または配布
- rootless コンテナ
- OpenAI互換ローカルAPI
- ローカル1台で完結する運用
- 宣言的 `Stack` 運用
- GPU/NPU runtime 自動検出
- 署名済みモデル/コンテナの検証
- AI固有メトリクス可視化

## 2.2 P1

P1 は、v1.5 から v2 で強い差別化になる要素である。

- 文脈継続
- モデル暗号化配布
- Attestation による鍵解放
- GPU スライスの QoS 制御
- Fabric Memory 制御
- CXL を前提にしたコールドデータ配置
- 小規模クラスターへの自動拡張

## 2.3 P2

P2 は将来価値は高いが、v1 で抱えると複雑さが勝ちやすい。

- 汎用 VRAM デデュープ
- マルチノード学習クラスタ統合
- Kubernetes フル互換制御プレーン
- GUI前提の一般OS体験
- ホスト直置きの多フレームワーク共存

## 3. 採用・適応・不採用・先送りマトリクス

## 3.1 Ubuntu Server

### 採用

- ハードウェア互換性重視
- 実務向けの導入しやすさ
- 幅広い管理系ツールとの親和性

### 適応

- パッケージ追加前提を、AIランタイムはコンテナ前提へ変更

### 不採用

- 汎用サーバーとしての全部入り発想

### 先送り

- 一般用途向けの豊富なデフォルトパッケージ

## 3.2 Ubuntu Core

### 採用

- image-based
- immutable
- transaction-based
- recovery

### 適応

- snap 中心を OCI / container first に寄せる

### 不採用

- アプリ配布を snap に固定すること

### 先送り

- IoT/組み込み向けの細かなモデル差分対応

## 3.3 Fedora Silverblue

### 採用

- `ostree` / `rpm-ostree` 的な壊れにくいOS更新
- ホスト汚染を抑える思想

### 適応

- デスクトップ寄りの運用思想をヘッドレスAI運用へ変換

### 不採用

- GUI中心の前提

### 先送り

- デスクトップワークフロー最適化

## 3.4 Fedora CoreOS / bootc

### 採用

- OCI を使ったOS更新
- prebuilt image
- first-boot provisioning
- auto-update と rollback

### 適応

- クラスタ向け最小OSを、ローカル1台から使える形へ緩和

### 不採用

- すべてをクラウド/クラスタ中心に設計すること

### 先送り

- 大規模クラスタ前提のチューニング

## 3.5 NixOS

### 採用

- 宣言的設定
- ロールバック
- 再現性

### 適応

- 独自DSLではなく、狭いAI専用マニフェストへ落とす

### 不採用

- 学習コストの高い設定言語をプロダクト前面に出すこと

### 先送り

- 完全なOS構成DSL

## 3.6 Talos Linux

### 採用

- API 駆動
- minimal
- immutable
- single declarative configuration

### 適応

- no SSH の厳しさを緩め、ローカル保守経路を安全に残す

### 不採用

- Kubernetes 前提

### 先送り

- 完全なシェルレス運用

## 3.7 ChromeOS / ChromiumOS

### 採用

- Verified Boot
- known-good recovery
- 改ざん検知と復旧の分離

### 適応

- AIモデル、文脈、キャッシュに対して同様の信頼連鎖を拡張

### 不採用

- 一般ユーザー向けの閉じた端末設計

### 先送り

- ハードウェアと一体化した高度な消費者向けUX

## 3.8 macOS on Apple Silicon

### 採用

- メモリ階層を単純に見せる開発者体験
- ローカルAI利用時の一体感

### 適応

- Linux側で `VRAM -> DRAM -> CXL -> NVMe` を自然に見せる Fabric abstraction を作る

### 不採用

- ベンダー固定の垂直統合前提

### 先送り

- Apple並みの完全統一メモリ体験

## 3.9 Windows ML

### 採用

- 実行プロバイダの自動発見
- 共有ランタイム
- ハードウェアに応じた配布/登録

### 適応

- Linux では `AI Runtime Broker` として実装

### 不採用

- Windows Update 依存の配布モデル

### 先送り

- すべてのランタイム更新をOS配布に統合すること

## 4. 採るべき具体機能

## 4.1 v1 に入れる

- `AI Runtime Broker`
  - NVIDIA / AMD / Intel 系 runtime の存在確認と選択
- `Local-first Orchestrator`
  - 1台のPCから始められる `aictl` + `Stack`
- `Image-based OS Update`
  - `bootc` 的なトランザクショナル更新
- `Signed Artifact Policy`
  - OS / container / model の署名検証
- `AI Observability`
  - VRAM、tokens/sec、model load time、queue
- `Rollback-safe Upgrades`
  - 更新失敗時の前版復帰

## 4.2 v1.5 に入れる

- `QoS Slice Broker`
- `Context Continuity Engine`
- `Degraded Mode Planner`
- `Small Cluster Join`

## 4.3 v2 に入れる

- `Fabric Memory Orchestrator`
- `Attested Model Vault`
- `CXL-aware cold placement`
- `Cross-node context resume`

## 5. 捨てるべきもの

以下は「できるが入れない」判断を推奨する。

- デスクトップOSとしての完成度競争
- 一般ユーザー向けアプリ群の標準搭載
- Kubernetes並みの大きな制御面をv1で抱えること
- すべてのAIフレームワークをホスト直置きすること
- 古い互換性のための広すぎるレイヤ

## 6. ロードマップ

## 6.1 v1

テーマ:

- 1台ですぐ動く
- 壊れにくい
- GPUを正しく使える

到達目標:

- `bootc` または同等の image-based 更新
- `aictl init`, `aictl apply`, `aictl doctor`
- OpenAI互換ローカルAPI
- GPU/NPU runtime 自動検出
- 署名済みモデル検証
- AIメトリクスの基本可視化

## 6.2 v1.5

テーマ:

- ローカル利用から部門運用へ伸ばす

到達目標:

- ノードペアリング
- 小規模クラスター
- QoS制御
- 文脈継続
- 自動縮退

## 6.3 v2

テーマ:

- 規制業界向けの強い差別化

到達目標:

- Attestation 前提のモデル利用
- 暗号化モデル配布
- Fabric メモリ制御
- CXL活用
- 高度な監査証跡

## 6.4 v3

テーマ:

- 大手企業が社内標準として全面採用できるOS

到達目標:

- 拠点横断運用
- ポリシー統合
- 全社監査統合
- 高信頼アップグレード

## 7. リスクと対策

## 7.1 リスク

- NPU サポートがベンダー依存で揺れる
- CXL の実機普及がまだ限定的
- 署名やAttestationを入れすぎると導入障壁が上がる
- v1 で差別化機能を詰め込みすぎると、導入性が落ちる

## 7.2 対策

- v1 は GPU 中心で完成させる
- NPU は対応機種リスト型で管理する
- セキュリティ強化は段階導入にする
- ローカル体験を絶対に損なわない

## 8. 最終判断

AI特化Linux OSは、既存OSの勝っている要素を全部入れるべきではない。必要なのは、以下の4つの軸で勝つことである。

- 更新しても壊れにくい
- AI実行が始めやすい
- GPU/NPU/メモリを賢く使える
- 企業が安心して社内標準にできる

この観点では、v1 の主役は「ローカル運用の簡単さ」と「壊れにくいAI実行基盤」であり、v2 以降で「Attestation」「CXL」「文脈継続」を広げるのが最も現実的である。

## 9. 根拠にした主な一次情報

- Ubuntu Core documentation
  - <https://documentation.ubuntu.com/core/>
- Inside Ubuntu Core
  - <https://documentation.ubuntu.com/core/explanation/core-elements/inside-ubuntu-core/>
- Fedora Silverblue technical information
  - <https://docs.fedoraproject.org/pt_BR/fedora-silverblue/technical-information/>
- Fedora CoreOS auto-updates and rollback
  - <https://docs.fedoraproject.org/id/fedora-coreos/auto-updates/>
- Fedora CoreOS supported platforms / first-boot provisioning
  - <https://docs.fedoraproject.org/id/fedora-coreos/platforms/>
- bootc introduction
  - <https://bootc-dev.github.io/bootc/>
- bootc upgrades and rollback
  - <https://bootc-dev.github.io/bootc/upgrades.html>
- NixOS Manual
  - <https://nixos.org/manual/nixos/stable/>
- Talos Linux introduction
  - <https://www.talos.dev/docs/latest/introduction/what-is-talos/>
- ChromiumOS Verified Boot
  - <https://www.chromium.org/chromium-os/chromiumos-design-docs/verified-boot/>
- Apple Metal resource storage modes
  - <https://developer.apple.com/documentation/metal/choosing-a-resource-storage-mode-for-apple-gpus>
  - <https://developer.apple.com/documentation/metal/choosing-a-resource-storage-mode-for-intel-and-amd-gpus>
- Windows ML overview
  - <https://learn.microsoft.com/en-gb/windows/ai/new-windows-ml/overview>
- Windows ML execution provider install
  - <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers>
- Windows ML get started
  - <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/get-started>
