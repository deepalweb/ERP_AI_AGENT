"""Customer analytical lookup -- Wave 1 Customer domain, Phase A + Phase B
measurement layer (see the "Customer Lookup Design" doc). Resolves a
customer by name/phone/email and returns an analytical profile: order
history, lifecycle category, next-basket prediction, and raw deterioration
signals -- never raw phone/email.

Phase B here is measurement only: recent-vs-prior order/revenue/AOV,
calendar recency, cadence deviation. No "deteriorating customer" verdict,
threshold, or automatic case -- see _deterioration()'s docstring. What
counts as material deterioration is a decision for real-data calibration,
not something this module decides.

This is the ONLY place Ask Celeste's customer tool reads from, never
customer_predictions.search_customers directly -- so masking can't be
bypassed by a future caller forgetting to mask. Raw contact detail is a
deliberately separate, deferred capability (see the design doc): the app
has no per-manager account system today (backend/app/core/access.py is
either unauthenticated local-only access, or one shared basic-auth login
for everyone), so there is no identity to gate an "unmask" action behind
yet.

Two different "as of" dates are genuinely in play here, not unified into
one -- reporting them separately is more honest than picking one:
- lifecycle_category/order history come from LIFECYCLE_SQL's own live
  as_of_date (computed fresh from the order data every call).
- next_basket carries its own as_of_date from customer_predictions'
  cached prediction snapshot (customer_prediction_stats_v2), which can
  lag behind the live figure above.
"""

import os
from datetime import datetime

from backend.app.db import appdb, reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from backend.app.services.customer_predictions import (
    DETERIORATION_WINDOW_DAYS_DEFAULT,
    LIFECYCLE_SQL,
    RECENT_WINDOW_GAPS,
    VALUE_PERCENTILE_SQL,
    _lifecycle_category,
    _value_tier,
    get_deterioration_window,
    get_predicted_next_basket,
    search_customers,
)

# Frontend Intelligence Integration: the Customers page's detail view
# already has a partner_id from a prior search result -- it needs only
# name/phone (to mask) and order_count, the same fields search_customers()
# already resolves, scoped identically (SALE_ORDER_LIVE_STATES + Celeste's
# own outlets -- a real bug fixed here: this subquery previously counted
# orders across ALL stores, including the 22 non-Celeste partner-brand
# outlets, while lifetime_revenue (LIFECYCLE_SQL, via customer_orders) was
# already correctly scoped to Celeste's 4 outlets. The two numbers shown
# together on the same profile card described two different order
# populations -- caught by independently recomputing a real customer's
# figures from raw SQL and finding order_count=25/revenue=228,780.61
# didn't reconcile against any single population, until store_id was
# added: 21 Celeste orders correctly matched the revenue figure).
PARTNER_IDENTITY_SQL = f"""
    SELECT rp.name, rp.phone,
           (SELECT COUNT(*) FROM sale_order so WHERE so.partner_id = rp.id AND so.state IN {SALE_ORDER_LIVE_STATES}
                AND so.store_id IN {CELESTE_STORE_IDS}) AS order_count
    FROM res_partner rp WHERE rp.id = :partner_id
"""

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query(
        """
        CREATE TABLE IF NOT EXISTS customer_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            partner_id INTEGER NOT NULL,
            access_type TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            auth_context TEXT
        )
        """
    )
    _SCHEMA_READY = True


def _mask_phone(phone: str | None) -> str | None:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < 4:
        return None
    return f"•••• {digits[-4:]}"


def _log_access(partner_id: int, access_type: str, sensitivity: str) -> None:
    """Honest framing: this is not a "who accessed this customer" audit
    trail -- auth_context is the deployment's auth mode (local/basic),
    never a named manager, until real user accounts exist. It answers
    when/which customer/which auth mode/masked-or-raw, not who."""
    _ensure_schema()
    appdb.run_query(
        "INSERT INTO customer_access_log (at, partner_id, access_type, sensitivity, auth_context) "
        "VALUES (:at, :partner_id, :access_type, :sensitivity, :auth_context)",
        {
            "at": datetime.utcnow().isoformat(),
            "partner_id": partner_id,
            "access_type": access_type,
            "sensitivity": sensitivity,
            "auth_context": os.getenv("APP_AUTH_MODE", "local"),
        },
    )


