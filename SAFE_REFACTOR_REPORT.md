# NeuralCoco safe refactoring — 25 October 2026

**Final status: PARTIALLY VERIFIED.** Incremental cleanup is complete for the selected safely verifiable boundaries. No new known regressions; existing failures and live integrations prevent a full-system PASS.

Branch: `refactor/safe-system-cleanup`. Existing uncommitted work was preserved. No commit, push, merge, deployment, dependency upgrade, live record access, asset deletion, or model modification was performed.

## Files and improvements

| File | Result |
| --- | --- |
| `app/services/detection_service.py` | Consolidated farmer/expert list formatting after their existing identity checks. Queries, sorting, limits, defaults, cache copying, and invalidation remain unchanged. |
| `app/api/expert.py` | Present each matched audit record once instead of twice, eliminating one deep copy and repeated image-reference checks per matched event. |
| `app/services/export_service.py` | Separated historical JSON/prediction row normalization from CSV formatting and statistics without changing calculations or output. PDF implementation unchanged. |
| `tests/test_firestore_only_detection_store.py` | Added eight farmer/expert cases for forged identity rejection, historical identities, unchanged stored data, and returned-record/cache isolation. Existing tests unchanged. |
| `tests/test_export_compatibility.py` | Added a complete expected CSV output check covering JSON fields, malformed data, fallback fields, zero coordinates, and inventory totals. |
| `tests/js/monitoring_dates.test.js` | Added three checks for Philippine midnight, leap days, year boundaries, urgency labels, and missing dates. |
| `SAFE_REFACTOR_REPORT.md` | This report. |

Three files were already modified at task start and were retained and tested: `app/api/monitoring.py` (shared case lookup), `app/services/media_service.py` (shared private-storage-field removal), and `static/monitoring.js` (shared Philippine date fields). Those edits are not attributed to this execution.

## Validation

| Suite | Baseline | Final |
| --- | --- | --- |
| Full offline Python | 199 passed, 10 failed | 208 passed, 10 failed |
| JavaScript | 21 passed, 0 failed | 24 passed, 0 failed |
| Targeted Python | — | 41 passed, 0 failed |

All 209 original Python outcomes match exactly between baseline and final JUnit files; all nine added Python cases passed. The previously supplied 196/13 baseline was not reproduced.

The initial sandbox run stalled in Windows asyncio socket-pair creation. Offline tests completed outside the sandbox with the repository's guards still blocking external Firebase/network and historical SQLite access. A first completed run also had two inaccessible shared-temp-directory errors; using separate new workspace temp directories eliminated both before the recorded baseline.

All ten remaining failures are in `tests/test_tree_identity.py`. Its existing `infer(image, conf)` test double rejects the monitoring inference call's `annotate=False` keyword. The resulting 503 response causes downstream workflow, identity-review, and cleanup assertions to fail. Neither these tests nor inference behavior was changed to suppress failures. Exact failing case names and traces are in `tmp/refactor-baseline.xml` and `tmp/refactor-final.xml`.

Additional checks: 100 CSV comparisons against Git HEAD produced identical text and unchanged input records; all API route decorators, signatures, and declared dependencies match HEAD; `git diff --check` passed. Evidence under `tmp/` is ignored by Git.

## Preservation, limits, and remaining work

The changes retain ownership and expert checks, private media presentation, Firestore record shapes and references, tree IDs, audit enrichment, cache behavior, treatment monitoring, and CSV output to the extent covered by offline tests. Model internals, confidence thresholds, inference algorithms, atomic writes, GPS interpretation, PDF layout, frontend markup/styles, credentials, and configuration were left untouched. Historical backups were preserved; SQLite was not introduced.

Further changes to inference, transaction boundaries, provider recovery/cleanup, legacy RTDB/local fallbacks, and PDF rendering were deferred because broader rewrites would require additional behavioral evidence. Existing Firebase RTDB uses and local caches were observed and retained; this cleanup does not claim to migrate them to Firestore.

Live Firebase/Cloudinary behavior, real YOLO/OpenVINO numerical equivalence, browser appearance, and PDF visual fidelity were not verified by offline mocks. No performance benchmark was run; only the demonstrably redundant audit presentation call was removed.

Manual checks in an approved test environment:

- Sign in as farmer and expert; verify expired-token rejection and cross-farmer access denial.
- Upload a representative image; check inference, annotations, saved Firestore references, and authenticated Cloudinary delivery.
- Register/reuse trees; verify unique IDs, expert recommendations, queue refresh, and audit ownership links.
- Schedule/apply treatment, upload follow-ups, review identity/outcomes, and finish monitoring; confirm history remains intact.
- Check EXIF GPS and missing-GPS mapping, zero coordinates, CSV values, and PDF content/layout against known examples.
- Check desktop/mobile navigation, thumbnails, caching, and dates around Philippine midnight.

Git scope: six implementation files, three test files, and this report; three implementation files contained pre-existing edits. Tracked code/test diff before this report: 82 insertions, 52 deletions across seven files, plus two new test files. No unrelated tracked changes were identified.
