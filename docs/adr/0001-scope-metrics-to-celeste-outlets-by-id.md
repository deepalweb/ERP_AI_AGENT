# Scope metrics to Celeste outlets by a hardcoded store-ID list

Every sales/operations number in this app must reflect only Celeste's own outlets, not the SPAR-branded, partner, or "Fulfilled By Celeste" storefronts that share the same physical warehouse. Because Storefront (`res_store`) and Warehouse (`stock_warehouse`) are many-to-one — several differently-branded storefronts can share one warehouse — neither store-name pattern-matching nor warehouse identity reliably distinguishes a genuine Celeste sale. We scope every query with a single shared constant, `CELESTE_STORE_IDS` (`backend/app/db/store_scope.py`), listing the 5 real outlet `res_store` IDs explicitly, rather than deriving the set dynamically from a name/brand field.

## Considered Options

- **Filter by store name pattern** (e.g. `LIKE 'Celeste Daily%'`): rejected — some non-Celeste storefronts (Budget Kade, 21+) are co-located under the same warehouse and could gain a similar-looking name in future without a code change catching them; a hardcoded list forces a deliberate decision when a new storefront appears.
- **Filter by warehouse**: rejected — a warehouse serves multiple storefronts, so it can't distinguish a genuine Celeste order from a co-located partner order at the same location.

## Consequences

New storefronts (or outlet closures) require a manual update to `CELESTE_STORE_IDS`; the list does not update itself as Odoo data changes.
