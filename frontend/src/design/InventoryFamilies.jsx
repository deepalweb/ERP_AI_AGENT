import Badge, { toneForPriority } from "./Badge";
import { prettifyMetricId } from "../format";

/** Inventory management families as navigation. Selecting a card filters the issue table
 * below to that family's findings; the cards do not repeat the issue list. Exposure is
 * shown as a note and never summed across findings. */
export default function InventoryFamilies({ families, selectedKey, onSelect }) {
  if (!families?.length) return null;
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {families.map((family) => {
        const pressed = selectedKey === family.key;
        return (
          <button
            key={family.key}
            type="button"
            aria-pressed={pressed}
            onClick={() => onSelect(family.key)}
            className={`min-w-0 rounded-xl border p-4 text-left transition hover:bg-surface-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
              pressed ? "border-accent-border bg-accent-soft" : "border-border bg-surface"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-sm font-semibold text-foreground-strong">{family.label}</h3>
              <Badge tone={toneForPriority(family.priority)}>{prettifyMetricId(family.priority || "Unranked")}</Badge>
            </div>
            <p className="mt-2 text-xs text-foreground-subtle">
              {family.active_children} active finding{family.active_children === 1 ? "" : "s"}
              {family.driving_title && <> · driven by {family.driving_title}</>}
            </p>
            {family.exposure_quantified_children > 0 && (
              <p className="mt-1 text-xs text-foreground-subtle">Exposure on {family.exposure_quantified_children} finding{family.exposure_quantified_children === 1 ? "" : "s"}, not summed.</p>
            )}
          </button>
        );
      })}
    </div>
  );
}
