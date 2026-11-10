import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import repair_sn_pdi  # noqa: E402


def client(existing=None, roles=()):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/sys_user"):
            return httpx.Response(200, json={"result": [existing] if existing else []})
        if request.method == "GET" and path.endswith("/sys_user_has_role"):
            return httpx.Response(200, json={"result": [{"role.name": r} for r in roles]})
        if request.method == "POST" and path.endswith("/sys_user"):
            return httpx.Response(201, json={"result": {"sys_id": "new1", **json.loads(request.content)}})
        if request.method == "PATCH":
            return httpx.Response(200, json={"result": {"sys_id": path.rsplit("/", 1)[-1]}})
        raise AssertionError(f"unexpected {request.method} {path}")

    c = httpx.Client(base_url="https://x.service-now.com", transport=httpx.MockTransport(handler))
    return c, calls


def test_creates_user_with_hashed_password_and_prints_once(capsys):
    c, calls = client()
    repair_sn_pdi.ensure_keepalive_user(c)
    post = next(r for r in calls if r.method == "POST")
    body = json.loads(post.content)
    assert post.url.params["sysparm_input_display_value"] == "true"
    assert body["user_name"] == "pdi.keepalive" and body["password_needs_reset"] == "false"
    out = capsys.readouterr().out
    assert out.count(body["user_password"]) == 1
    assert "gh secret set SN_KEEPALIVE_PASSWORD" in out and "gh secret set SN_KEEPALIVE_USER" in out


def test_existing_user_is_idempotent_and_password_untouched(capsys):
    c, calls = client({"sys_id": "u1", "active": "true", "locked_out": "false", "password_needs_reset": "false"})
    repair_sn_pdi.ensure_keepalive_user(c)
    assert [r.method for r in calls] == ["GET", "GET"]
    assert "Password (shown once)" not in capsys.readouterr().out


def test_rotate_and_unlock_existing_user():
    c, calls = client({"sys_id": "u1", "active": "true", "locked_out": "true", "password_needs_reset": "true"})
    repair_sn_pdi.ensure_keepalive_user(c, rotate=True)
    patches = [json.loads(r.content) for r in calls if r.method == "PATCH"]
    assert "user_password" in patches[0]
    assert patches[1] == {"locked_out": "false", "password_needs_reset": "false"}


def test_refuses_privileged_keepalive_user():
    c, _ = client({"sys_id": "u1", "active": "true", "locked_out": "false", "password_needs_reset": "false"},
                  roles=("admin",))
    with pytest.raises(SystemExit, match="privileged role"):
        repair_sn_pdi.ensure_keepalive_user(c)
