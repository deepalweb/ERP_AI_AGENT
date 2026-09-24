/** Problem-domain labels for grouping the Home findings feed — matches
 * mcp_server/tools/investigation.py's METRIC_DOMAIN + state_findings.py's
 * domain: "inventory" tag. Only domains with real detection behind them
 * today are listed; a domain with zero findings simply doesn't render a
 * group rather than being padded in as fake coverage. */
export const DOMAIN_ORDER = ["order_customer", "inventory", "delivery", "financial", "supplier", "controls"];

export const DOMAIN_LABEL = {
  order_customer: "Order & Customer",
  inventory: "Inventory",
  delivery: "Delivery",
  financial: "Financial",
  supplier: "Supplier",
  controls: "Business Data Controls",
};
