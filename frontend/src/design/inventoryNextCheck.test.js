import test from "node:test";
import assert from "node:assert/strict";
import { inventoryNextCheckText } from "./plainLanguage.js";

test("each inventory state type gets its own next check", () => {
  assert.match(inventoryNextCheckText({ impact: { kind: "products_out_of_stock" } }), /highest-impact products/);
  assert.match(
    inventoryNextCheckText({ impact: { kind: "top_sellers_out_of_stock" }, breakdowns: [{ top_contributors: [{ name: "Set Yogurt 80G - Ambewela - SKU 000638" }] }] }),
    /starting with Set Yogurt 80G/,
  );
  assert.match(
    inventoryNextCheckText({ impact: { kind: "stuck_stock_count_value_at_risk" }, breakdowns: [{ dimension: "store", top_contributors: [{ name: "Celeste Daily Ethul Kotte" }] }] }),
    /starting with Ethul Kotte/,
  );
  assert.match(inventoryNextCheckText({ impact: { kind: "completed_stock_count_variance" } }), /count sheets/);
});

test("transfer cases keep the caller's own next-check text", () => {
  assert.equal(inventoryNextCheckText({ impact: { kind: "transfer_discrepancy" } }), null);
});
