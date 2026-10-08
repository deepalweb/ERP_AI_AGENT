import sqlite3
from backend.app.services import management

def test_domain_summary_covers_all_statuses_beyond_page_size(monkeypatch):
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE business_cases (domain TEXT,status TEXT,priority TEXT,category TEXT,last_seen_at TEXT)')
    db.executemany('INSERT INTO business_cases VALUES (?,?,?,?,?)', [('financial','open','high','critical','2026-09-29')]*25 + [('inventory','verification_pending','high','critical','2026-09-28')]*3)
    monkeypatch.setattr(management,'_ready',lambda:None)
    def query(sql,*args):
        if 'GROUP BY domain, status, priority, category' in sql:
            return [dict(row) for row in db.execute(sql)]
        if 'FROM finding_actions' in sql: return [{}]
        return []
    monkeypatch.setattr(management.appdb,'run_query',query)
    rows=management.summary()['case_domains']
    assert sum(row['count'] for row in rows)==28
    assert rows[0]['count']==25
    assert rows[1]['status']=='verification_pending'
    db.close()

def test_impact_uses_latest_snapshot_and_actual_observation_span(monkeypatch):
    import json
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE business_cases (case_id TEXT,domain TEXT,metric_id TEXT,status TEXT,priority TEXT)')
    db.execute('CREATE TABLE case_observations (id INTEGER,case_id TEXT,observed_at TEXT,snapshot TEXT)')
    db.executemany('INSERT INTO business_cases VALUES (?,?,?,?,?)', [('a','financial','revenue','open','high'),('b','inventory','stock','resolved','high'),('c','controls','cost','verification_pending','medium')])
    db.executemany('INSERT INTO case_observations VALUES (?,?,?,?)', [(1,'a','2026-09-01T00:00:00',json.dumps({'impact':{'financial_impact_rs':900}})),(2,'a','2026-09-20T00:00:00',json.dumps({'impact':{'financial_impact_rs':0,'confidence':'measured'},'confirm':{'current':0,'period':'2026-09'}}))])
    monkeypatch.setattr(management.appdb,'run_query',lambda sql: [dict(r) for r in db.execute(sql)])
    items=management.impact_overview()
    assert [i['case_id'] for i in items]==['a','c']
    assert items[0]['impact']['financial_impact_rs']==0
    assert items[0]['observations']==2
    assert items[0]['first_observed']=='2026-09-01T00:00:00'
    assert items[0]['last_observed']=='2026-09-20T00:00:00'
    assert items[1]['first_observed'] is None
    assert items[1]['impact']['financial_impact_rs'] is None
    db.close()
