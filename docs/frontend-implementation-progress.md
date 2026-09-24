# Frontend implementation — 18 September 2026

The approved implementation brief supersedes the audit's initial sequencing:
management integrity → design foundation/Today → management workspaces →
analytical contract repairs → Explore integrations → remaining frontend completion.

## Milestone 1: Management data integrity

Implemented a small read-only projection over the existing `finding_actions`
and `business_cases` tables. No action store, Case Store schema, detection engine,
or resolution policy was added or replaced.

- `GET /management/actions` returns individual actions, including older actions
  and actions without a case association. Status, relevance, and outcome filters
  execute before pagination. Responses retain finding ID, stored case ID, action
  objective/target, owner/deadline, outcome, and evidence-version fields.
- `GET /management/summary` aggregates case and action counts across the database,
  independently of displayed pages. Relevant outstanding/completed actions and
  pending/blocked measurements are counted separately from case states/verdicts.
- `GET /findings/{finding_id}/case` resolves old finding deep links using persisted
  associations. A missing association remains missing; no metric/title guess.
- Actions and Outcomes no longer depend on the single latest-action field on
  `/cases`. That field remains available for compatibility with other consumers.
- Today uses persisted case states and verdicts. It no longer treats legacy
  tracking outcomes or frontend lifecycle derivations as case resolution.
- Case saves invalidate management summaries/lists as well as case detail.

Files: `backend/app/services/management.py`, `backend/app/main.py`,
`frontend/src/api.js`, `frontend/src/routes/{Home,Actions,Outcomes,CaseDetail}.jsx`,
`frontend/src/design/Management.jsx`, `tests/test_management_workspace.py`.

Validation at the first checkpoint: **98 backend tests**, **20 frontend unit
tests**, production build, and lint (warnings, no errors). Regression fixtures
include 205 cases, an older active action alongside newer completed/irrelevant
actions, server-side filters/pagination, and unresolved legacy associations.

## Milestone 2: Design foundation and Today

Replaced the old horizontal navigation and dark briefing layout with a forest-green
sidebar, light workspace, consistent typography, spacing, surfaces, and status
colors. The desktop navigation groups Management and Intelligence; small screens
use an expandable navigation menu. Customer Predictions is a primary destination.
Existing routes, notifications, authentication, and sign-out remain available.

Today is rebuilt around global management totals, actual order-data freshness,
the brief's separate check time, latest open/reopened cases, outstanding actions,
blocked measurements, and recent case-resolution verdicts. Business health and
the analytical briefing are secondary disclosures. Values come from APIs; no
sample business data ships in application components. Missing counts show a dash.

Files: `frontend/src/{App,Header}.jsx`, `frontend/src/index.css`,
`frontend/src/routes/Home.jsx`, `frontend/src/design/Management.jsx`.

Validation at this checkpoint: **7 browser scenarios passed**, **20 frontend
unit tests passed**, build and lint completed. Inspected screenshots at desktop
(1440 px) and mobile (390 px); browser checks also cover tablet (834 px), horizontal
overflow, stale-data labeling, and navigation. Screenshots use explicitly synthetic
test fixtures, not live measurements.

## Milestone 3: Management workspaces

- Investigate adopts the shared page heading, filters, and case-list layout.
- Case detail adopts the shared surfaces and typography while retaining its
  existing evidence, objectives, recurrence/history, first-action creation,
  version checks, conflict review, and action forms.
- Actions shows each work item independently, with server-filtered pages and
  links to the specific action within its authoritative case.
- Outcomes separates case resolution from action measurements. Completed work,
  achieved targets, recovered/captured/expired cases, and pending/blocked/
  inconclusive measurements remain distinct backend states.
- The global assistant adopts shared tokens and a native modal dialog, fixing
  stacking, focus containment, Escape dismissal, and focus return. Conversation
  history and specialized cards remain; no case-awareness claim was introduced.

Files: `frontend/src/routes/{Investigate,CaseDetail,Actions,Outcomes}.jsx`,
`frontend/src/design/Management.jsx`, `frontend/src/chat/AskCelesteOverlay.jsx`,
`frontend/src/index.css`, `frontend/e2e/{core-workflow,management-redesign}.spec.js`.

Latest deterministic checks: **113 backend tests passed**, **20 frontend unit
tests passed**, and **9 fixture-based browser scenarios passed**. The browser
suite verifies old-action visibility, page 11 / case 205, stored finding links,
target validation, stale-evidence saves, narrow-screen management routes,
assistant keyboard focus, and explicit error/missing-value states.

## Real full-stack validation

