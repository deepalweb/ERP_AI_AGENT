# Home briefing repair — 18 September 2026

Scope: Home and its existing briefing/management contracts only. No Explore
expansion, new intelligence model, or unrelated page redesign.

## Observed failure and root cause

The real app initially had a fresh cached briefing and rendered successfully.
The browser received HTTP 200 / `status: ok`, displayed health 80/100 and 13
long-term findings, and did not remain stuck after the response completed.

To reproduce the reported loading state safely, the application database was
copied using SQLite backup into a new temporary directory. The latest copied
brief was removed, exposing the **real** older snapshot generated
`2026-09-17T08:00:24.153642`. No business values were fabricated or edited.

With that expired cache, Home displayed “Loading briefing…” and unavailable
timestamps after 15 seconds. There was no completed `/brief` response. The
backend discarded snapshots older than 24 hours and called `run_daily_scan`
inline. The scan performs reporting queries, model explanations, case updates,
health and customer calculations under the shared scan lock. Another request
that joined this scan waited **160.984 seconds** before receiving HTTP 200 and
real findings. That duration is the joining request's wait, not the total scan
duration. No backend exception was observed in that completed run.

React was waiting for data, not failing to render a completed payload. Its
default retries could additionally repeat a failed slow read; the old handler
returned HTTP 200 even for `status: error`. Old evidence was unavailable to the
page during refresh. Independently fetched actions therefore appeared alone.
The empty blocked panel stretched to the height of its action-list sibling.

## Fix

- `/brief` returns the latest stored evidence immediately with `cache_status`
  and refresh state. Expired cache is explicit, not presented as current.
- Cold cache returns HTTP 202 and `status: loading`. Actual failures return
  HTTP 503; a failed background refresh preserves prior evidence and exposes
  a retry. No reporting failure becomes a successful empty briefing.
- A single background refresh per server process uses the existing
  cross-process scan lock. Failure is retained until explicit retry through
  `POST /brief/refresh`. The existing scheduler and other page contracts remain.
- Home polls only while generation is running. Its GET has a 15-second network
  timeout and no automatic error retries; cached UI data survives polling errors.
- Backend Home projection orders existing impact scores and counts supplied
  priorities separately for long-term and weekly findings. React displays
  these values and filters rows by domain; it does not calculate business scores.
- Health and customer opportunity remain independent of the horizon selector.
  The customer card includes its actual as-of date. Briefing check time and
  order-source timestamp are separate.
- Persistent action queries are independent of the current findings. Stable
  query keys avoid repeating both requests just because the first briefing
  arrives; a later completed generation invalidates management previews.
- Empty blocked verification is a compact row retaining access to Outcomes.
  Existing resolved cases are read separately; “no resolved cases” does not
  imply all business problems have disappeared.

## Home data-source inventory

| Component | Actual source and fields | Availability / limits |
|---|---|---|
| Outlet scope | `GET /store-scope` → `CELESTE_STORE_NAMES`, matching configured IDs `(1,2,45,46)` | Four configured Celeste outlets; not a claim that all warehouse/customer metrics have identical scope. Shared React Query cache with Header. |
| Briefing check time | `/brief.generated_at` → latest `daily_brief` row | Snapshot generation time, not page-load time or source freshness. |
| Order freshness | `/brief.data_freshness.latest_order_at/status/days_stale` → `brain._data_freshness`, maximum Odoo `sale_order.date_order` over configured outlets | Latest observed order is 15 September. This is order-source evidence only; stock, supplier and fulfillment feeds have no independent ingestion watermarks here. |
| Business health | `/brief.health` → `brain._snapshot_health` → `health.get_business_health`; prior deltas from `health_history` | Overall/domain scores, drivers and deltas exist. Prior comparison timestamp is absent from the payload. Composite uses metric-specific scopes and periods; customer concentration includes all-time reporting sales. Not recomputed on tab selection. |
| Severity counts | `/brief.home.long/week.severity_counts` | Backend counts existing `priority`, with `category=opportunity` shown as positive. Unknown classification remains explicit in the contract. Finding counts are not case counts. |
| Priority findings | `/brief.home.*.priority_ids/ranked_ids`, referencing `investigations`, `state_findings`, or `short_term_findings` | Selects three using existing `business_impact_score`. No new priority model or invented score. Long-term combines monthly and current-state findings; weekly stays separate. |
| Business impact | Each finding's `impact.detail`, `confirm`, `root_cause`, recommendations and breakdowns | Missing quantified impact remains “Impact not quantified.” No cross-metric monetary sum is manufactured. Expanded forecasts remain the existing lazily fetched `/forecast/forward-look` heuristic. |
| Customer opportunity | `/brief.customer_finding` → scoped customer `_v2` snapshots → `build_home_finding` | Approaching/high-confidence/overdue counts and estimated demand exist. As-of date is shown. This is qualifying repeat-customer demand, not booked sales or all-outlet demand. Older incompatible cards are omitted; absence is not zero customers. |
| Outstanding actions | `/management/actions?status=outstanding&relevant=true&limit=3` → all `finding_actions` | Persistent records across all periods, even absent from the current brief. Description, intended result, owner, status, deadline and total are available when saved. |
| Blocked verification | Same projection with `outcome_status=blocked`, `relevant=true`, limit 2 | Existing outcome status; not inferred from overdue work. Observed zero records. |
| Case outcomes | Existing `/cases?status=resolved&limit=3` → `business_cases` and latest stored observation | Stored case IDs, resolution verdict, closure date and latest title. Existing API sorts by last-seen time, not strictly closure time. Observed zero resolved cases. |
| All findings | Existing three `/brief` finding lists plus backend ranked IDs | Domain filtering and compact expandable rows reuse the same response. Detailed case navigation uses stored `case_id`; no metric/title-to-case inference. |

