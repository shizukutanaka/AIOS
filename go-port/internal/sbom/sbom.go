// Package sbom implements CycloneDX 1.5 SBOM generation from the running
// binary's runtime/debug.ReadBuildInfo() metadata.
//
// 設計判断 (security spec S1.4):
//   - ゼロ依存(ADR-0001 維持): cyclonedx-go ライブラリは使わず、CycloneDX 1.5 JSON
//     スキーマを最小サブセットで自前実装
//   - 出力は CycloneDX 1.5 JSON 形式(2026 年現在広く受容されているバージョン)
//   - 自己記述: 動作中の yagura 自身がメイン component、deps[] に Go module 情報
//   - SLSA L3 build provenance + cosign signature と組み合わせて Supply Chain
//     完全可視化を実現
//
// 用途:
//   - リリース時の `/sbom` 配布(consumer が依存を確認可能)
//   - 連続生成: scanner が起動時と日次で生成、整合性 drift を検出
//   - SBOM 形式は cosign attestation の predicate として attach 可能
//
// 参考: https://cyclonedx.org/docs/1.5/json/
package sbom

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"runtime"
	"runtime/debug"
	"sort"
	"strings"
	"time"
)

// Bom は CycloneDX 1.5 JSON のトップレベル構造。
//
// CycloneDX 1.5 仕様の中で SBOM 生成に必要な最小サブセットのみを実装。
// HBOM (Hardware) / OBOM (Operations) / VEX(vulnerability) は対象外。
type Bom struct {
	BomFormat    string      `json:"bomFormat"`             // "CycloneDX"
	SpecVersion  string      `json:"specVersion"`           // "1.5"
	SerialNumber string      `json:"serialNumber"`          // "urn:uuid:..."
	Version      int         `json:"version"`               // 1 (incremented on regeneration)
	Metadata     Metadata    `json:"metadata"`              // 生成情報
	Components   []Component `json:"components,omitempty"` // 依存ライブラリ群
	Dependencies []Dep       `json:"dependencies,omitempty"`
}

// Metadata は SBOM の生成情報。
type Metadata struct {
	Timestamp string     `json:"timestamp"`           // RFC3339
	Tools     []Tool     `json:"tools,omitempty"`     // SBOM 生成ツール
	Component *Component `json:"component,omitempty"` // SBOM の主題(yagura 自身)
}

// Tool は SBOM 生成に使ったツール。
type Tool struct {
	Vendor  string `json:"vendor,omitempty"`
	Name    string `json:"name"`
	Version string `json:"version,omitempty"`
}

// Component は SBOM の構成要素(application or library or framework)。
type Component struct {
	BomRef     string         `json:"bom-ref,omitempty"`    // 内部参照 ID
	Type       string         `json:"type"`                 // "application" | "library" | "framework"
	Name       string         `json:"name"`
	Version    string         `json:"version,omitempty"`
	PackageURL string         `json:"purl,omitempty"`       // "pkg:golang/path@version"
	Scope      string         `json:"scope,omitempty"`      // "required" | "optional" | "excluded"
	Hashes     []Hash         `json:"hashes,omitempty"`
	Licenses   []LicenseEntry `json:"licenses,omitempty"`
}

// Hash はコンポーネントの整合性チェック値。
type Hash struct {
	Alg     string `json:"alg"`     // "SHA-256" 等
	Content string `json:"content"` // hex
}

// LicenseEntry は CycloneDX 1.5 の license 構造体。
type LicenseEntry struct {
	License *License `json:"license,omitempty"`
}

// License はライセンス情報。id / name のいずれかを設定。
type License struct {
	ID   string `json:"id,omitempty"`   // SPDX ID(例: "MIT")
	Name string `json:"name,omitempty"` // ID が無いカスタムライセンス用
}

// Dep は components 間の依存関係グラフ。
type Dep struct {
	Ref       string   `json:"ref"`
	DependsOn []string `json:"dependsOn,omitempty"`
}

// ─── Generator ───────────────────────────────────────────────