The real suite uses Chromium → React → FastAPI → real reporting data, with
`APP_DATABASE_URL` and the scan lock overridden to temporary isolated paths.
PostgreSQL connections inherit `default_transaction_read_only=on` in this test
process. Production application state is not used for test writes.

The initial real scan succeeded and created genuine cases in the isolated
database. Its subsequent browser run hit an artifact-directory collision with
the fixture suite; the configurations now use separate output directories.
The next run created an action and target through the real browser, verified
their persisted evidence and reloaded them, then exceeded the six-minute rescan
request timeout. Dependent tests now run serially, preventing a worker restart
from attempting to delete an application database still used by a scan.

An isolated diagnostic sampled active reporting queries and a prolonged model
recommendation request. Two transport-level regression tests reproduced the
SDK's 600-second read timeout and three total attempts. The scan's existing
client factory now sets a **45-second SDK timeout and zero automatic retries**.
Optional recommendations/synthesis retain their existing `[]`/`None` fallback;
no evidence, metric, resolution rule, or model architecture changed. This can
leave recommendations unavailable when the provider is slow, which the existing
case view already represents explicitly. Reporting-query latency remains separate.
The installed SDK signature and [official timeout guidance](https://developers.openai.com/api/docs/guides/flex-processing#api-request-timeouts)
were checked before the change.

Additional files: `mcp_server/tools/investigation.py`,
`tests/test_scan_model_timeout.py`. The two new tests failed before the fix;
14 focused timeout/synthesis/scan-pipeline tests passed afterward. The live suite
now has a bounded ten-minute allowance per scan and retains all assertions.
The final isolated real-backend rerun passed all five tests in 9.2 minutes, including the rescan, action/target persistence, inventory evidence, and production-database isolation. The consolidated backend suite passed 127 tests.

Harness files: `frontend/playwright.real-backend.config.js`,
`frontend/e2e-real-backend/full-stack.spec.js`, `frontend/.gitignore`.

## Remaining scope and limitations

- Milestones 4–6 are not complete: analytical date/revenue/scope repairs, the new
  inventory/channel/profitability integrations, and full Explore/Customer
  Predictions/specialized-card layout migration remain in their approved order.
  Existing analytical screens inherit the new palette and shell.
- The existing bundle-size advisory and nine Fast Refresh lint warnings remain;
  these are not functional test failures. No frontend dependencies were removed.
- Today displays bounded previews with links to paginated workspaces. Global
  totals are database aggregates; previews are not presented as exhaustive lists.
- Offset pagination is stable for unchanged records; simultaneous updates can
  reorder subsequent pages. There is no cross-request snapshot guarantee.
- Case observations retain the existing daily-snapshot semantics. Saved decision
  evidence continues to live in the existing history; no history rewrite occurred.
- Tests establish navigation and persistence mechanics, not financial correctness
  of analytical services awaiting milestone 4.

## Visual direction correction — user reference

The user rejected the spacious light/green treatment and supplied a dense dark analytics reference. The shared foundation now uses midnight navy surfaces, violet accents, compact navigation and KPI panels. Today adds backend-derived case lifecycle and action workload charts, a verification queue, and a horizontally scrollable case table. These are current-state counts, not fabricated historical trends or inventory metrics.

Files: `frontend/src/index.css`, `frontend/src/routes/Home.jsx`, and new `frontend/src/design/WorkspaceAnalytics.jsx`. Build and lint pass (the existing nine warnings remain). The broader analytical integrations in milestones 4–6 remain outstanding.

Visual verification: all six management browser scenarios passed (five in the first run; the Today scenario passed after updating its selector from a card heading to the new table link). Desktop, tablet, and mobile screenshots were reviewed; the responsive test confirms no page overflow at 834px and 390px.

## Original layout restored at user request

Restored the original top navigation, Business Briefing home with Long-term / This week tabs, and original theme/typography from commit `49b3589`. Removed the sidebar and Today chart/table presentation from the active home. Kept current backend code, management routes, conflict-aware action components, sign-out, notifications, and stored finding-to-case redirects. Newer routes retain compatible styling. The replaced visual files were backed up outside the repository in the system temporary directory.

## Option A promoted to the live Home

The user selected the Executive briefing prototype. Home now renders five sections with real briefing data, independent persisted-action follow-up, compact ranked findings, lazy forecasts, and stored case links. Original top navigation remains. Sample layouts are archived on local branch `prototype/home-briefing-options`; their routing and source files were removed from the active frontend. See [the decision and validation record](home-prototype-review.md). Production build and all twelve applicable browser scenarios passed across the initial run and focused rerun. No backend logic changed.
