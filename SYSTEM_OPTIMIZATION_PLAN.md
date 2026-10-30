# System optimization plan

Prepared: 27 October 2026

## Objective

Improve response time, resource use, and operating cost while preserving the current UI, workflows, API contracts, detection results, and stored data. This document is a plan based on repository inspection, not a performance report. No application changes or benchmarks were performed to prepare it.

**Recommended approach:** measure representative workflows, optimize the largest measured source of unnecessary work, prove equivalence, and release one small change at a time. Do not begin with a rewrite, a different model, or more server workers.

## Preservation requirements

Treat the following as acceptance criteria for every optimization:

- Same markup, styling, layout, labels, navigation, loading/error states, focus, scroll position, and desktop/mobile appearance.
- Same routes, authentication requirements, status codes, response fields, defaults, ordering, limits, and user-visible errors.
- Same model weights, inference backend selection, preprocessing, image dimensions, confidence thresholds, class mapping, bounding boxes, annotations, and image encoding settings.
- Same farmer ownership checks, expert authorization, token validation, private media access, quotas, and WebSocket limits.
- Same Firestore paths and record shapes, IDs and references, expert review requirements, treatment transitions, notification semantics, and audit history.
- Same EXIF/GPS interpretation, missing-location behavior, Philippine date calculations, recommendation snapshots, CSV content, and PDF layout.
- Same cache freshness, forced refresh behavior, invalidation after mutations, and separation between signed-in users.
- Same persistence guarantees, transaction boundaries, cleanup behavior, and distinction between inference failure and a valid empty result.

Faster completion is intended. Changing request ordering or concurrency still requires checks for races, duplicate writes, and changed failure outcomes.

## Current observations

| Area | Evidence in this repository | Implication |
| --- | --- | --- |
| Frontend reads | `static/app.js` already coalesces record requests and has 45-second farmer/expert caches | Measure remaining duplicate requests before adding another cache. |
| Expert records | `app/services/detection_service.py` already uses ordered, limited Firestore queries, a locked cache, and defensive copies | Preserve ownership checks, mutation isolation, ordering, and invalidation. Removing copies or sorting is not automatically safe. |
| Monitoring | `app/services/monitoring_service.py` streams case collections, including a collection group for experts | Measure read counts and downstream enrichment as data grows; unrestricted scans are a scaling candidate, not a proven bottleneck. |
| Inference | `app/services/inference_service.py` serializes calls to the shared detector with a lock | Measure lock waiting separately from model execution. Do not remove the lock without proving backend safety. |
| Upload handling | `app/api/detections.py` offloads token verification, inference, and persistence in parts of the upload flow; JPEG/base64 encoding also occurs in the handler | Profile remaining event-loop work before moving anything. Existing offloading must not be duplicated. |
| Asset delivery | `app/main.py` deliberately disables caching for selected frontend paths | Changing those headers changes update behavior; retain them in the initial optimization scope. |
| Validation | `tests/conftest.py` blocks external services and historical SQLite access | Use offline tests for regression checks and an isolated integration environment for provider behavior. |

These observations identify investigation points. They do not establish which component consumes the most time.

## 1. Establish a reproducible baseline

Use synthetic or sanitized records and a dedicated test project. Record the commit, dependency versions, hardware, model files/backend, dataset size, image dimensions, and test settings. Keep credentials, tokens, private images, and personal information out of logs and benchmark artifacts.

Measure cold start separately from warmed requests. Run the same workload before and after each change, with enough repeated samples to report sample count, median, p95, and variability. Include normal single-user use and realistic concurrent farmer/expert activity.

| Workflow | Measurements |
| --- | --- |
| Sign-in and initial dashboard | Endpoint latency, browser requests/bytes, database reads, time until current content appears |
| Image upload | Validation/EXIF/decode time, inference lock wait, model execution, JPEG/base64 encoding, provider uploads, Firestore persistence, total duration |
| Expert queue and monitoring | Cold/warm latency, document reads, enrichment calls, cache hits, response bytes, rendering time |
| Live detection | Frame arrival-to-result latency, effective frame rate, queue depth, CPU/RAM, responsiveness of unrelated requests |
| CSV/PDF export | Duration, peak RAM, document reads, output size and equivalence |

Use monotonic timing such as `time.perf_counter()` for durations. Add stage timing at service boundaries without altering response payloads. Reuse existing measurements where possible. Keep detailed profiling temporary or sampled so instrumentation does not dominate results.

Before implementation, rerun the current Python and JavaScript suites and save results. `SAFE_REFACTOR_REPORT.md` records historical tree-identity test failures and integration limits; those results are context, not a current baseline. Investigate any reproduced failures and document them explicitly. Do not weaken assertions to obtain a pass.

## 2. Prioritize measured opportunities

### Priority A: remove repeated work within the same operation

Start here when profiling shows material cost:

1. Reuse a validated case, record, parsed payload, or normalized export row within one operation when the same immutable input is processed repeatedly.
2. Avoid repeated media presentation or recommendation enrichment for the same result within that operation.
3. Compute shared frontend filter/summary inputs once per render cycle if profiling demonstrates repeated expensive traversal.

Keep defensive copies where callers may mutate results. Keep authorization at its existing trust boundaries. Request-scoped reuse must not hide a state change that the workflow currently rereads. Existing cleanup in `SAFE_REFACTOR_REPORT.md` already removed some redundant work; verify the current code before proposing it again.

### Priority B: keep blocking work off the async event loop

Profile synchronous SDK calls, decoding, encoding, and export generation in async endpoints. Where they block unrelated requests, evaluate thread offloading using the project's existing patterns.

