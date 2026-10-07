function stampOf(value) {
  if (!value) return null;
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function fmt(value) {
  const date = stampOf(value);
  return date ? date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "not recorded";
}

/** System status for the investigation: how current the source data is and when the
 * detectors last ran. One compact line; the run button never runs on its own. */
export default function DataSyncBar({ sync, running, failed, onRun }) {
  const order = stampOf(sync?.latest_order_at);
  const dayOld = order ? (Date.now() - order.getTime()) / 86400000 > 1 : false;
  return (
    <section aria-label="Data status" className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-foreground-subtle">
      <span>
        Data up to <span className="text-foreground">{fmt(sync?.latest_order_at)}</span> (orders) · <span className="text-foreground">{fmt(sync?.latest_transfer_record_at)}</span> (transfers)
      </span>
      <span>Last full scan <span className="text-foreground">{fmt(sync?.last_run_at)}</span></span>
      {dayOld && <span className="text-warning">Newest order is over a day old</span>}
      {failed && <span className="text-critical">Last run failed</span>}
      <button
        type="button"
        onClick={onRun}
        disabled={running}
        title="Re-runs every detector against the reporting database. New results appear when it finishes."
        className="ml-auto rounded-lg border border-accent-border bg-accent-soft px-3 py-1.5 font-medium text-accent hover:bg-accent-soft disabled:opacity-60"
      >
        {running ? "Running investigation…" : "Run investigation"}
      </button>
    </section>
  );
}
