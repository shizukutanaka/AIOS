---
name: debug-engine
description: Debug inference engine connectivity and metrics
---
# Debug Engine

1. Check reachability:
```bash
python3 -m aictl net
python3 -m aictl doctor
```

2. Check adapters:
```python
from aictl.runtime.adapters import discover_engines
from aictl.core.config import load_config
config = load_config()
healths = discover_engines(config.engines.to_dict())
for h in healths:
    print(f"{h.engine}: reachable={h.reachable} status={h.status} models={h.models}")
```

3. Scrape metrics:
```python
from aictl.runtime.adapters import get_adapter
adapter = get_adapter("vllm", "http://localhost:8000")
metrics = adapter.scrape_metrics()
print(f"TTFT={metrics.ttft_ms_p95}ms queue={metrics.queue_depth}")
```

4. Check SLO:
```python
from aictl.metrics.slo import check_slo, SLOTarget, read_psi
verdict = check_slo(metrics, read_psi(), SLOTarget())
print(f"Compliant: {verdict.compliant} violations: {verdict.violations}")
```
