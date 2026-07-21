# Workers

URL scans are dispatched through the `ScanDispatcher` interface in
`app/services/scan_application.py`. The current Web/API runtime uses a bounded
thread pool; the CLI injects an inline dispatcher while calling the same use
case. A future durable queue belongs behind that interface and must not move
pipeline logic into workers or adapters.
