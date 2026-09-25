"""Customer analytical lookup -- Wave 1 Customer domain, Phase A (see the
"Customer Lookup Design" doc). Resolves a customer by name/phone/email and
returns an analytical profile: order history, lifecycle category, next-
basket prediction -- never raw phone/email.

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
from backend.app.services.customer_predictions import (
    LIFECYCLE_SQL,
    RECENT_WINDOW_GAPS,
    VALUE_PERCENTILE_SQL,
    _lifecycle_category,
    _value_tier,
    get_predicted_next_basket,
    search_customers,
)

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
        "deterioration": None,  # Phase B, not built yet
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
