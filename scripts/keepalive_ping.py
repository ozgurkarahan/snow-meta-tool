"""Keepalive: hit the ServiceNow instance via JWT Bearer to reset its hibernation timer.

Reads instance + client_id + kid from certs/sn-oauth-config.json and uses the cert
already in certs/. Idempotent and safe to run repeatedly.

Usage:
    python scripts/keepalive_ping.py                  # uses dev434731 from config
    python scripts/keepalive_ping.py --instance ...   # override

Exit code 0 if the instance is awake and responding to authenticated API calls.
Exit code != 0 if hibernating, network error, or auth fails.
"""
import argparse
import base64
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


REPO = Path(__file__).parent.parent
CONFIG = REPO / "certs" / "sn-oauth-config.json"
KEY_FILE = REPO / "certs" / "sn-jwt-bearer.key"
DEFAULT_INSTANCE = "https://dev434731.service-now.com"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance", default=None, help="SN instance URL (defaults to dev434731)")
    parser.add_argument("--quiet", action="store_true", help="Only print summary line")
    args = parser.parse_args()

    if not CONFIG.exists():
        print(f"[FAIL] Missing {CONFIG}", file=sys.stderr)
        sys.exit(2)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    key = serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)

    instance = (args.instance or DEFAULT_INSTANCE).rstrip("/")
    client_id, kid, sub = cfg["client_id"], cfg["kid"], cfg["test_email"]

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    payload = {"iss": client_id, "sub": sub, "aud": client_id,
               "iat": now, "exp": now + 300, "jti": str(uuid.uuid4())}
    parts = [
        b64url(json.dumps(header, separators=(",", ":")).encode()),
        b64url(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    sig = key.sign(f"{parts[0]}.{parts[1]}".encode(), padding.PKCS1v15(), hashes.SHA256())
    parts.append(b64url(sig))
    assertion = ".".join(parts)

    t0 = time.time()
    r = httpx.post(
        f"{instance}/oauth_token.do",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "client_id": client_id,
            "assertion": assertion,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if r.status_code != 200:
        body = r.text[:200] if not args.quiet else ""
        print(f"[FAIL] Token exchange: HTTP {r.status_code} {body}")
        sys.exit(1)
    token = r.json()["access_token"]

    r2 = httpx.get(
        f"{instance}/api/now/table/incident",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"sysparm_limit": "1", "sysparm_fields": "number"},
        timeout=30,
    )
    elapsed = time.time() - t0
    if r2.status_code != 200:
        print(f"[FAIL] Table API: HTTP {r2.status_code}")
        sys.exit(1)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[OK] {ts} keepalive {instance} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
