// Business-facing case types. The logic lives in inventoryDisplay.js; this module keeps
// the names the filter dropdown and family view already import.
import { INVENTORY_FAMILIES } from "./inventoryTaxonomy";
import { TYPE_LABELS, typeKeyFor } from "./inventoryDisplay";

export function caseTypeFor(item) {
  return typeKeyFor(item);
}

export const TYPE_LABEL = TYPE_LABELS;

export const TYPE_FILTERS = [
  { value: "", label: "All evidence classes" },
  { value: "trend", label: "Trend" },
  { value: "state", label: "State" },
  { value: "recurring", label: "Recurring pattern" },
  { value: "extreme", label: "Extreme event" },
  { value: "control", label: "Control" },
  { value: "recorded_loss", label: "Recorded financial event" },
  { value: "opportunity", label: "Opportunity outcome" },
  { value: "legacy", label: "Legacy" },
];

export const FINDING_TYPE_FILTERS = [
  { value: "", label: "All finding types" },
  ...[...new Set(Object.values(INVENTORY_FAMILIES).flatMap((family) => family.findingTypes))]
    .map((label) => ({ value: label, label })),
];
