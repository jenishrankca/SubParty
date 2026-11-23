# Usability stage (bench branch)

Branch: `cursor/subparty-engine-bench-7239` only (not main).

## What landed

| Area | Change |
|------|--------|
| First-run setup | `GET /setup` + `health.setup` / Models checklist; dismissible banner |
| Region calibrate | `GET/POST /region`, `POST /region/confirm`, `POST /region/clear`; Job panel Confirm/Clear on SSE crop |
| Evidence inspector | Visual vs speech bounds, inferred / audio-disagree pills, reconcile note |
| Review queue | Unified uncertain + audio + inferred + missing-EN (`buildReviewQueue`) |
| Live vs Quality | Mode banner + select labels; Verify forced off when switching to Live |
| Electron port ops | Async `netstat`/`taskkill`/`waitPortFree` (no sync Atomics.wait / execSync on hot path) |

## APIs

```bat
curl http://127.0.0.1:18765/setup
curl -H "Authorization: Bearer <token>" -X POST http://127.0.0.1:18765/region/confirm -d "{...}"
```

## Tests

```bat
cd engine
.venv\Scripts\python.exe -m unittest tests.test_setup_region tests.test_reconcile -v
cd ..\app
npx --yes tsx src/workspaceStats.selftest.ts
```

## Policy

Live keep-up defaults remain on **main**. This branch only.
