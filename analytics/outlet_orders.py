"""Offline seven-day order evaluation. No web requests, app writes, or ML imports.

Protocol is fixed before reading holdout results: four weekly validation origins,
then four untouched weekly holdouts; at least eight weeks of initial history.
Missing days are unknown, never silently filled with zero.
"""

from datetime import date, timedelta
from itertools import pairwise
from math import isfinite
from statistics import mean

PROTOCOL = {
    "horizon": 7,
    "validation_weeks": 4,
    "holdout_weeks": 4,
    "min_train_days": 56,
    "min_relative_improvement": 0.05,
    "max_outlet_regression": 0.05,
    "max_wape_pct": 25,
    "max_abs_bias_pct": 10,
    "min_interval_coverage_pct": 70,
    "max_interval_coverage_pct": 95,
    "max_source_age_days": 1,
}
MIN_HISTORY_DAYS = PROTOCOL["min_train_days"] + PROTOCOL["horizon"] * (
    PROTOCOL["validation_weeks"] + PROTOCOL["holdout_weeks"]
)


def prepare_series(rows, end_exclusive: date):
    """Drop first observed (potentially partial) day and unknown gaps.

    Retain only the final uninterrupted run per outlet. No claim that missing
    orders represent closure, stockout or ingestion failure can be made here.
    """
    grouped = {}
    for row in rows:
        day = date.fromisoformat(str(row["day"])[:10])
        if day < end_exclusive:
            grouped.setdefault(int(row["store_id"]), {})[day] = int(row["orders"])
    series, quality = {}, {}
    for outlet, values in sorted(grouped.items()):
        first = min(values) + timedelta(days=1)
        days = [first + timedelta(days=i) for i in range((end_exclusive - first).days)]
        missing = [d for d in days if d not in values]
        start = missing[-1] + timedelta(days=1) if missing else first
        kept = [d for d in days if d >= start]
        quality[str(outlet)] = {
            "first_observed": min(values).isoformat(),
            "unknown_days": [d.isoformat() for d in missing],
            "retained_days": len(kept),
            "eligible": len(kept) >= MIN_HISTORY_DAYS,
        }
        if len(kept) >= MIN_HISTORY_DAYS:
            series[str(outlet)] = {
                "dates": [d.isoformat() for d in kept],
                "values": [values[d] for d in kept],
            }
    return series, quality


def baseline(history, method, horizon=7):
    if len(history) < 28 or horizon != 7:
        raise ValueError("Baselines require 28 days and a seven-day horizon")
    if method == "seasonal_naive":
        return list(history[-7:])
    if method == "weekday_mean_4w":
        return [mean(history[-7 * k + h] for k in range(1, 5)) for h in range(7)]
    raise ValueError(f"Unknown method: {method}")


def score(rows):
    if not rows:
        raise ValueError("Cannot score empty evaluation")
    actual = sum(r["actual"] for r in rows)
    errors = [r["prediction"] - r["actual"] for r in rows]
    result = {
        "n": len(rows),
        "mae": mean(abs(e) for e in errors),
        "wape_pct": 100 * sum(abs(e) for e in errors) / actual if actual else None,
        "bias_pct": 100 * sum(errors) / actual if actual else None,
    }
    bands = [r for r in rows if "q10" in r]
    if bands:
        result["coverage_80_pct"] = 100 * mean(r["q10"] <= r["actual"] <= r["q90"] for r in bands)
        result["mean_interval_width"] = mean(r["q90"] - r["q10"] for r in bands)
        for q, key in ((0.1, "q10"), (0.9, "q90")):
            result[f"pinball_{key}"] = mean(
                max(q * (r["actual"] - r[key]), (q - 1) * (r["actual"] - r[key])) for r in bands
            )
    return result


