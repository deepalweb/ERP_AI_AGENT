from mcp_server.tools import transfer_accountability as ta


def _move(store, product, sku, expected, received, receiver="rcv@x", dispatcher="snd@x", receipt=1):
    return {"store_id": store, "receipt_id": receipt, "product": product, "sku": sku, "expected": expected,
            "received": received, "receipt_completed_by": receiver, "dispatch_completed_by": dispatcher}


def test_missing_is_expected_minus_received_and_extra_is_kept_apart():
    out = ta.summarize([_move(1, "A", "1", 10, 7), _move(1, "A", "1", 5, 6)])
    assert out["expected_units"] == 15
    assert out["missing_units"] == 3
    assert out["extra_units"] == 1


def test_person_rate_uses_handled_volume_and_names_largest_single_line():
    rows = [_move(1, "A", "1", 1000, 0, receiver="imesh"), _move(1, "A", "1", 100, 100, receiver="imesh"),
            _move(2, "B", "2", 100, 100, receiver="roshini")]
    people = {p["person"]: p for p in ta.summarize(rows)["receivers"]}
    assert people["imesh"]["missing_units"] == 1000
    assert people["imesh"]["largest_line_missing"] == 1000
    assert people["imesh"]["missing_rate_pct"] == round(1000 / 1100 * 100, 2)
    assert people["roshini"]["missing_units"] == 0


def test_zero_received_lines_and_unknown_person_are_counted():
    out = ta.summarize([_move(1, "A", "1", 10, 0, dispatcher=None)])
    assert out["dispatchers"][0]["person"] == ta.NOT_RECORDED
    assert out["dispatchers"][0]["zero_received_lines"] == 1


def test_product_is_recurring_only_across_moves_and_outlets():
    one_outlet = [_move(1, "P", "p", 10, 0) for _ in range(3)]
    recurring = [_move(1, "Q", "q", 10, 0), _move(2, "Q", "q", 10, 0), _move(3, "Q", "q", 10, 0)]
    patterns = {p["product"]: p["pattern"] for p in ta.summarize(one_outlet + recurring)["products"]}
    assert patterns == {"P": "one-off", "Q": "recurring"}


def test_products_with_no_gap_are_left_out():
    assert ta.summarize([_move(1, "A", "1", 10, 10)])["products"] == []


def test_daily_series_splits_recurring_products_from_the_rest():
    from datetime import datetime
    day = datetime(2026, 9, 27, 10, 0)
    rows = [{**_move(s, "Q", "q", 10, 0), "done_at": day} for s in (1, 2, 3)]
    rows.append({**_move(1, "Solo", "s", 50, 0), "done_at": day})
    daily = ta.summarize(rows)["daily"]
    assert daily == [{"date": "2026-09-27", "recurring_units": 30, "other_units": 50}]


def _filtered(rows, filters):
    keys = ta._recurring_keys(rows)
    kept = [r for r in rows if ta._matches(r, filters, keys)]
    return ta.summarize(kept, keys), keys


def test_outlet_filter_keeps_only_that_outlet():
    rows = [_move(1, "A", "1", 10, 0), _move(2, "A", "1", 10, 10)]
    out, _ = _filtered(rows, ta.make_filters(outlet=["2"]))
    assert out["moves"] == 1 and out["missing_units"] == 0


def test_person_filter_matches_either_role():
    rows = [_move(1, "A", "1", 10, 0, receiver="imesh", dispatcher="sandya"),
            _move(1, "A", "1", 10, 0, receiver="roshini", dispatcher="sandya"),
            _move(1, "A", "1", 10, 0, receiver="roshini", dispatcher="nalakar")]
    out, _ = _filtered(rows, ta.make_filters(person=["sandya"]))
    assert out["moves"] == 2
    out, _ = _filtered(rows, ta.make_filters(person=["imesh"]))
    assert out["moves"] == 1


