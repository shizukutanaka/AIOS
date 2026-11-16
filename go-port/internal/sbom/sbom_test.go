package sbom

import (
	"encoding/json"
	"runtime/debug"
	"strings"
	"testing"
	"time"
)

// ─── Generator ───────────────────────────────────────────────

func TestNew_NonNil(t *testing.T) {
	if New() == nil {
		t.Error("New returned nil")
	}
}

func TestGenerate_RealBuildInfo(t *testing.T) {
	g := New()
	g.NowFn = func() time.Time { return time.Date(2026, 5, 13, 0, 0, 0, 0, time.UTC) }
	g.SerialFn = func() string { return "urn:uuid:00000000-0000-4000-8000-000000000000" }

	bom, err := g.Generate("github.com/shizukutanaka/yagura", "0.8.0")
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}

	// 基本フィールド
	if bom.BomFormat != "CycloneDX" {
		t.Errorf("BomFormat: %q", bom.BomFormat)
	}
	if bom.SpecVersion != "1.5" {
		t.Errorf("SpecVersion: %q", bom.SpecVersion)
	}
	if bom.Version != 1 {
		t.Errorf("Version: %d", bom.Version)
	}
	if !strings.HasPrefix(bom.SerialNumber, "urn:uuid:") {
		t.Errorf("SerialNumber should start with urn:uuid: %q", bom.SerialNumber)
	}

	// metadata
	if bom.Metadata.Timestamp != "2026-05-13T00:00:00Z" {
		t.Errorf("Timestamp: %q", bom.Metadata.Timestamp)
	}
	if bom.Metadata.Component == nil {
		t.Fatal("metadata.component is nil")
	}
	if bom.Metadata.Component.Type != "application" {
		t.Errorf("main component type: %q", bom.Metadata.Component.Type)
	}
	if bom.Metadata.Component.Version != "0.8.0" {
		t.Errorf("main component version: %q", bom.Metadata.Component.Version)
	}

	// components: at minimum golang framework
	hasGolang := false
	for _, c := range bom.Components {
		if c.Name == "golang" && c.Type == "framework" {
			hasGolang = true
			break
		}
	}
	if !hasGolang {
		t.Error("expected golang framework component")
	}

	// dependency graph: main → at least 1 dep
	if len(bom.Dependencies) == 0 {
		t.Fatal("no dependencies declared")
	}
	if len(bom.Dependencies[0].DependsOn) == 0 {
		t.Error("main has no dependsOn entries")
	}
}

func TestGenerate_Reproducible(t *testing.T) {
	// Same fixed inputs → same JSON output (excluding serial)
	g := New()
	g.NowFn = func() time.Time { return time.Date(2026, 5, 13, 0, 0, 0, 0, time.UTC) }
	g.SerialFn = func() string { return "urn:uuid:fixed-serial-for-test" }

	bom1, err := g.Generate("github.com/shizukutanaka/yagura", "0.8.0")
	if err != nil {
		t.Fatal(err)
	}
	bom2, err := g.Generate("github.com/shizukutanaka/yagura", "0.8.0")
	if err != nil {
		t.Fatal(err)
	}

	j1, _ := bom1.JSON()
	j2, _ := bom2.JSON()
	if string(j1) != string(j2) {
		t.Errorf("BOM is not reproducible:\nfirst: %s\nsecond: %s", j1, j2)
	}
}

func TestBom_JSON_ValidCycloneDX15(t *testing.T) {
	g := New()
	bom, err := g.Generate("github.com/shizukutanaka/yagura", "0.8.0")
	if err != nil {
		t.Fatal(err)
	}
	data, err := bom.JSON()
	if err != nil {
		t.Fatalf("JSON: %v", err)
	}

	// 必須トップレベルフィールド検証
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	requiredFields := []string{"bomFormat", "specVersion", "serialNumber", "version", "metadata"}
	for _, f := range requiredFields {
		if _, ok := parsed[f]; !ok {
			t.Errorf("missing required CycloneDX 1.5 field: %s", f)
		}
	}
	if parsed["bomFormat"] != "CycloneDX" {
		t.Errorf("bomFormat: %v", parsed["bomFormat"])
	}
	if parsed["specVersion"] != "1.5" {
		t.Errorf("specVersion: %v", parsed["specVersion"])
	}
}

