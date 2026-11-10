import base64
import json
import sys
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pdi_health  # noqa: E402
import sn_jwt  # noqa: E402

INSTANCE = "https://dev000001.service-now.com"
ACCESS_TOKEN = "sekret-access-token-value"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


@pytest.fixture
def cfg(key_pem):
    return sn_jwt.SnConfig(instance=INSTANCE, client_id="cid", kid="kid1", sub="jwt.test@example.com",
                           key_pem=key_pem)


def make_client(stats=None, token=None, table=None, seen=None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        path = request.url.path
        for prefix, spec in (("/stats.do", stats), ("/oauth_token.do", token), ("/api/now/table", table)):
            if path.startswith(prefix):
                if isinstance(spec, Exception):
                    raise spec
                if spec is None:
                    raise AssertionError(f"unexpected call to {path}")
                return spec
        raise AssertionError(f"unexpected path {path}")
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)


ADC_502 = httpx.Response(502, headers={"server": "snow_adc", "content-type": "text/html"},
                         text="<html><head><title>502 Bad Gateway</title></head><body><center>snow_adc</center></body></html>")
STATS_OK = httpx.Response(200, text="<html><body>Build name: Zurich</body></html>")
TOKEN_OK = httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 1799})
TABLE_OK = httpx.Response(200, json={"result": [{"number": "INC0010001"}]})


def test_up_full_check(cfg):
    seen = []
    r = pdi_health.classify(cfg, make_client(STATS_OK, TOKEN_OK, TABLE_OK, seen))
    assert r["state"] == "UP" and r["exit_code"] == 0 and r["auth_checked"] is True
    assert [q.url.path for q in seen] == ["/stats.do", "/oauth_token.do", "/api/now/table/incident"]
    assert all(q.method == "GET" for q in seen if q.url.path != "/oauth_token.do")


def test_snow_adc_502_is_hibernating(cfg):
    r = pdi_health.classify(cfg, make_client(ADC_502))
    assert r["state"] == "HIBERNATING_OR_OFFLINE"
    assert r["exit_code"] == 10 and r["http_status"] == 502 and r["detail"] == "snow_adc"
    assert "developer.servicenow.com" in r["next_action"]


@pytest.mark.parametrize("status", [503, 504])
def test_gateway_errors_are_offline(cfg, status):
    assert pdi_health.classify(cfg, make_client(httpx.Response(status)))["state"] == "HIBERNATING_OR_OFFLINE"


def test_redirect_to_developer_portal_is_hibernating(cfg):
    resp = httpx.Response(302, headers={"location": "https://developer.servicenow.com/dev.do#!/home?wu=true"})
    assert pdi_health.classify(cfg, make_client(resp))["state"] == "HIBERNATING_OR_OFFLINE"


def test_hibernation_page_is_hibernating(cfg):
    resp = httpx.Response(200, text="<html><body>Your instance is hibernating. Wake it up.</body></html>")
    assert pdi_health.classify(cfg, make_client(resp))["state"] == "HIBERNATING_OR_OFFLINE"


def test_login_redirect_counts_as_reachable(cfg):
    resp = httpx.Response(302, headers={"location": f"{INSTANCE}/login.do"})
    assert pdi_health.classify(cfg, make_client(resp, TOKEN_OK, TABLE_OK))["state"] == "UP"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_token_auth_failure_is_reclaimed(cfg, status):
    tok = httpx.Response(status, json={"error": "server_error", "error_description": "access_denied"})
    r = pdi_health.classify(cfg, make_client(STATS_OK, tok))
    assert r["state"] == "RECLAIMED_OR_MISCONFIGURED" and r["exit_code"] == 11
    assert r["stage"] == "token" and "access_denied" in r["detail"]
    assert "repair_sn_pdi.py" in r["next_action"] and "SnOboClientId" in r["next_action"]


def test_token_502_is_hibernating(cfg):
    r = pdi_health.classify(cfg, make_client(STATS_OK, ADC_502))
    assert r["state"] == "HIBERNATING_OR_OFFLINE" and r["stage"] == "token"


def test_table_denied_is_reclaimed(cfg):
    r = pdi_health.classify(cfg, make_client(STATS_OK, TOKEN_OK, httpx.Response(403)))
    assert r["state"] == "RECLAIMED_OR_MISCONFIGURED" and r["stage"] == "table"


@pytest.mark.parametrize("exc", [httpx.ConnectError("dns"), httpx.ReadTimeout("slow")])
def test_network_errors(cfg, exc):
    r = pdi_health.classify(cfg, make_client(exc))
    assert r["state"] == "NETWORK_ERROR" and r["exit_code"] == 12 and r["detail"] == type(exc).__name__


