# CoconutAI performance optimization — 2 November 2026

The existing application was optimized incrementally. The main verified gains are shorter event-loop pauses, fewer duplicate Firestore reads, and substantially less map-restoration work. Real-model inference and total single-upload processing time were essentially unchanged. This is local validation, not a production load-test or field accuracy certification.

## Architecture and preserved behavior

`main.py` / `run.py` launch `app.main`. Its routers authenticate Firebase identities and use domain/service helpers. Uploads validate original image bytes, extract EXIF/DJI GPS, decode with OpenCV, and call the shared `model.detector` through its inference lock. The model uses the same local `weights.pt`, 640-pixel inference setting, confidence thresholds, class handling and NMS. Upload previews remain 1280 pixels maximum at JPEG quality 84.

Detection persistence still prepares the existing metadata-preserving storage copy, uploads authenticated evidence to Cloudinary, and creates canonical Firestore records. Ownership-checked media routes present private evidence. Monitoring retains case snapshots, separate tree identity evidence, treatment history, transactional follow-up limits and ambiguous-write cleanup. The frontend shares short-lived record requests between panels, renders charts, and maintains legacy map pins in Firebase RTDB.

The 11 pre-existing modified files were preserved. Nine have exactly the same diffs as at task start. This work additionally modifies `app/services/detection_service.py` and `static/index.html`; their earlier edits remain. The original patch and source snapshots are in `tmp/performance-20261102/`. No commit, push, deployment, live-record edit, or database migration was performed.

## Files changed by this task

| File | Change and reason |
| --- | --- |
| `app/main.py` | Imports the existing model singleton in a worker during startup, before accepting requests. Offloads existing expert-account initialization. Previously the first model-dependent request could initiate model loading. |
| `app/core/security.py` | Runs synchronous Firebase token verification in the shared worker pool, preserving all verification and rejection rules. |
| `app/api/auth.py` | Offloads body-token verification; lets FastAPI run the synchronous profile write in its worker pool. |
| `app/api/detections.py` | Moves unchanged validation/GPS/decode and preview encoding into workers; runs farm-location and farmer-advice handlers as synchronous worker endpoints; awaits canceled stream-saver cleanup. |
| `app/api/monitoring.py` | Moves case/quota reads, image decoding, inference, evidence uploads and persistence off the event loop. Keeps validation order and commit/cleanup operations together. |
| `app/infrastructure/imaging/tree_reference.py` | Offloads complete whole-tree image validation, including the full decode check. |
| `app/api/expert.py` | Uses FastAPI worker execution for synchronous record verification and recommendation reads/writes. |
| `app/api/notifications.py` | Offloads synchronous provider reads/writes. |
| `app/api/records.py` | Offloads record retrieval and CSV generation. |
| `app/api/reports.py` | Offloads record retrieval and PDF generation. |
| `app/services/export_service.py` | Serializes PDF rendering with a lock, protecting Matplotlib's shared pyplot state when report requests execute in workers. Report content/layout code is unchanged. |
| `app/services/detection_service.py` | Coalesces concurrent expert-cache misses. A generation check prevents a query started before a mutation from repopulating the cache afterward. TTL, defensive copies, ownership checks and query limits remain. |
| `static/app.js` | Defers map creation until map/drone navigation; attaches pins collected before map readiness; restores pins without saving duplicates; yields every 25 restored pins; batches heatmap/session updates; uses a Set for filter membership. Coalesces geocoding, limits its cache to 500 entries and adds a 5-second lookup timeout. Reuses unchanged charts, rejects invalidated record responses, adds 12-second record-read and 5-minute upload timeouts, and uses same-session cached records immediately when the browser reports offline. |
| `static/index.html` | Updates the application bundle version. Enables the existing map-pin code's missing Firebase RTDB compat module and configures the same RTDB URL already used by the backend. The module is deferred. CSS and visual markup are unchanged. |

Added tests: `tests/test_model_startup.py`, `tests/test_performance_concurrency.py`, and `tests/js/performance_workflows.test.js`.

Added tools: `tools/benchmark_responsiveness.py`, `tools/benchmark_model_pipeline.py`, `tools/benchmark_expert_cache.py`, `tools/benchmark_frontend.cjs`, and `tools/frontend_performance_harness.cjs`.

`.gitignore` adds an exception for the single synthetic measurement JSON below; general credential/JSON exclusions remain.

Durable measurement evidence: [performance/2026-11-02/measurements.json](performance/2026-11-02/measurements.json).

## Before / after measurements

Environment: Windows, Python 3.13.0, local existing dependencies. The baseline is the working tree at the beginning of this task, including its existing edits. Durations use high-resolution monotonic clocks. p95 and individual run data are in the evidence JSON. No production speed, cloud billing or field accuracy improvement is inferred from these fixtures.

### Responsiveness and duplicate work