## Saved actions and association findings

All seven inspected action records have **null** `action_taken` and
`intended_result`; this was not a dropped frontend field. The UI now says
“No action description saved” and shows the exact stored finding identifier.
When text is present, the saved description is preferred, then intended result.

The record `stock_availability-2026-09-14` actually stores deadline
**2020-01-01**, last updated **2026-09-14T07:42:03.889016**. It was not caused by
date parsing. It is preserved with a review note, not silently corrected.

All seven records have null `case_id` and zero exact `case_observations` matches.
They were last updated between 9 and 14 September; the first case was opened
on 18 September. Evidence supports historical pre-Case-Store records, not seven
broken foreign-key links. No safe automatic backfill can be derived.

The read projection distinguishes direct stored links, exact observation links
(where an action's association was never populated), absent associations and
explicit links to missing cases. Observation resolution uses the stored finding
ID and stored case ID only; it neither guesses by metric nor updates old records.

## Browser observations and artifacts

Real Chromium used frontend port 5191 and backend port 8198, with an isolated
SQLite backup. Reporting sessions enforced PostgreSQL read-only transactions.
No route mocks were used for these observations. Production `app.db` retained
its original latest briefing timestamp throughout the inspection.

- Corrected expired-cache response: HTTP 200 with stale evidence in **1.034 s**
  from initial navigation on the first controlled run; a later run under load
  took **2.524 s**. Both showed evidence while refreshing.
- Final fresh-cache browser response arrived in **1.665 s** from navigation.
- A subsequent observer joined the running refresh and saw it complete after
  **19.38 s**, with the stale banner disappearing and current evidence rendering
  automatically, without reload. This is the observer's remaining wait, not
  total scan runtime.
- Successful real snapshot: health **80/100**, **13** long-term findings,
  **0** weekly findings, three decision cards, and actual customer opportunity.
- Both tabs worked; health and customer opportunity text stayed identical.
- Three priority links matched stored case IDs; a clicked link fetched its
  real `/cases/{id}` successfully.
- Blank legacy action descriptions, exact 2020 date, zero blocked verification
  and zero case outcomes were shown honestly.
- The empty blocked panel measured **56.6 px** high in the desktop validation.
- Widths **1440, 834 and 390 px** had no horizontal document overflow and no
  browser JavaScript errors in the successful validation.

Screenshots (local, excluded from Git):

- [Before: expired cache/loading](../artifacts/home-review/before-stale-loading.png)
- [Before: successfully loaded old UI](../artifacts/home-review/before-desktop.png)
- [After: full briefing](../artifacts/home-review/after-desktop.png)
- [After: stale evidence remains visible](../artifacts/home-review/after-stale-desktop.png)
- [After: weekly horizon](../artifacts/home-review/after-week.png)
- [After: mobile](../artifacts/home-review/after-390.png)
- [After: automatic refresh completed](../artifacts/home-review/after-refresh-complete.png)

The first long-running browser refresh observer was interrupted by Vite hot
reload while code was still changing; that observer run is not counted as a
successful end-to-end refresh transition.
The subsequent stable observer run passed that transition.

## Validation

- Default backend suite: **287 passed**, 11 explicit real-backend tests excluded.
- Final focused Home/management suite: **10 passed**, including exact stored
  observation association without a write migration.
- Home browser state tests: **6 passed** (synthetic responses, separately from
  the real-browser observations above).
- Broader frontend browser suite: **15 passed**, including Home, existing case
  workflow and management navigation.
- Frontend unit tests: **20 passed**.
- Production build passed. Existing bundle-size warning remains.
- Frontend lint passed with **9 existing fast-refresh warnings**, no errors.
- Ruff checks passed for the new coordinator/tests and management projection.

## Files changed for this task

- `backend/app/main.py`: nonblocking Home read and explicit refresh/status codes.
- `backend/app/services/brain.py`: opt-in stale-cache reading; original scan consumers preserved.
- `backend/app/services/home_briefing.py`: refresh coordination and Home presentation projection.
- `backend/app/services/management.py`: saved description, date review note, exact association classification.
- `frontend/src/api.js`: bounded briefing requests, response validation, refresh API.
- `frontend/src/routes/Home.jsx`: state handling, server ranking/counts, persistent follow-up, outcomes and honest missing fields.
- `frontend/src/routes/Home.css`: compact empty/loading layouts.
- `tests/test_home_briefing.py`, `tests/test_case_api.py`, `tests/test_management_workspace.py`: regression and contract coverage.
- `frontend/e2e/home-briefing.spec.js`, `frontend/e2e/fixtures.js`: loading/error/stale/retry/horizon contracts.
- `.gitignore`: local browser/reporting artifacts excluded from version control.
- This report: source inventory, findings and validation evidence.

## Remaining limitations

The actual scan is still slow; this repair removes it from Home's read path.
The refresh coordinator is process-local, and its failure message is not a
durable job log. The existing cross-process file lock prevents simultaneous
scan writes; a restart can lose transient refresh status. Source freshness is
still limited to orders. Health is not a uniformly scoped outlet-only score.
Historical action descriptions/associations cannot be recovered from missing
records, and the 2020 deadline needs a manager's correction if unintended.
Missing impact, root-cause evidence and case verdicts are not manufactured.
