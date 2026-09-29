import asyncio
from datetime import datetime
import pytest
from backend.app.services import performance

def test_filters_reject_outside_scope():
    for month, store in [('2026-13',None),('2026-9',None),(None,68)]:
        with pytest.raises(ValueError): performance.validate_filters(month,store)

def test_shared_filters_and_empty_selection(monkeypatch):
    calls=[]
    async def query(sql,params=None):
        calls.append((sql,params))
        if params is None:
            return [{'month':'2026-09','first_order':datetime(2026,9,1),'last_order':datetime(2026,9,27)}]
        return []
    monkeypatch.setattr(performance.reporting,'fetch_report',query)
    result=asyncio.run(performance.get_performance('2026-09',1))
    assert result['latest_month_included'] is True
    assert result['start'] is None and result['outlets']==[]
    assert len(result['hours'])==24
    for sql,params in calls[1:]:
        assert params=={'month':'2026-09','store_id':1}
        assert 'Asia/Colombo' in sql and 'store_id = :store_id' in sql
        assert "to_char(date_order, 'YYYY-MM') = :month" in sql
