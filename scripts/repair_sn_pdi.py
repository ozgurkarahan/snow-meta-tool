"""Rebuild the JWT bearer setup on a (new or reset) ServiceNow PDI.

ServiceNow PDIs are reclaimed/reset after inactivity, which wipes the OAuth JWT
app, verifier, certificate and users -> APIM gets 401 access_denied from
oauth_token.do. This script restores that setup idempotently and verifies it.

ServiceNow-side only: it does NOT touch Azure. After it prints PASS for every
user, update APIM named values SnOboInstanceUrl / SnOboClientId / SnJwtBearerKid
and ca-sn-mcp SN_INSTANCE_URL with the printed values.

Usage (interactive; admin password is read with getpass, never argv/env/disk):
    python scripts/repair_sn_pdi.py

Zurich PDIs may reject REST basic auth for `admin` (HTTP 401 even with the right
password). In that case sign in to the instance UI and run the same Table API
calls from that session with the `X-UserToken: g_ck` header (done on 2026-10-31
for dev330554), then use `verify()` here for the JWT bearer check.
"""
import getpass
import json
import secrets
import shutil
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import test_jwt_bearer as tjb  # noqa: E402

DEFAULT_INSTANCE = "https://dev330554.service-now.com"
CERT_NAME = "snow-meta-tool-jwt-bearer"
APP_NAME = "snow-meta-tool-jwt"
REQUIRED_ROLES = ("itil", "personalize_dictionary")
USERS = [
    # (user_name, email, first_name, last_name)
    ("jwt.test", "jwt.test@example.com", "JWT", "Test"),
    ("ozgurkarahan", "ozgurkarahan@MngEnvMCAP549101.onmicrosoft.com", "Ozgur", "Karahan"),
]
APP_FIELDS = {
    "active": "true",
    "user_field": "email",
    "inbound_grant_type": "jwt",
    "public_client": "true",
    "enable_jti_verification": "true",
    "jti_claim": "jti",
    "clock_skew": "300",
}
SF_SCRIPTS = Path(r"C:\Users\ozgurkarahan\projects\salesforce-meta-tool-id-prop\scripts")


def fail(msg):
    sys.exit(f"[FAIL] {msg}")


def get(c, table, query, fields, limit=10):
    r = c.get(f"/api/now/table/{table}",
              params={"sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": str(limit)})
    if r.status_code != 200:
        fail(f"GET {table} HTTP {r.status_code}")
    return r.json().get("result", [])


def patch(c, table, sys_id, body):
    r = c.patch(f"/api/now/table/{table}/{sys_id}", json=body)
    if r.status_code != 200:
        fail(f"PATCH {table} HTTP {r.status_code}")
    return r.json()["result"]


def post(c, table, body):
    r = c.post(f"/api/now/table/{table}", json=body)
    if r.status_code not in (200, 201):
        fail(f"POST {table} HTTP {r.status_code} {r.text[:200]}")
    return r.json()["result"]


def norm_pem(p):
    return "".join(l.strip() for l in (p or "").splitlines() if l.strip() and "-----" not in l)


def ensure_cert(c):
    pem = (tjb.CERT_DIR / "sn-jwt-bearer.crt").read_text(encoding="utf-8")
    rows = get(c, "sys_certificate", f"name={CERT_NAME}", "sys_id,pem_certificate,active")
    if len(rows) > 1:
        fail(f"{len(rows)} certificates named {CERT_NAME}; resolve manually")
    if rows:
        row = rows[0]
        fix = {}
        if norm_pem(row.get("pem_certificate")) != norm_pem(pem):
            fix["pem_certificate"] = pem
        if row.get("active") != "true":
            fix["active"] = "true"
        if fix:
            patch(c, "sys_certificate", row["sys_id"], fix)
        print(f"[OK] Certificate {'repaired' if fix else 'verified'}: {row['sys_id']}")
        return row["sys_id"]
    row = post(c, "sys_certificate", {"name": CERT_NAME, "type": "trust_store_cert", "pem_certificate": pem,
                                      "active": "true", "short_description": "JWT bearer cert for snow-meta-tool"})
    print(f"[OK] Certificate created: {row['sys_id']}")
    return row["sys_id"]


