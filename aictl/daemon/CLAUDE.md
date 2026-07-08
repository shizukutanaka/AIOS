# Daemon Module Rules

- aiosd runs on port 7700 (configurable)
- All endpoints return JSON with Content-Type: application/json
- /metrics returns text/plain (Prometheus format)
- ThreadedHTTPServer for concurrent requests
- SLO Governor runs in background thread (15s interval)
- Scheduler daemon runs in background thread (60s interval) — fires due
  `batch add --schedule` jobs and `warmup schedule` (core/scheduler.py)
- Mock engine on port 9999 for testing
- NEVER expose daemon on 0.0.0.0 without API key auth
- Proxy gate ordering in `_proxy_completion`/`_proxy_embedding`, always
  BEFORE `router.route`: `_model_trust_ok` (trust_policy) then
  `_check_guard` (guard_policy). Response-side `_redact_response_pii`
  (guard_redact_output) runs on the non-streaming completion path only,
  before `_meter_tokens`/`_record_genai_span`. Never drop or reorder these —
  pinned by tests/test_new_features_166.py, _172.py, _175.py.
