"""Validate optional model enrichment before it enters a finding snapshot."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    action: str = Field(min_length=1, max_length=600)
    # Roles are descriptive suggestions, not authenticated assignee IDs.
    owner: str | None = Field(default=None, min_length=1, max_length=100)
    priority: Literal["high", "medium", "low"]
    expected_benefit: str = Field(min_length=1, max_length=600)
    evidence_refs: list[str] = Field(default_factory=list, max_length=10)


class Recommendations(list):
    def __init__(self, items=(), status="unavailable", rejected_count=0):
        super().__init__(items)
        self.generation_status = status
        self.rejected_count = rejected_count


def parse(content, allowed_refs=()):
    if not isinstance(content, str) or len(content) > 20_000:
        return Recommendations(status="rejected")
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    try:
        items = json.loads(content)
    except (TypeError, ValueError):
        return Recommendations(status="rejected")
    if not isinstance(items, list):
        return Recommendations(status="rejected")
    valid, rejected = [], 0
    for item in items:
        try:
            rec = Recommendation.model_validate(item)
            if not set(rec.evidence_refs).issubset(allowed_refs) or len(valid) >= 4:
                raise ValueError("Unknown evidence reference or recommendation limit exceeded")
            valid.append(rec.model_dump(exclude_unset=True))
        except (ValidationError, ValueError):
            rejected += 1
    return Recommendations(valid, "generated" if valid else "rejected", rejected)
