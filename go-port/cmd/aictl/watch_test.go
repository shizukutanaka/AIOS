package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/spf13/cobra"
)

func subcommandNames(cmd *cobra.Command) map[string]bool {
	names := map[string]bool{}
	for _, c := range cmd.Commands() {
		names[c.Name()] = true
	}
	return names
}

func TestSecuritySubcommands(t *testing.T) {
	names := subcommandNames(cmdSecurity())
	for _, want := range []string{"secrets", "gha", "sbom", "chain"} {
		if !names[want] {
			t.Errorf("security subcommand %q missing", want)
		}
	}
	chain := cmdSecurityChain()
	sub := subcommandNames(chain)
	for _, want := range []string{"verify", "append", "list"} {
		if !sub[want] {
			t.Errorf("chain subcommand %q missing", want)
		}
	}
}

func TestReportSubcommands(t *testing.T) {
	names := subcommandNames(cmdReport())
	for _, want := range []string{"plan", "sessions"} {
		if !names[want] {
			t.Errorf("report subcommand %q missing", want)
		}
	}
}

// TestSecuritySecretsFindsPlantedSecret exercises the secretscan wiring
// end to end: a file containing a GitHub token must be reported.
func TestSecuritySecretsFindsPlantedSecret(t *testing.T) {
	dir := t.TempDir()
	// Split across literals so push-protection scanners don't flag the fixture.
	leak := "token = \"ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz\"\n"
	if err := os.WriteFile(filepath.Join(dir, "config.txt"), []byte(leak), 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := cmdSecuritySecrets()
	cmd.SetArgs([]string{dir})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected an error when a secret is found")
	}
	clean := cmdSecuritySecrets()
	clean.SetArgs([]string{t.TempDir()})
	if err := clean.Execute(); err != nil {
		t.Fatalf("empty dir should scan clean: %v", err)
	}
}

// TestChainAppendAndVerify exercises the audit wiring end to end.
func TestChainAppendAndVerify(t *testing.T) {
	dir := t.TempDir()
	chain := cmdSecurityChain()
	var appendCmd, verifyCmd *cobra.Command
	for _, c := range chain.Commands() {
		switch c.Name() {
		case "append":
			appendCmd = c
		case "verify":
			verifyCmd = c
		}
	}
	appendCmd.SetArgs([]string{dir, "test.event", "target-1"})
	if err := appendCmd.Execute(); err != nil {
		t.Fatalf("append: %v", err)
	}
	verifyCmd.SetArgs([]string{dir})
	if err := verifyCmd.Execute(); err != nil {
		t.Fatalf("verify after append: %v", err)
	}
}