The upload fixture is an 11.65 MB, 4000 × 3000 synthetic JPEG. Its detector sleeps 20 ms and persistence sleeps 30 ms. A 5 ms asyncio heartbeat measures the longest scheduling gap during each operation. Windows timer resolution affects the residual gaps. These are medians of 12 independent operations, not model or cloud-service timings.

| Workload / measure | Before | After | Improvement / interpretation |
| --- | ---: | ---: | --- |
| Upload: longest event-loop heartbeat gap | 296.25 ms | 18.54 ms | About 94% shorter pause |
| Auth dependency: longest gap, with 40 ms fake verification | 40.72 ms | 16.22 ms | About 60% shorter pause |
| Records read: longest gap, with 40 ms fake database read | 40.69 ms | 16.62 ms | About 59% shorter pause |
| Notifications read: longest gap, with 40 ms fake database read | 40.70 ms | 16.32 ms | About 60% shorter pause |
| Complete upload handler, controlled detector/persistence | 417.74 ms | 426.86 ms | No latency gain; worker dispatch adds overhead |
| Four simultaneous expert readers: queries / documents | 4 / 2000 | 1 / 500 | 75% fewer duplicated simulated reads |
| Four-reader batch, 500 records, 40 ms fake query delay | 171.54 ms | 94.03 ms | About 45% lower fixture duration; 12 batches |
| Restore 200 saved map pins, Node VM | 215.37 ms | 63.36 ms | About 71% lower fixture duration; 12 runs |
| Firebase writes while restoring those 200 pins | 200 | 0 | Restoring no longer creates new remote copies |
| Heatmap updates during restoration | 400 | 1 | One final update |
| Session-storage serializations during restoration | 200 | 1 | One final serialization |

The map fixture uses DOM, MapLibre and Firebase doubles. Its times exclude real browser layout, GPU work, network and remote database latency. Address-completion redraw counts vary with scheduling; the final run had three log renders versus 400 before. No pins were dropped. New detections still autosave once.

### Real model and image pipeline

An additional paired benchmark runs the archived original upload handler and the optimized handler against the same real local model and synthetic JPEG. It alternates order for eight measured samples per variant after one warmup pair. Authentication and persistence remain doubles, and test guards block external providers. Predictions, image metadata, dimensions and annotated preview bytes matched exactly on every call.

| Stage | Before median | After median | Interpretation |
| --- | ---: | ---: | --- |
| Read already-uploaded file bytes | 0.30 ms | 0.35 ms | Not network upload time |
| Image validation | 0.15 ms | 0.20 ms | No speed gain |
| Metadata / GPS extraction | 9.25 ms | 10.50 ms | No speed gain |
| OpenCV decode | 218.15 ms | 216.95 ms | No material speed gain |
| Inference wrapper, including model work | 199.05 ms | 197.55 ms | No material speed gain |
| YOLO preprocessing, from backend timing | 3.14 ms | 3.13 ms | Unchanged |
| YOLO inference, from backend timing | 179.90 ms | 180.70 ms | Unchanged |
| YOLO postprocessing, from backend timing | 2.79 ms | 2.92 ms | No speed gain |
| Annotated preview resize / encode | 93.50 ms | 103.00 ms | No speed gain; moved off event loop |
| Total handler wall time | 537.29 ms | 537.43 ms | Essentially unchanged |

Cold model import took 14,134 ms in this run; the first handler call took 1,123 ms, including predictor initialization. Moving the import to startup changes when that cost is paid; it does not eliminate cold-start cost or all first-prediction warmup. There remains one singleton per server process. Multiple server workers would each load a model.

Weights SHA-256: `1576d2fff5f8e653e51acd792bb3d43d3c7621891517841c868b41fee631497b`. `model.py` and the weights were not modified. Synthetic-image equality does not establish accuracy on real diseased leaves.

### Measurements not obtained

Real client-to-server upload time, Firebase/Cloudinary network latency, production endpoint percentiles, authenticated dashboard loading time, first map opening time, whole-process CPU/RAM under load, and real browser before/after loading times were not measured. Controlled 20 ms / 10 ms cloud/database counters in the responsiveness fixture are test inputs, not measurements of those providers. The browser inspection interface did not expose navigation timing.

A guarded localhost browser preview confirmed the sign-in UI, no reported console errors, and zero map canvases before map navigation. Live authenticated dashboard/map workflows and production synchronization were not exercised.

## Validation and compatibility

| Check | Baseline | Final |
| --- | ---: | ---: |
| Complete offline Python suite | 274 passed | 280 passed |
| JavaScript suite | 25 passed | 39 passed |
| Original Python cases retained | 274 | All 274 present and passing |

