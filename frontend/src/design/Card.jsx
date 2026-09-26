/** A titled panel card — the shared wrapper used to group related content
 * (a KPI panel, a chart, a table) instead of letting everything run
 * together edge to edge. Renamed from `Section.jsx` (Frontend Rebuild
 * Phase 0): `routes/CaseDetail.jsx` has its own, incompatible local
 * `Section` component (different markup/props, same name) — a rebuild
 * landmine since importing the wrong one is an easy mistake. This is the
 * one that survives under its actual role; CaseDetail's stays local to
 * that file, since it's the actively-maintained one post "V3". */
export default function Card({ title, note, children }) {
  return (
    <section className="mt-4 rounded-2xl border border-border bg-chart-surface p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground-strong">{title}</h2>
        {note && <span className="text-xs text-foreground/50">{note}</span>}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}