// Generator は SBOM を生成する。fixed time + fixed serial 注入で
// テストの reproducibility を確保。
type Generator struct {
	// NowFn は生成時刻フック(テスト時固定用)。nil なら time.Now。
	NowFn func() time.Time
	// SerialFn は UUID 生成フック(テスト時固定用)。nil なら crypto/rand。
	SerialFn func() string
}

// New は標準 Generator を返す。
func New() *Generator {
	return &Generator{}
}

// Generate は runtime/debug.ReadBuildInfo() を元に CycloneDX 1.5 BOM を生成する。
//
// 戻り値:
//   - bom: 生成された SBOM(JSON marshal 可能)
//   - err: BuildInfo が取得不能な場合(static binary でない等)
//
// 主 component の name は mainPath の最後セグメント(例: "yagura")、
// version は mainVersion。test 環境では bi.Main.Path が空になるため、
// 明示的に main module path を渡せるよう引数化している。
//
// 呼出側の典型(main.go):
//
//	bom, err := sbom.New().Generate("github.com/shizukutanaka/yagura", "0.8.0")
func (g *Generator) Generate(mainPath, mainVersion string) (*Bom, error) {
	bi, ok := debug.ReadBuildInfo()
	if !ok {
		return nil, fmt.Errorf("debug.ReadBuildInfo failed: binary not built with module support")
	}

	now := g.now()
	serial := g.serial()

	// main path: 明示指定 > BuildInfo の Main.Path > "unknown"
	if mainPath == "" {
		mainPath = bi.Main.Path
	}
	if mainPath == "" {
		mainPath = "unknown"
	}

	// main component
	mainRef := purl(mainPath, mainVersion)
	mainComp := &Component{
		BomRef:     mainRef,
		Type:       "application",
		Name:       extractName(mainPath),
		Version:    mainVersion,
		PackageURL: mainRef,
		Scope:      "required",
		Licenses: []LicenseEntry{
			{License: &License{ID: "MIT"}}, // Yagura is MIT
		},
	}

	// components: Go toolchain + 全 module deps(yagura は zero-dep なので
	// Go toolchain のみが主要 component になる想定)
	components := []Component{
		{
			BomRef:     "pkg:generic/golang@" + bi.GoVersion,
			Type:       "framework",
			Name:       "golang",
			Version:    bi.GoVersion,
			PackageURL: "pkg:generic/golang@" + bi.GoVersion,
			Scope:      "required",
			Licenses: []LicenseEntry{
				{License: &License{ID: "BSD-3-Clause"}},
			},
		},
	}

	// dependency graph: main → all deps
	mainDeps := []string{"pkg:generic/golang@" + bi.GoVersion}

	depComps, depRefs := depComponents(bi.Deps)
	components = append(components, depComps...)
	mainDeps = append(mainDeps, depRefs...)

	// stable sort for reproducibility(必須: 同じ binary なら同じ SBOM)
	sort.SliceStable(components, func(i, j int) bool {
		return components[i].BomRef < components[j].BomRef
	})
	sort.Strings(mainDeps)

	return &Bom{
		BomFormat:    "CycloneDX",
		SpecVersion:  "1.5",
		SerialNumber: serial,
		Version:      1,
		Metadata: Metadata{
			Timestamp: now.UTC().Format(time.RFC3339),
			Tools: []Tool{{
				Vendor:  "Sovereign Computing Stack",
				Name:    "yagura/internal/sbom",
				Version: mainVersion,
			}},
			Component: mainComp,
		},
		Components: components,
		Dependencies: []Dep{
			{Ref: mainRef, DependsOn: mainDeps},
		},
	}, nil
}

// JSON は BOM を CycloneDX 1.5 JSON 形式に marshal する。
//
// インデント 2 スペース(human readable + diff friendly)。
func (b *Bom) JSON() ([]byte, error) {
	return json.MarshalIndent(b, "", "  ")
}

