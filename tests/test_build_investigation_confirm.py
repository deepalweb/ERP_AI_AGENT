"""Tests for investigation.build_investigation()'s confirm dict -- Celeste
Investigate Phase 3 revenue audit: confirms z_score (already computed by
intelligence.detect_anomaly, previously discarded before reaching a case)
is now exposed. Pure unit test: intelligence.investigate is monkeypatched
to a canned result (no live DB), and the LLM recommendation call is
monkeypatched to the same fake client pattern test_root_cause_evidence.py
already uses, so this never makes a real network call.
"""

from mcp_server.tools import investigation as inv


class _FakeMessage:
    content = "[]"


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]


class _FakeCompletions:
    def create(self, model, messages, temperature):
        return _FakeResponse()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


def _canned_investigate_result(pct_change, z_score, baseline_mean=100.0, latest_value=None):
    latest_value = latest_value if latest_value is not None else baseline_mean * (1 + pct_change / 100)
    return {
        "metric": {"id": "revenue", "label": "Revenue", "unit": "currency", "direction": "higher_is_good", "notes": None},
        "trend": [
            {"month": "2026-07", "is_partial": False, "value": baseline_mean},
            {"month": "2026-08", "is_partial": False, "value": latest_value},
        ],
        "anomaly": {
            "is_anomalous": False,
            "category": "opportunity",
            "verdict": "opportunity",
            "latest_month": "2026-08",
            "latest_value": latest_value,
            "baseline_mean": baseline_mean,
            "pct_change_vs_baseline": pct_change,
            "z_score": z_score,
            "moved_up": True,
        },
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0},
        "breakdowns": [],
        "notes": None,
    }


def test_confirm_exposes_the_already_computed_z_score(monkeypatch):
    # Regression: a large pct_change with a comparatively small z_score is
    # the exact honest signal that a volatile baseline (e.g. a business's
    # early ramp-up months) inflated the percentage -- this was computed by
    # detect_anomaly() all along but silently dropped before reaching a
    # case's confirm dict, so nothing downstream could ever show it.
    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: _canned_investigate_result(pct_change=84.85, z_score=1.19))
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient())

    result = inv.build_investigation("revenue")

    assert result["status"] == "completed"
    assert result["confirm"]["pct_change"] == 84.85
    assert result["confirm"]["z_score"] == 1.19


def test_confirm_z_score_is_none_when_baseline_has_no_variance(monkeypatch):
    # detect_anomaly() itself returns z_score=None when baseline_sd is 0
    # (fewer than 2 baseline months, or all identical) -- confirm must
    # pass that through honestly, not substitute a fabricated 0.
    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: _canned_investigate_result(pct_change=20.0, z_score=None))
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient())

    result = inv.build_investigation("revenue")

    assert result["confirm"]["z_score"] is None


# ---------------------------------------------------------------------------
# Step 7 validation: missing baseline, zero baseline, partial period, and
# insufficient history -- using the REAL intelligence.detect_anomaly()
# against a synthetic trend (a pure, DB-free function), not hand-typed
# anomaly dicts, so these exercise the actual guard logic, not an assumption
# of it.
# ---------------------------------------------------------------------------

from mcp_server.tools import intelligence  # noqa: E402


def _trend(*values, partial_last=True):
    months = [f"2026-{i + 2:02d}" for i in range(len(values))]
    return [
        {"month": m, "is_partial": partial_last and i == len(values) - 1, "value": v}
        for i, (m, v) in enumerate(zip(months, values))
    ]


def test_zero_baseline_produces_no_pct_change_and_no_z_score(monkeypatch):
    # Exactly one baseline month, and it's zero (this business's real Feb
    # 2026 launch-week revenue was Rs 19,365 -- close to this) -- baseline_
    # mean is 0 and baseline_sd is 0 (pstdev of a single value), so BOTH
    # the pct_change and z_score divisions are guarded, not inf/NaN.
    trend = _trend(0.0, 133_057_402.0, partial_last=False)
    anomaly = intelligence.detect_anomaly(trend, "higher_is_good")
    assert anomaly["pct_change_vs_baseline"] is None  # division by zero guarded, not inf/NaN
    assert anomaly["z_score"] is None  # pstdev of a single baseline value is 0

    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: {
        "metric": {"id": "revenue", "label": "Revenue", "unit": "currency", "direction": "higher_is_good", "notes": None},
        "trend": trend, "anomaly": anomaly,
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0}, "breakdowns": [], "notes": None,
    })
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient())
    result = inv.build_investigation("revenue")
    # With both pct_change and z_score unavailable, is_significant is False
    # (category="normal") -- correctly no finding at all rather than a case
    # built on a fabricated percentage. This is the honest behavior, not a
    # bug: a single zero-value baseline month genuinely can't support a
    # comparison.
    assert result["status"] == "no_finding"
    assert "confirm" not in result


def test_a_single_baseline_month_produces_no_z_score_but_a_real_pct_change(monkeypatch):
    # pstdev of a single value is 0 -- detect_anomaly's own z_score branch
    # divides by baseline_sd only `if baseline_sd else None`.
    trend = _trend(100_000_000.0, 185_000_000.0, partial_last=False)
    anomaly = intelligence.detect_anomaly(trend, "higher_is_good")
    assert anomaly["z_score"] is None
    assert anomaly["pct_change_vs_baseline"] == 85.0

    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: {
        "metric": {"id": "revenue", "label": "Revenue", "unit": "currency", "direction": "higher_is_good", "notes": None},
        "trend": trend, "anomaly": anomaly,
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0}, "breakdowns": [], "notes": None,
    })
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient())
    result = inv.build_investigation("revenue")
    assert result["confirm"]["z_score"] is None
    assert result["confirm"]["pct_change"] == 85.0


def test_insufficient_baseline_history_produces_no_finding_at_all(monkeypatch):
    # Fewer than 2 complete months -- detect_anomaly() short-circuits to
    # "insufficient_data", and build_investigation must not synthesize a
    # confirm/case for it (a case this thin shouldn't exist, not render
    # with fabricated zeros).
    trend = _trend(19_365.0, partial_last=False)  # one complete month only
    anomaly = intelligence.detect_anomaly(trend, "higher_is_good")
    assert anomaly["verdict"] == "insufficient_data"

    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: {
        "metric": {"id": "revenue", "label": "Revenue", "unit": "currency", "direction": "higher_is_good", "notes": None},
        "trend": trend, "anomaly": anomaly,
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0}, "breakdowns": [], "notes": None,
    })
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient())
    result = inv.build_investigation("revenue")
    assert result["status"] == "no_finding"
    assert "confirm" not in result


def test_partial_period_is_never_used_as_the_confirm_period(monkeypatch):
    # The most recent (partial) month must never become confirm.period --
    # detect_anomaly() only ever looks at complete months for "latest".
    trend = _trend(133_057_402.0, 142_664_348.0, 172_166_580.0, 197_328_256.0, 200_809_557.0, 101_970_628.0)
    assert trend[-1]["is_partial"] is True  # the synthetic September stand-in
    anomaly = intelligence.detect_anomaly(trend, "higher_is_good")
    assert anomaly["latest_month"] == trend[-2]["month"]  # the last COMPLETE month, not the partial one

    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: {
        "metric": {"id": "revenue", "label": "Revenue", "unit": "currency", "direction": "higher_is_good", "notes": None},
        "trend": trend, "anomaly": anomaly,
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0}, "breakdowns": [], "notes": None,
    })
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient())
    result = inv.build_investigation("revenue")
    assert result["confirm"]["period"] == trend[-2]["month"]
    assert result["confirm"]["current"] == trend[-2]["value"]
