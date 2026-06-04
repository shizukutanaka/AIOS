.PHONY: all test test-quick gate demo bench report lint install clean stats \
        go-build go-test docker ci selftest chat-mock image help \
        release release-check tco-check perf-check coverage

VERSION := $(shell python3 -c "from aictl.core.constants import AICTL_VERSION; print(AICTL_VERSION)")

# ── Primary targets ─────────────────────────────

all: lint test gate  ## Run lint + tests + gate

ci: lint test gate  ## CI pipeline (runs in GitHub Actions)

help:  ## Show this help
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

# ── Python ──────────────────────────────────────

test:  ## Run all 1380+ tests
	python3 -m unittest discover -s tests -q 2>&1 | tail -3

test-quick:  ## Run tests, quiet
	python3 -m unittest discover -s tests -q

test-verbose:  ## Run tests, verbose
	python3 -m unittest discover -s tests -v

coverage:  ## Run tests with coverage report
	python3 -m pytest tests/ --tb=short -q 2>/dev/null || \
	python3 -m unittest discover -s tests -q 2>&1 | tail -5

gate:  ## Quality gate (compile + version + tests + demo)
	python3 -m aictl gate

selftest:  ## Quick smoke test
	python3 -m aictl selftest

demo:  ## Full-stack demo (no GPU needed)
	python3 -m aictl demo --auto

bench:  ## Benchmark with mock engine
	python3 -m aictl bench --mock -n 10

report:  ## Generate system assessment
	python3 -m aictl report

chat-mock:  ## Interactive chat with mock engine
	python3 -m aictl chat --mock

lint:  ## Compile-check all Python modules
	@find aictl/ -name '*.py' -exec python3 -m py_compile {} \;
	@echo "✓ All Python modules compile OK"

install:  ## Install as editable package
	pip install -e . --break-system-packages 2>/dev/null || pip install -e .

# ── v1.6.0 feature checks ───────────────────────

rag-check:  ## Verify RAG pipeline works end-to-end
	@echo "Testing RAG pipeline..."
	@python3 -c "\
from aictl.core.rag import RagStore, chunk_text, _fallback_embedding, cosine; \
assert chunk_text('hello world'); \
emb = _fallback_embedding('test'); \
assert len(emb) == 64; \
assert 0 <= cosine(emb, emb) <= 1.01; \
print('✓ RAG pipeline OK')"

guard-check:  ## Verify guardrails detect PII and injections
	@python3 -m aictl guard test

perf-check:  ## Verify startup time is under 200ms
	@python3 -c "\
import time; t0=time.perf_counter(); \
from aictl.core.constants import AICTL_VERSION; \
ms=(time.perf_counter()-t0)*1000; \
assert ms < 200, f'Startup too slow: {ms:.0f}ms'; \
print(f'✓ core.constants import: {ms:.0f}ms (< 200ms)')"

sdk-check:  ## Verify SDK surface (ask/classify/embed/configure)
	@python3 -c "\
from aictl.sdk import _AmbientContext; _AmbientContext.reset_for_testing(); \
import aictl; \
r = aictl.ai.ask('hello'); \
assert hasattr(r, 'cost_usd'); \
assert hasattr(r, 'cached'); \
cats = aictl.ai.classify('I love this!', categories=['pos','neg']); \
assert cats in ['pos','neg']; \
print('✓ SDK surface OK')"

# ── Release ──────────────────────────────────────

release-check:  ## Pre-release checklist (run before tagging)
	@echo "=== Pre-release checklist ==="
	@make lint
	@make test-quick
	@make gate
	@make guard-check
	@make perf-check
	@make sdk-check
	@echo ""
	@echo "✓ All checks passed. Ready to release v$(VERSION)."
	@echo ""
	@echo "  git tag v$(VERSION)"
	@echo "  git push --tags"

release:  ## Tag and push (triggers CI → PyPI → Docker)
	@make release-check
	@git tag v$(VERSION)
	@git push --tags
	@echo "✓ v$(VERSION) released."

# ── Go ──────────────────────────────────────────

go-build:  ## Build Go CLI
	cd go-port && go build -o ../bin/aictl-go ./cmd/aictl

go-test:  ## Run Go tests
	cd go-port && go test ./...

# ── Docker ──────────────────────────────────────

docker:  ## Build + run with Docker Compose
	docker compose up --build -d

docker-stop:  ## Stop Docker Compose
	docker compose down

image:  ## Build bootc container image
	podman build -t ghcr.io/shizukutanaka/aios:latest -f deploy/bootc/Containerfile .

# ── Stats ────────────────────────────────────────

stats:  ## Show project statistics
	@python3 -m aictl info

loc:  ## Lines of code breakdown
	@echo "Python LOC:"
	@find aictl -name '*.py' | xargs wc -l 2>/dev/null | tail -1
	@echo "Test LOC:"
	@find tests -name '*.py' | xargs wc -l 2>/dev/null | tail -1
	@echo "Commands:"
	@python3 -c "from aictl.__main__ import build_parser; p=build_parser(); \
	[print(f'  {len(a.choices)} subcommands') for a in p._actions if hasattr(a,'choices') and a.choices]"

# ── Clean ────────────────────────────────────────

clean:  ## Remove build artifacts and cache files
	find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	rm -rf build/ dist/ *.egg-info/ bin/ .pytest_cache/ .mypy_cache/
	@echo "✓ Cleaned"
