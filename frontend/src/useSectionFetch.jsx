import { useEffect, useState } from "react";

// Fetches independently of whatever else is on the page — critical for any
// section backed by a slow endpoint (e.g. scan_attention, 30-40s): gating a
// whole page behind Promise.all/allSettled on every section left fast
// sections stuck behind the slowest one (found and fixed on the Insights
// page; reused here so Overview/Operations don't repeat the same mistake
// when adding their own new sections).
export function useSectionFetch(fetcher) {
  const [state, setState] = useState({ status: "loading", result: null, error: null });

  useEffect(() => {
    let cancelled = false;
    fetcher()
      .then((res) => {
        if (cancelled) return;
        if (res.status === "error") {
          setState({ status: "error", result: null, error: res.detail || "Could not load this data." });
        } else {
          setState({ status: "ok", result: res, error: null });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({ status: "error", result: null, error: "Could not reach the backend." });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return state;
}

export function SectionBody({ state, children }) {
  if (state.status === "loading") return <div className="chart-empty">Loading&hellip;</div>;
  if (state.status === "error") return <div className="page-state page-state-error">{state.error}</div>;
  return children;
}
