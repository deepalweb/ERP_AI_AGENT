# Celeste Intelligence

An AI business-intelligence layer over Celeste's live Odoo Postgres database — a Sri Lankan quick-commerce grocery retailer. It answers questions and surfaces investigations about Celeste's own retail business, not the partner brands and channels that share its infrastructure.

## Language

### Locations & channels

**Celeste outlet**:
One of Celeste's 5 own physical retail locations (Colombo 6, Pannipitiya, Attidiya, Ethul Kotte, Ja-Ela) — the only stores every sales/operations metric in this app is scoped to. Identified explicitly by the `CELESTE_STORE_IDS` constant, not by name pattern or warehouse.
_Avoid_: store (ambiguous — see Storefront and Warehouse)

**Storefront**:
An Odoo `res_store` record — a sales channel or brand, not necessarily a Celeste outlet. Includes co-located SPAR counters, Budget Kade, 21+, and brand-specific "Fulfilled By Celeste" storefronts (Red Bull, Nestle, Fonterra, UStore) that share a Celeste outlet's warehouse without being genuine Celeste sales.
_Avoid_: store, branch (when precision matters)

**Warehouse**:
An Odoo `stock_warehouse` record — the physical fulfillment location a storefront's orders ship from. Many storefronts can point at the same warehouse, so warehouse identity alone doesn't prove an order is a genuine Celeste sale.

**Order channel**:
How a customer placed an order — direct, PickMe, or Uber — tracked per-order via fields like `is_pickme_order` and `uber_order_id`, not via a separate storefront. `source_id`/`utm_source` exists on the order but is unused (always null) and is not the real channel signal.
_Avoid_: source, utm_source

### Fulfillment & data quality

**Lost sale**:
An order line the customer wanted but didn't receive. Only `ue_item_removed` (Uber-specific) is a reliable signal today. `pickme_item_removed` is a naming trap — it also appears on orders where `is_pickme_order` is false, so it almost certainly names Celeste's internal picking app, not the PickMe delivery platform, and must not be used as a PickMe lost-sale signal. `sale_order_line.is_unavailable` is dead (0% populated).
_Avoid_: treating `pickme_item_removed` as a PickMe-platform signal

**Promised delivery time**:
Odoo's standard `commitment_date` field on `sale_order`, meant to capture when a delivery was promised to the customer. Currently 0% populated across the dataset, so true delivery-speed-to-customer cannot be measured from current data.
