# AI向けOS 比較調査メモ

- 作成日: 2026-04-09
- 目的: AI特化Linux OSの設計に必要な要素を洗い出し、既存OSの長所短所から改善案を抽出する

## 1. AI向けOSに必要なもの

AI向けOSで重要なのは、一般的な「使いやすいOS」よりも、巨大モデル、アクセラレータ、更新、隔離、観測性をどう扱うかである。

### 1.1 必須要素

- ハードウェア抽象化
  - GPU、NPU、NVMe、CXL、ネットワークをOSが把握し、実行時に適切な経路へ載せる
- 資源制御
  - cgroup v2、PSI、OOM事前制御で、AIジョブを止める前に縮退や隔離ができる
- メモリ観測と再配置
  - DAMON、ページ移動、メモリ階層管理により、重み、KVキャッシュ、RAGキャッシュを温度別に置ける
- 高速I/O
  - `io_uring` やバッファ登録を活かし、大きなモデルを低CPU負荷で流せる
- Immutable更新
  - 失敗時に巻き戻せる更新モデルが必要
- 宣言的運用
  - サービス、モデル、キャッシュ、文脈、GPUスライスを宣言で扱えること
- セキュアブートと真正性
  - 署名済みOS、署名済みコンテナ、署名済みモデルをつなげて扱えること
- ローカル優先の運用性
  - 1台ですぐ始められ、そのまま小規模クラスターへ伸ばせること
- AI固有の観測性
  - CPUやメモリだけでなく、VRAM、tokens/sec、モデルロード時間、キュー長、再開可能文脈を見られること

### 1.2 あえて不要にすべき要素

- 重いデスクトップ環境の常駐
- 汎用オフィス用途の標準搭載
- ホストOSへの巨大MLライブラリの直置き
- AI用途に不要な常駐デーモン
- 手作業前提の複雑な環境構築

## 2. 既存OSの比較

## 2.1 Ubuntu Server

### 長所

- 汎用サーバーOSとして対応範囲が広い
- インストールと自動化の導線が整っている
- セキュリティ、コンテナ、仮想化、可観測性まで広く文書化されている

### 短所

- 汎用性が高い反面、AI専用の最適化は薄い
- 依存関係やパッケージの積み上げでホストが太りやすい
- モデル、GPU、文脈、推論SLOはOSの第一級概念ではない

### 改善案

- Ubuntu Serverの広いハードウェア互換性は残しつつ、ホストをImmutable化する
- 汎用パッケージ管理ではなく、AIランタイムはコンテナ配布へ寄せる

## 2.2 Ubuntu Core

### 長所

- image-based、immutable、transaction-based の設計が明確
- サンドボックスと回復モードが強い
- 組み込みや専用機向けの運用モデルがある

### 短所

- snap中心で、AI開発ワークフローとの相性に工夫が要る
- ローカルAI開発用ワークステーションとしては窮屈になりやすい
- GPU/NPU中心の実行基盤としては抽象度が高すぎる

### 改善案

- 更新、回復、分離の考え方は採用する
- ただしアプリ配布は snap 固定ではなく OCI も第一級にする

## 2.3 Fedora Silverblue

### 長所

- `ostree` と `rpm-ostree` による読み取り専用寄りの設計
- ルートが読み取り専用で壊れにくい
- Toolbox や Flatpak でホスト汚染を抑えやすい

### 短所

- GUI寄りの思想が強く、AIサーバー/ヘッドレス前提ではない
- パッケージ layering は便利だが、再起動や運用理解が必要
- AI専用のメモリ制御やモデル運用機能は持たない

### 改善案

- Silverblue の「壊れにくいデスクトップ」思想を「壊れにくいAI実行基盤」へ転用する
- 開発用コンテナと本番用コンテナを同じ仕組みで扱えるようにする

## 2.4 Fedora CoreOS / bootc 系

### 長所

- Ignition による初期構成が明快
- 自動更新やイメージ更新の方向性が強い
- `bootc` により、OCIイメージでOS更新する流れが見えている

### 短所

- クラウド、コンテナ、サーバー向けに最適化されており、ローカル利用の親しみやすさは弱い
- 既定では AIワークステーション体験が前面に出ていない
- モデル管理やGPUスライス管理は別実装が必要

### 改善案

- OS更新は `bootc` 方向を採用する
- その上に、ローカルレシピ、モデルキャッシュ、推論サービスを標準化する

## 2.5 NixOS

### 長所

- 宣言的で再現性が高い
- ロールバックと構成管理が強い
- 「OS状態もコードで持つ」思想が徹底している

### 短所

- 学習コストが高い
- GPUドライバやAIスタックでは、一般利用者には少し難しく感じやすい
- 宣言力は強いが、AI固有の抽象はまだ薄い

### 改善案

- NixOSの再現性は取り込みたい
- ただしユーザー向けには Nix のような独自言語ではなく、AI専用の狭い宣言モデルに絞る

## 2.6 Talos Linux

### 長所

- API駆動、immutable、minimal の一貫性が非常に高い
- SSHやシェルを削ることで攻撃面と揺らぎを減らしている
- Kubernetesノードとしての規律が強い

### 短所

- 汎用OSではなく、Kubernetes前提の思想が強い
- ローカル1台の探索的利用には厳しい
- AI研究や試行錯誤には、完全API管理が重く感じられる場面がある

### 改善案

- TalosのAPI駆動思想は採用する
- ただし SSH ゼロではなく、ローカル限定の安全な保守経路は残す
- Kubernetes前提ではなく、単一ノード first にする

## 2.7 ChromeOS / ChromiumOS