def _deterioration(partner_id: int, lifecycle_row: dict | None, window_days: int = DETERIORATION_WINDOW_DAYS_DEFAULT) -> dict | None:
    """Wave 1 Customer Phase B, measurement layer only: recent-vs-prior
    order count/revenue/AOV, calendar recency, and cadence deviation --
    no blended score, no "deteriorating" verdict. Cadence deviation reuses
    LIFECYCLE_SQL's own last_gap/prior_avg_gap (already fetched for this
    customer) rather than a second copy of that math; recent/prior
    order+revenue come from the day-window DETERIORATION_SQL, a genuinely
    different comparison LIFECYCLE_SQL doesn't make (order-count windows,
    not day windows)."""
    rows = get_deterioration_window(partner_id, window_days)
    if not rows:
        return None
    d = rows[0]
    recent_orders = int(d["recent_orders"] or 0)
    prior_orders = int(d["prior_orders"] or 0)
    recent_revenue = float(d["recent_revenue"] or 0)
    prior_revenue = float(d["prior_revenue"] or 0)
    as_of_date = d["as_of_date"]

    last_order = lifecycle_row["last_order"] if lifecycle_row else None
    days_since_last = (
        round((as_of_date - last_order).total_seconds() / 86400.0) if last_order and as_of_date else None
    )
    prior_avg_gap = (
        float(lifecycle_row["prior_avg_gap"])
        if lifecycle_row and lifecycle_row["prior_avg_gap"] is not None
        else None
    )
    cadence_ratio = (
        round(days_since_last / prior_avg_gap, 1)
        if days_since_last is not None and prior_avg_gap and prior_avg_gap > 0
        else None
    )

    return {
        "window_days": window_days,
        "as_of": as_of_date.date().isoformat() if as_of_date else None,
        "recent": {
            "orders": recent_orders,
            "revenue": round(recent_revenue, 2),
            "avg_order_value": round(recent_revenue / recent_orders, 2) if recent_orders else None,
        },
        "prior": {
            "orders": prior_orders,
            "revenue": round(prior_revenue, 2),
            "avg_order_value": round(prior_revenue / prior_orders, 2) if prior_orders else None,
        },
        "days_since_last_order": days_since_last,
        "historical_reorder_interval_days": round(prior_avg_gap, 1) if prior_avg_gap else None,
        # >1 = slower than this customer's own normal cadence; <1 = faster.
        # Deliberately not labeled "risk" or "score" -- a plain ratio the
        # reader interprets themselves, same posture as every other raw
        # figure in this module.
        "cadence_ratio": cadence_ratio,
    }


def _customer_profile(partner_id: int, name: str, phone: str | None, order_count: int) -> tuple[dict, str | None]:
    rows = reporting.run_query(LIFECYCLE_SQL, {"recent_window_gaps": RECENT_WINDOW_GAPS, "partner_id": partner_id})
    row = rows[0] if rows else None
    lifecycle_category = None
    value_tier = None
    revenue = None
    last_order_date = None
    as_of_date = None
    if row:
        as_of_date = row["as_of_date"]
        lifecycle_category = _lifecycle_category(row, as_of_date)
        revenue = float(row["revenue"] or 0)
        last_order_date = row["last_order"].date().isoformat() if row["last_order"] else None

        # p80/p50 always come from the whole customer population, never a
        # one-person population -- classifying this customer's own revenue
        # against percentiles computed from only their own revenue would be
        # meaningless. Same query compute_lifecycle_summary() uses, so
        # "high value" can never mean two different things in this app.
        percentile_rows = reporting.run_query(VALUE_PERCENTILE_SQL)
        if percentile_rows:
            p80 = float(percentile_rows[0]["p80"] or 0)
            p50 = float(percentile_rows[0]["p50"] or 0)
            value_tier = _value_tier(revenue, p80, p50)

    profile = {
        "partner_id": partner_id,
        "name": name,
        "phone_masked": _mask_phone(phone),
        "email_masked_or_omitted": None,  # deliberately never populated here -- see module docstring
        "order_count": order_count,
        "lifetime_revenue": round(revenue, 2) if revenue is not None else None,
        "last_order_date": last_order_date,
        "lifecycle_category": lifecycle_category,
        "value_tier": value_tier,
        "next_basket": get_predicted_next_basket(partner_id),
        "deterioration": _deterioration(partner_id, row),
    }
    return profile, (as_of_date.date().isoformat() if as_of_date else None)


def get_customer_lookup(name: str | None = None, phone: str | None = None, email: str | None = None) -> dict:
    """Ask Celeste's only customer-resolution tool. Raw phone/email inputs
    are used to resolve a match and then discarded -- never echoed back
    in the response, never written to the access log."""
    matches = search_customers(name=name, phone=phone, email=email, limit=10)
    if not matches:
        return {"status": "not_found"}

    if len(matches) == 1:
        m = matches[0]
        profile, as_of = _customer_profile(m["partner_id"], m["name"], m["phone"], m["order_count"])
        _log_access(m["partner_id"], "analytical_lookup", "masked")
        return {"status": "found", "customer": profile, "as_of": as_of}

    return {
        "status": "multiple",
        "matches": [
            {
                "partner_id": m["partner_id"],
                "name": m["name"],
                "phone_masked": _mask_phone(m["phone"]),
                "order_count": m["order_count"],
            }
            for m in matches
        ],
    }


def get_customer_profile(partner_id: int) -> dict | None:
    """The Customers page's detail view, for a partner_id already resolved
    from a prior search/list -- the REST counterpart to
    get_customer_lookup()'s single-match path, for a caller that already
    knows exactly which customer it wants rather than searching by name/
    phone/email. Same masking, same access logging."""
    rows = reporting.run_query(PARTNER_IDENTITY_SQL, {"partner_id": partner_id})
    if not rows:
        return None
    r = rows[0]
    name = (r["name"] or "").strip() or "Unknown"
    profile, as_of = _customer_profile(partner_id, name, r["phone"], int(r["order_count"] or 0))
    _log_access(partner_id, "analytical_lookup", "masked")
    return {"customer": profile, "as_of": as_of}
