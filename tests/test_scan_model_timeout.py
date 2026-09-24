"""A stalled optional recommendation must not hold the scan for SDK retries."""
import httpx
import pytest
from openai import OpenAI

from mcp_server.tools import investigation


@pytest.fixture
def stalled_model(monkeypatch):
    requests = []
    clients = []
    monkeypatch.setenv("NVIDIA_API_KEY", "isolated-test-key")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://example.invalid/v1")

    def stall(request):
        requests.append(request)
        raise httpx.ReadTimeout("Simulated stalled provider", request=request)

    def client_factory(**kwargs):
        client = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(stall)))
        clients.append(client)
        return client

    monkeypatch.setattr(investigation, "OpenAI", client_factory)
    yield requests
    for client in clients:
        client.close()


def test_scan_model_has_short_read_timeout(stalled_model):
    result = investigation._generate_recommendations({"id": "revenue"}, {}, [], None)
    assert result == []  # keep the existing honest unavailable state
    assert stalled_model[0].extensions["timeout"]["read"] == 45.0


def test_stalled_optional_recommendation_is_not_retried(stalled_model):
    assert investigation._generate_recommendations({"id": "revenue"}, {}, [], None) == []
    assert len(stalled_model) == 1
