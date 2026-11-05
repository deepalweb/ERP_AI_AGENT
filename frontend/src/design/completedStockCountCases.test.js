import test from "node:test";
import assert from "node:assert/strict";
import { typeKeyFor } from "./inventoryDisplay.js";
import { INVENTORY_TAGS, tagFor } from "./inventoryTags.js";

test("scoped completed-count cases keep their control detail presentation", () => {
  const metricId = "completed_stock_count_variance-all-2026-07-19_2026-10-16";

  assert.equal(tagFor(metricId), INVENTORY_TAGS.NAMED_INCIDENT);
  assert.equal(typeKeyFor({ metric_id: metricId, domain: "inventory" }), "control");
  assert.equal(typeKeyFor({ metric_id: "completed_stock_count_variance", domain: "inventory" }), "control");
});