### 長所

- Verified Boot と回復導線が非常に強い
- 改ざん検知と既知良好状態への復帰思想が明快
- アップデートと信頼の連鎖をOS設計で扱っている

### 短所

- 一般的なローカルAIサーバー用途には閉じすぎている
- 高度なGPU/NPU制御やAI研究用途の自由度は低い
- ユーザー状態と検証済み状態の分離で、AI文脈継続は別設計が必要

### 改善案

- モデル署名とAttestationの発想に Verified Boot の考え方を持ち込む
- ただしユーザーデータとAI文脈は、復旧可能な別レイヤとして扱う

## 2.8 macOS on Apple Silicon

### 長所

- CPU/GPUの共有メモリ設計がローカルAIに非常に効く
- メモリ配置モデルが開発者にわかりやすい
- 単体マシンでの体験が強い

### 短所

- OSレベルの自由度が低く、Linux系のような深い制御は難しい
- GPU/NPU制御やサーバー展開の自由度は限定的
- ベンダー依存が強い

### 改善案

- Apple Silicon の「統一メモリを前提にした開発者体験」は強く参考にする
- Linux側では VRAM/DRAM/CXL/NVMe を Apple の共有メモリほど自然に見せる抽象化が必要

## 2.9 Windows ML / Windows系AI実行基盤

### 長所

- 実行プロバイダの発見、取得、登録をOS側に近い位置で吸収している
- ハードウェア差異を吸収する方向性が明確

### 短所

- AI実行基盤の統一には近いが、OS全体の更新モデルや隔離と一体ではない
- Linuxベースのサーバー文化とは接続しづらい

### 改善案

- 「実行プロバイダをOSが見つけてつなぐ」発想は取り入れる
- Linux版では GPU/NPU runtime broker として内蔵する

## 3. 結論として採るべき設計

以下は、上記比較からの推論である。

- Ubuntu Server からは互換性と現実的な導入性を採る
- Ubuntu Core、Silverblue、CoreOS、bootc からは immutable 更新と回復性を採る
- NixOS からは宣言的・再現性の発想を採る
- Talos からは API駆動と最小構成の規律を採る
- ChromeOS からは Verified Boot と回復導線を採る
- macOS からはメモリ階層を単純に見せるUXを採る
- Windows ML からは実行プロバイダ自動接続の発想を採る

## 4. 具体的な改善案

- `AI Runtime Broker`
  - GPU/NPU runtime の検出、導入、切替をOS内サービスとして提供する
- `Memory Fabric Manager`
  - VRAM、DRAM、CXL、NVMe をAIデータ温度に応じて再配置する
- `Model Trust Chain`
  - OS署名、コンテナ署名、モデル署名、Attestation を一続きで扱う
- `Local-first Orchestrator`
  - 1台で始められ、同じ宣言で数台へ伸ばせる
- `Context Continuity`
  - 更新や障害でもAI文脈を守る
- `Recovery-aware AI OS`
  - 改ざんや更新失敗時でも、モデルと文脈を適切に保ちながら戻せる

## 5. この調査から見えた重要判断

- v1は「万能OS」より「AI実行に必要な要素を正しく限定したOS」にするべき
- 競争力の源泉は、単なる推論速度ではなく、更新、再現性、運用性、信頼性の統合にある
- 買収候補になるには、高性能機能だけでなく、導入の簡単さと運用の簡単さを同時に満たす必要がある

## 6. 参考にした主な公式情報

- Linux Kernel cgroup v2
  - <https://docs.kernel.org/5.10/admin-guide/cgroup-v2.html>
- Linux Kernel PSI
  - <https://docs.kernel.org/accounting/psi.html>
- Linux Kernel DAMON
  - <https://docs.kernel.org/6.16/admin-guide/mm/damon/usage.html>
- io_uring
  - <https://man7.org/linux/man-pages/man2/io_uring_setup.2.html>
  - <https://man7.org/linux/man-pages/man2/io_uring_register.2.html>
- systemd-oomd
  - <https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html>
- Linux CXL
  - <https://docs.kernel.org/6.17/driver-api/cxl/index.html>
- Ubuntu Server
  - <https://documentation.ubuntu.com/server/>
- Ubuntu Core
  - <https://documentation.ubuntu.com/core/>
- Fedora Silverblue
  - <https://docs.fedoraproject.org/nn/fedora-silverblue/getting-started/>
  - <https://docs.fedoraproject.org/pt_BR/fedora-silverblue/technical-information/>
- Fedora CoreOS
  - <https://docs.fedoraproject.org/id/fedora-coreos/platforms/>
  - <https://docs.fedoraproject.org/pt_BR/fedora-coreos/major-changes/>
- bootc
  - <https://bootc-dev.github.io/bootc/>
- NixOS Manual
  - <https://nixos.org/manual/nixos/stable/>
- Talos Linux
  - <https://www.talos.dev/latest/introduction/what-is-talos/>
  - <https://www.talos.dev/v1.10/learn-more/architecture/>
  - <https://www.talos.dev/v1.10/learn-more/faqs/>
- ChromiumOS Verified Boot
  - <https://www.chromium.org/chromium-os/chromiumos-design-docs/verified-boot/>
  - <https://www.chromium.org/chromium-os/chromiumos-design-docs/verified-boot-data-structures/>
- Apple Metal memory model
  - <https://developer.apple.com/documentation/metal/choosing-a-resource-storage-mode-for-apple-gpus>
  - <https://developer.apple.com/documentation/metal/choosing-a-resource-storage-mode-for-intel-and-amd-gpus>
- Windows ML
  - <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/get-started>
