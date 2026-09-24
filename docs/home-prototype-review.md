# Home briefing decision

The user selected **A — Executive briefing**. The live `/` page now uses the five-section reading order, original top navigation, compact priority cards, and real backend data.

The three throwaway variants were captured on local branch `prototype/home-briefing-options`, commit `0b31809e248124781f87f75e10c25cc88f95fbe2`. Prototype routing, fixtures, and styles were removed from the active frontend. The user index and current branch were not switched or committed.

Production files: `frontend/src/routes/Home.jsx`, `frontend/src/routes/Home.css`, and `frontend/src/App.jsx`.

Briefing content comes from `/brief`; current-state and monthly findings share Long-term, while This week uses short-term findings. Backend impact scores rank the top three. Persisted actions load independently from `/management/actions`, including work whose findings are absent from the briefing. Case links use stored IDs. Unlinked findings retain expandable evidence; action editing remains in the case workspace. Forecasts load only on expansion for supported metrics. No backend schema, metric, or lifecycle changes were made.

Missing health stays unavailable, source freshness is separate from briefing time, and a cleared weekly alert is not labelled a resolved case. Health comparison dates are not supplied by the backend; the detail disclosure states that limitation. Follow-up panels are bounded latest-record previews with links to their complete workspaces.

Validation: production build passed, lint passed with nine pre-existing warnings. All twelve applicable browser scenarios passed across the initial run and focused rerun: three core workflows, six management scenarios, and three new Home regressions. The initial Home error test needed a longer assertion timeout to cover normal query retries. Desktop/tablet/mobile widths were checked; desktop and mobile screenshots reviewed. Browser tests use intercepted representative backend responses; this pass did not run a new reporting-database scan. Previous full-stack integration results remain recorded in the implementation progress report.