def test_date_filter_is_inclusive_and_drops_undated_rows():
    from datetime import datetime
    rows = [{**_move(1, "A", "1", 10, 0), "done_at": datetime(2026, 9, d, 9)} for d in (26, 27, 28)]
    rows.append(_move(1, "A", "1", 10, 0))  # no done_at
    out, _ = _filtered(rows, ta.make_filters(date_from="2026-09-27", date_to="2026-09-28"))
    assert out["moves"] == 2


def test_recurring_only_uses_full_window_status_not_the_filtered_slice():
    # Product Q is recurring across three outlets; filtering to one outlet must keep it recurring.
    rows = [_move(s, "Q", "q", 10, 0) for s in (1, 2, 3)]
    out, keys = _filtered(rows, ta.make_filters(outlet=["1"], recurring_only=True))
    assert ("q" in keys) and out["moves"] == 1
    assert out["products"][0]["pattern"] == "recurring"


def test_bad_date_is_ignored_and_no_filter_is_inactive():
    f = ta.make_filters(date_from="27/09/2026")
    assert f.date_from is None and not f.active


def test_over_plan_summary_counts_units_above_plan_by_dispatcher():
    rows = [
        {"transfer_out": "A", "outlet": "X", "product": "P", "sku": "1", "plan": 2, "sent": 2000, "dispatch_completed_by": "gayan@x"},
        {"transfer_out": "B", "outlet": "Y", "product": "Q", "sku": "2", "plan": 10, "sent": 15, "dispatch_completed_by": "sandya@x"},
    ]
    out = ta.over_plan_summary(rows)
    assert out["lines"] == 2
    assert out["units_over"] == 2003
    assert out["top_lines"][0]["over_qty"] == 1998
    assert out["by_dispatcher"][0] == {"person": "gayan@x", "lines": 1, "units_over": 1998}


def test_over_plan_summary_respects_the_person_filter():
    # Previously over_plan_summary never filtered at all -- confirmed live,
    # filtering a dispatch_pattern case to 2 dispatchers still showed a 3rd,
    # unrelated dispatcher's line in "Sent above plan", contradicting the
    # finding directly above it on the same page.
    rows = [
        {"transfer_out": "A", "outlet": "X", "store_id": 1, "product": "P", "sku": "1", "plan": 2, "sent": 2000, "dispatch_completed_by": "gayan@x"},
        {"transfer_out": "B", "outlet": "Y", "store_id": 2, "product": "Q", "sku": "2", "plan": 10, "sent": 15, "dispatch_completed_by": "sandya@x"},
    ]
    out = ta.over_plan_summary(rows, ta.make_filters(person=["sandya@x"]))
    assert out["lines"] == 1
    assert out["top_lines"][0]["dispatch_completed_by"] == "sandya@x"


def test_over_plan_summary_respects_the_outlet_and_product_filters():
    rows = [
        {"transfer_out": "A", "outlet": "X", "store_id": 1, "product": "P", "sku": "1", "plan": 2, "sent": 2000, "dispatch_completed_by": "gayan@x"},
        {"transfer_out": "B", "outlet": "Y", "store_id": 2, "product": "Q", "sku": "2", "plan": 10, "sent": 15, "dispatch_completed_by": "sandya@x"},
    ]
    assert ta.over_plan_summary(rows, ta.make_filters(outlet=["2"]))["lines"] == 1
    assert ta.over_plan_summary(rows, ta.make_filters(product=["1"]))["lines"] == 1


def test_not_on_plan_counts_receipt_lines_whose_note_has_no_such_product():
    rows = [
        {"transfer_out": "A", "out_lines": 0, "received": 5},
        {"transfer_out": "A", "out_lines": 1, "received": 7},
        {"transfer_out": None, "out_lines": 0, "received": 9},  # no outbound note: not counted here
    ]
    assert ta.not_on_plan_summary(rows) == {"lines": 1, "units_received": 5}
