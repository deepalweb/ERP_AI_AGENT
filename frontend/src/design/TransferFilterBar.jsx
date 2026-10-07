import { useState } from "react";

/** Filters for the transfer accountability panel. The value is the page's filter state
 * (outlet, product and person are arrays of ids or keys; from and to are ISO dates;
 * recurring is a boolean). Changes go up through onChange and live in the page URL. */

export const EMPTY_TRANSFER_FILTERS = { outlet: [], product: [], person: [], from: "", to: "", recurring: false };

export const displayPerson = (login) => (login === "Not recorded" ? "No outbound link" : (login ?? "").replace(/@celeste\.lk$/, ""));

function MultiSelect({ label, options, selected, onToggle, searchable = false }) {
  const [query, setQuery] = useState("");
  const shown = searchable && query ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase())) : options;
  return (
    <details className="relative">
      <summary className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm text-foreground hover:bg-surface-sunken">
        {label}
        {selected.length > 0 && <span className="rounded-full bg-accent/15 px-1.5 text-xs text-accent">{selected.length}</span>}
      </summary>
      <div className="absolute z-10 mt-1 w-72 max-w-[calc(100vw-32px)] rounded-lg border border-border bg-surface p-2 shadow-lg">
        {searchable && (
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${label.toLowerCase()}`}
            aria-label={`Search ${label.toLowerCase()}`}
            className="mb-2 w-full rounded-md border border-border bg-surface-sunken px-2 py-1 text-sm text-foreground"
          />
        )}
        <ul className="max-h-64 space-y-1 overflow-y-auto">
          {shown.map((option) => (
            <li key={option.value}>
              <label className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 text-sm text-foreground hover:bg-surface-sunken">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={selected.includes(option.value)}
                  onChange={() => onToggle(option.value)}
                />
                <span>{option.label}</span>
              </label>
            </li>
          ))}
          {shown.length === 0 && <li className="px-2 py-1 text-sm text-foreground-subtle">No matches</li>}
        </ul>
      </div>
    </details>
  );
}

function Chip({ text, onRemove }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-sunken px-2.5 py-0.5 text-xs text-foreground">
      {text}
      <button type="button" onClick={onRemove} aria-label={`Remove ${text}`} className="rounded px-1 text-foreground-subtle hover:text-foreground">×</button>
    </span>
  );
}

export default function TransferFilterBar({ options, value, onChange }) {
  const set = (patch) => onChange({ ...value, ...patch });
  const toggle = (field, item) => set({
    [field]: value[field].includes(item) ? value[field].filter((x) => x !== item) : [...value[field], item],
  });

  const outletName = Object.fromEntries((options.outlets ?? []).map((o) => [o.id, o.name]));
  const productName = Object.fromEntries((options.products ?? []).map((p) => [p.key, p.name]));
  const chips = [
    ...value.outlet.map((id) => ({ text: outletName[id] ?? id, remove: () => toggle("outlet", id) })),
    ...value.product.map((key) => ({ text: productName[key] ?? key, remove: () => toggle("product", key) })),
    ...value.person.map((who) => ({ text: displayPerson(who), remove: () => toggle("person", who) })),
    ...(value.from ? [{ text: `From ${value.from}`, remove: () => set({ from: "" }) }] : []),
    ...(value.to ? [{ text: `To ${value.to}`, remove: () => set({ to: "" }) }] : []),
    ...(value.recurring ? [{ text: "Recurring only", remove: () => set({ recurring: false }) }] : []),
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <MultiSelect
          label="Outlet"
          options={(options.outlets ?? []).map((o) => ({ value: o.id, label: o.name }))}
          selected={value.outlet}
          onToggle={(id) => toggle("outlet", id)}
        />
        <MultiSelect
          label="Product"
          searchable
          options={(options.products ?? []).map((p) => ({ value: p.key, label: p.name }))}
          selected={value.product}
          onToggle={(key) => toggle("product", key)}
        />
        <MultiSelect
          label="Person"
          options={(options.people ?? []).map((p) => ({ value: p, label: displayPerson(p) }))}
          selected={value.person}
          onToggle={(who) => toggle("person", who)}
        />
        <label className="flex flex-col text-xs text-foreground-subtle">
          From
          <input
            type="date"
            min={options.date_min ?? undefined}
            max={options.date_max ?? undefined}
            value={value.from}
            onChange={(e) => set({ from: e.target.value })}
            className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col text-xs text-foreground-subtle">
          To
          <input
            type="date"
            min={options.date_min ?? undefined}
            max={options.date_max ?? undefined}
            value={value.to}
            onChange={(e) => set({ to: e.target.value })}
            className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-foreground"
          />
        </label>
        <label className="inline-flex cursor-pointer items-center gap-2 pb-1.5 text-sm text-foreground">
          <input type="checkbox" checked={value.recurring} onChange={(e) => set({ recurring: e.target.checked })} />
          Recurring only
        </label>
        {chips.length > 0 && (
          <button type="button" onClick={() => onChange(EMPTY_TRANSFER_FILTERS)} className="pb-1.5 text-sm text-accent hover:underline">
            Clear all
          </button>
        )}
      </div>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {chips.map((chip) => <Chip key={chip.text} text={chip.text} onRemove={chip.remove} />)}
        </div>
      )}
    </div>
  );
}
