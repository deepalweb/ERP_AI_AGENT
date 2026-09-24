import httpx
import pytest

from backend.app import main


def client(address="127.0.0.1", base_url="http://localhost"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, client=(address, 12345)),
        base_url=base_url,
    )


@pytest.fixture(autouse=True)
def local_access(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    monkeypatch.delenv("APP_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    async def schema():
        return {}
    monkeypatch.setattr(main.reporting, "fetch_schema", schema)


async def test_local_mode_allows_loopback():
    async with client() as api:
        assert (await api.get("/store-scope")).status_code == 200


@pytest.mark.parametrize("path", ["/store-scope", "/reporting/schema", "/docs", "/openapi.json"])
async def test_local_mode_blocks_remote_clients(path):
    async with client("192.0.2.10") as api:
        assert (await api.get(path)).status_code == 403
        assert (await api.get("/health")).status_code == 200


async def test_local_mode_rejects_untrusted_host():
    async with client(base_url="http://untrusted.example") as api:
        assert (await api.get("/store-scope")).status_code == 403


async def test_basic_mode_challenges_missing_or_wrong_credentials(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    monkeypatch.setenv("APP_AUTH_USERNAME", "manager")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-password-only")
    async with client("192.0.2.10") as api:
        missing = await api.get("/store-scope")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"].startswith("Basic")
        wrong = await api.get("/store-scope", auth=("manager", "wrong"))
        assert wrong.status_code == 401
        correct = await api.get("/store-scope", auth=("manager", "test-password-only"))
        assert correct.status_code == 200


@pytest.mark.parametrize("mode", ["basic", "typo"])
async def test_missing_configuration_fails_closed(monkeypatch, mode):
    monkeypatch.setenv("APP_AUTH_MODE", mode)
    async with client() as api:
        assert (await api.get("/store-scope")).status_code == 503


async def test_unauthenticated_mutation_never_reaches_service(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    monkeypatch.setenv("APP_AUTH_USERNAME", "manager")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-password-only")
    calls = []
    monkeypatch.setattr(main.finding_actions, "set_action", lambda *args: calls.append(args))
    async with client() as api:
        result = await api.patch(
            "/findings/test/action",
            json={"relevant": True, "status": "completed", "action_taken": "test"},
        )
    assert result.status_code == 401
    assert calls == []


@pytest.mark.parametrize("path", ["/cases/test-case/dismiss", "/cases/test-case/reopen"])
async def test_unauthenticated_case_mutation_never_reaches_service(monkeypatch, path):
    # Phase 2b's two case mutations get exactly the same protection as any
    # other write route -- the app-wide AccessControlMiddleware, not a new
    # per-route mechanism (this app has no per-user/role authorization to
    # hook into beyond the single shared operator login).
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    monkeypatch.setenv("APP_AUTH_USERNAME", "manager")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-password-only")
    calls = []
    monkeypatch.setattr(main.case_engine, "dismiss_case", lambda *args: calls.append(args))
    monkeypatch.setattr(main.case_engine, "reopen_case", lambda *args: calls.append(args))
    async with client() as api:
        result = await api.post(path, json={"reason": "test"})
    assert result.status_code == 401
    assert calls == []


async def test_unauthenticated_case_read_never_reaches_service(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    monkeypatch.setenv("APP_AUTH_USERNAME", "manager")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-password-only")
    calls = []
    monkeypatch.setattr(main.case_engine, "list_cases", lambda *a, **k: calls.append((a, k)))
    async with client() as api:
        result = await api.get("/cases")
    assert result.status_code == 401
    assert calls == []


async def test_cors_preflight_does_not_require_credentials(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    async with client() as api:
        result = await api.options("/store-scope", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        })
    assert result.status_code == 200
    assert result.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_auth_errors_include_cors_headers(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    monkeypatch.setenv("APP_AUTH_USERNAME", "manager")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "test-password-only")
    async with client() as api:
        result = await api.get("/store-scope", headers={"Origin": "http://localhost:5173"})
    assert result.status_code == 401
    assert result.headers["access-control-allow-origin"] == "http://localhost:5173"
