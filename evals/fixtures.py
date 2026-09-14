"""Real-shaped evidence fixtures for evals/run.py — mirrors what
evidence_engine.py's build_trend_evidence / get_finding_memory actually
produced against the live database during this project's phased root-cause
and memory rollouts, not invented numbers. Breakdown contributor lists are
reconstructed to be internally consistent with those real verdicts (the
original raw contributor arrays weren't retained), same "representative,
not literally replayed" posture tests/test_evidence_engine.py already uses
for its synthetic breakdowns.

Each fixture supplies `root_cause` ({"checks": [...]} or None) and
`memory`, which evals/run.py assembles into the evidence_pkg
_generate_recommendations(metric, anomaly, breakdowns, impact, evidence_pkg)
expects.
"""

FIXTURES = [
    {
        "name": "order_volume_confirmed_store_concentration",
        "metric": {"id": "order_volume", "label": "Confirmed order volume", "unit": "count"},
        "anomaly": {
            "latest_month": "2026-08", "category": "opportunity",
            "latest_value": 31200, "baseline_mean": 19500, "pct_change_vs_baseline": 60.0,
        },
        "breakdowns": [
            {
                "dimension": "store", "label": "Store", "mode": "contribution", "baseline_month": "2026-07",
                "top_contributors": [
                    {"name": "Celeste Daily Colombo 6", "current": 9800, "baseline": 4200, "delta": 5600},
                    {"name": "Celeste Daily - Attidiya", "current": 6100, "baseline": 3400, "delta": 2700},
                    {"name": "Celeste Daily Ethul Kotte", "current": 4200, "baseline": 3600, "delta": 600},
                    {"name": "Celeste Daily Pannipitiya", "current": 3900, "baseline": 3600, "delta": 300},
                ],
            },
            {
                "dimension": "customer", "label": "Customer", "mode": "contribution", "baseline_month": "2026-07",
                "top_contributors": [
                    {"name": "Mohamed F.", "current": 42, "baseline": 20, "delta": 22},
                    {"name": "Dinusha K.", "current": 38, "baseline": 19, "delta": 19},
                ],
            },
        ],
        "impact": None,
        "root_cause": {
            "checks": [
                {
                    "source": "store_concentration", "verdict": "confirmed",
                    "detail": "60% of the increase came from 2 segments: Celeste Daily Colombo 6, "
                    "Celeste Daily - Attidiya.",
                    "segments": ["Celeste Daily Colombo 6", "Celeste Daily - Attidiya"], "concentration_pct": 60.1,
                },
                {
                    "source": "customer_concentration", "verdict": "no_evidence_found",
                    "detail": "Checked — no single customer or small group accounts for a majority of the "
                    "increase; the top segments cover only 1% of it.",
                },
            ]
        },
        "memory": None,
    },
    {
        "name": "revenue_memory_unchanged",
        # The exact real case verified live: July's revenue "opportunity" (+106%)
        # was flagged, and by the time August landed the deviation had reversed
        # to -88.6% (verdict: unchanged, i.e. the move did NOT hold). A model
        # that ignores memory treats this like a fresh, sustained gain; one that
        # uses it correctly should read this as a reversal, not an opportunity
        # to extend — see the live A/B check in this conversation for the real
        # recommendation-text difference this produced.
        "metric": {"id": "revenue", "label": "Revenue", "unit": "currency"},
        "anomaly": {"latest_month": "2026-08", "category": "opportunity"},
        "breakdowns": [],
        "impact": None,
        "root_cause": None,
        "memory": {
            "prior_period": "2026-07",
            "prior_recommendations": [
                {"action": "Analyze and optimize the sales strategy for CONVENIENCE FEE 605",
                 "owner": "Product team", "priority": "high"},
                {"action": "Investigate the increased spending of José P. and offer personalized promotions",
                 "owner": "Customer success team", "priority": "medium"},
                {"action": "Review and improve the operations at Celeste Daily Colombo 6 to sustain the "
                 "increased revenue", "owner": "Store operations team", "priority": "medium"},
            ],
            "outcome_verdict": "unchanged",
            "outcome_detail": "Deviation from baseline went from 106.43% to -88.6% by 2026-08.",
        },
    },
    {
        "name": "cancellation_rate_mixed_breakdown_modes",
        # store is "rate" mode and reason is "composition" mode -- both
        # correctly excluded from concentration math (see
        # evidence_engine.segment_concentration_checks) and so should never
        # be named as a cause; only product's "confirmed" concentration
        # should be.
        "metric": {"id": "cancellation_rate", "label": "Order cancellation rate", "unit": "percent"},
        "anomaly": {
            "latest_month": "2026-08", "category": "critical",
            "latest_value": 2.8, "baseline_mean": 3.5, "pct_change_vs_baseline": -20.0,
        },
        "breakdowns": [
            {
                "dimension": "store", "label": "Store", "mode": "rate", "baseline_month": "2026-07",
                "top_contributors": [{"name": "Ethul Kotte", "current": 2.98, "baseline": 4.3, "delta": -1.32}],
            },
            {
                "dimension": "reason", "label": "Cancellation reason", "mode": "composition",
                "baseline_month": "2026-07",
                "top_contributors": [{"name": "ITEM_ISSUE", "current": 967.0, "baseline": 653.0, "delta": 16.4}],
            },
            {
                "dimension": "product", "label": "Product", "mode": "contribution", "baseline_month": "2026-07",
                "top_contributors": [
                    {"name": "Araliya Bags (45 X 27.5 X 19) M - Celeste Daily - SKU 004714",
                     "current": 55, "baseline": 226, "delta": -171},
                    {"name": "Araliya Bags (39.5 X 25 X 15.5) S - Celeste Daily - SKU 004713",
                     "current": 46, "baseline": 189, "delta": -143},
                ],
            },
        ],
        "impact": None,
        "root_cause": {
            "checks": [
                {
                    "source": "store_concentration", "verdict": "unknown",
                    "detail": "This breakdown (store, rate mode) measures each segment's own rate rather "
                    "than a raw contribution to the total — not yet supported by this concentration check.",
                },
                {
                    "source": "reason_concentration", "verdict": "unknown",
                    "detail": "This breakdown (cancellation reason, composition mode) measures each "
                    "segment's share of the mix rather than a raw contribution to the total.",
                },
                {
                    "source": "product_concentration", "verdict": "confirmed",
                    "detail": "75% of the decline came from 2 segments: Araliya Bags (45 X 27.5 X 19) M - "
                    "Celeste Daily - SKU 004714, Araliya Bags (39.5 X 25 X 15.5) S - Celeste Daily - SKU 004713.",
                    "segments": ["Araliya Bags (45 X 27.5 X 19) M - Celeste Daily - SKU 004714",
                                 "Araliya Bags (39.5 X 25 X 15.5) S - Celeste Daily - SKU 004713"],
                    "concentration_pct": 74.9,
                },
            ]
        },
        "memory": None,
    },
]