The tests cover auth/ownership, private media, failed inference, EXIF/MPO and large-image handling, persistence failure cleanup, expert workflows, monitoring/tree identity, transactional upload limits, treatment history and exports. New checks cover startup failures, concurrent cache reads and invalidation, another request completing while monitoring inference is held busy, map restoration cancellation, deferred marker attachment, offline cache isolation, geocoding coalescing and chart reuse.

An intermediate run found a WebSocket compatibility test required retaining the annotated-image argument supplied to persistence. That optional memory change was reverted; the original argument remains and the final suite passes. Existing tests were not weakened or edited by this task.

An AST comparison of the 41 routes in archived changed API modules found unchanged route decorators and parameter/dependency declarations. Some handlers now execute as synchronous FastAPI worker endpoints; HTTP methods, URLs, request parameters and response contracts remain. No database collections, schemas, indexes, model settings or record migrations were changed. Python compilation, JavaScript syntax and `git diff --check` passed.

## Dependencies, offline behavior and limits

No Python or npm dependencies were added or upgraded. One browser module, `firebase-database-compat.js` at the already-used Firebase version 10.8.0, was enabled because the existing map code calls `firebase.database()`. The backend's existing database URL was added to the frontend Firebase config. SDK delivery still depends on the CDN/cache; real RTDB authorization rules and synchronization require a live integration check.

Offline record access uses memory from the current signed-in page session, with an explicit offline toast. It is cleared on identity changes. Online cache expiry and mutation invalidation remain active. Fresh reads resume through the normal fetch path after connectivity returns. Offline map restoration/geocoding skip network calls. Existing failed-upload staging and Firebase SDK map-write behavior remain.

The existing canonical detection store explicitly has no durable local detection queue. Verified sign-in and saving a new detection require online Firebase/provider access. This work does not provide offline sign-in, AI detection after a cold offline browser start, persistent offline evidence storage, or automatic replay of uncertain detection writes. No unsafe retries or alternate detection database were introduced. Those remaining offline requirements need a separately designed durable outbox and server idempotency contract.

Cache coalescing and invalidation are process-local, as before. Cross-instance cache freshness remains limited by the existing 45-second TTL. The load lock deliberately serializes cache fills. PDF rendering is serialized for pyplot safety. Worker pools and the existing inference lock can still queue requests under high load; native work already running in a thread is not forcibly stopped by request cancellation. Production CPU/RAM and cancellation behavior still need load validation.

The map retains all historical pins and DOM markers after initialization. Restoration is batched, but it does not add viewport-based marker virtualization or clustering. Existing duplicate pins already stored remotely are not deleted. Dashboard totals still describe the existing bounded response windows (100 farmer / up to 500 expert records), not all-time server aggregates. Online read timeouts surface an error rather than silently presenting stale data as current.

## How to test and reproduce

From the repository root, with the existing dependencies installed:

```powershell
python -u -m pytest tests -q -p no:cacheprovider --basetemp=tmp/performance-manual-tests
node --test tests/js/*.test.js
python tools/benchmark_responsiveness.py
python tools/benchmark_expert_cache.py
node tools/benchmark_frontend.cjs
python tools/benchmark_model_pipeline.py tmp/performance-20261102/source/app/api/detections.py
```

Use a fresh `--basetemp` path. The Python benchmark tools install the repository's offline guards before application imports; they do not load deployment credentials or write live records. The paired model command requires the local task-start snapshot, which is Git-ignored. Archived metrics in `performance/2026-11-02/measurements.json` retain the evidence if that snapshot is later removed. Benchmark durations vary by machine and load.

For a live acceptance check, start normally with `python main.py` and configured providers. Confirm the startup model load occurs before startup completes. Sign in as a farmer, upload a normal image and a large EXIF-tagged drone photo, navigate during analysis, inspect stored evidence/GPS and verify the record as an expert. Open map/drone tabs repeatedly and confirm maps persist. Reload saved pins and confirm the RTDB pin count does not increase. Check monitoring uploads, identity review, treatment logs, CSV and PDF exports. After loading records, disconnect the browser, navigate between record/dashboard tabs, and confirm cached data appears; reconnect and refresh. Use a disposable test account/deployment for write checks.

## Future optimization priorities

1. Profile authenticated farmer/expert traffic against an isolated Firebase/Cloudinary test deployment, including concurrent uploads and provider failures. Record p50/p95, query counts, process RSS/CPU and browser long tasks.
2. Design durable offline queuing with authenticated replay, stable idempotency keys, bounded storage and conflict/uncertain-commit handling before enabling automatic upload retries.
3. Introduce viewport-based or clustered map rendering for large inventories, preserving tree selection, filters and the existing visual design.
4. Add cursor pagination and explicit all-time server aggregates as an additive, versioned API design. Do not silently replace current bounded-window totals.
5. Validate any future inference-resolution/backend/precision change against a labeled coconut-leaf dataset before considering it. No such accuracy-affecting change was made here.
