// Business-facing case types. The logic lives in inventoryDisplay.js; this module keeps
// the names the filter dropdown and family view already import.
import { TYPE_LABELS, typeKeyFor } from "./inventoryDisplay";

export function caseTypeFor(item) {
  return typeKeyFor(item);
}

export const TYPE_LABEL = TYPE_LABELS;

export const TYPE_FILTERS = [
  { value: "", label: "All types" },
  { value: "trend", label: "Trend" },
  { value: "state", label: "State" },
  { value: "recurring", label: "Recurring pattern" },
  { value: "extreme", label: "Extreme event" },
  { value: "control", label: "Control" },
  { value: "opportunity", label: "Opportunity" },
];