def ensure_app(c):
    fields = "sys_id,client_id," + ",".join(APP_FIELDS)
    rows = get(c, "oauth_jwt", f"name={APP_NAME}", fields)
    if len(rows) > 1:
        fail(f"{len(rows)} oauth_jwt apps named {APP_NAME}; resolve manually")
    if rows:
        row = rows[0]
        fix = {k: v for k, v in APP_FIELDS.items() if str(row.get(k, "")).lower() != v}
        if fix:
            patch(c, "oauth_jwt", row["sys_id"], fix)
        print(f"[OK] OAuth JWT app {'repaired ' + ','.join(fix) if fix else 'verified'}: {row['client_id']}")
        return row
    if get(c, "oauth_entity", f"name={APP_NAME}", "sys_id"):
        fail(f"oauth_entity {APP_NAME} exists but is not an oauth_jwt app; resolve manually")
    row = post(c, "oauth_jwt", {"name": APP_NAME, **APP_FIELDS, "access_token_lifespan": "1800",
                                "default_grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                "comments": "JWT bearer for snow-meta-tool MCP server"})
    print(f"[OK] OAuth JWT app created: {row['client_id']}")
    return row


def ensure_verifier(c, app_sys_id, cert_sys_id):
    rows = get(c, "jwt_verifier_map", f"oauth_jwt={app_sys_id}", "sys_id,kid,sys_certificate")
    if len(rows) > 1:
        fail(f"{len(rows)} verifiers on {APP_NAME}; resolve manually")
    if rows:
        row = rows[0]
        cert_ref = row.get("sys_certificate")
        cert_ref = cert_ref.get("value") if isinstance(cert_ref, dict) else cert_ref
        if cert_ref != cert_sys_id:
            row = patch(c, "jwt_verifier_map", row["sys_id"], {"sys_certificate": cert_sys_id})
            print("[OK] Verifier certificate repaired")
    else:
        row = post(c, "jwt_verifier_map", {"name": "snow-meta-tool-verifier", "oauth_jwt": app_sys_id,
                                           "sys_certificate": cert_sys_id})
        print("[OK] Verifier created")
    kid = row.get("kid") or get(c, "jwt_verifier_map", f"sys_id={row['sys_id']}", "kid")[0].get("kid")
    if not kid:
        fail("Verifier has no kid")
    print(f"[OK] Verifier kid={kid}")
    return kid


def role_ids(c):
    ids = {}
    for name in REQUIRED_ROLES:
        rows = get(c, "sys_user_role", f"name={name}", "sys_id", 1)
        if not rows:
            fail(f"Role {name} not found")
        ids[name] = rows[0]["sys_id"]
    return ids


def ensure_user(c, roles, user_name, email, first, last):
    by_email = get(c, "sys_user", f"email={email}", "sys_id,user_name,email,active,locked_out")
    if len(by_email) > 1:
        fail(f"{len(by_email)} users share email {email}; JWT sub mapping would be ambiguous")
    if by_email:
        u = by_email[0]
    else:
        by_name = get(c, "sys_user", f"user_name={user_name}", "sys_id,email", 1)
        if by_name:
            fail(f"user_name {user_name} exists with a different email; resolve manually")
        u = post(c, "sys_user", {"user_name": user_name, "email": email, "first_name": first, "last_name": last,
                                 "active": "true", "locked_out": "false",
                                 "user_password": secrets.token_urlsafe(24) + "aA1!"})  # JWT-only; never stored
        print(f"[OK] User created: {user_name}")
    fix = {k: v for k, v in (("active", "true"), ("locked_out", "false")) if str(u.get(k, v)).lower() != v}
    if fix:
        patch(c, "sys_user", u["sys_id"], fix)
    held = {r["role.name"] for r in get(c, "sys_user_has_role", f"user={u['sys_id']}", "role.name", 200)}
    if "admin" in held:
        fail(f"{email} has admin role; ServiceNow blocks JWT bearer for admins")
    for name, rid in roles.items():
        if name not in held:
            post(c, "sys_user_has_role", {"user": u["sys_id"], "role": rid})
            print(f"[OK] Granted {name} to {email}")
    print(f"[OK] User verified: {email}")


def verify(instance, client_id, kid, email):
    assertion = tjb._build_jwt(key_path=tjb.CERT_DIR / "sn-jwt-bearer.key", client_id=client_id, kid=kid, sub=email)
    r = httpx.post(f"{instance}/oauth_token.do", timeout=30,
                   data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                         "client_id": client_id, "assertion": assertion})
    if r.status_code != 200:
        return f"FAIL token HTTP {r.status_code} {r.text[:150]}"
    r2 = httpx.get(f"{instance}/api/now/table/incident", timeout=30,
                   params={"sysparm_limit": "1", "sysparm_fields": "number"},
                   headers={"Authorization": "Bearer " + r.json()["access_token"], "Accept": "application/json"})
    return "PASS" if r2.status_code == 200 else f"FAIL table HTTP {r2.status_code}"


def main():
    instance = (input(f"ServiceNow instance URL [{DEFAULT_INSTANCE}]: ").strip() or DEFAULT_INSTANCE).rstrip("/")
    if not instance.startswith("https://") or not instance.endswith(".service-now.com"):
        fail("Instance must look like https://devNNNNNN.service-now.com")
    admin_user = input("Admin user name [admin]: ").strip() or "admin"
    pw = getpass.getpass(f"Password for {admin_user}@{instance} (hidden): ")

    with httpx.Client(base_url=instance, auth=(admin_user, pw), timeout=30,
                      headers={"Accept": "application/json", "Content-Type": "application/json"}) as c:
        r = c.get("/api/now/table/sys_user", params={"sysparm_limit": "1", "sysparm_fields": "sys_id"})
        if r.status_code != 200:
            fail(f"Admin access HTTP {r.status_code} (wrong password, or instance hibernating: wake it in the dev portal)")
        print("[OK] Admin access verified")
        cert_sys_id = ensure_cert(c)
        app = ensure_app(c)
        kid = ensure_verifier(c, app["sys_id"], cert_sys_id)
        roles = role_ids(c)
        for user in USERS:
            ensure_user(c, roles, *user)

    results = {u[1]: verify(instance, app["client_id"], kid, u[1]) for u in USERS}
    for email, res in results.items():
        print(f"  JWT bearer {email}: {res}")
    if any(v != "PASS" for v in results.values()):
        fail("JWT bearer verification failed; config file and Azure NOT updated")

    cfg_path = tjb.CERT_DIR / "sn-oauth-config.json"
    if cfg_path.exists():
        shutil.copy2(cfg_path, cfg_path.with_name(f"sn-oauth-config.{time.strftime('%Y%m%d-%H%M%S')}.bak.json"))
    cfg = {"client_id": app["client_id"], "kid": kid, "test_email": USERS[0][1], "instance": instance}
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"[OK] Saved {cfg_path}")

    if (input("Seed Customer 360 demo data (companies/incidents/problems/changes, idempotent)? [Y/n]: ")
            .strip().lower() in ("", "y", "yes")):
        sys.path.insert(0, str(SF_SCRIPTS))
        import seed_demo_data
        seed_demo_data.seed_servicenow(instance, admin_user, pw)
    del pw
    print("\nRESULT " + json.dumps({"instance": instance, "client_id": app["client_id"], "kid": kid,
                                    "jwt_bearer": results}))


if __name__ == "__main__":
    main()
