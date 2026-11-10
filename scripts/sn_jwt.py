"""Shared ServiceNow JWT bearer helpers for the PDI ops scripts.

Configuration comes from environment variables (CI) and falls back per field to
certs/sn-oauth-config.json + certs/sn-jwt-bearer.key (local, gitignored):

    SN_INSTANCE_URL      instance URL           (config "instance")
    SN_CLIENT_ID         OAuth JWT app client   (config "client_id")
    SN_KID               jwt_verifier_map kid   (config "kid")
    SN_SUB               non-admin user email   (config "test_email")
    SN_JWT_PRIVATE_KEY   RSA private key PEM    (certs/sn-jwt-bearer.key)

Nothing here prints or returns secrets, assertions or access tokens.
"""
import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

REPO = Path(__file__).resolve().parent.parent
CERT_DIR = REPO / "certs"
CONFIG_FILE = CERT_DIR / "sn-oauth-config.json"
KEY_FILE = CERT_DIR / "sn-jwt-bearer.key"
DEFAULT_INSTANCE = "https://dev330554.service-now.com"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"


@dataclass(frozen=True)
class SnConfig:
    instance: str
    client_id: str | None = None
    kid: str | None = None
    sub: str | None = None
    key_pem: bytes | None = None

    @property
    def has_jwt(self) -> bool:
        return all((self.client_id, self.kid, self.sub, self.key_pem))

    def __repr__(self):  # never leak the key through logs/tracebacks
        return f"SnConfig(instance={self.instance!r}, jwt={'yes' if self.has_jwt else 'no'})"


def _normalize_pem(value: str) -> bytes:
    value = value.strip()
    if "\n" not in value and "\\n" in value:
        value = value.replace("\\n", "\n")
    return (value + "\n").encode("ascii")


def load_config(env=None, config_file: Path = CONFIG_FILE, key_file: Path = KEY_FILE,
                instance_override: str | None = None, default_instance: str | None = None) -> SnConfig:
    """Env first, then certs/ files. Missing fields stay None (has_jwt=False)."""
    env = os.environ if env is None else env
    file_cfg = {}
    if config_file and Path(config_file).exists():
        file_cfg = json.loads(Path(config_file).read_text(encoding="utf-8"))

    def pick(env_name, file_key):
        return (env.get(env_name) or "").strip() or file_cfg.get(file_key) or None

    key_pem = None
    if (env.get("SN_JWT_PRIVATE_KEY") or "").strip():
        key_pem = _normalize_pem(env["SN_JWT_PRIVATE_KEY"])
    elif key_file and Path(key_file).exists():
        key_pem = Path(key_file).read_bytes()

    instance = instance_override or pick("SN_INSTANCE_URL", "instance") or default_instance or ""
    return SnConfig(instance=instance.rstrip("/"), client_id=pick("SN_CLIENT_ID", "client_id"),
                    kid=pick("SN_KID", "kid"), sub=pick("SN_SUB", "test_email"), key_pem=key_pem)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_assertion(key_pem: bytes, client_id: str, kid: str, sub: str, now: int | None = None) -> str:
    """RS256 JWT bearer assertion as ServiceNow expects it (aud = iss = client_id)."""
    key = serialization.load_pem_private_key(key_pem, password=None)
    now = int(time.time()) if now is None else now
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    payload = {"iss": client_id, "sub": sub, "aud": client_id,
               "iat": now, "exp": now + 300, "jti": str(uuid.uuid4())}
    parts = [b64url(json.dumps(header, separators=(",", ":")).encode()),
             b64url(json.dumps(payload, separators=(",", ":")).encode())]
    sig = key.sign(f"{parts[0]}.{parts[1]}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return ".".join(parts + [b64url(sig)])


def exchange_token(client: httpx.Client, cfg: SnConfig) -> httpx.Response:
    assertion = build_assertion(cfg.key_pem, cfg.client_id, cfg.kid, cfg.sub)
    return client.post(f"{cfg.instance}/oauth_token.do",
                       data={"grant_type": JWT_GRANT, "client_id": cfg.client_id, "assertion": assertion},
                       headers={"Content-Type": "application/x-www-form-urlencoded"})


def read_one_incident(client: httpx.Client, instance: str, access_token: str) -> httpx.Response:
    return client.get(f"{instance}/api/now/table/incident",
                      headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                      params={"sysparm_limit": "1", "sysparm_fields": "number"})
