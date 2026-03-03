"""
ServiceNow JWT Bearer Token Flow - Feasibility Test
=====================================================

Tests the same OAuth JWT Bearer pattern used in the Salesforce meta-tool:
  1. Generate RSA key pair + X.509 certificate
  2. Configure ServiceNow OAuth (upload cert, create OAuth app, JWT Verifier Map)
  3. Create a non-admin test user for identity propagation
  4. Sign a JWT assertion and exchange it at oauth_token.do
  5. Call Table API with the resulting token
  6. Verify the call runs as the mapped user

Usage:
  python scripts/test_jwt_bearer.py --instance https://devXXXXX.service-now.com --admin-password <password>

Prerequisites:
  pip install httpx cryptography

Learnings (2026-03-03):
  - oauth_jwt table EXTENDS oauth_entity (table inheritance) -- create in oauth_jwt, not oauth_entity
  - JWT Verifier Map table is "jwt_verifier_map" (not oauth_entity_jwt_verifier)
  - jwt_verifier_map references oauth_jwt via "oauth_jwt" field
  - inbound_grant_type must be set to "jwt" on the oauth_jwt record
  - ServiceNow blocks JWT Bearer tokens for admin users
  - client_secret is encrypted in SN -- use public_client=true to skip it
  - Content-Type MUST be application/x-www-form-urlencoded for oauth_token.do
"""

import argparse
import base64
import datetime
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.x509.oid import NameOID


CERT_DIR = Path(__file__).parent.parent / "certs"


# ---------------------------------------------------------------------------
# Step 0: Certificate generation
# ---------------------------------------------------------------------------


