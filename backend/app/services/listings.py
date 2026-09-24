"""Store/channel listing lookup: is a product actually listed for sale at each
Celeste outlet, and enabled on Uber/PickMe there — a different question from
stock quantity (product_lookup.get_product_lookup). A product can have stock
but not be listed, or be listed with a channel disabled, and neither shows up
as a stock number.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, CELESTE_STORE_NAMES
from backend.app.services.product_lookup import _resolve

LISTING_SQL = f"""
    SELECT rs.name AS outlet, rsp.available_in_uber, rsp.available_in_pickme, rsp.store_price_unit
    FROM res_store_product rsp
    JOIN res_store rs ON rs.id = rsp.store_id
    JOIN product_product pp ON pp.id = rsp.product_id
    WHERE pp.product_tmpl_id = :tmpl_id AND rsp.store_id IN {CELESTE_STORE_IDS}
    ORDER BY rs.name
"""


def get_product_listing(query: str):
    chosen, candidates, reason = _resolve(query)

    if chosen is None:
        if reason == "not_found":
            return {"query": query, "status": "not_found"}
        # "archived_only" / "duplicate_sku" / "ambiguous" -- see
        # product_lookup._resolve's own docstring for what each means.
        # Never arbitrarily picked between them here either.
        return {
            "query": query,
            "status": reason,
            "matches": [{"name": r["name"], "sku": r["default_code"]} for r in candidates],
        }

    rows = reporting.run_query(LISTING_SQL, {"tmpl_id": chosen["id"]})
    listed_outlets = {r["outlet"] for r in rows}
    not_listed_at = [name for name in CELESTE_STORE_NAMES if name not in listed_outlets]

    return {
        "query": query,
        "status": "found",
        "product": {"name": chosen["name"], "sku": chosen["default_code"]},
        "listings": [
            {
                "outlet": r["outlet"],
                "available_in_uber": bool(r["available_in_uber"]),
                "available_in_pickme": bool(r["available_in_pickme"]),
                "price": float(r["store_price_unit"]) if r["store_price_unit"] is not None else None,
            }
            for r in rows
        ],
        "not_listed_at": not_listed_at,
    }