Preserve operation ordering, exception mapping, locks, transaction ownership, and response-after-persistence behavior. Account for thread-pool saturation, cancellation, and work that continues after client disconnect. Benchmark mixed workloads: improving uploads must not make sign-in or monitoring slower.

### Priority C: reduce database work while returning identical results

Use query/read measurements to identify repeated lookups and growing scans, especially monitoring overviews and expert case listings. Prefer eliminating duplicate reads inside a request before introducing new shared state.

For query changes, verify the exact existing result set, missing-field handling, legacy records, malformed records, tie ordering, ownership checks, and totals. A database filter can exclude records that application-side normalization currently retains. Add indexes only for the actual verified query shape and confirm them in the test environment.

Do not add pagination, lower limits, omit history, or project away response fields under this preservation scope. Those changes can reduce work by changing what users receive.

### Priority D: improve existing cache efficiency only with equivalence evidence

Inspect hit rates, concurrent misses, invalidation paths, and memory usage first. Consider coalescing identical concurrent reads only when sharing the result preserves each caller's authorization, requested limit, freshness, error handling, and cancellation behavior.

Keep TTLs and forced-refresh semantics unchanged. Test upload, verification, recommendation edits, treatment changes, sign-out, and account switching. A backend cache-fill race can publish an old read after invalidation; include that interleaving in validation. Never reuse private results across users or return mutable cache objects directly.

### Priority E: optimize frontend computation without changing presentation

Use browser profiling before modifying rendering. Candidate work includes reusing derived data within an existing render and removing duplicate listener registration if demonstrated.

Compare screenshots and interactions after every change. Preserve DOM content/order, focus, selection, scroll, loading messages, accessibility behavior, and refresh timing. Defer virtualization, debouncing, lazy loading, and new polling schedules because they can change observable behavior.

## 3. Keep higher-risk changes outside the initial scope

These require a separate proposal and explicit behavioral evaluation:

- Changing model weights, quantization, image resolution, inference precision/backend, confidence thresholds, JPEG quality, or frame sampling.
- Removing the shared inference lock, adding detector replicas, increasing worker counts, or changing execution queues. Workers duplicate model memory and process-local caches, locks, and rate-limit state; startup also initializes external integrations.
- Adding retries to writes without proven idempotency, moving durable work into background tasks, or changing transaction boundaries.
- Increasing cache TTLs, caching token verification, changing private-media caching, or replacing deliberate frontend no-cache headers.
- Removing legacy storage paths, migrating data, changing record schemas, upgrading dependencies broadly, or replacing the frontend framework.
- Altering request/response formats, pagination, errors, quotas, or monitoring rules to make benchmarks look faster.

Do not assume an existing OpenVINO setup needs replacing. Profile the current detector implementation and preserve its outputs.

## 4. Verification and release gates

For each proposed change, record the baseline cost, hypothesis, files affected, expected benefit, equivalence checks, and rollback step before editing.

1. **Contract checks:** compare representative successful and failing API responses, including authorization failures, malformed inputs, empty results, provider failures, and unavailable inference. Normalize only truly nondeterministic fields such as generated timestamps/IDs, while checking their format and relationships separately.
2. **State checks:** compare database writes, audit entries, notifications, media references, and cleanup effects. Exercise concurrent updates, repeated requests, and partial provider failure.
3. **Targeted regression checks:** run relevant existing suites for inference, Firestore storage, private media, authorization, tree identity, treatment lifecycle, monitoring, and export compatibility, plus the JavaScript tests under `tests/js`. Add focused coverage only where changed behavior boundaries lack it.
4. **Visual and workflow checks:** compare fixed-viewport screenshots using the same browser, data, and fonts. Exercise farmer upload/history/map, expert verification/recommendations, treatment follow-ups, live detection, and desktop/mobile navigation. Compare CSV text and rendered PDF pages.
5. **Integration checks:** use isolated Firebase/Cloudinary resources and representative model images. Offline mocks cannot prove provider timing, numerical inference equivalence, or browser/PDF fidelity.
6. **Performance checks:** repeat the baseline workload on the same environment. Accept a change only when its improvement exceeds measurement noise and it causes no material regression in unrelated workflows, failure rate, memory, database reads, or provider cost.

Set numeric targets after collecting baseline data. Do not promise a speedup percentage without measurements. A small local change can proceed with focused checks during development; run the complete regression suite before release and explain any remaining failures against the fresh baseline.

Release one independently reversible change at a time through the normal review/deployment process. Preserve the prior revision and configuration for rollback. Compare post-release latency, errors, memory, read counts, and user workflows against baseline. Roll back on changed output, authorization, persistence, freshness, appearance, or sustained performance regression. Avoid schema migrations in this first pass so code rollback remains straightforward.

## Suggested first implementation batch

1. Establish fresh test results and benchmark fixtures.
2. Add minimal stage timing around upload, expert queue, monitoring overview, and exports.
3. Select the largest measured avoidable cost. Prefer request-local duplicate work removal when it offers comparable benefit to a concurrency or caching change.
4. Implement one focused optimization, demonstrate equivalence and performance, then review the next bottleneck.

## Results record

Complete one row per implemented optimization; leave benefits unclaimed until verified.

| Change / revision | Workload / samples | Before median / p95 | After median / p95 | RAM / reads / errors | Equivalence evidence | Rollback |
| --- | --- | --- | --- | --- | --- | --- |
| Not implemented | Not measured | Pending | Pending | Pending | Pending | Prior revision |

Completion means measured improvement on the agreed workload, preserved observable behavior, passing applicable checks, documented integration limits, and a practical rollback path.
