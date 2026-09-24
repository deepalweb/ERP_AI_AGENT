"""Tests for backend/app/services/product_lookup.py's _resolve() --
Business Data Controls Step 0: correcting duplicate-SKU entity resolution
without arbitrary record selection. Verified live: product_template has no
unique constraint on default_code, and every real duplicate SKU found
(e.g. 004716, 004713, 007357) is exactly one active row + one archived
(active=False) row -- never two active rows for the same SKU. Preferring
the active row is a principled tie-break (Odoo's own lifecycle flag), not
a guess between look-alikes.
"""

from backend.app.services import product_lookup as pl
from backend.app.services import listings


def _row(id, name, sku, active=True):
    return {"id": id, "name": name, "default_code": sku, "active": active}


def _fake_run_query(rows):
    def run_query(sql, params=None):
        return rows
    return run_query


def test_resolve_found_for_a_single_active_exact_match(monkeypatch):
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query([_row(1, "Milk 1L", "000208")]))
    chosen, candidates, reason = pl._resolve("000208")
    assert reason == "found"
    assert chosen["id"] == 1
    assert candidates == [_row(1, "Milk 1L", "000208")]


def test_resolve_returns_not_found_for_no_rows(monkeypatch):
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query([]))
    chosen, candidates, reason = pl._resolve("nonexistent")
    assert chosen is None
    assert candidates == []
    assert reason == "not_found"


def test_resolve_prefers_the_active_row_over_an_archived_duplicate(monkeypatch):
    """The exact real-world shape found live: same SKU, one archived row
    (an old, superseded record) and one active row (the current one).
    Must resolve cleanly to the active row, not report 'ambiguous'."""
    rows = [
        _row(11577, "Araliya Bags XL (old)", "004716", active=False),
        _row(77510, "Araliya Bags XL", "004716", active=True),
    ]
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query(rows))
    chosen, candidates, reason = pl._resolve("004716")
    assert reason == "found"
    assert chosen["id"] == 77510
    assert chosen["active"] is True


def test_resolve_returns_archived_only_when_the_sole_match_is_inactive(monkeypatch):
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query([_row(1, "Colmans Tartare Sauce", "002169", active=False)]))
    chosen, candidates, reason = pl._resolve("002169")
    assert chosen is None
    assert reason == "archived_only"
    assert len(candidates) == 1


def test_resolve_returns_archived_only_when_every_match_is_inactive(monkeypatch):
    """Multiple archived duplicates, no active twin at all -- still
    'archived_only', not 'ambiguous': the distinguishing question is
    whether ANY currently-usable record exists, not how many stale ones do."""
    rows = [
        _row(1, "Old Product A", "999999", active=False),
        _row(2, "Old Product A (dup)", "999999", active=False),
    ]
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query(rows))
    chosen, candidates, reason = pl._resolve("999999")
    assert chosen is None
    assert reason == "archived_only"


def test_resolve_returns_duplicate_sku_when_two_active_rows_share_the_exact_sku(monkeypatch):
    """Not observed live yet, but must be handled correctly if it occurs:
    a genuine data-quality duplicate (same SKU, both active) is reported
    by name, distinct from a normal multi-product name search."""
    rows = [
        _row(1, "Product A", "111111", active=True),
        _row(2, "Product B (different item, same SKU typo'd in)", "111111", active=True),
    ]
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query(rows))
    chosen, candidates, reason = pl._resolve("111111")
    assert chosen is None
    assert reason == "duplicate_sku"
    assert len(candidates) == 2


def test_resolve_returns_ambiguous_for_a_name_search_matching_several_active_products(monkeypatch):
    rows = [
        _row(1, "Milk Powder 400G - Ratthi", "000352", active=True),
        _row(2, "Milk Powder 1Kg - Ratthi", "000350", active=True),
    ]
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query(rows))
    chosen, candidates, reason = pl._resolve("Milk Powder")
    assert chosen is None
    assert reason == "ambiguous"


def test_get_product_lookup_surfaces_archived_only_status(monkeypatch):
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query([_row(1, "Old Sauce", "002169", active=False)]))
    result = pl.get_product_lookup("002169")
    assert result["status"] == "archived_only"
    assert result["matches"] == [{"name": "Old Sauce", "sku": "002169"}]


def test_get_product_lookup_surfaces_duplicate_sku_status(monkeypatch):
    rows = [_row(1, "A", "111111", active=True), _row(2, "B", "111111", active=True)]
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query(rows))
    result = pl.get_product_lookup("111111")
    assert result["status"] == "duplicate_sku"


def test_get_product_listing_surfaces_archived_only_status(monkeypatch):
    monkeypatch.setattr(listings.reporting, "run_query", _fake_run_query([]))
    monkeypatch.setattr(pl.reporting, "run_query", _fake_run_query([_row(1, "Old Sauce", "002169", active=False)]))
    result = listings.get_product_listing("002169")
    assert result["status"] == "archived_only"
