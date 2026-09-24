# Celeste Intelligence frontend redesign plan

Status: implementation started on 2026-09-18. Management integrity, the new
navigation/Today foundation, and management workspace changes are implemented.
See the [milestone report](frontend-implementation-progress.md) for changed files,
verified checks, live-test status, and remaining work.

The approved sequence is now: management integrity → foundation/Today →
management workspaces → analytical contract repairs → Explore integrations →
remaining frontend completion. This sequence supersedes the original staging.

The [frontend intelligence audit](frontend-intelligence-audit.md) contains the
22-tool capability mapping, actual response contracts, Case Engine comparison,
API gaps, integration prerequisites, and acceptance tests. Its findings and
sequencing take precedence over the original proposal below.

## Objective

Create a consistent, readable business-intelligence interface that helps a manager
understand what changed, inspect the evidence, decide what to do, and track the
result. Cover the entire frontend, including AI result cards, login, and failure states.

## Current starting point

- React, Vite, Tailwind, React Query, React Router, and Recharts are already in place.
- Existing routes are Home (`/`), Investigate (`/investigate`), case detail
  (`/investigate/:caseId`), Actions (`/actions`), Outcomes (`/outcomes`), Explore
  (`/dashboard`), and Customer Predictions (`/customers/predictions`).
- The Case Store already persists case identity, observations, evidence versions,
  decision history, and resolution. Retain this operational foundation.
- Ask Celeste is a global overlay with conversation history and specialized tool-result cards.
- Shared theme variables coexist with legacy chat styles and repeated chart/card patterns.
- The current palette is dark with teal accents. The final visual direction remains
  open to the user's preference; the structural plan supports either light or dark.
- Recent fixes added local/shared access handling, configurable API routing, and error propagation.

## Proposed navigation and screen responsibilities

Use a collapsible desktop sidebar and a compact mobile navigation menu. Keep the
top bar for the page title, actual data scope, freshness where relevant, notifications,
Ask Celeste, and account controls. Preserve existing URLs and finding deep links.

| Area | Primary user question | Proposed experience |
| --- | --- | --- |
| Today | What needs my attention today? | Decision brief with priorities, next actions, and stored links to authoritative cases |
| Investigate | What does the evidence support? | Case list and persisted case detail with observations, evidence versions, uncertainty, recommendations, and decisions |
| Actions | What are we doing about it? | Individual actions with status, owner, deadline, targets, and evidence links; retain multiple actions per case |
| Outcomes | Did the action achieve its target, and is the case resolved? | Separate action measurements and confidence from case resolution policy and verdict |
| Explore | Where is the business changing? | One workspace with Overview, Sales, Products, Inventory, Channels, Profitability, and Customers sections introduced as supported content becomes available |
| Customer Predictions | Who is due or overdue to order? | Preserve the separate prediction workspace, search, filters, pagination, explanations, and next-basket details |
| Ask Celeste | What else can I learn? | Desktop side panel, full-screen mobile conversation, consistent answer cards, follow-up suggestions, and conversation history |
| Access | How do I enter or leave the app? | Polished shared-login screen, local access handling, sign-out, and understandable connection errors |

Group Today, Investigate, Actions, and Outcomes under Management; Explore and
Customer Predictions under Intelligence. Ask Celeste remains global. Analytical
queries must not automatically create cases, and tools do not each need a page.

The detailed investigation view should follow a consistent reading order:
observation → contributors → evidence and uncertainty → suggested action → owner
and deadline → measured outcome. Keep summaries compact and reveal supporting
detail on demand. Preserve the user's page context when opening a finding.

## Visual and interaction principles

- One typography scale, spacing system, surface hierarchy, icon treatment, and set of controls.
- Use the brand accent for interaction, reserving status colors for meaningful business states.
- Use concise labels, readable body text, aligned numbers, and clear empty states.
- Standardize currency as Rs, dates, percentages, tooltips, legends, and chart annotations.
- Show each metric's actual period. Distinguish partial periods, estimates, predictions,
  stale data, and missing data. Do not render missing values as zero.
- Read outlet scope from the API; do not hardcode the outlet count in the UI.
- Use restrained transitions and respect reduced-motion preferences.
- Design keyboard navigation, focus visibility, contrast, and non-color status cues from the start.

## Implementation sequence

### 1. Complete audit and management/navigation alignment

The capability and contract audit is complete in the companion document. Use it
to align the existing management screens and grouped navigation. Today must link
by persisted case association, Actions must retain individual actions, and
Outcomes must distinguish action results from case resolution. Address the
current latest-action-only view and 200-case pagination limit.

Deliverables: screen inventory, navigation map, desktop/mobile wireframes, and
one detailed Today concept. Use representative fixtures with clearly labeled
sample values, rather than repeatedly querying Odoo or the model for visual work.

### 2. Design foundation and application shell

