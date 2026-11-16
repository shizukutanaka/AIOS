# go-port

The Go implementation of the `aictl` command surface (Cobra). 29 commands.

## Imported from Yagura

The packages `internal/secretscan`, `internal/ghaaudit`, `internal/sbom`,
`internal/audit`, `internal/plantracker`, `internal/agentevent` and
`internal/sessionsummary` were imported from
[Yagura](https://github.com/shizukutanaka/Yagura) v0.119.0 (MIT, same
author) when that repository was consolidated into AIOS. They are
stdlib-only, keep their original tests, and are exposed as subcommands
of the existing `security` and `report` commands (`aictl security
secrets|gha|sbom|chain`, `aictl report plan|sessions`) so the
top-level command count above is unchanged. The rest of Yagura's
harness (its MCP daemon and per-language micro-linters) was not
imported: AIOS already has its own MCP server and quality gates.

## Building

```bash
cd go-port
go build ./...
go test ./...
```

## Why `go.sum` is pinned in a test

The `go.sum` that used to be here was fabricated — not corrupted, fabricated.
Four of its eight entries recorded values no hash function had produced: each
shared a long prefix with the real checksum and then diverged into a
plausible-looking tail, one was 43 base64 characters and so could not decode to
a 32-byte SHA-256 at all, and entries a real `go mod tidy` emits were missing.
Independent SHA-256 values share essentially no prefix, so a 36-character
agreement is not coincidence. The build failed with Go's `SECURITY ERROR`,
which read like an attack in progress and was in fact the toolchain correctly
refusing to trust a file that had never attested to anything.

The current file is different in kind: **every entry was read from
`sum.golang.org` and matched against what `proxy.golang.org` served.** All ten
agree. The checksum database is the authority `go.sum` exists to record, so
those values are pinned in `tests/test_new_features_212.py` — a future edit that
changes a hash fails that test until whoever makes it re-verifies against the
checksum database and updates the pinned list too.

The intermediate step is worth recording, because it was the safe move while
the checksum database was believed unreachable: the fabricated entries were
**removed rather than replaced**. Once a hash sits in `go.sum`, Go trusts it and
never consults the checksum database again, so writing in proxy-derived values
would have made the build pass by converting *unverified once, on one machine*
into *unverified permanently, for everyone*. Deleting them forced Go to ask
`sum.golang.org` instead. Removal required trusting nothing, which is what made
it safe — and it is the right move for anyone who hits this without a way to
reach the checksum database.

With the checksum barrier gone, the build surfaced the defect it had been
hiding: an unused `path/filepath` import in `internal/runtime/broker.go`. That
is now fixed, and the port builds, vets and tests clean.

`aictl gate` runs `lint_go_sum()` (see `aictl/core/goport.py`) on every
invocation, which re-checks each entry's well-formedness locally in
microseconds, without a network or a toolchain. It cannot tell you an entry is
*correct* — only the checksum database can — but it makes a value that no hash
function could have produced impossible to reintroduce silently.
