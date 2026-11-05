# Investigation integrity implementation

Implemented 2026-09-28 following the [original review](investigation-review.md) and the supplied A–D roadmap. [Open the updated interactive map](investigation-flow-v2.html).

## Result

The investigation lifecycle now requires affirmative measurement evidence before treating a missing finding as recovery. Human action targets and their measured outcomes retain version history. Recommendations are validated after corroboration, and case details explain the evidence and verification hold.

## A — Measurement and recovery

`FindingBatch` carries findings and measurement receipts independently. Each receipt records detector/metric, scope, horizon, measured period, status, coverage, source freshness, reason, observation time and measured value. Monthly, weekly, snapshot and scoped incident scans emit receipts; detector failures produce unavailable coverage.

A case advances only on an exact metric/horizon receipt with `measured_healthy`, `complete` coverage and `fresh` source data. Missing receipts become `not_remeasured`. Unavailable, stale, unknown and inapplicable measurements hold verification. A manually saved case cannot recover just because the scheduled scan did not visit its scope. Monthly verification uses the period in the receipt; repeated scans of one cadence unit do not count twice. Snapshot and weekly verification retain their existing daily cadence.

Snapshot outcome tracking and weekly streak resolution use the same coverage gate. A healthy weekly outcome uses the measured healthy value rather than the last anomalous value. Findings can still be surfaced when freshness is unknown, but their absence cannot establish recovery.

## B — Targets and outcomes

Target changes create a new numbered target version and clear the current outcome projection to pending. Unchanged saves preserve the version and verdict. Reopening a completed targeted action creates a new cycle; recompletion starts a new completion timestamp. Changing a target on an already completed action also starts a new measurement boundary. Prior targets, completion dates and outcomes remain available in history.

Outcome writes check the target version and action state, then update the current projection and append history in one transaction. History failure rolls back the projection. This transaction is local to the action outcome; scan-wide publication remains deferred.

Case objective designation checks case ownership, action relevance, opportunity policy and compatible cadence. The case pins the designated target version; changing that target requires re-designation before it can satisfy the case objective. Relative targets require an actual baseline; numeric inputs must be finite. Manual case saves share the case write lock.

Schema initialization adds target-version and outcome-history tables plus the required case/action columns. Existing targets are adopted as version 1 with their recorded dates and outcomes. Historical data is preserved, not retroactively revalidated. Older objective designations without a version pin safely hold until re-designated.

## C — Detector and recommendation robustness

Zero-baseline activation is metric-specific. Cancellation, delayed-order, Uber and PickMe rate metrics use an initial 5-percentage-point activation threshold with at least 15 observations of exposure. Order-volume activation uses an absolute count threshold of 15. Other metrics with a nonzero current value and zero baseline report that an activation rule is required; they are not silently declared healthy. These are explicit initial policies, not statistically calibrated thresholds.

Scans build deterministic evidence, apply corroboration, then generate recommendations against the final evidence. AI items must pass bounded schema validation: nonempty action and expected benefit, allowed priority, bounded optional owner role, and evidence references limited to supplied evidence. Invalid items are rejected individually; unavailable or rejected generation is exposed to the UI. The owner text is a suggested role, not an authenticated assignment.

Agent and investigation generation share one model selector: `OPENAI_MODEL`, falling back to `meta/muse-glimmer-30b` on the NVIDIA OpenAI-compatible endpoint. No live model-quality or threshold-calibration study was performed.

## D — Explanation and audit trail

Case details show measurement coverage, source freshness, last verification check, provenance and the reason verification is held. Target versions and historical outcomes are expandable. The timeline records meaningful measurement, evidence, policy, target and action-cycle changes. Current category determines the displayed resolution policy, including legacy rows. Recovery wording distinguishes a recovered condition from proof that an underlying cause was resolved.

## Completed stock-count variance follow-up

The two open completed-count cases previously reviewed were checked by session ID: both contained the exact same 172 completed sessions and Rs 13,399,873.16 gross absolute variance. The saved snapshots therefore establish a duplicate finding, not independent exposures. This comparison does not validate the underlying source freshness or the amount against source records.

New measurements and saved-case evidence now retain the exact date bounds, outlet scope, query time, gross absolute variance, signed net variance, both denominator values/rates, and line/value valuation coverage. Variance rates use gross absolute variance over either counted value or system value; these denominators include source-cost and catalog-estimate lines only. The UI shows that coverage and warns when unpriced lines can make the rates overstate the full-inventory rate. Query time is not presented as a source-refresh watermark.

When an ad-hoc completed-count investigation is saved, exact session-ID matches reuse a canonical active case and are linked as `duplicate_of`; partial intersections are linked as `overlaps` without merging case evidence or amounts. Existing cases are not automatically closed or rewritten: the two already-persisted duplicate cases will receive a relationship only if a matching scope is saved again, and still require a manager's disposition.

## Remaining boundaries

- Inventory/control/count sources that lack a trustworthy refresh watermark remain unknown. Sales recency and query time are not accepted as evidence that inventory refreshed. These cases cannot automatically clear until their source exposes reliable freshness coverage.
- Manually scoped cases need actual matching remeasurement; this work does not add a scheduler for every arbitrary manual scope.
- A scan's application-database changes now commit together with its daily brief. If any scan stage fails, case, action-outcome, tracking, customer-snapshot, and health-history writes from that scan roll back; reporting-database reads remain read-only and outside this transaction.
- New detectors and broad UI redesign remain deferred.
- Existing historical outcomes are retained as recorded; the migration does not recompute them.

## Validation

- Default backend suite: 448 passed, 11 real-backend tests deselected. This run preceded the final monthly receipt-period correction.
- After that correction: 139 relevant lifecycle/API/regression tests passed.
- Final integrity suite after timestamp cleanup: 20 passed, including failure rollback, legacy adoption, target cycles, coverage holds and receipt-period verification.
- Frontend unit tests: 64 passed; production build passed. Existing bundle-size and deprecation warnings remain.
- Browser regressions: all 5 passed (three core workflows and two new integrity/coverage/history checks). Tests use mocked APIs and temporary databases, not production mutations.
- Syntax/import lint checks passed for the final touched Python files; repository-wide lint still has pre-existing findings.
- Follow-up, 19 October 2026: a forced late-stage scan failure now verifies rollback of case/tracking writes and retention of the last successful brief; all 6 scan-pipeline tests passed. The refresh-request error/retry browser test and existing Investigate domains browser test passed, as did all 80 frontend unit tests and the production build. The default backend suite reported 600 passed; 3 unrelated tests failed on reporting-database connection timeouts.

The historical probe script describes the original defects and is not a post-fix acceptance suite. The maintained regressions are in `tests/test_investigation_integrity.py` and `frontend/e2e/investigation-integrity.spec.js`.

## Map validation receipt

The updated map passed 9/9 showcase checks with zero errors or warnings and automated browser checks at 1440×900, 1600×1000, 1920×1080 and 2048×1320. Light and dark screenshots were visually inspected after generation. The browser receipt retains its automated `visualReview: pending` field; that field is not a claim of human approval.

- Specification SHA-256: `91c846ef7524557676570573408d153b31e007875f1fc9a8f05024aa9dad2a9b`
- HTML SHA-256: `e73ad40ad74ab86012d8cd6ca16221356777d869f54eef1313dc2465e90e23ba`
- [Automated browser receipt](investigation-flow-v2.visual-check.json)