def test_network_error_during_token(cfg):
    r = pdi_health.classify(cfg, make_client(STATS_OK, httpx.ConnectTimeout("t")))
    assert r["state"] == "NETWORK_ERROR" and r["stage"] == "token"


def test_unexpected_status(cfg):
    assert pdi_health.classify(cfg, make_client(STATS_OK, httpx.Response(429)))["state"] == "UNEXPECTED"
    assert pdi_health.classify(cfg, make_client(STATS_OK, httpx.Response(200, json={})))["state"] == "UNEXPECTED"


def test_reachability_only_without_jwt_config():
    cfg = sn_jwt.SnConfig(instance=INSTANCE)
    r = pdi_health.classify(cfg, make_client(STATS_OK))
    assert r["state"] == "UP" and r["auth_checked"] is False


def test_not_configured():
    r = pdi_health.classify(sn_jwt.SnConfig(instance=""), make_client())
    assert r["state"] == "NOT_CONFIGURED" and r["exit_code"] == 20 and r["reachable"] is False


@pytest.mark.parametrize("stats,token,table,reachable", [
    (STATS_OK, TOKEN_OK, TABLE_OK, True),
    (STATS_OK, httpx.Response(401, json={"error": "access_denied"}), None, True),  # keep-alive must still run
    (STATS_OK, TOKEN_OK, httpx.Response(403), True),
    (ADC_502, None, None, False),
    (STATS_OK, ADC_502, None, False),
    (httpx.ConnectError("x"), None, None, False),
])
def test_reachable_flag_gates_keepalive(cfg, stats, token, table, reachable):
    assert pdi_health.classify(cfg, make_client(stats, token, table))["reachable"] is reachable


def test_output_never_contains_secrets(cfg, key_pem, capsys, tmp_path, monkeypatch):
    seen = []
    client = make_client(STATS_OK, TOKEN_OK, TABLE_OK, seen)
    monkeypatch.setattr(pdi_health.httpx, "Client", lambda **kw: client)
    monkeypatch.setattr(pdi_health.sn_jwt, "load_config", lambda **kw: cfg)
    out_file = tmp_path / "r.json"
    assert pdi_health.main(["--output", str(out_file)]) == 0
    printed = capsys.readouterr().out
    assertion = dict(httpx.QueryParams(seen[1].content.decode()))["assertion"]
    for secret in (ACCESS_TOKEN, assertion, key_pem.decode().splitlines()[1]):
        assert secret not in printed and secret not in out_file.read_text()
    assert json.loads(printed)["state"] == "UP" and len(printed.strip().splitlines()) == 1
    assert "jwt=yes" in repr(cfg) and "PRIVATE" not in repr(cfg)


def test_assertion_is_valid_rs256(key, key_pem):
    token = sn_jwt.build_assertion(key_pem, "cid", "kid1", "u@example.com", now=1_000)
    h, p, s = token.split(".")
    pad = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))  # noqa: E731
    assert json.loads(pad(h)) == {"alg": "RS256", "typ": "JWT", "kid": "kid1"}
    claims = json.loads(pad(p))
    assert claims["iss"] == claims["aud"] == "cid" and claims["sub"] == "u@example.com"
    assert claims["exp"] - claims["iat"] == 300
    key.public_key().verify(pad(s), f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_load_config_env_overrides_files(tmp_path, key_pem):
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"instance": "https://file.service-now.com/", "client_id": "file-cid",
                                    "kid": "file-kid", "test_email": "file@example.com"}))
    key_file = tmp_path / "k.key"
    key_file.write_bytes(b"file-key")
    escaped = key_pem.decode().strip().replace("\n", "\\n")
    env = {"SN_INSTANCE_URL": "https://env.service-now.com/", "SN_KID": "env-kid", "SN_JWT_PRIVATE_KEY": escaped}
    c = sn_jwt.load_config(env=env, config_file=cfg_file, key_file=key_file)
    assert (c.instance, c.client_id, c.kid, c.sub) == ("https://env.service-now.com", "file-cid", "env-kid",
                                                      "file@example.com")
    assert c.key_pem.strip() == key_pem.strip() and c.has_jwt


def test_load_config_files_only(tmp_path):
    c = sn_jwt.load_config(env={}, config_file=tmp_path / "missing.json", key_file=tmp_path / "missing.key")
    assert c.instance == "" and not c.has_jwt