// ─── Summarize ───────────────────────────────────────────────

func TestSummarize(t *testing.T) {
	g := New()
	g.NowFn = func() time.Time { return time.Date(2026, 5, 13, 0, 0, 0, 0, time.UTC) }
	g.SerialFn = func() string { return "urn:uuid:test" }

	bom, _ := g.Generate("github.com/shizukutanaka/yagura", "0.8.0")
	s := bom.Summarize()

	if s.SpecVersion != "1.5" {
		t.Errorf("SpecVersion: %q", s.SpecVersion)
	}
	if s.Application == "" {
		t.Error("Application empty")
	}
	if s.Version != "0.8.0" {
		t.Errorf("Version: %q", s.Version)
	}
	if s.GoVersion == "" {
		t.Error("GoVersion empty")
	}
	if s.TotalComponents < 1 {
		t.Errorf("TotalComponents: %d", s.TotalComponents)
	}
}

// ─── helpers ─────────────────────────────────────────────────

func TestPurl(t *testing.T) {
	cases := []struct {
		path, ver, want string
	}{
		{"github.com/example/lib", "v1.0.0", "pkg:golang/github.com/example/lib@v1.0.0"},
		{"github.com/example/lib", "", "pkg:golang/github.com/example/lib"},
		{"runtime", "go1.22.0", "pkg:golang/runtime@go1.22.0"},
	}
	for _, c := range cases {
		got := purl(c.path, c.ver)
		if got != c.want {
			t.Errorf("purl(%q,%q) = %q, want %q", c.path, c.ver, got, c.want)
		}
	}
}

