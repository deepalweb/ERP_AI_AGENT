import { useState } from "react";

function DomainChip({ domain, expanded, onToggle }) {
  return (
    <div className={`health-chip health-chip-${domain.status}`}>
      <button type="button" className="health-chip-toggle" onClick={onToggle} aria-expanded={expanded}>
        <span className="health-chip-label">{domain.label}</span>
        <span className={`health-chip-score health-text-${domain.status}`}>{domain.score}</span>
      </button>
      <div className="health-track" aria-hidden="true">
        <span className={`health-fill health-fill-${domain.status}`} style={{ width: `${Math.max(0, Math.min(domain.score, 100))}%` }} />
      </div>
      {expanded && (
        <ul className="health-chip-drivers">
          {domain.drivers.length === 0 ? (
            <li>Nothing notable &mdash; behaving normally.</li>
          ) : (
            domain.drivers.map((d, i) => <li key={i}>{d}</li>)
          )}
        </ul>
      )}
    </div>
  );
}

export default function HealthScore({ health }) {
  const [expandedId, setExpandedId] = useState(null);

  if (!health) return null;

  const radius = 47;
  const circumference = 2 * Math.PI * radius;
  const progress = circumference * (1 - health.overall / 100);

  return (
    <div className={`health-card health-card-${health.status}`}>
      <div className="health-overall">
        <svg className="health-ring" viewBox="0 0 120 120" role="img" aria-label={`Business health ${health.overall} out of 100`}>
          <circle className="health-ring-track" cx="60" cy="60" r={radius} />
          <circle className={`health-ring-value health-ring-${health.status}`} cx="60" cy="60" r={radius} strokeDasharray={circumference} strokeDashoffset={progress} />
          <text x="60" y="57" className="health-ring-score" textAnchor="middle">{health.overall}</text>
          <text x="60" y="75" className="health-ring-total" textAnchor="middle">of 100</text>
        </svg>
        <div className="health-overall-copy">
          <div className="health-overall-label">Business health</div>
          <strong>{health.status === "good" ? "Healthy" : health.status === "warning" ? "Watch closely" : "Needs attention"}</strong>
        </div>
      </div>

      <div className="health-domains">
        {health.domains.map((d) => (
          <DomainChip
            key={d.id}
            domain={d}
            expanded={expandedId === d.id}
            onToggle={() => setExpandedId(expandedId === d.id ? null : d.id)}
          />
        ))}
      </div>

      {health.excluded?.length > 0 && (
        <div className="health-excluded">
          {health.excluded.map((e) => (
            <span key={e.label} title={e.reason}>
              Not shown: {e.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
