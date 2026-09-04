import { useState } from "react";
import { formatCurrency, formatNumber, monthLabel } from "./format";

const NAV_ITEMS = [
  { id: "overview", label: "Executive" },
  { id: "operations", label: "Operations" },
  { id: "chat", label: "Ask intelligence" },
];

function ScopeChip({ stores }) {
  const [expanded, setExpanded] = useState(false);

  if (!stores) return null;

  return (
    <span className="app-header-scope">
      <button
        type="button"
        className="app-header-scope-chip"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        {stores.length} Celeste outlets
      </button>
      {expanded && (
        <div className="app-header-scope-list">
          <div className="app-header-scope-list-title">Every number here is scoped to:</div>
          <ul>
            {stores.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}

export default function Header({ view, onNavigate, kpis, storeScope }) {
  return (
    <header className="app-header">
      <div className="app-header-top">
        <div className="brand-lockup">
          <span className="brand-mark">C</span>
          <div>
            <h1>Celeste Intelligence</h1>
            <span>Enterprise briefing</span>
          </div>
        </div>
        <nav className="app-nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "app-nav-active" : ""}
              onClick={() => onNavigate(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="app-header-kpis">
        <ScopeChip stores={storeScope} />
        {storeScope && <span className="app-header-divider" aria-hidden="true" />}
        {kpis ? (
          <>
            <span className="app-header-kpi">
              <span className="app-header-kpi-label">Revenue</span>
              <span className="app-header-kpi-value">{formatCurrency(kpis.revenue)}</span>
            </span>
            <span className="app-header-divider" aria-hidden="true" />
            <span className="app-header-kpi">
              <span className="app-header-kpi-label">Orders</span>
              <span className="app-header-kpi-value">{formatNumber(kpis.orders)}</span>
            </span>
            <span className="app-header-divider" aria-hidden="true" />
            <span className="app-header-kpi">
              <span className={`app-header-kpi-dot app-header-kpi-dot-${kpis.healthStatus}`} aria-hidden="true" />
              <span className="app-header-kpi-label">Health</span>
              <span className="app-header-kpi-value">{kpis.healthScore}</span>
            </span>
            {kpis.dataThroughMonth && (
              <span className="app-header-kpi app-header-kpi-muted">
                Data through {monthLabel(kpis.dataThroughMonth)}
              </span>
            )}
          </>
        ) : (
          <span className="app-header-kpi app-header-kpi-muted">Loading&hellip;</span>
        )}
      </div>
    </header>
  );
}
