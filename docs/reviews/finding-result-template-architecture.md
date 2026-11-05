# Finding result template architecture

## Existing selection and data

The case detail route is `frontend/src/routes/CaseDetail.jsx`. Before this refactor it chose result panels with a sequence of metric-ID/domain booleans, then fell through to a generic trend chart. Inventory had a second selector in `InventoryCaseView.jsx` driven by `inventoryTags.js` and `impact.kind`. Investigation list type labels are derived in `inventoryDisplay.js`. Family navigation comes from the read-only `/management/families` response assembled by `backend/app/services/management.py`.

Case detail reads the latest saved observation snapshot and returns case metadata, measurement freshness, observation history, relationships, actions, and history. The snapshot already carries `metric_id`, `domain`, `category`, `confirm`, `metric.direction`, `trend`, `persistence`, `breakdowns`, `impact`, `root_cause`, recommendations, and metric-specific record arrays. Inventory control snapshots also carry `observation_window`, `calculated_at`, `valuation_coverage`, and denominator values where the detector has them. `case_relationships` already distinguishes `duplicate_of`, `overlaps`, and recurrence links.

The family endpoint returns the family label, highest child priority, active child count, and child case IDs, type metadata, status, priority, title, and a deliberately slim `impact_summary`. It does not return child histories/actions or full evidence; child links open the existing case detail, which retains them. Family exposure is not aggregated.

## Presentation mapping

`frontend/src/design/findingTemplates.js` is the read-only presentation registry. It resolves archetype, domain evidence module, interpretation, and the named renderer key from fields already present in the case/snapshot. `FindingResult.jsx` maps renderer keys to existing specialist evidence modules. Inventory's existing `InventoryCaseView` remains the inventory evidence adapter; its opportunity path now includes the saved comparison and trend chart so a favorable movement keeps trend evidence. `inventoryDisplay.js` also uses this registry for list type filtering, keeping an opportunity-tagged trend under Trend while displaying Opportunity as its interpretation.

| Existing finding shape | Archetype | Result renderer |
| --- | --- | --- |
| `stock_availability`, `top_seller_stock` | `state_snapshot` | Inventory case view |
| `monthly` / `weekly` metric | `trend_deviation` | Domain panel or saved trend evidence |
| transfer discrepancy, incident, dispatch pattern | `recurring_pattern` | Inventory / incident panel |
| `extreme_event_*`, `dispatch_sent_above_plan` | `extreme_event` | Extreme-event / inventory panel |
| stock-count, negative-stock, missing-cost control | `control_exception` | Stock-count / controls panel |
| `damage_writeoff` | `recorded_loss` | Inventory case view |
| opportunity category on a trend | `trend_deviation` + `opportunity` interpretation | Trend evidence with opportunity context |
| opportunity without a trend horizon | `opportunity_outcome` | Safe opportunity/legacy renderer |

Unknown legacy types receive the generic fallback and are never sent through an incompatible specialist panel. Existing domain identifiers are preserved: `order_customer`, `inventory`, `delivery`, `financial`, `supplier`, and `controls`.

## Field gaps and limits

- The persisted API has no explicit archetype or business-interpretation enum. The frontend derives those from existing type, horizon, category, and metric direction without a migration.
- A metric with no declared `metric.direction` cannot be interpreted as favorable/adverse from movement alone; it is labeled verification-required when a direction would otherwise be inferred.
- Family summaries do not include child evidence coverage or per-child freshness. The detail route exposes the child's latest evidence and measurement metadata.
- `calculated_at`, valuation basis/coverage, eligible denominator, recurrence threshold, and source refresh time are metric-specific rather than common fields. Templates only display fields returned by their finding shape; missing amounts remain unquantified, and source freshness remains separate from last observation time.
- Current inventory family data is a read-only grouping of atomic cases, not a persisted parent case. Child action/history remains on each child case.

## Representative finding validation

The following examples were read from the saved case observations in `app.db` on 2026-10-19 and checked against the registry and evidence renderers. These checks validate which template receives each record and which evidence/limits the renderer can display; they are not a browser screenshot review.

| Example | Resolved presentation | Evidence the current finding carries | Limitation or decision guardrail |
| --- | --- | --- | --- |
| Recurring transfer shortage (`transfer_discrepancy_missing_item`) | `recurring_pattern` · transfer discrepancy panel | 123 current lines, 14 of 15 observed days, plan/sent/received quantities, and 111 trace rows; impact separates planning and transit gaps | Keep the planning gap separate from the receipt gap; don't infer fault from the person or treat the full discrepancy as confirmed loss. |
| Extreme shortage (`extreme_event_missing_item`) | `extreme_event` · event panel | 11 source events, 1,948 units, and about Rs 88,215 estimated exposure | Keep the individual dated source events visible; don't describe this one-off extreme as recurrence. Catalog-cost exposure is not confirmed loss. |
| Completed stock-count variance (`completed_stock_count_variance`) | `control_exception` · completed variance panel | 172 sessions and about Rs 13.40m gross variance, with session and outlet evidence | The saved observation in the local database predates the new system-quantity and counted-quantity rates. The current renderer shows those rates as unknown until a scan writes them; it must not substitute zero or the older single rate. Cost coverage and source refresh metadata also need to be present before presenting the rates as fully representative. |
| Damage write-offs (`damage_writeoff`) | `recorded_loss` · inventory evidence panel | 157 orders, about Rs 506,937 recorded, and valuation coverage for 2,720 lines (592 catalog-valued; 2,128 without usable cost) | The write-off is recorded; the rupee amount is a lower-bound estimate due to incomplete cost coverage. Keep separate from transfer damage and shortage exposure. |
| Stockout cancellation improvement (`stockout_cancellation_rate`) | `trend_deviation` + `opportunity` · saved trend evidence | 0.49% current vs 0.82% baseline, nine history points, two improving periods, estimated 314 recovered orders / about Rs 733,943 | This is an estimated benefit, not verified realized savings; the trend does not establish causation. |
| Customer retention (`customer_retention_rate`) | `trend_deviation` · customer-domain trend evidence | 54.23% current vs 63.98% baseline, eight history points; metric direction marks higher as good | Metric notes say retention is a following-month repeat-order rate and has no valid outlet breakdown. Preserve those notes and avoid inventing outlet attribution. |
| Supplier fill (`supplier_fill_rate`) | `trend_deviation` · supplier evidence panel | 35.92% current vs 42.86% baseline, seven history points, four consecutive declining periods; metric direction marks higher as good | This blends delivery timeliness and quantity completeness; it cannot identify which component declined. Keep the business-wide scope and source-data caveat visible. |

Family summaries group and link atomic findings, show active count and highest priority, and never sum child exposure. The current stock-count family contains active cases linked by `duplicate_of`; the summary now cross-references those cases and labels duplicate/overlapping values as non-additive. Child detail pages retain their own evidence, status, history, and actions.

## Inventory result reading path

Inventory case details now open with the finding family and archetype above a single plain-language summary and the finding-specific metric strip. Evidence occupies the wide column; the recommended check, recorded limitations, and optional source records sit in a compact decision rail. Filters describe which evidence they affect. Methodology and provisional current-period checks remain available below the primary decision path. Opportunity results use the same evidence/decision split while keeping estimated benefit visually separate from loss.

The backend currently defines nine inventory family labels, including `Inventory health`, while the requested user-facing list names eight. The display mapping preserves the existing `Inventory health` group for `dead_overstock_value`; it does not silently merge that distinct finding into stock availability.
