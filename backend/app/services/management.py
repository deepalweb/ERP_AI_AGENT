"""Small read projections over the existing Case Store and finding_actions.

No scans, model calls, new tables, inferred associations, or lifecycle rules.
Counts cover the database; filters run before pagination, never over a case cap.
"""
import json
from datetime import date

from backend.app.db import appdb
from backend.app.db.store_scope import CELESTE_STORE_IDS
from backend.app.services import case_engine, finding_actions


def _ready():
    case_engine._ensure_schema()
    finding_actions._ensure_schema()


def list_actions(status=None, outcome_status=None, relevant=None, domain=None, owner=None, limit=20, offset=0):
    _ready()
    where, params = [], {}
    if status == "outstanding":
        where.append("a.status IN ('not_started','in_progress')")
    elif status:
        where.append("a.status=:status")
        params["status"] = status
    if outcome_status:
        where.append("a.outcome_status=:outcome_status")
        params["outcome_status"] = outcome_status
    if relevant is not None:
        where.append("a.relevant=:relevant")
        params["relevant"] = int(relevant)
    if domain:
        # LEFT JOIN means an action with no linked case naturally can't
        # match a domain filter -- correct, there's nothing to filter by.
        where.append("c.domain=:domain")
        params["domain"] = domain
    if owner:
        # Partial, case-insensitive (SQLite's default LIKE behavior for
        # ASCII) -- owner is free text a manager typed when recording the
        # action, so an exact match would silently return nothing for
        # anything but a perfect retype.
        where.append("a.owner LIKE :owner")
        params["owner"] = f"%{owner}%"
    clause = "WHERE " + " AND ".join(where) if where else ""
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    # Frontend Intelligence Integration: domain/owner filter against the
    # same joined shape the SELECT below uses, so COUNT(*) and the page
    # agree on what "matches" means -- has to join here too, not just
    # filter on finding_actions alone.
    join = """FROM finding_actions a LEFT JOIN business_cases c ON c.case_id=COALESCE(
                a.case_id, (SELECT o.case_id FROM case_observations o
                            WHERE o.finding_id=a.finding_id
                            ORDER BY o.observed_at DESC, o.id DESC LIMIT 1))"""
    total = appdb.run_query(f"SELECT COUNT(*) AS n {join} {clause}", params)[0]["n"]
    rows = appdb.run_query(
        f"""SELECT a.*, c.case_id AS linked_case_id, c.metric_id AS case_metric_id,
                   c.status AS case_status, c.resolution_verdict AS case_resolution_verdict,
                   c.domain AS case_domain, c.horizon AS case_horizon, c.priority AS case_priority
            {join}
            {clause} ORDER BY a.updated_at DESC, a.finding_id ASC LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset},
    )
    # Frontend Intelligence Integration: "Review session" told a manager
    # nothing about WHY -- the case's own title (from its latest finding
    # snapshot, e.g. "8 stock counts started but never finished") is the
    # actual business reason, not stored on business_cases itself so it's
    # batch-fetched the same way list_cases() already does, never a
    # per-row query.
    case_ids = [row["linked_case_id"] for row in rows if row["linked_case_id"]]
    latest_by_case = case_engine._latest_observations_for(case_ids)
    items = []
    for row in rows:
        action = finding_actions._action_row_to_dict(row, include_finding_id=True)
        action["description"] = (action.get("action_taken") or "").strip() or (action.get("intended_result") or "").strip() or None
        action["case_link_status"] = ("linked" if row.get("case_id") else "observation_link") if row["linked_case_id"] else (
            "missing_case" if row.get("case_id") else "unlinked"
        )
        action["deadline_note"] = None
        try:
            if date.fromisoformat(action["deadline"]).year < date.fromisoformat(action["updated_at"][:10]).year:
                action["deadline_note"] = "Saved due date is earlier than the record's update year; review this date."
        except (ValueError, TypeError):
            pass
        case_title = None
        if row["linked_case_id"]:
            latest_row = latest_by_case.get(row["linked_case_id"])
            if latest_row:
                case_title = json.loads(latest_row["snapshot"]).get("title")
        action["case"] = {
            "case_id": row["linked_case_id"], "metric_id": row["case_metric_id"],
            "status": row["case_status"], "resolution_verdict": row["case_resolution_verdict"],
            "domain": row["case_domain"], "horizon": row["case_horizon"], "priority": row["case_priority"],
            "title": case_title,
        } if row["linked_case_id"] else None
        items.append(action)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def summary():
    _ready()
    cases = {r["status"]: r["n"] for r in appdb.run_query(
        "SELECT status, COUNT(*) AS n FROM business_cases GROUP BY status"
    )}
    verdicts = {r["resolution_verdict"]: r["n"] for r in appdb.run_query(
        "SELECT resolution_verdict, COUNT(*) AS n FROM business_cases "
        "WHERE status='resolved' AND resolution_verdict IS NOT NULL GROUP BY resolution_verdict"
    )}
    # Investigate V3's compact summary strip needs "Critical 4 / High 6"
    # as a real, global count, not derived from whatever page of results
    # happens to be loaded client-side (case_engine.list_cases() paginates,
    # so a client-side count would silently go wrong past one page).
    # Scoped to still-active cases (open/reopened) -- a resolved or
    # dismissed case's priority isn't "needs attention" anymore.
    priorities = {r["priority"]: r["n"] for r in appdb.run_query(
        "SELECT priority, COUNT(*) AS n FROM business_cases "
        "WHERE status IN ('open','reopened') AND priority IS NOT NULL GROUP BY priority"
    )}
    actions = appdb.run_query("""
        SELECT COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN relevant=1 AND status IN ('not_started','in_progress') THEN 1 ELSE 0 END),0) AS outstanding,
          COALESCE(SUM(CASE WHEN relevant=1 AND status='completed' THEN 1 ELSE 0 END),0) AS completed,
          COALESCE(SUM(CASE WHEN relevant=1 AND outcome_status='blocked' THEN 1 ELSE 0 END),0) AS blocked,
          COALESCE(SUM(CASE WHEN relevant=1 AND outcome_status='pending' THEN 1 ELSE 0 END),0) AS pending
        FROM finding_actions
    """)[0]
    domains = appdb.run_query("""
        SELECT domain, status, priority, category, COUNT(*) AS count,
               MAX(last_seen_at) AS last_seen_at
        FROM business_cases GROUP BY domain, status, priority, category
        ORDER BY domain, status, priority
    """)
    return {"cases": {**cases, **verdicts}, "case_total": sum(cases.values()), "case_priorities": priorities, "actions": actions, "case_domains": domains, "case_impacts": impact_overview(), "sync": sync_status()}


def finding_case_id(finding_id):
    _ready()
    # A saved action retains its own case association even if observations
    # have changed. Never infer the case from a metric/title/period string.
    rows = appdb.run_query("SELECT case_id FROM finding_actions WHERE finding_id=:id", {"id": finding_id})
    if rows and rows[0]["case_id"]:
        return rows[0]["case_id"]
    return case_engine.get_case_id_for_finding(finding_id)


def impact_overview():
    """Latest evidence per unresolved case; no summing overlapping exposures."""
    rows = appdb.run_query("""
        SELECT c.case_id, c.domain, c.metric_id, c.status, c.priority,
               o.snapshot, h.first_observed, h.last_observed, h.observations
        FROM business_cases c
        LEFT JOIN (SELECT case_id, MIN(observed_at) AS first_observed,
                          MAX(observed_at) AS last_observed, COUNT(*) AS observations
                   FROM case_observations GROUP BY case_id) h ON h.case_id=c.case_id
        LEFT JOIN case_observations o ON o.id=(
            SELECT id FROM case_observations WHERE case_id=c.case_id
            ORDER BY observed_at DESC, id DESC LIMIT 1)
        WHERE c.status IN ('open','reopened','verification_pending')
        ORDER BY CASE c.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                 h.first_observed, c.case_id
    """)
    items = []
    for row in rows:
        snapshot = json.loads(row['snapshot']) if row.get('snapshot') else {}
        summary = case_engine._summarize_snapshot(snapshot)
        items.append({
            **{key: row.get(key) for key in ('case_id','domain','metric_id','status','priority','first_observed','last_observed','observations')},
            'title': snapshot.get('title'), 'impact': {key: (snapshot.get('impact') or {}).get(key) for key in ('financial_impact_rs', 'detail', 'confidence', 'kind', 'month', 'units_affected', 'valued_units_pct')},
            'measurement': summary.get('confirm'), 'metric': summary.get('metric'),
            'top_contributor': summary.get('top_contributor'),
        })
    return items


# Management families for Inventory V1: a navigation/aggregation layer over the
# existing atomic cases. Read-only: case IDs, actions, outcomes and histories
# are untouched, and no exposure is summed across children (they can overlap).
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
FAMILY_LABELS = {
    "warehouse_transfer_shortages": "Warehouse transfer shortages",
    "warehouse_transfer_overages": "Warehouse transfer overages",
    "warehouse_dispatch_overage": "Warehouse dispatch overage",
    "stock_availability": "Stock availability",
    "stock_count_controls": "Stock-count controls",
    "damage_and_writeoffs": "Damage and write-off events",
    "inventory_health": "Inventory health",
    "inventory_opportunities": "Inventory opportunities",
}
FAMILY_ORDER = list(FAMILY_LABELS)
FAMILY_DOMAIN_KEYS = {
    "warehouse_transfer_shortages": "stock_movement",
    "warehouse_transfer_overages": "stock_movement",
    "warehouse_dispatch_overage": "stock_movement",
    "stock_availability": "stock_availability_risk",
    "stock_count_controls": "stock_record_controls",
    "damage_and_writeoffs": "damage_writeoff_losses",
    "inventory_health": "inventory_performance",
    "inventory_opportunities": "inventory_performance",
}


def family_for(item):
    """Which Inventory V1 family a case belongs to, or None (outside V1).
    Matches the metric_id suffix for transfer findings so that legacy per-outlet
    IDs (transfer_discrepancy_s46_missing_item) fall into the same family."""
    metric_id = item.get("metric_id") or ""
    if metric_id.startswith(("transfer_discrepancy_", "extreme_event_")):
        if metric_id.endswith("_missing_item") or metric_id.startswith("extreme_event_"):
            return "warehouse_transfer_shortages"
        if metric_id.endswith("_extra_quantity"):
            return "warehouse_transfer_overages"
        if metric_id.endswith("_product_damage"):
            return "damage_and_writeoffs"
    if metric_id == "dispatch_sent_above_plan":
        return "warehouse_dispatch_overage"
    if metric_id == "dispatch_pattern":
        return "warehouse_transfer_shortages"
    if metric_id in ("stock_availability", "top_seller_stock"):
        return "stock_availability"
    if (
        metric_id in ("stuck_stock_count", "completed_stock_count_variance")
        or metric_id.startswith("completed_stock_count_variance-")
    ):
        return "stock_count_controls"
    if metric_id == "damage_writeoff":
        return "damage_and_writeoffs"
    if metric_id == "dead_overstock_value":
        return "inventory_health"
    if item.get("domain") == "inventory" and item.get("category") == "opportunity":
        return "inventory_opportunities"
    return None


def _impact(item):
    return ((item.get("latest") or {}).get("impact_summary") or {})


def families_from_cases(items, relationships=None):
    groups = {key: [] for key in FAMILY_ORDER}
    for item in items:
        key = family_for(item)
        if key:
            groups[key].append(item)
    cases_by_id = {item["case_id"]: item for item in items}
    linked_evidence = {}
    for relationship in relationships or []:
        relation_type = relationship.get("relationship_type")
        if relation_type not in ("duplicate_of", "overlaps"):
            continue
        left, right = relationship.get("case_id"), relationship.get("related_case_id")
        if left and right:
            linked_evidence.setdefault(left, []).append((right, relation_type, relation_type == "duplicate_of"))
            linked_evidence.setdefault(right, []).append((left, relation_type, False))
    families = []
    for key in FAMILY_ORDER:
        children = groups[key]
        if not children:
            continue
        children.sort(key=lambda c: (PRIORITY_RANK.get(c.get("priority"), 4), -(c.get("business_impact_score") or 0), c["case_id"]))
        driver = children[0]
        quantified = [c for c in children if _impact(c).get("financial_impact_rs") is not None]
        families.append({
            "key": key,
            "label": FAMILY_LABELS[key],
            "domain_key": FAMILY_DOMAIN_KEYS[key],
            "priority": driver.get("priority"),
            "driving_case_id": driver["case_id"],
            "driving_title": (driver.get("latest") or {}).get("title") or driver["metric_id"],
            "active_children": len(children),
            "exposure_quantified_children": len(quantified),
            "exposure_note": "Not summed: child findings can reference the same shortage lines, so values may overlap.",
            "children": [
                {
                    "case_id": c["case_id"], "metric_id": c["metric_id"], "domain": c["domain"],
                    "category": c.get("category"), "horizon": c.get("horizon"), "status": c["status"],
                    "priority": c.get("priority"), "last_seen_at": c.get("last_seen_at"),
                    "title": (c.get("latest") or {}).get("title") or c["metric_id"],
                    "impact_summary": (c.get("latest") or {}).get("impact_summary"),
                    "related_evidence": [
                        {
                            "case_id": related_id,
                            "title": (cases_by_id.get(related_id, {}).get("latest") or {}).get("title")
                                or cases_by_id.get(related_id, {}).get("metric_id"),
                            "relationship_type": relation_type,
                            "is_duplicate_of": is_duplicate_of,
                        }
                        for related_id, relation_type, is_duplicate_of in linked_evidence.get(c["case_id"], [])
                    ],
                }
                for c in children
            ],
        })
    return families


def families():
    _ready()
    listing = case_engine.list_cases(status="active", limit=200)
    relationships = appdb.run_query(
        "SELECT case_id, related_case_id, relationship_type FROM case_relationships "
        "WHERE relationship_type IN ('duplicate_of', 'overlaps')"
    )
    return families_from_cases(listing["items"], relationships)


def sync_status():
    """What the investigation was last computed from, and when. Read-only: never
    starts a scan (home_briefing.get_brief does, so it is deliberately not used)."""
    from backend.app.db import reporting
    from backend.app.services import brain, home_briefing
    brain._ensure_schema()
    last_run = appdb.run_query("SELECT generated_at FROM daily_brief ORDER BY generated_at DESC LIMIT 1")
    orders = reporting.run_query("SELECT MAX(date_order) AS at FROM sale_order WHERE store_id = ANY(:stores)", {"stores": list(CELESTE_STORE_IDS)})
    transfers = reporting.run_query("SELECT MAX(create_date) AS at FROM stock_move_shortage")
    return {
        "last_run_at": last_run[0]["generated_at"] if last_run else None,
        "latest_order_at": orders[0]["at"].isoformat() if orders and orders[0]["at"] else None,
        "latest_transfer_record_at": transfers[0]["at"].isoformat() if transfers and transfers[0]["at"] else None,
        "refresh": home_briefing.refresh_state(),
    }
