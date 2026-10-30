# NeuralCoco performance optimization — 27 October 2026

Status: one measured, independently reversible optimization retained. Offline equivalence checks passed for the changed boundary. This is not full-system or deployment approval: ten existing tests still fail and live integrations were not exercised.

## Scope and implementation

Read `SYSTEM_OPTIMIZATION_PLAN.md` completely and inspected the requested application, upload, inference, detection, monitoring, frontend, media and export paths plus the prior refactor report. Existing request coalescing, 45-second caches, defensive copies, ordered/limited queries, inference lock, partial upload thread offloading, and frontend no-cache headers were preserved.

Priority A finding: `apply_record_defaults` called `Path.exists()` for both fallback image files even when the corresponding URL was already present. For 500 records with two remote URLs, these are 1,000 filesystem probes whose results cannot affect the returned record. An unchanged-versus-unchanged control measured medians of 54.40/53.20 ms (standard deviations 6.80/7.16 ms), establishing that small timing differences should not be claimed as gains.

The only production edit reverses the operands of two `and` conditions: check the existing URL first, then probe the fallback file only if needed. Stale local URL validation still runs first; missing URLs still probe the same files in the same order, and the annotated-to-original fallback remains intact. No copies, authorization checks, provider operations, or writes were removed. This hypothesis and the synthetic workload were identified before editing; the original implementation was measured before the change.

Files added or modified:

- `app/domain/detection.py`: two short-circuit conditions.
- `tests/test_record_defaults_optimization.py`: 16 combinations covering remote, missing, stale-local and empty URLs with all original/annotated fallback-file combinations.
- `tools/benchmark_record_defaults.py`: guarded, paired normalization benchmark against original Git source.
- `tools/benchmark_optimization_workflows.py`: guarded service benchmarks using the repository's in-memory Firestore double, complete output comparisons, and stored-state checks.
- `SYSTEM_OPTIMIZATION_REPORT.md`: this report.

The user's initially untracked `SYSTEM_OPTIMIZATION_PLAN.md` was left unchanged. No branch, commit, push, deployment, dependency change or live-record operation was performed.

## Measurements

Baseline revision: `852070a2f3932b814d181e5662a875eb1ee54f18`. Windows 11 build 26200, Python 3.13.0, AMD64 Family 23 Model 104 Stepping 1; environment reports 16 logical processors. Dependency versions were not changed. Benchmarks use 500 synthetic records with remote original/annotated URLs, fixed IDs and predictions, and no private data. These timings exclude real network/provider latency and are not production response-time estimates.

All reported durations use `time.perf_counter()`. Each final variant has 60 samples, alternating baseline/optimized order to reduce timing drift. p95 is the sorted sample at index `int(.95 * (n - 1))`; variability below is sample standard deviation. Service cache misses explicitly invalidate the cache before measurement; hits explicitly populate it first. These are cache-cold measurements in an initialized process, not application/model cold starts.

| Workload | Before median / p95 (ms) | After median / p95 (ms) | Before / after standard deviation (ms) |
| --- | --- | --- | --- |
| Record normalization | 72.39 / 79.46 | 10.34 / 12.65 | 6.25 / 1.26 |
| Expert queue, cache miss | 81.69 / 116.31 | 29.29 / 42.83 | 17.99 / 9.96 |
| Expert queue, cache hit | 6.01 / 9.25 | 5.49 / 8.22 | 1.67 / 1.16 |
| Farmer history | 72.85 / 106.82 | 22.25 / 31.39 | 14.87 / 4.07 |
| CSV, including cache-miss record loading | 80.64 / 105.09 | 31.05 / 37.72 | 11.72 / 6.89 |

Normalization's first batch, reported separately, was 66.38 ms before and 10.70 ms after. An earlier paired run also showed a clear improvement (68.08 to 9.95 ms median). The final normalization median improvement is about 86%; expert cache-miss service improvement is about 64%. These gains exceed observed noise. No cache-hit improvement is claimed: that code is unchanged and its difference is within variability. An initial non-alternating service run showed drift in cache-hit timings; the final alternating run resolved that concern without code changes.

CPU time across the 60 normalization batches (`time.process_time`) was 4,109.38 ms before and 500.00 ms after. Windows CPU-time granularity makes individual short samples unreliable, so use the aggregate. `tracemalloc` peak for the normalization operation was 242,848 versus 241,116 bytes; batch creation was excluded. This is Python allocation tracking, not process RSS or whole-system RAM, and does not establish a material RAM reduction.

