# Daemon Module Rules

- aiosd runs on port 7700 (configurable)
- All endpoints return JSON with Content-Type: application/json
- /metrics returns text/plain (Prometheus format)
- ThreadedHTTPServer for concurrent requests
- SLO Governor runs in background thread (15s interval)
- Mock engine on port 9999 for testing
- NEVER expose daemon on 0.0.0.0 without API key auth