Establish semantic color tokens, typography, spacing, borders, elevation, and
responsive rules. Build reusable buttons, fields, badges, cards, tabs, tables,
drawers/dialogs, loading states, and error messages. Implement navigation and
the page shell, then use the Today concept to validate the system in context.

Deliverable: one coherent shell and component set used by a complete Today screen.

### 3. Findings and action workflow

Redesign findings, investigation details, evidence presentation, action editing,
and the Actions page. Preserve stored case/finding associations, observation
versions, stale-evidence review, relevance, status, owner, deadline, targets, and
notes. Make saving, success, and failure visible. Distinguish a manager marking
work complete from a measured improvement in business results.

Deliverable: a complete finding-to-action-to-outcome journey, including deep links.

### 4. Explore and customer intelligence

Before new charts, repair the audited date boundaries, revenue/coverage bases,
affected outlet scoping, and sparse-history scan failure in shared services, with
focused regression tests. Keep the Case Store and detection engine intact.

Deliver the first new sections in this order:

1. Inventory: existing depth/availability plus a small read-only outlet-stock
   adapter; label snapshots, units, valuation coverage, and unknown freshness.
2. Channels: existing contribution endpoint and metric investigation reads plus
   a removed-items adapter; distinguish failed orders from removed item lines.
3. Profitability: a gross-margin adapter after reconciliation; disclose current
   catalog-cost estimates and expose only supported totals and period-compatible
   existing monthly/outlet analysis.

Reuse deterministic tool functions through thin HTTP adapters. Do not add a
second alert engine, investigation pipeline, or AI infrastructure. The audit
defines exact existing-versus-missing routes and their acceptance gates.

Restructure dense analytics into focused sections. Standardize charts and tables,
preserve tooltips and breakdowns, and improve customer search, filters, pagination,
prediction explanations, and next-basket details. Introduce filters only where the
existing API supports the requested scope and period; do not add cosmetic global
filters that leave some panels unchanged without explanation.

Deliverable: redesigned analytics and customer routes with all current capabilities mapped.

### 5. AI assistant and remaining surfaces

Redesign the conversation layout, composer, history, suggested questions, Markdown
tables, every specialized tool-result card, notifications, and login. Contextual
"Ask Celeste" entry points currently carry prompt text, not authoritative case
evidence. Preserve their behavior without claiming case awareness. A future
case-aware assistant needs an explicit case-data/context contract and separate
acceptance criteria before presenting saved evidence as conversational context.
Show supported request states without inventing progress percentages or streaming
behavior that the API does not provide.

Deliverable: consistent assistant and access experiences with no leftover legacy visual system.

### 6. Validation, performance, and cleanup

Test desktop, tablet, and narrow mobile layouts. Verify keyboard use, focus return,
scroll behavior, overflow, chart resizing, and readable contrast. Exercise loading,
empty, stale, partial, error, unauthenticated, and expired-login states.

Split routes and the heavy assistant UI where practical; check that opening the app
does not eagerly load every chart and chat card. Preserve useful request caching
and avoid duplicate expensive requests. Remove superseded components and CSS only
after checking references and feature parity. Complete tests, build, lint, and a
browser walkthrough of the main journeys.

Deliverable: a verified frontend with a short before/after record and known limitations.

## Scope boundaries

This is a frontend redesign using the existing stack, Case Store, and shared
deterministic services, with narrowly scoped contract fixes and read-only adapters
documented in the audit.
Preserve the scan and outcome fixes, authentication behavior, data definitions,
and existing uncommitted work. Track backend requirements explicitly rather than
hiding them in frontend calculations. New forecasting algorithms, individual user accounts, new notification
channels, and unsupported data filters are not implied by this redesign.

## Completion criteria

- Every existing screen, AI result card, and workflow has a verified replacement.
- A manager can identify the top issue and its next action without expanding every card.
- Findings can be inspected, acted on, revisited, and linked directly.
- All actions remain accessible, including older actions on the same case;
  pagination never silently presents a capped list as all records.
- Persisted decision evidence and stale-version checks survive the redesign.
- Live analytical anomalies, action outcomes, and case resolution remain distinct.
- No loss of customer search, predictions, conversation history, or action fields.
- Consistent financial formatting, periods, outlet scope, and uncertainty labels.
- Core workflows work by keyboard and at desktop, tablet, and mobile widths.
- Login, API errors, failed saves, and slow scans produce understandable states.
- Frontend tests, build, and lint complete; relevant backend regression tests remain green.
- Browser verification is completed when an automation browser is available; any
  unverified visual behavior is recorded explicitly rather than assumed correct.

## First implementation milestone

Align the existing navigation shell and Today screen with the persisted case
workflow, using representative fixtures for visual work. Preserve Customer
Predictions as a first-class destination. Resolve the audited contract prerequisites
before introducing the inventory, channel, and profitability sections. Continue
through existing Explore content, customer experiences, and Ask Celeste without
rewriting the Case System.