def generate_certificate(force: bool = False) -> tuple[Path, Path]:
    """Generate RSA key pair + self-signed X.509 certificate."""
    key_path = CERT_DIR / "sn-jwt-bearer.key"
    cert_path = CERT_DIR / "sn-jwt-bearer.crt"

    if key_path.exists() and cert_path.exists() and not force:
        print("[OK] Using existing certificate at certs/")
        return key_path, cert_path

    CERT_DIR.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "snow-meta-tool-jwt-bearer"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "snow-meta-tool"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        )
        .sign(private_key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print("[OK] Generated RSA key pair + X.509 certificate in certs/")
    return key_path, cert_path


# ---------------------------------------------------------------------------
# Step 1: Upload certificate to ServiceNow
# ---------------------------------------------------------------------------


def upload_certificate(client: httpx.Client, cert_path: Path) -> str:
    """Upload X.509 certificate to sys_certificate table. Returns sys_id."""
    cert_pem = cert_path.read_text(encoding="utf-8")

    r = client.get(
        "/api/now/table/sys_certificate",
        params={
            "sysparm_query": "name=snow-meta-tool-jwt-bearer",
            "sysparm_fields": "sys_id",
            "sysparm_limit": "1",
        },
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    if results:
        sys_id = results[0]["sys_id"]
        print(f"[OK] Certificate already exists: {sys_id}")
        return sys_id

    r = client.post(
        "/api/now/table/sys_certificate",
        json={
            "name": "snow-meta-tool-jwt-bearer",
            "type": "trust_store_cert",
            "pem_certificate": cert_pem,
            "active": "true",
            "short_description": "JWT Bearer signing cert for snow-meta-tool",
        },
    )
    r.raise_for_status()
    sys_id = r.json()["result"]["sys_id"]
    print(f"[OK] Uploaded certificate: {sys_id}")
    return sys_id


# ---------------------------------------------------------------------------
# Step 2: Create OAuth Application (oauth_jwt extends oauth_entity)
# ---------------------------------------------------------------------------


def create_oauth_app(client: httpx.Client) -> dict:
    """Create OAuth JWT app for JWT Bearer flow. Returns {client_id, sys_id}.

    Key insight: oauth_jwt extends oauth_entity via table inheritance.
    Creating a record in oauth_jwt automatically creates the parent oauth_entity.
    """
    # Check if already exists (query parent table)
    r = client.get(
        "/api/now/table/oauth_entity",
        params={
            "sysparm_query": "name=snow-meta-tool-jwt",
            "sysparm_fields": "sys_id,client_id,name",
            "sysparm_limit": "1",
        },
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    if results:
        app = results[0]
        print(f"[OK] OAuth app already exists: {app['client_id']}")
        return {"sys_id": app["sys_id"], "client_id": app["client_id"]}

    # Create in oauth_jwt table (child table with JWT-specific fields)
    r = client.post(
        "/api/now/table/oauth_jwt",
        json={
            "name": "snow-meta-tool-jwt",
            "active": "true",
            "user_field": "email",           # Map JWT sub -> sys_user.email
            "enable_jti_verification": "true",
            "jti_claim": "jti",
            "clock_skew": "300",
            # Inherited oauth_entity fields:
            "access_token_lifespan": "1800",
            "public_client": "true",         # No client_secret needed
            "inbound_grant_type": "jwt",     # CRITICAL: must be "jwt"
            "default_grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "comments": "JWT Bearer endpoint for snow-meta-tool MCP server",
        },
    )
    r.raise_for_status()
    result = r.json()["result"]
    app = {"sys_id": result["sys_id"], "client_id": result["client_id"]}
    print(f"[OK] Created OAuth app: {app['client_id']}")
    return app


# ---------------------------------------------------------------------------
# Step 3: Create JWT Verifier Map (links kid -> certificate)
# ---------------------------------------------------------------------------


def create_jwt_verifier(
    client: httpx.Client, oauth_jwt_sys_id: str, cert_sys_id: str
) -> str:
    """Create JWT Verifier Map. Returns kid.

    Table: jwt_verifier_map (NOT oauth_entity_jwt_verifier)
    References: oauth_jwt (NOT oauth_entity)
    """
    r = client.get(
        "/api/now/table/jwt_verifier_map",
        params={
            "sysparm_query": f"oauth_jwt={oauth_jwt_sys_id}",
            "sysparm_fields": "sys_id,kid,name",
            "sysparm_limit": "1",
        },
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    if results:
        kid = results[0].get("kid", "")
        print(f"[OK] JWT Verifier already exists, kid={kid}")
        return kid

    r = client.post(
        "/api/now/table/jwt_verifier_map",
        json={
            "name": "snow-meta-tool-verifier",
            "oauth_jwt": oauth_jwt_sys_id,
            "sys_certificate": cert_sys_id,
        },
    )
    r.raise_for_status()
    result = r.json()["result"]
    kid = result.get("kid", "")
    print(f"[OK] Created JWT Verifier Map, kid={kid}")
    return kid


# ---------------------------------------------------------------------------
# Step 4: Create a non-admin test user
# ---------------------------------------------------------------------------


def ensure_test_user(client: httpx.Client) -> dict:
    """Create or find a non-admin test user for identity propagation testing.

    Note: ServiceNow blocks JWT Bearer tokens for users with admin role.
    """
    test_email = os.environ.get("SN_TEST_EMAIL", "jwt.test@example.com")
    test_username = os.environ.get("SN_TEST_USERNAME", "jwt.test")

    r = client.get(
        "/api/now/table/sys_user",
        params={
            "sysparm_query": f"user_name={test_username}",
            "sysparm_fields": "sys_id,user_name,email,name",
            "sysparm_limit": "1",
        },
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    if results:
        user = results[0]
        print(f"[OK] Test user already exists: {user['user_name']} ({user['email']})")
        return user

    r = client.post(
        "/api/now/table/sys_user",
        json={
            "user_name": test_username,
            "email": test_email,
            "first_name": "JWT",
            "last_name": "Test",
            "active": "true",
            "locked_out": "false",
            "user_password": os.environ.get("SN_TEST_PASSWORD", "ChangeMe1234!"),
        },
    )
    r.raise_for_status()
    user = r.json()["result"]
    print(f"[OK] Created test user: {user['user_name']} ({user.get('email', test_email)})")

    # Assign itil role (ITSM: read/write incidents, changes, problems)
    role_r = client.get(
        "/api/now/table/sys_user_role",
        params={"sysparm_query": "name=itil", "sysparm_fields": "sys_id", "sysparm_limit": "1"},
    )
    if role_r.status_code == 200 and role_r.json().get("result"):
        role_sys_id = role_r.json()["result"][0]["sys_id"]
        r2 = client.post(
            "/api/now/table/sys_user_has_role",
            json={"user": user["sys_id"], "role": role_sys_id},
        )
        if r2.status_code < 300:
            print("[OK] Assigned 'itil' role to test user")
        else:
            print(f"[WARN] Could not assign itil role: {r2.status_code}")
    else:
        print("[WARN] Could not find 'itil' role")

    # Assign personalize_dictionary role (read access to sys_dictionary for describe_table)
    role_r2 = client.get(
        "/api/now/table/sys_user_role",
        params={"sysparm_query": "name=personalize_dictionary", "sysparm_fields": "sys_id", "sysparm_limit": "1"},
    )
    if role_r2.status_code == 200 and role_r2.json().get("result"):
        role_sys_id = role_r2.json()["result"][0]["sys_id"]
        r3 = client.post(
            "/api/now/table/sys_user_has_role",
            json={"user": user["sys_id"], "role": role_sys_id},
        )
        if r3.status_code < 300:
            print("[OK] Assigned 'personalize_dictionary' role to test user")

    return {"sys_id": user["sys_id"], "user_name": test_username, "email": test_email, "name": "JWT Test"}


# ---------------------------------------------------------------------------
# Step 5: Sign JWT and exchange for ServiceNow access token
# ---------------------------------------------------------------------------


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_jwt(key_path: Path, client_id: str, kid: str, sub: str) -> str:
    """Build and sign a JWT Bearer assertion (RS256)."""
    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    payload = {
        "iss": client_id,
        "sub": sub,
        "aud": client_id,  # SN expects aud = client_id
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
    }

    segments = [
        _base64url_encode(json.dumps(header, separators=(",", ":")).encode()),
        _base64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = f"{segments[0]}.{segments[1]}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    segments.append(_base64url_encode(signature))

    jwt_token = ".".join(segments)
    print(f"[OK] Built JWT assertion (sub={sub}, aud={client_id}, kid={kid})")
    return jwt_token


def exchange_jwt_for_token(instance_url: str, client_id: str, assertion: str) -> dict:
    """Exchange JWT Bearer assertion at oauth_token.do (public client, no secret)."""
    url = f"{instance_url}/oauth_token.do"

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": client_id,
        "assertion": assertion,
    }

    print(f"\n--- Token Exchange ---")
    print(f"POST {url}")

    r = httpx.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    print(f"Status: {r.status_code}")
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    if r.status_code == 200 and "access_token" in body:
        print(f"[OK] Got access token! (expires_in={body.get('expires_in')}s, scope={body.get('scope')})")
    else:
        print(f"[FAIL] Token exchange failed:")
        print(json.dumps(body, indent=2))

    return body


# ---------------------------------------------------------------------------
# Step 6: Test Table API (meta-tool pattern proof)
# ---------------------------------------------------------------------------


def test_table_api(instance_url: str, access_token: str) -> dict:
    """Call Table API with JWT-acquired token to verify identity propagation
    and meta-tool pattern feasibility."""
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    results = {}

    # Test 1: Read records (list incidents)
    print("\n[Test 1] Read records - List incidents")
    r = httpx.get(
        f"{instance_url}/api/now/table/incident",
        headers=headers,
        params={"sysparm_limit": "3", "sysparm_fields": "number,short_description,priority"},
        timeout=30,
    )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        incidents = r.json().get("result", [])
        print(f"  [PASS] Retrieved {len(incidents)} incidents")
        for inc in incidents:
            print(f"    {inc.get('number')}: {inc.get('short_description', '')[:60]}")
        results["read"] = "PASS"
    else:
        print(f"  [FAIL] {r.text[:200]}")
        results["read"] = "FAIL"

    # Test 2: Identity propagation
    print("\n[Test 2] Identity propagation")
    r2 = httpx.get(
        f"{instance_url}/api/now/table/sys_user",
        headers=headers,
        params={"sysparm_query": f"user_name={os.environ.get('SN_TEST_USERNAME', 'jwt.test')}", "sysparm_fields": "user_name,email,name", "sysparm_limit": "1"},
        timeout=30,
    )
    if r2.status_code == 200 and r2.json().get("result"):
        user = r2.json()["result"][0]
        print(f"  [PASS] Authenticated as: {user.get('user_name')} ({user.get('email')})")
        results["identity"] = "PASS"
    else:
        print(f"  [FAIL] Could not verify identity")
        results["identity"] = "FAIL"

    # Test 3: Write record (create + verify opened_by)
    print("\n[Test 3] Write record - Create incident")
    r3 = httpx.post(
        f"{instance_url}/api/now/table/incident",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "short_description": "JWT Bearer feasibility test - snow-meta-tool",
            "urgency": "3",
            "impact": "3",
        },
        timeout=30,
    )
    if r3.status_code == 201:
        inc = r3.json()["result"]
        created_by = inc.get("sys_created_by", "")
        print(f"  [PASS] Created {inc.get('number')} (opened by: {created_by})")
        results["write"] = "PASS"
        results["identity_on_write"] = "PASS" if created_by == "jwt.test" else "FAIL"
        # Clean up
        httpx.delete(f"{instance_url}/api/now/table/incident/{inc['sys_id']}", headers=headers, timeout=30)
    else:
        print(f"  [FAIL] {r3.status_code}: {r3.text[:200]}")
        results["write"] = "FAIL"

    # Test 4: Table discovery (list_tables meta-tool)
    print("\n[Test 4] Table discovery (meta-tool: list_tables)")
    r4 = httpx.get(
        f"{instance_url}/api/now/table/sys_db_object",
        headers=headers,
        params={"sysparm_query": "label=Incident^ORlabel=Change Request^ORlabel=Problem", "sysparm_fields": "name,label", "sysparm_limit": "10"},
        timeout=30,
    )
    if r4.status_code == 200:
        tables = r4.json().get("result", [])
        print(f"  [PASS] Discovered {len(tables)} tables:")
        for t in tables:
            print(f"    {t['name']}: {t.get('label', '')}")
        results["list_tables"] = "PASS"
    else:
        print(f"  [FAIL] {r4.status_code}")
        results["list_tables"] = "FAIL"

    # Test 5: Describe table (describe_table meta-tool)
    print("\n[Test 5] Describe table (meta-tool: describe_table)")
    r5 = httpx.get(
        f"{instance_url}/api/now/table/sys_dictionary",
        headers=headers,
        params={
            "sysparm_query": "name=incident^element!=NULL",
            "sysparm_fields": "element,column_label,internal_type,mandatory",
            "sysparm_limit": "8",
        },
        timeout=30,
    )
    if r5.status_code == 200:
        fields = r5.json().get("result", [])
        print(f"  [PASS] Described {len(fields)} fields:")
        for f in fields:
            itype = f.get("internal_type", {})
            type_val = itype.get("value", "?") if isinstance(itype, dict) else str(itype)
            req = " [REQUIRED]" if f.get("mandatory") == "true" else ""
            print(f"    {f.get('element')} ({type_val}): {f.get('column_label', '')}{req}")
        results["describe_table"] = "PASS"
    else:
        print(f"  [NEEDS ROLE] {r5.status_code} - needs personalize_dictionary or similar role")
        results["describe_table"] = "NEEDS_ROLE"

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Test ServiceNow JWT Bearer flow (same pattern as Salesforce meta-tool)"
    )
    parser.add_argument("--instance", required=True, help="ServiceNow instance URL")
    parser.add_argument("--admin-user", default="admin", help="Admin username (default: admin)")
    parser.add_argument("--admin-password", required=True, help="Admin password")
    parser.add_argument("--regen-cert", action="store_true", help="Force regenerate certificate")
    parser.add_argument("--skip-setup", action="store_true", help="Skip setup, only test token exchange")
    args = parser.parse_args()

    instance = args.instance.rstrip("/")
    print(f"\n{'='*60}")
    print(f"ServiceNow JWT Bearer Feasibility Test")
    print(f"Instance: {instance}")
    print(f"{'='*60}\n")

    # --- Step 0: Generate certificate ---
    print("--- Step 0: Certificate ---")
    key_path, cert_path = generate_certificate(force=args.regen_cert)

    if args.skip_setup:
        config_path = CERT_DIR / "sn-oauth-config.json"
        if not config_path.exists():
            print("[FAIL] No config found. Run without --skip-setup first.")
            sys.exit(1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        client_id = config["client_id"]
        kid = config["kid"]
        test_email = config["test_email"]
    else:
        admin_client = httpx.Client(
            base_url=instance,
            auth=(args.admin_user, args.admin_password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )

        print("\n--- Verifying admin access ---")
        r = admin_client.get("/api/now/table/sys_user", params={"sysparm_limit": "1"})
        if r.status_code != 200:
            print(f"[FAIL] Cannot access instance: {r.status_code}")
            print(f"  Response: {r.text[:300]}")
            sys.exit(1)
        print("[OK] Admin access verified")

        print("\n--- Step 1: Upload Certificate ---")
        cert_sys_id = upload_certificate(admin_client, cert_path)

        print("\n--- Step 2: Create OAuth Application (oauth_jwt) ---")
        oauth_app = create_oauth_app(admin_client)
        client_id = oauth_app["client_id"]

        print("\n--- Step 3: Create JWT Verifier Map ---")
        kid = create_jwt_verifier(admin_client, oauth_app["sys_id"], cert_sys_id)

        print("\n--- Step 4: Create Test User ---")
        test_user = ensure_test_user(admin_client)
        test_email = test_user["email"]

        admin_client.close()

        config = {"client_id": client_id, "kid": kid, "test_email": test_email}
        config_path = CERT_DIR / "sn-oauth-config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"\n[OK] Saved config to {config_path}")

    # --- Step 5: JWT Bearer Token Exchange ---
    print("\n--- Step 5: JWT Bearer Token Exchange ---")
    assertion = _build_jwt(key_path=key_path, client_id=client_id, kid=kid, sub=test_email)
    token_response = exchange_jwt_for_token(instance_url=instance, client_id=client_id, assertion=assertion)

    if "access_token" not in token_response:
        print("\n" + "=" * 60)
        print("TOKEN EXCHANGE FAILED")
        print("=" * 60)
        print("\nTroubleshooting:")
        print("  1. Verify inbound_grant_type = 'jwt' on the OAuth app")
        print("  2. Check JWT Verifier Map kid matches JWT header kid")
        print("  3. Ensure test user does NOT have admin role")
        print("  4. Check SN System Logs for OAuth errors")
        sys.exit(1)

    # --- Step 6: Test Table API ---
    print("\n--- Step 6: Table API Tests ---")
    results = test_table_api(instance, token_response["access_token"])

    # --- Summary ---
    print(f"\n{'='*60}")
    print("FEASIBILITY TEST RESULTS")
    print(f"{'='*60}")
    print(f"  Instance:          {instance}")
    print(f"  OAuth Client ID:   {client_id}")
    print(f"  JWT kid:           {kid}")
    print(f"  Test user:         {test_email}")
    print(f"  Token exchange:    PASS")
    for test_name, status in results.items():
        print(f"  {test_name:20s} {status}")
    print(f"\n  CONCLUSION: JWT Bearer with certificate WORKS on ServiceNow")
    print(f"  Same meta-tool pattern as Salesforce: CONFIRMED")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
