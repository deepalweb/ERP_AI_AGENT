"""Small read projections over the existing Case Store and finding_actions.

No scans, model calls, new tables, inferred associations, or lifecycle rules.
Counts cover the database; filters run before pagination, never over a case cap.
"""
from datetime import date

from backend.app.db import appdb
from backend.app.services import case_engine, finding_actions


def _ready():
    case_engine._ensure_schema()
    finding_actions._ensure_schema()


def list_actions(status=None, outcome_status=None, relevant=None, limit=20, offset=0):
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
    clause = "WHERE " + " AND ".join(where) if where else ""
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    total = appdb.run_query(f"SELECT COUNT(*) AS n FROM finding_actions a {clause}", params)[0]["n"]
    rows = appdb.run_query(
        f"""SELECT a.*, c.case_id AS linked_case_id, c.metric_id AS case_metric_id,
                   c.status AS case_status, c.resolution_verdict AS case_resolution_verdict,
                   c.domain AS case_domain, c.horizon AS case_horizon
            FROM finding_actions a LEFT JOIN business_cases c ON c.case_id=COALESCE(
                a.case_id, (SELECT o.case_id FROM case_observations o
                            WHERE o.finding_id=a.finding_id
                            ORDER BY o.observed_at DESC, o.id DESC LIMIT 1))
            {clause} ORDER BY a.updated_at DESC, a.finding_id ASC LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset},
    )
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
        action["case"] = {
            "case_id": row["linked_case_id"], "metric_id": row["case_metric_id"],
            "status": row["case_status"], "resolution_verdict": row["case_resolution_verdict"],
            "domain": row["case_domain"], "horizon": row["case_horizon"],
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
    actions = appdb.run_query("""
        SELECT COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN relevant=1 AND status IN ('not_started','in_progress') THEN 1 ELSE 0 END),0) AS outstanding,
          COALESCE(SUM(CASE WHEN relevant=1 AND status='completed' THEN 1 ELSE 0 END),0) AS completed,
          COALESCE(SUM(CASE WHEN relevant=1 AND outcome_status='blocked' THEN 1 ELSE 0 END),0) AS blocked,
          COALESCE(SUM(CASE WHEN relevant=1 AND outcome_status='pending' THEN 1 ELSE 0 END),0) AS pending
        FROM finding_actions
    """)[0]
    return {"cases": {**cases, **verdicts}, "case_total": sum(cases.values()), "actions": actions}


def finding_case_id(finding_id):
    _ready()
    # A saved action retains its own case association even if observations
    # have changed. Never infer the case from a metric/title/period string.
    rows = appdb.run_query("SELECT case_id FROM finding_actions WHERE finding_id=:id", {"id": finding_id})
    if rows and rows[0]["case_id"]:
        return rows[0]["case_id"]
    return case_engine.get_case_id_for_finding(finding_id)
