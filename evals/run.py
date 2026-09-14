"""Recommendation evals: run _generate_recommendations() against real
fixtures with a REAL LLM call (no mocking) and grade the output
deterministically (see graders.py).

Distinct from tests/test_root_cause_evidence.py: that suite mocks the LLM
client to test prompt CONSTRUCTION — fast, deterministic, safe to run on
every commit. This instead tests actual MODEL BEHAVIOR against a live API —
slower, costs a real call, and can vary run to run, so it's a separate,
run-on-demand suite rather than part of `pytest tests/`. Run this after any
change to the recommendation prompt or evidence shape, not on every commit.

Usage: python -m evals.run
"""

import sys

from evals.fixtures import FIXTURES
from evals.graders import check_no_fabricated_numbers, check_no_repeated_failed_recommendation, check_schema
from mcp_server.tools import investigation as inv


def run() -> int:
    total_violations = 0
    for fixture in FIXTURES:
        print(f"\n=== {fixture['name']} ===")
        root_cause = fixture.get("root_cause")
        evidence_pkg = {
            "checks": root_cause["checks"] if root_cause else [],
            "historical_context": fixture.get("historical_context"),
            "data_quality": fixture.get("data_quality"),
            "memory": fixture.get("memory"),
        }
        recommendations = inv._generate_recommendations(
            fixture["metric"],
            fixture["anomaly"],
            fixture["breakdowns"],
            fixture.get("impact"),
            evidence_pkg,
        )
        evidence = {
            "metric": fixture["metric"],
            "anomaly": fixture["anomaly"],
            "breakdowns": fixture["breakdowns"],
            "impact": fixture.get("impact"),
            "root_cause": root_cause,
            "memory": fixture.get("memory"),
        }

        violations = check_schema(recommendations)
        # The content-level graders assume a well-formed list — skip them
        # rather than crash on a malformed response the schema check already
        # caught (that's the schema violation's job to report, not a reason
        # to also throw a TypeError here).
        if not violations:
            violations += check_no_fabricated_numbers(evidence, recommendations)
            violations += check_no_repeated_failed_recommendation(evidence, recommendations)

        if violations:
            print(f"FAIL ({len(violations)} violation(s)):")
            for v in violations:
                print(f"  - {v}")
        else:
            print(f"PASS ({len(recommendations)} recommendations)")
        total_violations += len(violations)

    print(f"\n{'=' * 60}\n{len(FIXTURES)} fixtures, {total_violations} total violation(s)")
    return 0 if total_violations == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
