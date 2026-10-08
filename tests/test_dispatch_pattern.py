from mcp_server.tools import dispatch_pattern as dp


def _move(store, product, sku, expected, received, dispatcher):
    return {"store_id": store, "product": product, "sku": sku, "expected": expected,
            "received": received, "dispatch_completed_by": dispatcher}


def _build(monkeypatch, rows):
    monkeypatch.setattr(dp, "_raw_rows", lambda: rows)
    return dp.build_dispatch_pattern_findings()


# A large, low-loss "everything else" baseline, the way a real dispatcher's full
# handled volume dwarfs any one product -- keeps a flagged pair's own missing
# units from meaningfully shifting the "own baseline" denominator, same as the
# real data (a dispatcher handling 75,826 units overall).
def _baseline(dispatcher, rate_pct=0.5):
    expected = 100_000
    missing = expected * rate_pct / 100
    return _move(99, "Everything else", "baseline", expected, expected - missing, dispatcher)


def test_no_rows_produces_no_case(monkeypatch):
    assert _build(monkeypatch, []) == []


def test_concentration_alone_does_not_trigger():
    # One dispatcher sends 100% of a recurring product's volume, with a small
    # gap -- this is route assignment, not a rate anomaly, and must not fire.
    rows = [_move(s, "Q", "q", 100, 99, "amal") for s in (1, 2, 3)]
    recurring_keys = dp._recurring_keys(rows)
    assert dp._pairs(rows, recurring_keys) == []


def test_elevated_rate_fires_and_matches_the_dispatcher_own_baseline(monkeypatch):
    # sandya: a normal ~0.5% baseline on everything else, but 15% on one
    # specific recurring product -- the Sandya/Potato pattern, generalized.
    rows = [_baseline("sandya")] + [_move(s, "Potato", "p", 200, 170, "sandya") for s in (1, 2, 3)]
    [finding] = _build(monkeypatch, rows)
    assert finding["metric_id"] == "dispatch_pattern"
    [row] = finding["pattern_rows"]
    assert row["dispatcher"] == "sandya" and row["sku"] == "p"
    assert row["rate_pct"] == 15.0
    assert row["own_rate_pct"] < 2.0  # well below the 15% pair rate, not contaminated by it
    assert row["ratio"] >= dp.RATIO_MIN


def test_below_the_volume_floor_does_not_fire(monkeypatch):
    # Same 15% rate, but only 30 units handled on the pair -- below MIN_PAIR_UNITS.
    rows = [_baseline("sandya")] + [_move(s, "Potato", "p", 10, 8.5, "sandya") for s in (1, 2, 3)]
    assert _build(monkeypatch, rows) == []


def test_product_must_be_recurring_overall_not_just_for_one_dispatcher(monkeypatch):
    # All three short lines at one outlet -- fails the product-level recurring
    # gate (needs 2+ outlets), even though the volume and rate both qualify.
    rows = [_baseline("sandya")] + [_move(1, "Potato", "p", 200, 170, "sandya")] * 3
    assert _build(monkeypatch, rows) == []


def test_rate_below_the_floor_does_not_fire(monkeypatch):
    rows = [_baseline("sandya")] + [_move(s, "Potato", "p", 200, 198, "sandya") for s in (1, 2, 3)]  # 1% rate
    assert _build(monkeypatch, rows) == []


def test_priority_is_medium_just_above_the_floor(monkeypatch):
    rows = [_baseline("a")] + [_move(s, "P", "p", 200, 188, "a") for s in (1, 2, 3)]  # 6% rate, one pair
    [finding] = _build(monkeypatch, rows)
    assert finding["priority"] == "medium"


def test_priority_is_high_at_double_the_floor(monkeypatch):
    rows = [_baseline("a")] + [_move(s, "P", "p", 200, 176, "a") for s in (1, 2, 3)]  # 12% rate, one pair
    [finding] = _build(monkeypatch, rows)
    assert finding["priority"] == "high"


def test_priority_is_critical_at_triple_the_floor(monkeypatch):
    rows = [_baseline("a")] + [_move(s, "P", "p", 200, 160, "a") for s in (1, 2, 3)]  # 20% rate, one pair
    [finding] = _build(monkeypatch, rows)
    assert finding["priority"] == "critical"


def test_breakdowns_group_by_dispatcher_and_product(monkeypatch):
    rows = (
        [_baseline("sandya"), _baseline("madusanka")]
        + [_move(s, "Potato", "p", 200, 170, "sandya") for s in (1, 2, 3)]
        + [_move(s, "Onion", "o", 200, 170, "madusanka") for s in (1, 2, 3)]
    )
    [finding] = _build(monkeypatch, rows)
    by_dim = {b["dimension"]: b for b in finding["breakdowns"]}
    assert {c["name"] for c in by_dim["person"]["top_contributors"]} == {"sandya", "madusanka"}
    assert {c["name"] for c in by_dim["product"]["top_contributors"]} == {"Potato", "Onion"}
