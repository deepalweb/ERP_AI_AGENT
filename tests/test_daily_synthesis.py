"""Tests for investigation.generate_daily_synthesis: the one LLM call in
this pipeline that reasons ACROSS findings instead of within one. Which
findings count as "connected" must be decided deterministically from each
investigation's own root_cause.checks (evidence_engine.
cross_metric_corroboration_checks' output) -- never left to the model -- so
these tests focus on that gating logic as much as the prompt content. Pure
unit tests; the OpenAI client is monkeypatched.
"""

from mcp_server.tools import investigation as inv


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, captured, content="A connected pattern."):
        self._captured = captured
        self._content = content

    def create(self, model, messages, temperature):
        self._captured["prompt"] = messages[0]["content"]
        self._captured["calls"] = self._captured.get("calls", 0) + 1
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, captured, content="A connected pattern."):
        self.completions = _FakeCompletions(captured, content)


class _FakeClient:
    def __init__(self, captured, content="A connected pattern."):
        self.chat = _FakeChat(captured, content)


def _investigation(metric_id, label, checks):
    return {
        "metric_id": metric_id,
        "title": f"{label} moved",
        "category": "critical",
        "domain": "financial",
        "root_cause": {"checks": checks},
    }


def _corroborated_check(detail):
    # Only verdict cross_metric_corroboration_checks ever actually emits
    # for this source after the independence/direction fix -- "confirmed"
    # is no longer a possible value here (see evidence_engine.py).
    return {"source": "cross_metric_corroboration", "verdict": "possible_contributor", "detail": detail}


def test_no_synthesis_call_when_nothing_is_corroborated(monkeypatch):
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))
    investigations = [
        _investigation("revenue", "Revenue", [{"source": "store_concentration", "verdict": "confirmed"}]),
        _investigation("order_volume", "Confirmed order volume", [{"source": "store_concentration", "verdict": "no_evidence_found"}]),
    ]
    result = inv.generate_daily_synthesis(investigations)
    assert result is None
    assert "calls" not in captured  # never even called the client


def test_no_synthesis_call_when_agreement_is_only_related_not_independent(monkeypatch):
    # A "related_metric_agreement" check (e.g. revenue and order_volume
    # agreeing, guaranteed by their shared formula) must never trigger a
    # synthesis narrative -- only genuine cross_metric_corroboration does.
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))
    dependent_check = {"source": "related_metric_agreement", "verdict": "related_not_independent", "detail": "moved with Revenue"}
    investigations = [
        _investigation("revenue", "Revenue", [dependent_check]),
        _investigation("order_volume", "Confirmed order volume", [dependent_check]),
    ]
    result = inv.generate_daily_synthesis(investigations)
    assert result is None
    assert "calls" not in captured


def test_no_synthesis_call_when_only_one_finding_is_corroborated(monkeypatch):
    # A finding can't be "connected" to itself -- needs 2+.
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))
    investigations = [
        _investigation("revenue", "Revenue", [_corroborated_check("Colombo 6 (also flagged by Confirmed order volume)")]),
        _investigation("order_volume", "Confirmed order volume", [{"source": "store_concentration", "verdict": "no_evidence_found"}]),
    ]
    result = inv.generate_daily_synthesis(investigations)
    assert result is None
    assert "calls" not in captured


def test_synthesis_runs_and_returns_deterministic_metric_ids_when_corroborated(monkeypatch):
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured, "Revenue and order volume share the same 3 outlets."))
    investigations = [
        _investigation("revenue", "Revenue", [_corroborated_check("Colombo 6 (also flagged by Confirmed order volume)")]),
        _investigation("order_volume", "Confirmed order volume", [_corroborated_check("Colombo 6 (also flagged by Revenue)")]),
        _investigation("cancellation_rate", "Order cancellation rate", [{"source": "store_concentration", "verdict": "unknown"}]),
    ]
    result = inv.generate_daily_synthesis(investigations)
    assert result is not None
    assert result["narrative"] == "Revenue and order volume share the same 3 outlets."
    # metric_ids comes from which investigations were ACTUALLY corroborated
    # (computed here, not asked of the model) -- the uncorroborated third
    # finding must not appear.
    assert sorted(result["metric_ids"]) == ["order_volume", "revenue"]
    assert captured["calls"] == 1


def test_prompt_only_includes_the_already_verified_corroboration_detail(monkeypatch):
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))
    investigations = [
        _investigation("revenue", "Revenue", [_corroborated_check("Colombo 6 (also flagged by Confirmed order volume)")]),
        _investigation("order_volume", "Confirmed order volume", [_corroborated_check("Colombo 6 (also flagged by Revenue)")]),
    ]
    inv.generate_daily_synthesis(investigations)
    assert "Colombo 6" in captured["prompt"]
    assert "ONLY basis you may use to connect them" in captured["prompt"]
    assert "Do not invent a mechanism" in captured["prompt"]


def test_synthesis_returns_none_on_empty_llm_response(monkeypatch):
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient({}, content="   "))
    investigations = [
        _investigation("revenue", "Revenue", [_corroborated_check("Colombo 6")]),
        _investigation("order_volume", "Confirmed order volume", [_corroborated_check("Colombo 6")]),
    ]
    assert inv.generate_daily_synthesis(investigations) is None


def test_synthesis_returns_none_on_client_error(monkeypatch):
    class _BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("network error")

    monkeypatch.setattr(inv, "_get_client", lambda: _BrokenClient())
    investigations = [
        _investigation("revenue", "Revenue", [_corroborated_check("Colombo 6")]),
        _investigation("order_volume", "Confirmed order volume", [_corroborated_check("Colombo 6")]),
    ]
    assert inv.generate_daily_synthesis(investigations) is None
