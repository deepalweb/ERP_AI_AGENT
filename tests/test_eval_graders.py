"""Tests for evals/graders.py — the deterministic checks evals/run.py uses
to grade real LLM output. These graders are pure functions, so they're
tested the normal fast/deterministic way even though evals/run.py itself
(which calls the real LLM) is not part of this suite.
"""

from evals.graders import check_no_fabricated_numbers, check_no_repeated_failed_recommendation, check_schema


def _rec(action="Do the thing", owner="Ops team", priority="high", expected_benefit="Should help."):
    return {"action": action, "owner": owner, "priority": priority, "expected_benefit": expected_benefit}


# ---------------------------------------------------------------------------
# check_schema

def test_schema_passes_for_a_valid_list():
    assert check_schema([_rec(), _rec()]) == []


def test_schema_flags_wrong_count():
    assert any("2-4" in v for v in check_schema([_rec()]))


def test_schema_flags_missing_keys():
    bad = {"action": "Do it", "owner": "Ops"}  # missing priority, expected_benefit
    violations = check_schema([_rec(), bad])
    assert any("missing keys" in v for v in violations)


def test_schema_flags_invalid_priority():
    violations = check_schema([_rec(), _rec(priority="urgent")])
    assert any("invalid" in v for v in violations)


def test_schema_flags_empty_string_fields():
    violations = check_schema([_rec(), _rec(action="  ")])
    assert any("missing or empty" in v for v in violations)


# ---------------------------------------------------------------------------
# check_no_fabricated_numbers

def test_fabricated_numbers_passes_when_number_traces_to_evidence():
    evidence = {"impact": {"orders": 245, "estimated_revenue_impact": 18500.5}}
    recs = [_rec(expected_benefit="Should recover roughly 245 orders, about Rs. 18500.5.")]
    assert check_no_fabricated_numbers(evidence, recs) == []


def test_fabricated_numbers_flags_an_unsourced_precise_figure():
    evidence = {"impact": None, "breakdowns": []}
    recs = [_rec(expected_benefit="Should increase revenue by roughly 23.7%.")]
    violations = check_no_fabricated_numbers(evidence, recs)
    assert len(violations) == 1
    assert "23.7" in violations[0]


def test_fabricated_numbers_ignores_small_round_counts():
    # "the top 3 stores" describes the evidence shape, it isn't a fact FROM it.
    evidence = {"breakdowns": [{"dimension": "store", "top_contributors": [{"name": "A"}, {"name": "B"}]}]}
    recs = [_rec(action="Review the top 3 stores driving this.")]
    assert check_no_fabricated_numbers(evidence, recs) == []


def test_fabricated_numbers_accepts_a_sign_flipped_rephrasing():
    # Caught live: evidence had `delta: -1.32` (a rate DECLINE); the model
    # faithfully wrote "reduced by 1.32" -- no minus sign in prose, since
    # "reduced" already conveys direction. That's not a fabrication.
    evidence = {"breakdowns": [{"dimension": "store", "top_contributors": [{"name": "Ethul Kotte", "delta": -1.32}]}]}
    recs = [_rec(expected_benefit="Should reduce the rate by roughly 1.32 points, similar to Ethul Kotte.")]
    assert check_no_fabricated_numbers(evidence, recs) == []


def test_fabricated_numbers_checks_action_text_too_not_just_benefit():
    evidence = {"root_cause": {"checks": [{"concentration_pct": 60.1}]}}
    recs = [_rec(action="Focus on the segment responsible for 60.1% of the increase.")]
    assert check_no_fabricated_numbers(evidence, recs) == []
    recs_bad = [_rec(action="Focus on the segment responsible for 92.4% of the increase.")]
    assert len(check_no_fabricated_numbers(evidence, recs_bad)) == 1


# ---------------------------------------------------------------------------
# check_no_repeated_failed_recommendation

def test_repeated_failed_recommendation_is_flagged():
    evidence = {
        "memory": {
            "outcome_verdict": "unchanged",
            "prior_recommendations": [{"action": "Analyze and optimize the sales strategy for SKU 605"}],
        }
    }
    recs = [_rec(action="Analyze and optimize the sales strategy for SKU 605")]
    violations = check_no_repeated_failed_recommendation(evidence, recs)
    assert len(violations) == 1


def test_reworded_recommendation_is_not_flagged():
    evidence = {
        "memory": {
            "outcome_verdict": "unchanged",
            "prior_recommendations": [{"action": "Analyze and optimize the sales strategy for SKU 605"}],
        }
    }
    recs = [_rec(action="Investigate why the earlier revenue spike reversed at Colombo 6.")]
    assert check_no_repeated_failed_recommendation(evidence, recs) == []


def test_no_check_when_memory_is_absent():
    assert check_no_repeated_failed_recommendation({"memory": None}, [_rec()]) == []


def test_no_check_when_prior_outcome_improved():
    evidence = {
        "memory": {
            "outcome_verdict": "improved",
            "prior_recommendations": [{"action": "Repeat the promo at Colombo 6"}],
        }
    }
    recs = [_rec(action="Repeat the promo at Colombo 6")]
    # Reinforcing what already worked is explicitly allowed -- not a violation.
    assert check_no_repeated_failed_recommendation(evidence, recs) == []
