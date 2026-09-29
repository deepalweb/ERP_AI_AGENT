import { useState } from "react";
import { NavLink } from "react-router-dom";
import { Search } from "lucide-react";
import NotificationsBell from "./design/NotificationsBell";
import { formatCurrency, formatNumber, monthLabel } from "./format";

const NAV_ITEMS = [
  { to: "/", label: "Home", end: true },
  { to: "/investigate", label: "Investigate" },
  { to: "/actions", label: "Actions" },
  { to: "/outcomes", label: "Outcomes" },
  { to: "/dashboard", label: "Business Performance" },
  { to: "/customers/predictions", label: "Customers" },
];

function ScopeChip({ stores }) {
  const [expanded, setExpanded] = useState(false);

  if (!stores) return null;

  return (
    <span className="relative">
      <button
        type="button"
        className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground/70 hover:text-foreground"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        {stores.length} Celeste outlets
      </button>
      {expanded && (
        <div className="absolute left-0 top-full z-10 mt-2 w-56 rounded-lg border border-border bg-chart-surface p-3 text-left shadow-sm">
          <div className="text-xs font-medium text-foreground/70">Configured outlets; individual reports show their scope:</div>
          <ul className="mt-2 space-y-1 text-sm">
            {stores.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}

export default function Header({ kpis, storeScope, onAskCeleste, onAskCelesteQuestion, newCriticalCount = 0, onSignOut }) {
  const [question, setQuestion] = useState("");

  function submitQuestion(e) {
    e.preventDefault();
    const text = question.trim();
    if (!text) return;
    (onAskCelesteQuestion || onAskCeleste)?.(text);
    setQuestion("");
  }

  return (
    <header className="original-header border-b border-border bg-background">
      <div className="flex items-center justify-between px-6 py-3">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-sm font-bold text-white">C</span>
          <div className="leading-tight">
            <div className="text-base font-semibold text-foreground-strong">Celeste</div>
            <span className="text-xs text-foreground/60">Business Briefing</span>
          </div>
        </div>

        {/* Opens the same Ask Celeste overlay every other entry point uses
            (App.jsx's askCeleste()) -- never a second chat implementation. */}
        <form onSubmit={submitQuestion} className="relative hidden max-w-md flex-1 md:block">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground/40" strokeWidth={2} />
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask a question about your business…"
            className="w-full rounded-lg border border-border bg-surface-sunken py-2 pl-9 pr-3 text-sm text-foreground-strong placeholder:text-foreground/40 focus:border-accent-border focus:outline-none"
          />
        </form>

        <nav aria-label="Main navigation" className="flex flex-wrap items-center gap-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `relative rounded-full px-3 py-1.5 text-sm font-medium ${
                  isActive ? "bg-accent-soft text-accent" : "text-foreground/70 hover:text-foreground"
                }`
              }
            >
              {item.label}
              {/* New-critical-finding badge lives on Home specifically (not a
                  separate bell icon) — that's where acknowledging happens,
                  so the badge and the place that clears it are the same
                  link. See notifications.py for what counts as "new." */}
              {item.to === "/" && newCriticalCount > 0 && (
                <span
                  className="absolute -right-1.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-critical px-1 text-[10px] font-bold leading-none text-white"
                  title={`${newCriticalCount} new critical finding${newCriticalCount === 1 ? "" : "s"}`}
                >
                  {newCriticalCount > 9 ? "9+" : newCriticalCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="flex items-center gap-2"><NotificationsBell />
        <button
          type="button"
          onClick={onAskCeleste}
          className="rounded-full border border-accent-border bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent"
        >
          Ask Celeste
        </button>
        {onSignOut && <button type="button" onClick={onSignOut} className="text-xs text-foreground">Sign out</button>}
        </div>
      </div>

      <div className="flex items-center gap-3 px-6 pb-2 text-sm">
        <ScopeChip stores={storeScope} />
        {kpis ? (
          <>
            <span className="h-4 w-px bg-border" aria-hidden="true" />
            <span className="text-foreground/60">Revenue <strong className="text-foreground-strong">{formatCurrency(kpis.revenue)}</strong></span>
            <span className="h-4 w-px bg-border" aria-hidden="true" />
            <span className="text-foreground/60">Orders <strong className="text-foreground-strong">{formatNumber(kpis.orders)}</strong></span>
            <span className="h-4 w-px bg-border" aria-hidden="true" />
            <span className="flex items-center gap-1 text-foreground/60">
              <span
                className={`h-2 w-2 rounded-full ${
                  kpis.healthStatus === "critical" ? "bg-critical" : kpis.healthStatus === "warning" ? "bg-warning" : "bg-good"
                }`}
                aria-hidden="true"
              />
              Health <strong className="text-foreground-strong">{kpis.healthScore}</strong>
            </span>
            {kpis.dataThroughMonth && (
              <span className="text-foreground/40">Data through {monthLabel(kpis.dataThroughMonth)}</span>
            )}
          </>
        ) : (
          null
        )}
      </div>
    </header>
  );
}