Instrumented synthetic Firestore counts were unchanged: one query/500 document snapshots per cache-miss expert queue, farmer history, and CSV operation; zero queries/documents per warmed expert queue operation. No Firestore billing or Cloudinary cost reduction is claimed. Reduced local CPU/filesystem work may help resource efficiency; monetary savings were not measured.

Raw summaries: `tmp/optimization-precheck.json`, `tmp/optimization-paired-final.json`, and `tmp/optimization-workflows-paired.json`. Earlier exploratory summaries remain in `tmp/optimization-paired.json` and `tmp/optimization-workflows.json`. These local evidence files are Git-ignored. Reproduce from the repository root with:

```powershell
python tools/benchmark_record_defaults.py
python tools/benchmark_optimization_workflows.py
```

Both scripts install `tests/conftest.py` guards before application imports. They block external providers/network and historical SQLite and do not load deployment credentials. The baseline function is extracted from the recorded Git revision, so reproduction does not depend on an ignored source backup.

## Validation

| Check | Baseline | Final |
| --- | --- | --- |
| Complete offline Python suite | 208 passed, 10 failed | 224 passed, 10 failed |
| JavaScript suite | 25 passed | 25 passed |
| Original Python test outcomes compared by classname/name | 218 original cases | All 218 unchanged |
| New image fallback cases | Not present | 16 passed |
| Whitespace check | — | `git diff --check` passed |

Full tests were run with `python -m pytest tests -q -p no:cacheprovider`, fresh workspace temp directories, and JUnit output. The initial sandbox run stalled; the completed runs used the approved execution outside the sandbox with offline guards retained. Logs and JUnit files: `tmp/optimization-baseline-2.log`, `tmp/optimization-baseline-2.xml`, `tmp/optimization-final.log`, `tmp/optimization-final.xml`; final JS log: `tmp/optimization-js-final.log`.

The same ten failures remain in `tests/test_tree_identity.py`. Its inference test double rejects the `annotate=False` argument used by monitoring, leading to inference failure and downstream response/identity assertions. Existing tests were not weakened or edited. See the JUnit/log files for exact names and traces.

The service benchmark compared full returned queue/history records and exact CSV text across all variants and iterations. Synthetic stored data remained identical after every operation. The existing offline suites cover inference failure handling/settings at mocked boundaries, ownership/expert authorization, private media, Firestore persistence, Cloudinary failure handling, GPS, monitoring and treatment workflows; their outcomes did not regress. This does not establish that the ten failing workflows work end to end.

## Preservation, deferred work, and limits

Production diff inspection confirms no edits to model weights, inference settings, backend, locking, confidence/class mappings, encoding, annotations, API definitions, auth, schemas, audit writes, treatment transitions, date/GPS logic, cache TTL/invalidation, CSS, markup, frontend behavior, CSV formatting or PDF layout. Changed record output and fallback behavior were checked directly. Actual model numerical outputs and browser pixels were not compared.

No isolated Firebase/Cloudinary test deployment or authenticated browser session was used. Therefore live upload/provider processing, real Firestore billing/read latency, real YOLO execution/lock-wait timing, realistic concurrent farmer/expert traffic, browser render cost, desktop/mobile screenshots, monitoring-overview performance, and PDF timing/render fidelity remain unmeasured. Model weights exist locally, but a representative sanitized model-validation dataset was not supplied and the deployment singleton/startup path was not invoked for benchmarking. Offline inference tests are not real-model benchmarks. Full process CPU/RAM and provider error rates under load also remain unmeasured.

Additional candidates were deliberately not implemented: moving upload/encoding/PDF work to threads needs mixed-workload and cancellation/concurrency evidence; monitoring scans/query changes need representative result-set and legacy-record checks; cache-miss coalescing needs invalidation-race tests; frontend calculation changes require browser profiling and presentation checks. They are investigation candidates, not confirmed measured bottlenecks. The retained change adds no concurrency/shared state and leaves cache-hit behavior untouched.

No production optimization was reverted. Only this measured change is retained; no claim is made that all system bottlenecks have been resolved. Rollback is limited to swapping the operands back in the two image fallback conditions in `app/domain/detection.py`; no data rollback or migration is required.
