import HBarList from "../charts/HBarList";
import { monthLabel } from "../format";

export default function ChannelBreakdownCard({ result }) {
  if (!result || result.error || !result.channels || result.channels.length === 0) return null;

  return (
    <div className="tool-card channel-breakdown-card">
      <div className="tool-card-title">Sales by channel &mdash; {monthLabel(result.period)}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">
          Partial month &mdash; {result.days_elapsed} of {result.days_in_period} days elapsed.
        </div>
      )}
      <HBarList
        data={result.channels}
        valueKey="revenue"
        detailKey="orders"
        detailLabel="orders"
        ariaLabel="Revenue by sales channel"
      />
    </div>
  );
}
