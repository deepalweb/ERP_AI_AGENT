export default function SignalStrip({ health }) {
  if (!health || !health.domains?.length) return null;

  return (
    <div className="signal-strip" role="list" aria-label="What's happening, by business domain">
      {health.domains.map((d) => (
        <div className="signal-tile" role="listitem" key={d.id}>
          <div className="signal-tile-label">
            <span className={`signal-tile-dot signal-tile-dot-${d.status}`} aria-hidden="true" />
            {d.label}
          </div>
          <div className="signal-tile-signal">
            {d.drivers?.[0] || "Nothing notable — behaving normally."}
          </div>
        </div>
      ))}
    </div>
  );
}
