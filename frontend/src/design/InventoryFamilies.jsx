import Badge, { toneForPriority } from "./Badge";
import { prettifyMetricId } from "../format";
import { INVENTORY_DOMAINS, inventoryFamilyMeta } from "./inventoryTaxonomy";

/** Inventory navigation grouped into business domains, then finding families. */
export default function InventoryFamilies({ families, selectedKey, onSelect }) {
  const familyRows = families ?? [];
  const byDomain = new Map(INVENTORY_DOMAINS.map((domain) => [domain.key, []]));
  for (const family of familyRows) {
    const domainKey = family.domain_key ?? inventoryFamilyMeta(family.key)?.domainKey;
    if (byDomain.has(domainKey)) byDomain.get(domainKey).push(family);
  }

  return (
    <div className="space-y-6">
      {INVENTORY_DOMAINS.map((domain) => {
        const domainFamilies = byDomain.get(domain.key);
        return (
          <section key={domain.key} aria-labelledby={"inventory-domain-" + domain.key}>
            <div className="mb-3">
              <h3 id={"inventory-domain-" + domain.key} className="font-display text-base font-semibold text-foreground-strong">{domain.label}</h3>
              <p className="mt-1 text-xs text-foreground-subtle">{domain.description}</p>
            </div>
            {domainFamilies.length === 0 ? <p className="rounded-lg border border-dashed border-border px-4 py-3 text-sm text-foreground-subtle">No active findings in this domain.</p> : <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {domainFamilies.map((family) => {
                const pressed = selectedKey === family.key;
                const meta = inventoryFamilyMeta(family.key);
                return (
                  <button
                    key={family.key}
                    type="button"
                    aria-pressed={pressed}
                    onClick={() => onSelect(family.key)}
                    className={"min-w-0 rounded-xl border p-4 text-left transition hover:bg-surface-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent " + (pressed ? "border-accent-border bg-accent-soft" : "border-border bg-surface")}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <h4 className="text-sm font-semibold text-foreground-strong">{meta?.label ?? family.label}</h4>
                      <Badge tone={toneForPriority(family.priority)}>{prettifyMetricId(family.priority || "Unranked")}</Badge>
                    </div>
                    <p className="mt-2 text-xs text-foreground-subtle">
                      {family.active_children} active investigation{family.active_children === 1 ? "" : "s"}
                    </p>
                    {meta?.findingTypes?.length > 0 && <p className="mt-2 text-xs leading-relaxed text-foreground-subtle">{meta.findingTypes.join(" · ")}</p>}
                    {family.exposure_quantified_children > 0 && (
                      <p className="mt-2 text-xs text-foreground-subtle">Measures stay separate by finding; totals are not combined.</p>
                    )}
                  </button>
                );
              })}
            </div>}
          </section>
        );
      })}
    </div>
  );
}
