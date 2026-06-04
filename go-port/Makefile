.PHONY: build test clean cross

BINARY = aictl
VERSION = 0.7.0
LDFLAGS = -s -w -X main.version=$(VERSION)

build:
	CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(BINARY) ./cmd/aictl

test:
	go test ./...

cross:
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(BINARY)-linux-amd64 ./cmd/aictl
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(BINARY)-linux-arm64 ./cmd/aictl
	GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(BINARY)-darwin-arm64 ./cmd/aictl

clean:
	rm -f $(BINARY) $(BINARY)-*
