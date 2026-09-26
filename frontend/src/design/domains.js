/** Problem-domain vocabulary for grouping the Home findings feed — matches
 * mcp_server/tools/investigation.py's METRIC_DOMAIN + state_findings.py's
 * domain: "inventory" tag. Only domains with real detection behind them
 * today are listed; a domain with zero findings simply doesn't render a
 * group rather than being padded in as fake coverage.
 *
 * Frontend Intelligence Integration, Phase 0: the backend's real domain
 * ids (order_customer/inventory/delivery/financial/supplier/controls)
 * don't read as product language on their own -- financial reads as
 * "Revenue", delivery as "Operations", order_customer bundles Customers
 * and Orders together. Rather than rename the backend enum (a bigger,
 * separate decision), DOMAIN_META is the one frontend mapping layer every
 * page reads through, so the same domain gets the same label everywhere
 * instead of each page inventing its own wording. DOMAIN_LABEL below is
 * kept as a derived export (shortLabel) so every existing call site keeps
 * working unchanged. */
export const DOMAIN_ORDER = ["order_customer", "inventory", "delivery", "financial", "supplier", "controls"];

export const DOMAIN_META = {
  order_customer: { label: "Customers & Orders", shortLabel: "Customers" },
  inventory: { label: "Inventory & Shrinkage", shortLabel: "Inventory" },
  delivery: { label: "Operations & Fulfilment", shortLabel: "Operations" },
  financial: { label: "Revenue & Profitability", shortLabel: "Revenue" },
  supplier: { label: "Suppliers & Purchasing", shortLabel: "Suppliers" },
  controls: { label: "Business Controls", shortLabel: "Controls" },
};

export const DOMAIN_LABEL = Object.fromEntries(
  Object.entries(DOMAIN_META).map(([id, meta]) => [id, meta.shortLabel])
);
