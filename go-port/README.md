# go-port

The Go implementation of the `aictl` command surface (Cobra).

## Building

```bash
cd go-port
go mod download   # records verified checksums in go.sum, then
go build ./...
```

`go mod download` needs to reach `proxy.golang.org` **and** `sum.golang.org`.

## Why there is no `go.sum` in the tree

There used to be one. It was fabricated — not corrupted, fabricated. Four of
its eight entries recorded values that no hash function had produced: each
shared a long prefix with the real checksum and then diverged into a
plausible-looking tail, one was 43 base64 characters and so could not decode to
a 32-byte SHA-256 at all, and entries a real `go mod tidy` emits were missing.
Independent SHA-256 values share essentially no prefix, so a 36-character
agreement is not coincidence. The build failed with Go's `SECURITY ERROR`,
which read like an attack in progress and was in fact the toolchain correctly
refusing to trust a file that had never attested to anything.

The entries were **removed rather than replaced**, and the distinction matters:

- Writing in hashes derived from the module proxy would make the build pass,
  but once a hash is present in `go.sum` Go trusts it and never consults the
  checksum database again. That would turn "unverified once, on one machine"
  into "unverified permanently, for everyone".
- With the entries absent, Go *must* ask `sum.golang.org` and writes a verified
  hash on the first `go mod download`. Removal requires trusting nothing, which
  is exactly why it is the safe direction.

The checksum database is unreachable from the environment this was found in, so
the verified values could not be generated here. Running `go mod download` on
any machine with normal network access produces them, verified, and that
`go.sum` is safe to commit.

`aictl gate` runs `lint_go_sum()` (see `aictl/core/goport.py`), which re-checks
every entry's well-formedness locally on each run. That catches a value that
cannot be a checksum in microseconds, without a network or a toolchain. It
cannot tell you an entry is *correct* — only the checksum database can — but it
makes this particular failure impossible to reintroduce silently.
