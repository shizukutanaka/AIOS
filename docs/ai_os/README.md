# AI Native Linux OS Docs

## 文書一覧

- [AI特化Linux OS 仕様書](../AI_NATIVE_LINUX_OS_SPEC.md)
- [AI特化Linux OS エンタープライズ製品仕様書](AI_NATIVE_LINUX_OS_ENTERPRISE_SPEC.md)
- [AI特化Linux OS ローカル運用・内蔵オーケストレーション仕様](LOCAL_ORCHESTRATION_SPEC.md)
- [AI向けOS 比較調査メモ](OS_COMPARATIVE_RESEARCH.md)
- [AI向けOS 意思決定マトリクスとロードマップ](OS_DECISION_MATRIX_AND_ROADMAP.md)
- [AI特化Linux OS v1 製品要求仕様書](PRODUCT_REQUIREMENTS_V1.md)
- [AI特化Linux OS MVP 実装バックログ](MVP_IMPLEMENTATION_BACKLOG.md)
- [AI特化Linux OS 技術レディネス評価](TECHNOLOGY_READINESS_ASSESSMENT.md)
- [AI特化Linux OS 技術DDメモ](TECHNICAL_DUE_DILIGENCE_MEMO.md)

## API

- [aiosd OpenAPI](aiosd-openapi.yaml) — **the API that actually ships** (30 endpoints)
- [Runtime Broker OpenAPI](runtime-broker.openapi.yaml) — broker subset of the above
- [Control Plane OpenAPI](control-plane.openapi.yaml) — **design specification, not implemented** (11 of 13 paths have no code)

## 宣言ファイル例

> これらは **設計仕様（`aios/v1alpha1`）の例** であり、未実装 API を対象とする。`aictl apply` が受け付ける実際の形式は
> [examples/stack.local-rag.yaml](../../examples/stack.local-rag.yaml) を参照。

- [FabricPolicy 例](examples/fabric-policy.enterprise.yaml)
- [ModelBundle 例](examples/model-bundle.attested.yaml)
- [InferenceService 例](examples/workload.low-latency.yaml)
- [TenantClass 例](examples/tenant-class.regulated.yaml)
- [Local RAG Stack 例](examples/stack.local-rag.yaml)
- [Home Lab Cluster 例](examples/cluster.home-lab.yaml)

## 比較データ

- [OSスコアカードCSV](data/os_scorecard.csv)
- [機能リリースマトリクスCSV](data/feature_release_matrix.csv)
- [技術レディネスCSV](data/technology_readiness.csv)

## ねらい

このディレクトリは、AI特化Linux OSの製品像を、上位構想だけでなく、制御APIと宣言的運用モデルまで一貫して追えるように整理したものである。
