"""stream_agent_turn -- the streaming replacement for the old one-shot
run_agent_turn. Same MockTransport pattern test_scan_model_timeout.py
already uses to exercise the real openai SDK against a fabricated HTTP
response, rather than hand-mocking SDK-internal chunk objects (fragile --
their exact shape is the SDK's own implementation detail, not something
this codebase should re-derive by hand)."""

import json as json_module

import httpx
import pytest

from backend.app.services import agent


def _sse(chunks: list[dict]) -> bytes:
    body = "".join(f"data: {json_module.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


# Round 1: the model asks for get_sales_summary, streamed across 3 chunks --
# tool_calls fragments (id/name in the first, arguments in the second) must
# be reassembled by index before the JSON is valid.
ROUND_1_CHUNKS = [
    {"id": "1", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
        {"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_sales_summary", "arguments": ""}}
    ]}, "finish_reason": None}]},
    {"id": "1", "choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "{}"}}
    ]}, "finish_reason": None}]},
    {"id": "1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
]

# Round 2: the final answer, with a FOLLOW_UPS trailer that must be stripped
# from the reply and parsed into its own list.
ROUND_2_CHUNKS = [
    {"id": "2", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Sales were "}, "finish_reason": None}]},
    {"id": "2", "choices": [{"index": 0, "delta": {"content": "good.\n\nFOLLOW_UPS: What about returns? | Any outliers?"}, "finish_reason": None}]},
    {"id": "2", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
]


@pytest.fixture
def two_round_stream(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        chunks = ROUND_1_CHUNKS if calls["n"] == 1 else ROUND_2_CHUNKS
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(chunks)
        )

    monkeypatch.setenv("NVIDIA_API_KEY", "isolated-test-key")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr(
        agent,
        "_get_client",
        lambda: agent.AsyncOpenAI(
            api_key="isolated-test-key",
            base_url="https://example.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    monkeypatch.setattr(agent, "TOOL_FUNCTIONS", {"get_sales_summary": lambda: {"revenue": 123}})
    return calls


async def test_stream_agent_turn_executes_tool_then_streams_final_answer(two_round_stream):
    events = []
    async for event in agent.stream_agent_turn([{"role": "user", "content": "How were sales?"}]):
        events.append(event)

    types = [e["type"] for e in events]
    assert "tool_start" in types and "tool_result" in types
    assert types.index("tool_start") < types.index("tool_result")
    # Round 1 produced no visible content (tool call only); round 2's
    # "Sales were " + "good...." tokens must have streamed as separate events.
    tokens = [e["delta"] for e in events if e["type"] == "token"]
    assert tokens == ["Sales were ", "good.\n\nFOLLOW_UPS: What about returns? | Any outliers?"]

    done = events[-1]
    assert done["type"] == "done"
    # The FOLLOW_UPS trailer must be stripped from the reply and parsed out.
    assert done["reply"] == "Sales were good."
    assert done["follow_ups"] == ["What about returns?", "Any outliers?"]
    assert done["tool_calls"] == [{"name": "get_sales_summary", "arguments": {}, "result": {"revenue": 123}}]


async def test_stream_agent_turn_reports_unknown_tool_without_crashing(two_round_stream, monkeypatch):
    monkeypatch.setattr(agent, "TOOL_FUNCTIONS", {})  # get_sales_summary now unknown
    events = [e async for e in agent.stream_agent_turn([{"role": "user", "content": "How were sales?"}])]
    done = events[-1]
    assert done["type"] == "done"
    assert done["tool_calls"][0]["result"] == {"error": "Unknown tool 'get_sales_summary'"}
