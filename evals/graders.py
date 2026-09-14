"""Deterministic graders for _generate_recommendations() output — Priority 4
(Evals). No LLM-as-judge here: every check below enforces a rule the
recommendation prompt itself already states explicitly (see
mcp_server/tools/investigation.py's _generate_recommendations docstring/
prompt), so a violation is unambiguous, not a matter of a second model's
opinion. That keeps this suite fast, free, and reproducible — an
LLM-judge grader is a real option for softer questions (is this a GOOD
recommendation, not just a valid one) but is a separate, heavier tool
deliberately left for a later phase, not built here.

Each grader returns a list of violation strings (empty = pass).
"""

import re

REQUIRED_KEYS = {"action", "owner", "priority", "expected_benefit"}
VALID_PRIORITIES = {"high", "medium", "low"}


def check_schema(recommendations: list) -> list[str]:
    """Enforces the prompt's own required shape: a list of 2-4 objects with
    exactly {action, owner, priority, expected_benefit}, priority one of
    high/medium/low, and no empty strings standing in for real content."""
    if not isinstance(recommendations, list) or not recommendations:
        return ["recommendations must be a non-empty list"]

    violations = []
    if not (2 <= len(recommendations) <= 4):
        violations.append(f"expected 2-4 recommendations, got {len(recommendations)}")
    for i, rec in enumerate(recommendations):
        if not isinstance(rec, dict):
            violations.append(f"recommendation[{i}] is not an object")
            continue
        missing = REQUIRED_KEYS - rec.keys()
        if missing:
            violations.append(f"recommendation[{i}] missing keys: {sorted(missing)}")
        if rec.get("priority") not in VALID_PRIORITIES:
            violations.append(f"recommendation[{i}].priority is invalid: {rec.get('priority')!r}")
        for key in ("action", "owner", "expected_benefit"):
            if key in rec and (not isinstance(rec.get(key), str) or not rec[key].strip()):
                violations.append(f"recommendation[{i}].{key} is missing or empty")
    return violations


def _extract_evidence_numbers(evidence: dict) -> set[float]:
    """Magnitudes only (abs value) — a recommendation restating a `delta:
    -1.32` as 'reduced by 1.32' is a faithful rephrasing, not a fabricated
    number, and sign alone shouldn't fail this check. Caught live: the
    unsigned version is what actually distinguishes a real number from an
    invented one, which is the only thing this grader is meant to check."""
    numbers = set()

    def walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            numbers.add(round(abs(float(obj)), 1))
        elif isinstance(obj, str):
            for m in re.findall(r"-?\d[\d,]*\.?\d*", obj):
                try:
                    numbers.add(round(abs(float(m.replace(",", ""))), 1))
                except ValueError:
                    pass

    walk(evidence)
    return numbers


def check_no_fabricated_numbers(evidence: dict, recommendations: list) -> list[str]:
    """Every 'precise' number (has a decimal point, or is >=100 — the kind a
    real figure would be, not a small round count like 'the top 3 products')
    appearing in a recommendation's action/expected_benefit text must trace
    back to a number literally present somewhere in the evidence payload.
    Directly enforces the prompt's own rule: 'Never invent a number that
    isn't derivable from the evidence.'

    Heuristic, not exact NLP — small integers are excluded on purpose: a
    recommendation legitimately says things like 'the top 3 stores' as a
    description of the evidence, not as a fact FROM it, and flagging every
    such count as 'fabricated' would make this check noise, not signal."""
    evidence_numbers = _extract_evidence_numbers(evidence)
    violations = []
    for i, rec in enumerate(recommendations):
        if not isinstance(rec, dict):
            continue
        text = " ".join(str(rec.get(k, "")) for k in ("action", "expected_benefit"))
        for m in re.findall(r"-?\d[\d,]*\.?\d*", text):
            cleaned = m.replace(",", "")
            try:
                value = round(float(cleaned), 1)
            except ValueError:
                continue
            is_precise = "." in cleaned or abs(value) >= 100
            if is_precise and abs(value) not in evidence_numbers:
                violations.append(
                    f"recommendation[{i}] cites {m}, not found anywhere in the evidence: "
                    f"{rec.get('expected_benefit') or rec.get('action')!r}"
                )
    return violations


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def check_no_repeated_failed_recommendation(evidence: dict, recommendations: list) -> list[str]:
    """When memory shows the metric's deviation did NOT improve after a
    prior recommendation (verdict 'unchanged' or 'worsened'), a new
    recommendation that repeats that same prior action near-verbatim is
    exactly the failure mode Context+Memory exists to prevent (see
    evidence_engine.py's get_finding_memory). Near-verbatim, not fuzzy
    semantic similarity — a genuine rewording with a new angle is allowed
    (the prompt explicitly permits persisting with the same underlying fix
    if there's a reason); only a copy-pasted repeat of already-failed
    advice is flagged."""
    memory = evidence.get("memory")
    if not memory or memory.get("outcome_verdict") not in {"unchanged", "worsened"}:
        return []
    failed_actions = {_normalize(r.get("action", "")) for r in memory.get("prior_recommendations", [])}
    violations = []
    for i, rec in enumerate(recommendations):
        if not isinstance(rec, dict):
            continue
        if _normalize(rec.get("action", "")) in failed_actions:
            violations.append(
                f"recommendation[{i}] repeats a prior action already verdict="
                f"{memory['outcome_verdict']!r}: {rec.get('action')!r}"
            )
    return violations


GRADERS = [check_no_fabricated_numbers, check_no_repeated_failed_recommendation]
