# Current-period investigations

Implemented 2026-09-28 to include available September data while preserving completed-month comparisons.

The Investigate page now has a **Current period investigations** section for all 12 registered metrics. Monthly and weekly case pages show the corresponding current-period metric alongside their saved evidence. This section reads reporting data independently of the daily case scan, caches results for five minutes, and provides **Check latest data** to bypass that cache.

## Date contract

- Each metric uses the latest timestamp in its own scoped source. September is included even when the month is unfinished. An older source remains visibly older; the code does not substitute today's date.
- The current window begins at that timestamp's month boundary and includes the latest record. Bounds are UTC, matching the existing investigation metric definitions. The exclusive upper bound is one microsecond after the latest timestamp.
- Each historical window covers the same elapsed duration from its month boundary. The baseline is the arithmetic mean of valid earlier matched-window metric values, following the existing anomaly detector. It is not a comparison with whole prior months.
- Months too short for the elapsed duration are excluded from the baseline, without shortening or discarding the current period. Missing history and zero-denominator observations do not become healthy readings.
- The frontend exposes the source cutoff, current window, individual baseline windows, exclusions, relative change and percentage-point difference for rates.

## Metric definitions

Revenue, volume, average order value, cancellations, delivery delays, Uber failures, PickMe cancellations, retention, estimated margin, supplier fill, purchase-price mismatch and supplier concentration retain their existing source inclusion rules. Sales and operations use the existing Celeste outlet ID constant. Purchasing remains business-wide.

Rates are computed from aggregate numerators and denominators. Average order value is total revenue divided by orders, not an average of daily averages. Retention measures customers returning so far against the previous full-month cohort, applying that same rule to historical matched windows. Supplier concentration uses the largest supplier spend divided by total spend; ties do not duplicate the observation. Estimated margin remains based on current catalog costs, with its existing limitation displayed.

## Lifecycle and operational boundaries

These assessments are provisional, read-only signals, including normal and unavailable metrics. They do not create or resolve cases, write action outcomes, or replace saved case evidence. Existing completed-month, weekly, incident and snapshot workflows continue alongside them. Case pages explicitly identify the period to which their actions and targets belong.

This covers all registered investigation metrics, not every arbitrary table in the database. Source event timestamps are not ETL completeness guarantees. Matched durations do not adjust for weekdays, seasonality or changing business scale; the flags reuse existing heuristic anomaly thresholds.

The database pool has four slots; assessments run at most two queries concurrently, with a five-second connection timeout and ten-second PostgreSQL statement timeout. PostgreSQL transactions are read-only. Source failures are returned per metric. Frontend requests time out explicitly instead of presenting unavailable measurements as zero.

## Validation

- 21 current-period service/API tests passed.
- 13 optional local SQL execution checks passed using DuckDB, covering all 12 metric queries, source scope, aggregation, latest-record inclusion and shorter-month exclusion. This is local semantic validation, not a claim of successful live PostgreSQL execution. DuckDB was installed into a temporary validation directory, not added as a runtime dependency.
- 20 existing investigation-integrity regressions passed.
- Seven browser regressions passed, including September visibility, exact comparison dates, source failures, manual refresh and mobile layout. Desktop and mobile screenshots were inspected.
- Production frontend build, scoped frontend lint and new Python module/test lint passed. The existing bundle-size and deprecation warnings remain.

The final running-app request on September 28 at 16:08 UTC returned all 12 metrics with explicit `unavailable` status. An independent read-only `SELECT 1` confirmed a reporting connection timeout. Live PostgreSQL values were unverified at that time. This limitation was subsequently cleared by the September 29 validation below. Fixture-based tests do not establish live source freshness.


## Performance correction — September 29

The reported failures had two reproduced causes: overlapping requests each launched their own metric batch, and the date-window queries revisited too many raw historical rows. The original live margin and retention queries both exceeded the ten-second statement limit (SQLSTATE 57014).

The endpoint now shares one in-flight assessment across normal reads and forced refreshes within each server process. A forced refresh joins an existing run instead of clearing its cache and starting another batch. Cache publication and failure cleanup are synchronized; failed calculations can be retried. Engine initialization happens before query workers start.

Queries now aggregate matched rows by month before joining date windows. Retention reduces orders to each customer's first order per month before measuring returns, while retaining the full prior-month cohort. Supplier comparisons use an explicit month equality join. No historical date range was removed, and the ten-second query limit was retained. Query concurrency was reduced from four to two to limit contention.

Expected pool and query timeouts now produce distinct failure reasons and concise log messages, rather than misleading users into treating every failure as an unreachable database. Unexpected exceptions still retain diagnostic tracebacks.

Validation:

- 39 service, API, concurrency and local SQL regression checks passed.
- Optimized standalone retention read completed in 1.45 seconds; the complete live batch measured all 12 metrics in 10.88 seconds.
- Two simultaneous forced refreshes through the running HTTP API both completed in 13.55 seconds and returned the same `checked_at` (`2026-09-29T04:39:34.673504+00:00`). Both contained 12 measured metrics and zero unavailable metrics.
- These are observed timings, not latency guarantees. Sharing is process-local; separate server workers have independent pools/caches.