def evaluate(series, model=None):
    """Predict before accessing targets; model gets only the origin's history."""
    rows = []
    ends = set()
    for outlet, data in series.items():
        values, dates = data["values"], data["dates"]
        if len(values) < MIN_HISTORY_DAYS:
            raise ValueError("Insufficient evaluation history")
        if len(values) != len(dates) or any(not isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid observed order history")
        parsed_dates = [date.fromisoformat(d) for d in dates]
        if any(b - a != timedelta(days=1) for a, b in pairwise(parsed_dates)):
            raise ValueError("Evaluation requires uninterrupted daily histories")
        ends.add(dates[-1])
        if len(ends) != 1:
            raise ValueError("Outlet histories must share one end date")
        for weeks_back in range(PROTOCOL["validation_weeks"] + PROTOCOL["holdout_weeks"], 0, -1):
            cutoff = len(values) - weeks_back * 7
            history = values[:cutoff]
            forecasts = {
                name: (baseline(history, name), None)
                for name in ("seasonal_naive", "weekday_mean_4w")
            }
            if model:
                forecasts["timesfm_2.5"] = model(history)
            for method, (points, quantiles) in forecasts.items():
                if len(points) != 7 or any(not isfinite(p) or p < 0 for p in points):
                    raise ValueError("Invalid point forecasts")
                for h, prediction in enumerate(points):
                    row = {
                        "outlet": outlet,
                        "method": method,
                        "split": "validation"
                        if weeks_back > PROTOCOL["holdout_weeks"]
                        else "holdout",
                        "origin": dates[cutoff - 1],
                        "target": dates[cutoff + h],
                        "horizon": h + 1,
                        "prediction": float(prediction),
                        "actual": values[cutoff + h],
                    }
                    if quantiles is not None:
                        low, high = map(float, quantiles[h])
                        if not isfinite(low + high) or not 0 <= low <= high:
                            raise ValueError("Invalid quantile forecast")
                        row.update(q10=low, q90=high)
                    rows.append(row)
    if not rows:
        raise ValueError("No eligible outlet histories")
    metrics = {}
    for split in ("validation", "holdout"):
        metrics[split] = {}
        for method in sorted({r["method"] for r in rows}):
            selected = [r for r in rows if r["split"] == split and r["method"] == method]
            metrics[split][method] = {
                "overall": score(selected),
                "outlets": {o: score([r for r in selected if r["outlet"] == o]) for o in series},
                "horizons": {
                    str(h): score([r for r in selected if r["horizon"] == h]) for h in range(1, 8)
                },
            }
    best_baseline = min(
        ("seasonal_naive", "weekday_mean_4w"),
        key=lambda m: metrics["validation"][m]["overall"]["mae"],
    )
    return {
        "protocol": PROTOCOL,
        "best_baseline": best_baseline,
        "metrics": metrics,
        "predictions": rows,
    }


def promotion_decision(evaluation, source_date, today, all_outlets_present=True):
    """Offline suitability only. Publishing also requires an operational job/store."""
    reasons = []
    baseline_name = evaluation["best_baseline"]
    validation = evaluation["metrics"]["validation"]
    holdout = evaluation["metrics"]["holdout"]
    chosen = baseline_name
    model = "timesfm_2.5"
    if model in validation:
        improved = all(
            evaluation["metrics"][s][model]["overall"]["mae"]
            <= evaluation["metrics"][s][baseline_name]["overall"]["mae"]
            * (1 - PROTOCOL["min_relative_improvement"])
            for s in ("validation", "holdout")
        )
        stable = all(
            v["mae"]
            <= holdout[baseline_name]["outlets"][o]["mae"] * (1 + PROTOCOL["max_outlet_regression"])
            for o, v in holdout[model]["outlets"].items()
        )
        coverage = holdout[model]["overall"].get("coverage_80_pct", 0)
        if (
            improved
            and stable
            and (
                PROTOCOL["min_interval_coverage_pct"]
                <= coverage
                <= PROTOCOL["max_interval_coverage_pct"]
            )
        ):
            chosen = model
    selected = holdout[chosen]
    if not all_outlets_present:
        reasons.append("At least one configured outlet lacks eligible history")
    for outlet, metric in selected["outlets"].items():
        if metric["wape_pct"] is None or metric["wape_pct"] > PROTOCOL["max_wape_pct"]:
            reasons.append(f"Outlet {outlet}: holdout WAPE exceeds 25% or is undefined")
        if metric["bias_pct"] is None or abs(metric["bias_pct"]) > PROTOCOL["max_abs_bias_pct"]:
            reasons.append(f"Outlet {outlet}: absolute holdout bias exceeds 10% or is undefined")
    if not 0 <= (today - source_date).days <= PROTOCOL["max_source_age_days"]:
        reasons.append("Reporting source is stale or future-dated")
    return {"candidate": chosen, "eligible_for_integration": not reasons, "reasons": reasons}