// depComponents は module deps を CycloneDX component + purl ref に変換する。
//
// yagura 本体は zero-dep(ADR-0001)なので production では deps は常に空だが、
// 万一 dep が入った瞬間に正しく動く必要がある supply-chain ロジック
// (replacement chain の解決・Go module checksum の添付・nil 安全)なので、
// Generate から切り出して合成 []*debug.Module で直接テストできるようにしている。
func depComponents(deps []*debug.Module) (components []Component, refs []string) {
	for _, dep := range deps {
		// nil safety
		if dep == nil || dep.Path == "" {
			continue
		}
		// indirect な replacement chains を考慮
		actual := dep
		for actual.Replace != nil {
			actual = actual.Replace
		}
		ref := purl(actual.Path, actual.Version)
		comp := Component{
			BomRef:     ref,
			Type:       "library",
			Name:       extractName(actual.Path),
			Version:    actual.Version,
			PackageURL: ref,
			Scope:      "required",
		}
		// Go module checksum を hash として添付(supply chain verification)
		if actual.Sum != "" {
			comp.Hashes = []Hash{{
				Alg:     "Go-Module-Sum",
				Content: actual.Sum,
			}}
		}
		components = append(components, comp)
		refs = append(refs, ref)
	}
	return components, refs
}

// ─── helpers ─────────────────────────────────────────────────

// now は時刻取得(テスト hook 経由 or time.Now)。
func (g *Generator) now() time.Time {
	if g.NowFn != nil {
		return g.NowFn()
	}
	return time.Now()
}

// serial は serialNumber 生成(テスト hook 経由 or crypto/rand UUID)。
// CycloneDX 1.5 仕様により "urn:uuid:" prefix 必須。
func (g *Generator) serial() string {
	if g.SerialFn != nil {
		return g.SerialFn()
	}
	return "urn:uuid:" + randomUUIDv4()
}

// randomUUIDv4 は UUID v4 を crypto/rand から生成する。
// 外部ライブラリに依存しない最小実装。
func randomUUIDv4() string {
	var b [16]byte
	rand.Read(b[:])
	// UUID v4 ビット設定 (RFC 4122)
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant bits
	hexed := hex.EncodeToString(b[:])
	return fmt.Sprintf("%s-%s-%s-%s-%s",
		hexed[0:8], hexed[8:12], hexed[12:16], hexed[16:20], hexed[20:32])
}

// purl は Package URL (purl) 形式の Go module ID を返す。
// 仕様: https://github.com/package-url/purl-spec
//
// 例: pkg:golang/github.com/shizukutanaka/yagura@v0.8.0
func purl(modulePath, version string) string {
	if version == "" {
		return "pkg:golang/" + modulePath
	}
	return "pkg:golang/" + modulePath + "@" + version
}

// extractName は module path から最後のセグメントを抽出する(name field 用)。
// 例: "github.com/CycloneDX/cyclonedx-go" → "cyclonedx-go"
//     "runtime"                            → "runtime"
func extractName(modulePath string) string {
	if i := strings.LastIndex(modulePath, "/"); i >= 0 {
		return modulePath[i+1:]
	}
	return modulePath
}

// ─── 統計値 ──────────────────────────────────────────────────

// Summary は SBOM の人間向け要約(MCP tool で 1 行返却用)。
type Summary struct {
	TotalComponents int    `json:"total_components"`
	Application     string `json:"application"`
	Version         string `json:"version"`
	GoVersion       string `json:"go_version"`
	GeneratedAt     string `json:"generated_at"`
	SpecVersion     string `json:"spec_version"`
	SerialNumber    string `json:"serial_number"`
}

// Summarize は BOM から要約を抽出する。
func (b *Bom) Summarize() Summary {
	s := Summary{
		TotalComponents: len(b.Components),
		SpecVersion:     b.SpecVersion,
		SerialNumber:    b.SerialNumber,
		GeneratedAt:     b.Metadata.Timestamp,
	}
	if b.Metadata.Component != nil {
		s.Application = b.Metadata.Component.Name
		s.Version = b.Metadata.Component.Version
	}
	// Go toolchain を探す
	for _, c := range b.Components {
		if c.Name == "golang" {
			s.GoVersion = c.Version
			break
		}
	}
	// runtime fallback
	if s.GoVersion == "" {
		s.GoVersion = runtime.Version()
	}
	return s
}