func TestExtractName(t *testing.T) {
	cases := map[string]string{
		"github.com/CycloneDX/cyclonedx-go": "cyclonedx-go",
		"runtime":                           "runtime",
		"":                                  "",
		"a/b/c":                             "c",
	}
	for in, want := range cases {
		if got := extractName(in); got != want {
			t.Errorf("extractName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRandomUUIDv4_Format(t *testing.T) {
	for i := 0; i < 5; i++ {
		u := randomUUIDv4()
		// RFC 4122 UUID v4 = 36 chars (8-4-4-4-12)
		if len(u) != 36 {
			t.Errorf("UUID length: %d (%q)", len(u), u)
		}
		parts := strings.Split(u, "-")
		if len(parts) != 5 {
			t.Errorf("UUID parts: %d", len(parts))
		}
		// version 4: 13th char must be '4'
		if u[14] != '4' {
			t.Errorf("UUID v4 marker missing at pos 14: %q", u)
		}
	}
}

func TestRandomUUIDv4_Unique(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 100; i++ {
		u := randomUUIDv4()
		if seen[u] {
			t.Errorf("UUID collision: %s", u)
		}
		seen[u] = true
	}
}

// ─── Generator hooks ─────────────────────────────────────────

func TestGenerator_NowFnAndSerialFn(t *testing.T) {
	g := &Generator{
		NowFn:    func() time.Time { return time.Unix(0, 0).UTC() },
		SerialFn: func() string { return "urn:uuid:custom" },
	}
	bom, _ := g.Generate("test/module", "test")
	if bom.SerialNumber != "urn:uuid:custom" {
		t.Errorf("Serial hook not used: %q", bom.SerialNumber)
	}
	if bom.Metadata.Timestamp != "1970-01-01T00:00:00Z" {
		t.Errorf("Now hook not used: %q", bom.Metadata.Timestamp)
	}
}

func TestGenerator_NilHooks_UseDefaults(t *testing.T) {
	g := &Generator{}
	bom, err := g.Generate("test/module", "test")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(bom.SerialNumber, "urn:uuid:") {
		t.Errorf("default serial: %q", bom.SerialNumber)
	}
}

// TestGenerate_EmptyMainPath exercises the bi.Main.Path fallback: when
// mainPath is "" the generator falls through to bi.Main.Path, which is
// also "" in a test binary, landing at "unknown".
func TestGenerate_EmptyMainPath(t *testing.T) {
	g := New()
	bom, err := g.Generate("", "")
	if err != nil {
		t.Fatalf("Generate with empty path: %v", err)
	}
	if bom.Metadata.Component == nil {
		t.Fatal("metadata.component is nil")
	}
	// name must be either the last segment of bi.Main.Path (if set) or "unknown"
	name := bom.Metadata.Component.Name
	if name == "" {
		t.Error("component name should not be empty")
	}
}

// TestSummarize_RuntimeFallback exercises the `s.GoVersion == ""` branch:
// manually build a BOM with no golang component so the runtime.Version()
// fallback is taken.
func TestSummarize_RuntimeFallback(t *testing.T) {
	bom := &Bom{
		SpecVersion:  "1.5",
		SerialNumber: "urn:uuid:test",
		Metadata: Metadata{
			Timestamp: "2026-01-01T00:00:00Z",
			Component: &Component{Name: "app", Version: "1.0"},
		},
		Components: []Component{
			{Name: "some-lib", Type: "library"}, // no "golang" component
		},
	}
	s := bom.Summarize()
	if s.GoVersion == "" {
		t.Error("GoVersion should fall back to runtime.Version(), not be empty")
	}
}

// ─── depComponents (supply-chain logic, synthetic deps) ──────

func TestDepComponents_Empty(t *testing.T) {
	comps, refs := depComponents(nil)
	if comps != nil || refs != nil {
		t.Errorf("zero deps should yield nil slices, got %v / %v", comps, refs)
	}
}

func TestDepComponents_SkipsNilAndEmptyPath(t *testing.T) {
	comps, refs := depComponents([]*debug.Module{
		nil,
		{Path: "", Version: "v1.0.0"},
	})
	if len(comps) != 0 || len(refs) != 0 {
		t.Errorf("nil / empty-path deps must be skipped, got %v / %v", comps, refs)
	}
}

func TestDepComponents_BasicDepWithChecksum(t *testing.T) {
	comps, refs := depComponents([]*debug.Module{
		{Path: "github.com/x/dep", Version: "v1.2.3", Sum: "h1:abc123="},
	})
	if len(comps) != 1 || len(refs) != 1 {
		t.Fatalf("expected 1 component, got %d / %d", len(comps), len(refs))
	}
	c := comps[0]
	if c.Name != "dep" || c.Version != "v1.2.3" || c.Type != "library" || c.Scope != "required" {
		t.Errorf("component fields wrong: %+v", c)
	}
	if c.BomRef != refs[0] || c.PackageURL != c.BomRef {
		t.Errorf("BomRef / PackageURL / ref must agree: %+v vs %q", c, refs[0])
	}
	if len(c.Hashes) != 1 || c.Hashes[0].Alg != "Go-Module-Sum" || c.Hashes[0].Content != "h1:abc123=" {
		t.Errorf("checksum not attached: %+v", c.Hashes)
	}
}

func TestDepComponents_NoChecksumNoHashes(t *testing.T) {
	comps, _ := depComponents([]*debug.Module{
		{Path: "github.com/x/nosum", Version: "v0.1.0"},
	})
	if len(comps) != 1 {
		t.Fatal("expected 1 component")
	}
	if comps[0].Hashes != nil {
		t.Errorf("dep without Sum must have no Hashes, got %+v", comps[0].Hashes)
	}
}

func TestDepComponents_ResolvesReplacementChain(t *testing.T) {
	// dep → replaced by fork → replaced again by local pin: the final module in
	// the chain is what actually got linked, so the SBOM must record it.
	final := &debug.Module{Path: "github.com/fork/final", Version: "v2.0.0", Sum: "h1:final="}
	mid := &debug.Module{Path: "github.com/fork/mid", Version: "v1.5.0", Replace: final}
	comps, refs := depComponents([]*debug.Module{
		{Path: "github.com/orig/dep", Version: "v1.0.0", Replace: mid},
	})
	if len(comps) != 1 {
		t.Fatal("expected 1 component")
	}
	c := comps[0]
	if c.Name != "final" || c.Version != "v2.0.0" {
		t.Errorf("replacement chain not followed to the end: %+v", c)
	}
	if !strings.Contains(refs[0], "fork/final") {
		t.Errorf("ref should point at the final replacement, got %q", refs[0])
	}
	if len(c.Hashes) != 1 || c.Hashes[0].Content != "h1:final=" {
		t.Errorf("checksum must come from the final module: %+v", c.Hashes)
	}
}
