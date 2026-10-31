import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "src" / "servicenow-mcp"))
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("SN_INSTANCE_URL", "https://servicenow.example")
    spec = importlib.util.spec_from_file_location("servicenow_test_app", ROOT / "src" / "servicenow-mcp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    asyncio.run(module.sn.close())
    sys.modules.pop(spec.name, None)


def test_identity_lookup_is_get_only_and_uses_current_request_token(server, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "result": [{"sys_id": "test-id", "user_name": "test-user", "email": "test@example.com"}]
        })

    async def exercise():
        await server.sn._client.aclose()
        server.sn._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        authenticate = AsyncMock(side_effect=AssertionError("Identity lookup must not use local credentials"))
        monkeypatch.setattr(server.sn, "_authenticate", authenticate)
        token = server._request_token.set("current-user-token")
        email = server._request_user_email.set("test@example.com")
        try:
            result = json.loads(await server.query(
                table="sys_user", query="email=test@example.com",
                fields="sys_id,user_name,email", limit=1,
            ))
        finally:
            server._request_user_email.reset(email)
            server._request_token.reset(token)
        assert result["returned"] == 1
        assert authenticate.await_count == 0

    asyncio.run(exercise())
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/now/table/sys_user"
    assert requests[0].headers["authorization"] == "Bearer current-user-token"
    assert requests[0].url.params["sysparm_query"] == "email=test@example.com"
    assert requests[0].url.params["sysparm_fields"] == "sys_id,user_name,email"


@pytest.mark.parametrize("status", [401, 403])
def test_read_denial_does_not_refresh_or_switch_identity(server, monkeypatch, status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "Unauthorized", "detail": "Consent or access required"}})

    async def exercise():
        await server.sn._client.aclose()
        server.sn._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        authenticate = AsyncMock(side_effect=AssertionError("Do not switch to local credentials"))
        monkeypatch.setattr(server.sn, "_authenticate", authenticate)
        token = server._request_token.set("denied-user-token")
        try:
            result = json.loads(await server.query(table="sys_user", fields="sys_id,email", limit=1))
        finally:
            server._request_token.reset(token)
        assert result["success"] is False
        assert result["httpStatus"] == status
        assert authenticate.await_count == 0

    asyncio.run(exercise())
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].headers["authorization"] == "Bearer denied-user-token"


def test_concurrent_identity_reads_keep_each_users_token(server, monkeypatch):
    requests = []

    async def respond(request):
        await asyncio.sleep(0)
        requests.append(request)
        return httpx.Response(200, json={"result": [{"sys_id": "test-id"}]})

    async def exercise():
        await server.sn._client.aclose()
        server.sn._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        authenticate = AsyncMock(side_effect=AssertionError("Do not use local credentials"))
        monkeypatch.setattr(server.sn, "_authenticate", authenticate)

        async def read_as(user):
            token = server._request_token.set(f"{user}-token")
            email = server._request_user_email.set(f"{user}@example.com")
            try:
                await server.query(
                    table="sys_user", query=f"email={user}@example.com", fields="sys_id,email", limit=1,
                )
            finally:
                server._request_user_email.reset(email)
                server._request_token.reset(token)

        await asyncio.gather(read_as("first-user"), read_as("second-user"))
        assert authenticate.await_count == 0

    asyncio.run(exercise())
    assert len(requests) == 2
    for request in requests:
        user = request.url.params["sysparm_query"].split("=", 1)[1].split("@", 1)[0]
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {user}-token"


def test_middleware_resets_identity_after_success_and_failure(server):
    async def exercise():
        previous_token = server._request_token.set("outer-token")
        previous_email = server._request_user_email.set("outer@example.com")
        middleware = server.BearerTokenMiddleware(None)
        request = Request({
            "type": "http", "method": "GET", "path": "/mcp", "query_string": b"",
            "headers": [(b"authorization", b"Bearer inner-token"), (b"x-user-email", b"inner@example.com")],
        })

        async def next_handler(_):
            assert server._request_token.get() == "inner-token"
            assert server._request_user_email.get() == "inner@example.com"
            return JSONResponse({"ok": True})

        async def failing_handler(_):
            raise RuntimeError("test failure")

        try:
            await middleware.dispatch(request, next_handler)
            assert server._request_token.get() == "outer-token"
            assert server._request_user_email.get() == "outer@example.com"
            with pytest.raises(RuntimeError, match="test failure"):
                await middleware.dispatch(request, failing_handler)
            assert server._request_token.get() == "outer-token"
            assert server._request_user_email.get() == "outer@example.com"
        finally:
            server._request_user_email.reset(previous_email)
            server._request_token.reset(previous_token)

    asyncio.run(exercise())
