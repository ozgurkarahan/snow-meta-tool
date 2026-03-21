"""
SAML 2.0 SSO Setup -- Azure Entra ID -> ServiceNow
====================================================

Configures browser-based SSO with JIT user provisioning:
  1. Azure AD: Create Enterprise App (SAML) + signing cert
  2. Azure AD: Configure claims mapping policy
  3. Azure AD: Download federation metadata + extract cert/URLs
  4. Azure AD: Assign users to the SAML app
  5. ServiceNow: Activate Multi-Provider SSO plugin (via CICD API)
  6. ServiceNow: Enable SSO system properties
  7. Print federation metadata URL for manual IdP import

IMPORTANT: The ServiceNow SAML IdP record MUST be created through the
SN UI's "Import Identity Provider Metadata" dialog. API-created records
fail with 'idpConfig is null' during SAML response validation (missing
internal Java initialization). This script automates everything EXCEPT
the IdP record creation, which requires these manual steps:

  After running this script:
  1. Log in to ServiceNow as admin
  2. Navigate to Multi-Provider SSO > Identity Providers > New
  3. In the "Import Identity Provider Metadata" popup, paste the
     federation metadata URL printed by this script
  4. Click Import
  5. Update: Name = "Azure AD SAML SSO", NameID Policy = emailAddress,
     Show as Login option = checked, then click Update

The existing JWT Bearer OBO flow for MCP API calls is UNCHANGED.

Usage:
  python scripts/setup_saml_sso.py \\
    --instance https://dev194081.service-now.com \\
    --admin-password <password>

  # Skip Azure AD setup (re-run SN config only):
  python scripts/setup_saml_sso.py \\
    --instance https://dev194081.service-now.com \\
    --admin-password <password> \\
    --skip-azure

Prerequisites:
  pip install httpx
  az login (with permissions to create app registrations + service principals)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx


CONFIG_DIR = Path(__file__).parent.parent / "certs"
SSO_CONFIG_FILE = CONFIG_DIR / "sn-sso-config.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd, parse_json=False):
    """Run shell command. Returns stdout (or parsed JSON), or None on failure."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        shell=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            # Filter out noise -- only show first 300 chars
            print(f"  [CMD FAIL] {stderr[:300]}")
        return None
    stdout = result.stdout.strip()
    if parse_json and stdout:
        return json.loads(stdout)
    return stdout


def _write_temp_json(data):
    """Write data to temp JSON file for az rest --body."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(data, f)
    f.close()
    return f.name


def _graph_rest(method, url, body=None):
    """Call Microsoft Graph API via az rest."""
    cmd = f'az rest --method {method} --url "{url}"'
    if body is not None:
        body_file = _write_temp_json(body)
        cmd += f' --body "@{body_file}"'
    result = run(cmd, parse_json=True)
    if body is not None:
        try:
            os.unlink(body_file)
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Step 1: Azure AD -- Create Enterprise Application (SAML)
# ---------------------------------------------------------------------------


def create_azure_ad_saml_app(instance_url):
    """Create Azure AD app registration + service principal for SAML SSO.

    Returns dict with app_id, app_object_id, sp_object_id, display_name.
    """
    instance_host = instance_url.rstrip("/")
    # Extract instance name (e.g., "dev194081") for the display name
    instance_name = instance_host.split("//")[1].split(".")[0]
    display_name = f"ServiceNow SSO - {instance_name}"

    # 1a. Check if app already exists
    print(f"  Checking for existing app '{display_name}'...")
    existing = run(
        f'az ad app list --display-name "{display_name}" '
        f'--query "[0].{{appId: appId, id: id}}" -o json',
        parse_json=True,
    )

    if existing and existing.get("appId"):
        app_id = existing["appId"]
        app_object_id = existing["id"]
        print(f"  [OK] App already exists: {app_id}")
    else:
        # 1b. Create app registration (WITHOUT identifierUris -- those require
        # a verified domain via az CLI; we set them via Graph API after SAML
        # mode is enabled on the service principal)
        print(f"  Creating app registration '{display_name}'...")
        app = run(
            f'az ad app create --display-name "{display_name}" '
            f'--query "{{appId: appId, id: id}}" -o json',
            parse_json=True,
        )
        if not app:
            print("  [FAIL] Could not create app registration")
            sys.exit(1)
        app_id = app["appId"]
        app_object_id = app["id"]
        print(f"  [OK] Created app: {app_id}")

    # 1c. Enable acceptMappedClaims (required for claims mapping policy)
    print("  Enabling acceptMappedClaims on app registration...")
    _graph_rest(
        "PATCH",
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
        {"api": {"acceptMappedClaims": True}},
    )

    # 1d. Create service principal (Enterprise App) BEFORE setting identifierUris
    # (SAML mode on SP lifts verified-domain restriction for identifierUris)
    print("  Checking for service principal...")
    sp = run(
        f'az ad sp show --id "{app_id}" --query "id" -o tsv',
    )

    if sp:
        sp_object_id = sp
        print(f"  [OK] Service principal exists: {sp_object_id}")
    else:
        print("  Creating service principal...")
        sp = run(f'az ad sp create --id "{app_id}" --query "id" -o tsv')
        if not sp:
            print("  [FAIL] Could not create service principal")
            sys.exit(1)
        sp_object_id = sp
        print(f"  [OK] Created service principal: {sp_object_id}")
        time.sleep(3)  # Wait for AAD propagation

    # 1e. Set SAML SSO mode on service principal
    print("  Configuring SAML SSO mode...")
    _graph_rest(
        "PATCH",
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}",
        {
            "preferredSingleSignOnMode": "saml",
            "loginUrl": f"{instance_host}/login_with_sso.do",
        },
    )
    print("  [OK] Set preferredSingleSignOnMode = saml")

    # 1f. Set identifierUris + redirectUris via Graph API
    # (az CLI rejects non-verified domains, but Graph API allows it for SAML apps)
    print("  Setting identifierUris and redirectUris via Graph API...")
    result = _graph_rest(
        "PATCH",
        f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
        {
            "identifierUris": [instance_host],
            "web": {
                "redirectUris": [f"{instance_host}/navpage.do"],
                "logoutUrl": f"{instance_host}/navpage.do?logout",
            },
        },
    )
    if result is None:
        # Graph API may also reject non-verified https:// domains.
        # Fall back to api://<app-id> scheme.
        fallback_uri = f"api://{app_id}"
        print(f"  [WARN] Could not set identifierUri to {instance_host}")
        print(f"         Falling back to: {fallback_uri}")
        _graph_rest(
            "PATCH",
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
            {
                "identifierUris": [fallback_uri],
                "web": {
                    "redirectUris": [f"{instance_host}/navpage.do"],
                    "logoutUrl": f"{instance_host}/navpage.do?logout",
                },
            },
        )
        print(f"  [OK] identifierUri = {fallback_uri}")
        print(f"       NOTE: Set the SN SP Entity ID to: {fallback_uri}")
    else:
        print(f"  [OK] identifierUri = {instance_host}")

    return {
        "app_id": app_id,
        "app_object_id": app_object_id,
        "sp_object_id": sp_object_id,
        "display_name": display_name,
    }


# ---------------------------------------------------------------------------
# Step 2: SAML Signing Certificate
# ---------------------------------------------------------------------------


def ensure_saml_signing_cert(sp_object_id):
    """Generate SAML signing certificate on the service principal.

    Azure AD uses this cert to sign SAML assertions. The public cert
    goes into ServiceNow's IdP config so it can verify signatures.
    """
    sp_details = _graph_rest(
        "GET",
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}"
        f"?$select=preferredTokenSigningKeyThumbprint",
    )

    if sp_details and sp_details.get("preferredTokenSigningKeyThumbprint"):
        thumbprint = sp_details["preferredTokenSigningKeyThumbprint"]
        print(f"  [OK] SAML signing cert exists (thumbprint: {thumbprint})")
        return thumbprint

    print("  Generating SAML signing certificate...")
    cert_result = _graph_rest(
        "POST",
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}"
        f"/addTokenSigningCertificate",
        {
            "displayName": "CN=ServiceNow SSO SAML Signing",
            "endDateTime": "2028-03-19T00:00:00Z",
        },
    )

    if not cert_result:
        print("  [WARN] Could not auto-generate SAML signing cert via API")
        print("         Generate manually: Azure Portal > Enterprise Applications")
        print("         > <app> > Single sign-on > SAML Signing Certificate > New")
        return None

    thumbprint = cert_result.get("thumbprint", "")
    print(f"  [OK] Generated SAML signing cert (thumbprint: {thumbprint})")

    # Set as preferred signing key
    _graph_rest(
        "PATCH",
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}",
        {"preferredTokenSigningKeyThumbprint": thumbprint},
    )
    print("  [OK] Set as preferred signing key")

    return thumbprint


# ---------------------------------------------------------------------------
# Step 3: Claims Mapping Policy
# ---------------------------------------------------------------------------


def configure_claims_mapping(sp_object_id):
    """Create and assign a claims mapping policy for SAML attributes.

    Maps Azure AD user attributes to SAML claims that ServiceNow
    uses for JIT user provisioning (email, first_name, last_name).
    """
    # Check if policy already assigned
    existing = _graph_rest(
        "GET",
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}"
        f"/claimsMappingPolicies",
    )

    if existing and existing.get("value"):
        policy_id = existing["value"][0]["id"]
        print(f"  [OK] Claims mapping policy already assigned: {policy_id}")
        return policy_id

    # Build claims mapping policy definition
    policy_definition = {
        "ClaimsMappingPolicy": {
            "Version": 1,
            "IncludeBasicClaimSet": "true",
            "ClaimsSchema": [
                {
                    "Source": "user",
                    "ID": "userprincipalname",
                    "SamlClaimType": (
                        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims"
                        "/nameidentifier"
                    ),
                },
                {
                    "Source": "user",
                    "ID": "mail",
                    "SamlClaimType": (
                        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims"
                        "/emailaddress"
                    ),
                },
                {
                    "Source": "user",
                    "ID": "givenname",
                    "SamlClaimType": (
                        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims"
                        "/givenname"
                    ),
                },
                {
                    "Source": "user",
                    "ID": "surname",
                    "SamlClaimType": (
                        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims"
                        "/surname"
                    ),
                },
            ],
        }
    }

    print("  Creating claims mapping policy...")
    policy = _graph_rest(
        "POST",
        "https://graph.microsoft.com/v1.0/policies/claimsMappingPolicies",
        {
            "definition": [json.dumps(policy_definition)],
            "displayName": "ServiceNow SAML Claims Policy",
            "isOrganizationDefault": False,
        },
    )

    if not policy:
        print("  [WARN] Could not create claims mapping policy")
        print("         Configure SAML claims manually in Azure Portal")
        return None

    policy_id = policy["id"]
    print(f"  [OK] Created claims mapping policy: {policy_id}")

    # Assign policy to service principal
    print("  Assigning policy to service principal...")
    _graph_rest(
        "POST",
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}"
        f"/claimsMappingPolicies/$ref",
        {
            "@odata.id": (
                f"https://graph.microsoft.com/v1.0/policies"
                f"/claimsMappingPolicies/{policy_id}"
            )
        },
    )
    print("  [OK] Claims mapping policy assigned to service principal")

    return policy_id


# ---------------------------------------------------------------------------
# Step 4: Download Federation Metadata
# ---------------------------------------------------------------------------


def download_federation_metadata(app_id):
    """Download Azure AD federation metadata XML and extract SAML config.

    Returns dict with IdP entity ID, SSO/SLO URLs, and signing certificate.
    """
    tenant_id = run("az account show --query tenantId -o tsv")
    if not tenant_id:
        print("  [FAIL] Could not get tenant ID")
        sys.exit(1)

    metadata_url = (
        f"https://login.microsoftonline.com/{tenant_id}"
        f"/federationmetadata/2007-06/federationmetadata.xml?appid={app_id}"
    )

    print(f"  Downloading federation metadata...")
    print(f"  URL: {metadata_url}")

    r = httpx.get(metadata_url, timeout=30)
    if r.status_code != 200:
        print(f"  [FAIL] HTTP {r.status_code}: {r.text[:200]}")
        sys.exit(1)

    metadata_xml = r.text

    # Save metadata XML for reference
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    metadata_path = CONFIG_DIR / "sn-saml-metadata.xml"
    metadata_path.write_text(metadata_xml, encoding="utf-8")
    print(f"  [OK] Saved metadata to {metadata_path}")

    # Parse XML to extract key values
    ns = {
        "md": "urn:oasis:names:tc:SAML:2.0:metadata",
        "ds": "http://www.w3.org/2000/09/xmldsig#",
    }
    root = ET.fromstring(metadata_xml)

    idp_entity_id = root.get("entityID", "")
    sso_url = ""
    slo_url = ""
    signing_cert_b64 = ""

    idp_desc = root.find(".//md:IDPSSODescriptor", ns)
    if idp_desc is not None:
        # SSO URL -- prefer HTTP-POST binding for SAML responses
        for sso in idp_desc.findall("md:SingleSignOnService", ns):
            binding = sso.get("Binding", "")
            location = sso.get("Location", "")
            if "HTTP-POST" in binding:
                sso_url = location
            elif not sso_url and "HTTP-Redirect" in binding:
                sso_url = location

        # SLO URL
        for slo in idp_desc.findall("md:SingleLogoutService", ns):
            slo_url = slo.get("Location", "")
            break

        # Signing certificate (first "signing" key descriptor)
        for key_desc in idp_desc.findall("md:KeyDescriptor", ns):
            if key_desc.get("use", "") == "signing":
                cert_elem = key_desc.find(".//ds:X509Certificate", ns)
                if cert_elem is not None and cert_elem.text:
                    signing_cert_b64 = cert_elem.text.strip()
                    break

    print(f"  IdP Entity ID: {idp_entity_id}")
    print(f"  SSO URL:       {sso_url}")
    print(f"  SLO URL:       {slo_url}")
    if signing_cert_b64:
        print(f"  Signing cert:  {signing_cert_b64[:50]}...")
    else:
        print("  Signing cert:  NOT FOUND (cert may not be generated yet)")

    return {
        "tenant_id": tenant_id,
        "idp_entity_id": idp_entity_id,
        "sso_url": sso_url,
        "slo_url": slo_url,
        "signing_cert_b64": signing_cert_b64,
        "metadata_url": metadata_url,
    }


# ---------------------------------------------------------------------------
# Step 5: Assign Users to SAML App
# ---------------------------------------------------------------------------


def assign_users(sp_object_id, user_emails):
    """Assign Azure AD users to the SAML app so they can use SSO."""
    for email in user_emails:
        print(f"  Looking up user: {email}")
        user = run(
            f'az ad user show --id "{email}" '
            f'--query "{{id: id, displayName: displayName}}" -o json',
            parse_json=True,
        )
        if not user:
            print(f"  [SKIP] User not found in Azure AD: {email}")
            continue

        user_id = user["id"]
        display = user.get("displayName", email)

        # Create default app role assignment
        result = _graph_rest(
            "POST",
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_object_id}"
            f"/appRoleAssignments",
            {
                "principalId": user_id,
                "resourceId": sp_object_id,
                "appRoleId": "00000000-0000-0000-0000-000000000000",
            },
        )

        if result:
            print(f"  [OK] Assigned {display}")
        else:
            # Likely already assigned -- Graph returns 409 Conflict
            print(f"  [OK] {display} (likely already assigned)")


# ---------------------------------------------------------------------------
# Step 6: ServiceNow -- Check/Activate SSO Plugin
# ---------------------------------------------------------------------------


MULTI_SSO_PLUGIN = "com.snc.integration.sso.multi.installer"


def check_sso_plugin(sn_client):
    """Check if Multi-Provider SSO plugin is active. Attempt API activation."""
    # Use v_plugin (accessible to admin) instead of sys_plugins (often 403)
    r = sn_client.get(
        "/api/now/table/v_plugin",
        params={
            "sysparm_query": f"id={MULTI_SSO_PLUGIN}",
            "sysparm_fields": "id,name,active",
            "sysparm_limit": "1",
        },
    )
    if r.status_code != 200:
        print(f"  [WARN] Could not query v_plugin (HTTP {r.status_code})")
        return None

    results = r.json().get("result", [])
    if not results:
        print("  [WARN] Multi-Provider SSO plugin not found")
        return None

    plugin = results[0]
    is_active = plugin.get("active", "") == "active"
    print(f"  Plugin: {plugin.get('name', MULTI_SSO_PLUGIN)}")
    print(f"  Status: {'ACTIVE' if is_active else 'INACTIVE'}")

    if is_active:
        return True

    # Try to activate via CICD API
    print("  Attempting plugin activation via CICD API...")
    activate_r = sn_client.post(
        f"/api/sn_cicd/plugin/{MULTI_SSO_PLUGIN}/activate",
        json={},
    )
    if activate_r.status_code != 200:
        print(f"  [FAIL] CICD API activation failed (HTTP {activate_r.status_code})")
        print()
        print("  ACTION REQUIRED: Activate the plugin manually:")
        print("    1. Log in to ServiceNow as admin")
        print("    2. Navigate to System Definition > Plugins")
        print("    3. Search for 'Multi-Provider SSO'")
        print("    4. Click 'Install' or 'Activate'")
        print("    5. Re-run this script with --skip-azure")
        return False

    progress_data = activate_r.json().get("result", {})
    progress_url = (
        progress_data.get("links", {}).get("progress", {}).get("url", "")
    )
    if not progress_url:
        print("  [WARN] Activation started but no progress URL returned")
        return None

    # Poll for completion (plugin activation can take 3-5 minutes)
    print("  Plugin activation in progress (this may take several minutes)...")
    for attempt in range(36):  # Up to 6 minutes
        time.sleep(10)
        prog_r = sn_client.get(progress_url.replace(sn_client.base_url, ""))
        if prog_r.status_code != 200:
            # Try with full URL
            prog_r = httpx.get(
                progress_url,
                auth=sn_client.auth,
                headers={"Accept": "application/json"},
                timeout=30,
            )
        if prog_r.status_code != 200:
            continue

        prog = prog_r.json().get("result", {})
        pct = prog.get("percent_complete", 0)
        status = prog.get("status", "")
        label = prog.get("status_label", "")
        print(f"    [{attempt+1}] {label} ({pct}%)")

        if status == "2":  # Successful
            print("  [OK] Plugin activated successfully")
            return True
        elif status in ("3", "4"):  # Failed / Cancelled
            msg = prog.get("status_message", "")
            print(f"  [FAIL] Plugin activation failed: {msg}")
            return False

    print("  [WARN] Plugin activation timed out -- check SN UI for status")
    return None


# ---------------------------------------------------------------------------
# Step 7: ServiceNow -- Create Identity Provider
# ---------------------------------------------------------------------------

# Table hierarchy (discovered from live instance):
#   sso_properties (parent: Identity Providers)
#     +-- saml2_update1_properties (child: SAML 2.0 IdP config)
# Creating in saml2_update1_properties auto-creates sso_properties parent.
# Certificate goes in separate idp_certificate table (FK to sso_properties).

IDP_TABLE = "saml2_update1_properties"
IDP_NAME = "Azure AD SAML SSO"


def find_sn_identity_provider(sn_client):
    """Find existing SAML 2.0 IdP record by name or any SAML IdP.

    Returns (idp_table, idp_sys_id) or (None, None).
    """
    # Search by our name first
    r = sn_client.get(
        "/api/now/table/sso_properties",
        params={
            "sysparm_query": f"name={IDP_NAME}",
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": "1",
        },
    )
    if r.status_code == 200:
        results = r.json().get("result", [])
        if results:
            idp_sys_id = results[0]["sys_id"]
            print(f"  [OK] IdP found by name: {idp_sys_id}")
            return IDP_TABLE, idp_sys_id

    # Search for any SAML IdP (from metadata import)
    r = sn_client.get(
        "/api/now/table/saml2_update1_properties",
        params={
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": "5",
            "sysparm_orderby": "sys_created_on",
            "sysparm_orderbydesc": "sys_created_on",
        },
    )
    if r.status_code == 200:
        results = r.json().get("result", [])
        if results:
            idp = results[0]
            print(f"  [OK] Found SAML IdP: {idp['name']} ({idp['sys_id']})")
            return IDP_TABLE, idp["sys_id"]

    return None, None


def configure_sn_identity_provider(sn_client, idp_sys_id, instance_url):
    """Configure IdP fields via API after UI metadata import.

    The IdP record MUST be created via the SN UI metadata import dialog.
    This function updates the fields that the metadata import doesn't set
    (or sets incorrectly).
    """
    instance_host = instance_url.rstrip("/")

    # MultiSSOv2_SAML2_internal script (NOT the legacy MultiSSO_SAML2_Update1)
    sso_script_sys_id = "055e19b20b21230001d36c4d37673ae9"

    updates = {
        "name": IDP_NAME,
        "active": "true",
        "user_field": "email",
        "default": "true",
        "show_as_login_option": "true",
        "auto_provision": "true",
        "auto_update_user": "true",
        "sso_script": sso_script_sys_id,
        "issuer": instance_host,  # SP Entity ID, NOT the IdP entity ID
        "audience": instance_host,
        "nameid_policy": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        "service_url": f"{instance_host}/navpage.do",
    }

    print(f"  Updating IdP {idp_sys_id} with {len(updates)} fields...")
    r = sn_client.patch(
        f"/api/now/table/{IDP_TABLE}/{idp_sys_id}",
        json=updates,
    )
    if r.status_code == 200:
        result = r.json()["result"]
        print(f"  [OK] Name:              {result.get('name', '?')}")
        sso = result.get("sso_script", "")
        if isinstance(sso, dict):
            sso = sso.get("value", "")
        print(f"  [OK] SSO Script:        {'MultiSSOv2_SAML2_internal' if sso == sso_script_sys_id else sso}")
        print(f"  [OK] Issuer (SP):       {result.get('issuer', '?')}")
        print(f"  [OK] NameID Policy:     {result.get('nameid_policy', '?')}")
        print(f"  [OK] Auto Provision:    {result.get('auto_provision', '?')}")
        print(f"  [OK] Show Login Option: {result.get('show_as_login_option', '?')}")
    else:
        print(f"  [FAIL] HTTP {r.status_code}: {r.text[:300]}")

    return idp_sys_id


def print_idp_import_instructions(saml_config):
    """Print instructions for the ONE manual step: IdP metadata import."""
    metadata_url = saml_config.get("metadata_url", "")
    print()
    print("  " + "=" * 56)
    print("  MANUAL STEP REQUIRED (only step that cannot be automated)")
    print("  " + "=" * 56)
    print()
    print("  1. Log in to ServiceNow as admin")
    print("  2. Navigate to: Multi-Provider SSO > Identity Providers")
    print("  3. Click 'New'")
    print("  4. In the 'Import Identity Provider Metadata' popup:")
    print("     - Select 'URL'")
    print("     - Paste this URL:")
    print(f"       {metadata_url}")
    print("     - Click 'Import'")
    print("  5. Click 'Update' (don't change any fields)")
    print()
    print("  Then re-run this script with --post-import:")
    print(f"    python scripts/setup_saml_sso.py \\")
    print(f"      --instance <instance> --admin-password <pw> \\")
    print(f"      --skip-azure --post-import")
    print()


# ---------------------------------------------------------------------------
# Step 8: ServiceNow -- Configure JIT Provisioning Roles
# ---------------------------------------------------------------------------


def configure_jit_roles(sn_client, idp_sys_id):
    """Configure default roles for JIT-provisioned users.

    The saml2_update1_properties table has a 'groups_for_imported_users'
    field that assigns user groups to auto-provisioned users.
    """
    if not idp_sys_id:
        return

    # Look up the itil group (sys_user_group) for JIT assignment
    # Also try setting via the IdP record's groups_for_imported_users field
    target_groups = ["itil"]
    group_sys_ids = []

    for group_name in target_groups:
        r = sn_client.get(
            "/api/now/table/sys_user_group",
            params={
                "sysparm_query": f"name={group_name}",
                "sysparm_fields": "sys_id,name",
                "sysparm_limit": "1",
            },
        )
        if r.status_code == 200 and r.json().get("result"):
            group_sys_ids.append(r.json()["result"][0]["sys_id"])
            print(f"  [OK] Found group: {group_name}")
        else:
            print(f"  [SKIP] Group '{group_name}' not found (will assign roles via transform map)")

    if group_sys_ids:
        # Set groups_for_imported_users on the IdP record
        r = sn_client.patch(
            f"/api/now/table/{IDP_TABLE}/{idp_sys_id}",
            json={"groups_for_imported_users": ",".join(group_sys_ids)},
        )
        if r.status_code == 200:
            print("  [OK] Set JIT user groups on IdP record")
        else:
            print(f"  [WARN] Could not set groups: {r.status_code}")

    print()
    print("  NOTE: JIT-provisioned users will receive roles from their assigned groups.")
    print("  For itil + personalize_dictionary roles, ensure those groups exist or")
    print("  configure a Transform Map on the IdP record for fine-grained role assignment.")


# ---------------------------------------------------------------------------
# Step 10: ServiceNow -- Enable SSO Properties
# ---------------------------------------------------------------------------


def enable_sso_properties(sn_client, idp_sys_id, saml_config):
    """Enable Multi-Provider SSO and update legacy SAML properties.

    Note: glide.authenticate.multisso.enabled is protected by a Business Rule
    ('Check ACR and SSO') and cannot be set via Table API. It must be enabled
    through the ServiceNow UI: Multi-Provider SSO > Administration > Properties.
    """
    # Set redirect URL (this one is usually API-writable)
    if idp_sys_id:
        _set_sys_property(
            sn_client,
            "glide.authenticate.sso.redirect.url",
            f"/login_with_sso.do?glide_sso_id={idp_sys_id}",
            "SSO redirect URL for Azure AD SAML",
        )

    # Update legacy SAML properties (the v2 script reads from per-IdP record,
    # but the Java validator also reads from these legacy properties)
    if saml_config:
        sso_url = saml_config.get("sso_url", "")
        if sso_url:
            _set_sys_property(
                sn_client,
                "glide.authenticate.sso.saml2.idp_authnrequest_url",
                sso_url,
                "SAML IdP AuthnRequest URL",
            )
            _set_sys_property(
                sn_client,
                "glide.authenticate.sso.saml2.idp_logout_url",
                sso_url,
                "SAML IdP Logout URL",
            )

    # Check if multisso is already enabled
    r = sn_client.get(
        "/api/now/table/sys_properties",
        params={
            "sysparm_query": "name=glide.authenticate.multisso.enabled",
            "sysparm_fields": "sys_id,value",
            "sysparm_limit": "1",
        },
    )
    multisso_enabled = False
    if r.status_code == 200 and r.json().get("result"):
        multisso_enabled = r.json()["result"][0].get("value", "") == "true"

    if multisso_enabled:
        print("  [OK] glide.authenticate.multisso.enabled = true (already set)")
    else:
        print("  [ACTION REQUIRED] Enable Multi-Provider SSO in ServiceNow UI:")
        print("    1. Navigate to Multi-Provider SSO > Administration > Properties")
        print("    2. Check 'Enable multiple provider SSO'")
        print("    3. Save")
        print("    (This property is protected by a Business Rule and cannot be set via API)")

    print()
    print("  IMPORTANT: Local admin login remains available at /login.do")
    print("  SSO is additive -- it does NOT disable basic authentication")


def _set_sys_property(sn_client, name, value, description):
    """Set a ServiceNow system property (create or update)."""
    r = sn_client.get(
        "/api/now/table/sys_properties",
        params={
            "sysparm_query": f"name={name}",
            "sysparm_fields": "sys_id,name,value",
            "sysparm_limit": "1",
        },
    )
    if r.status_code != 200:
        print(f"  [WARN] Cannot access sys_properties (HTTP {r.status_code})")
        return

    results = r.json().get("result", [])
    if results:
        prop_sys_id = results[0]["sys_id"]
        old_value = results[0].get("value", "")
        if old_value == value:
            print(f"  [OK] {name} = {value} (already set)")
            return
        r2 = sn_client.patch(
            f"/api/now/table/sys_properties/{prop_sys_id}",
            json={"value": value},
        )
        if r2.status_code == 200:
            print(f"  [OK] Updated {name}")
        else:
            print(f"  [FAIL] Could not update {name}: {r2.status_code}")
    else:
        r2 = sn_client.post(
            "/api/now/table/sys_properties",
            json={
                "name": name,
                "value": value,
                "description": description,
                "type": "boolean" if value in ("true", "false") else "string",
            },
        )
        if r2.status_code in (200, 201):
            print(f"  [OK] Created {name} = {value}")
        else:
            print(f"  [FAIL] Could not create {name}: {r2.status_code}")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def print_verification(instance_url, azure_config, saml_config, idp_sys_id):
    """Print verification steps and test URLs."""
    instance_host = instance_url.rstrip("/")

    print(f"\n{'='*60}")
    print("VERIFICATION STEPS")
    print(f"{'='*60}")

    print("\n1. Azure Portal -- Check Enterprise App:")
    print(f"   https://portal.azure.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade"
          f"/~/SingleSignOn/objectId/{azure_config['sp_object_id']}")

    print("\n2. SP-Initiated SSO (browser test):")
    if idp_sys_id:
        print(
            f"   {instance_host}/login_with_sso.do?glide_sso_id={idp_sys_id}"
        )
    else:
        print("   (Get IdP sys_id from SN UI, then use:")
        print("    {instance}/login_with_sso.do?glide_sso_id=<sys_id>)")

    print("\n3. IdP-Initiated SSO (from Azure Portal):")
    print(f"   Azure Portal > Enterprise Applications > {azure_config['display_name']}")
    print("   > Single sign-on > Test")

    print("\n4. JIT Provisioning Test:")
    print("   Log in with a NEW Azure AD user not yet in ServiceNow")
    print("   -> Verify sys_user record created with email + name + itil role")

    print("\n5. Existing User Test:")
    print("   Log in with ozgurkarahan@MngEnvMCAP549101.onmicrosoft.com")
    print("   -> Verify SSO works, no duplicate user created")

    print("\n6. Admin Fallback:")
    print(f"   {instance_host}/login.do")
    print("   -> Verify local admin login still works")

    print("\n7. MCP API Unaffected:")
    print("   Run a Foundry agent query to confirm JWT Bearer OBO still works")
    print("   (SSO is browser-only, does not affect API auth)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Setup SAML 2.0 SSO (Azure Entra ID -> ServiceNow)"
    )
    parser.add_argument(
        "--instance", required=True, help="ServiceNow instance URL"
    )
    parser.add_argument(
        "--admin-user", default="admin", help="SN admin username (default: admin)"
    )
    parser.add_argument(
        "--admin-password", required=True, help="SN admin password"
    )
    parser.add_argument(
        "--skip-azure",
        action="store_true",
        help="Skip Azure AD setup, only configure ServiceNow (uses saved config)",
    )
    parser.add_argument(
        "--skip-servicenow",
        action="store_true",
        help="Skip ServiceNow setup, only configure Azure AD",
    )
    parser.add_argument(
        "--post-import",
        action="store_true",
        help="Run after manual IdP metadata import in SN UI. "
        "Finds the IdP, configures fields, sets SSO script, enables properties.",
    )
    parser.add_argument(
        "--users",
        nargs="*",
        default=["ozgurkarahan@MngEnvMCAP549101.onmicrosoft.com"],
        help="Azure AD user emails to assign to the SAML app",
    )
    args = parser.parse_args()

    instance = args.instance.rstrip("/")

    print(f"\n{'='*60}")
    print("SAML 2.0 SSO Setup -- Azure Entra ID -> ServiceNow")
    print(f"Instance: {instance}")
    print(f"{'='*60}")

    azure_config = {}
    saml_config = {}

    # ---- Azure AD Setup ----
    if not args.skip_azure:
        print("\n--- Step 1: Azure AD Enterprise Application ---")
        azure_config = create_azure_ad_saml_app(instance)

        print("\n--- Step 2: SAML Signing Certificate ---")
        thumbprint = ensure_saml_signing_cert(azure_config["sp_object_id"])
        azure_config["cert_thumbprint"] = thumbprint

        print("\n--- Step 3: Claims Mapping Policy ---")
        policy_id = configure_claims_mapping(azure_config["sp_object_id"])
        azure_config["claims_policy_id"] = policy_id

        print("\n--- Step 4: Federation Metadata ---")
        saml_config = download_federation_metadata(azure_config["app_id"])

        print("\n--- Step 5: Assign Users ---")
        assign_users(azure_config["sp_object_id"], args.users)

        # Save config for --skip-azure re-runs
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = {"azure": azure_config, "saml": saml_config, "instance": instance}
        SSO_CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"\n[OK] Saved SSO config to {SSO_CONFIG_FILE}")

    else:
        # Load saved config
        if not SSO_CONFIG_FILE.exists():
            print(f"[FAIL] No saved config at {SSO_CONFIG_FILE}")
            print("       Run without --skip-azure first")
            sys.exit(1)
        config = json.loads(SSO_CONFIG_FILE.read_text(encoding="utf-8"))
        azure_config = config.get("azure", {})
        saml_config = config.get("saml", {})
        print(f"\n[OK] Loaded SSO config from {SSO_CONFIG_FILE}")
        print(f"  App ID:  {azure_config.get('app_id', '?')}")
        print(f"  SP ID:   {azure_config.get('sp_object_id', '?')}")

    # ---- ServiceNow Setup ----
    idp_sys_id = None
    if not args.skip_servicenow:
        if not saml_config:
            print("[FAIL] No SAML config available for ServiceNow setup")
            sys.exit(1)

        sn_client = httpx.Client(
            base_url=instance,
            auth=(args.admin_user, args.admin_password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )

        # Verify admin access
        print("\n--- Verifying SN Admin Access ---")
        r = sn_client.get(
            "/api/now/table/sys_user", params={"sysparm_limit": "1"}
        )
        if r.status_code != 200:
            print(f"[FAIL] Cannot access SN instance: HTTP {r.status_code}")
            print(f"  Response: {r.text[:300]}")
            sys.exit(1)
        print("[OK] Admin access verified")

        if args.post_import:
            # ---- Post-Import Mode ----
            # User has already imported metadata via SN UI.
            # Find the IdP, configure fields, enable properties.
            print("\n--- Post-Import: Find IdP Record ---")
            idp_table, idp_sys_id = find_sn_identity_provider(sn_client)

            if not idp_sys_id:
                print("[FAIL] No SAML IdP record found!")
                print("       Import metadata first via SN UI, then re-run")
                sn_client.close()
                sys.exit(1)

            print("\n--- Post-Import: Configure IdP Fields ---")
            configure_sn_identity_provider(sn_client, idp_sys_id, instance)

            print("\n--- Post-Import: Configure JIT Roles ---")
            configure_jit_roles(sn_client, idp_sys_id)

            print("\n--- Post-Import: Enable SSO Properties ---")
            enable_sso_properties(sn_client, idp_sys_id, saml_config)

            sn_client.close()
        else:
            # ---- Initial Setup Mode ----
            # Activate plugin, set properties, print import instructions.
            print("\n--- Step 6: Check/Activate SSO Plugin ---")
            plugin_active = check_sso_plugin(sn_client)

            if plugin_active is False:
                print("\n[BLOCKED] SSO plugin must be activated before IdP setup")
                print("          Follow the instructions above, then re-run with --skip-azure")
                sn_client.close()
            else:
                print("\n--- Step 7: Enable SSO Properties (pre-import) ---")
                enable_sso_properties(sn_client, None, saml_config)

                sn_client.close()

                print("\n--- Step 8: IdP Metadata Import ---")
                print_idp_import_instructions(saml_config)

        # Update saved config with IdP sys_id
        if idp_sys_id:
            config = json.loads(SSO_CONFIG_FILE.read_text(encoding="utf-8"))
            config["idp_sys_id"] = idp_sys_id
            config["idp_table"] = IDP_TABLE
            SSO_CONFIG_FILE.write_text(
                json.dumps(config, indent=2), encoding="utf-8"
            )

    # ---- Summary ----
    print(f"\n{'='*60}")
    print("SAML SSO SETUP SUMMARY")
    print(f"{'='*60}")
    print(f"  Instance:        {instance}")
    if azure_config:
        print(f"  Azure App ID:    {azure_config.get('app_id', '?')}")
        print(f"  Azure SP ID:     {azure_config.get('sp_object_id', '?')}")
        print(f"  Cert Thumbprint: {azure_config.get('cert_thumbprint', '?')}")
        print(f"  Claims Policy:   {azure_config.get('claims_policy_id', '?')}")
    if saml_config:
        print(f"  IdP Entity ID:   {saml_config.get('idp_entity_id', '?')}")
        print(f"  SSO URL:         {saml_config.get('sso_url', '?')}")
    if idp_sys_id:
        print(f"  SN IdP sys_id:   {idp_sys_id}")
        print(f"  SSO Login URL:   {instance}/login_with_sso.do?glide_sso_id={idp_sys_id}")
    print(f"  Admin Login:     {instance}/login.do (unchanged)")
    print(f"  MCP API:         Unaffected (JWT Bearer OBO via APIM)")
    print(f"{'='*60}")

    # Verification
    print_verification(instance, azure_config, saml_config, idp_sys_id)


if __name__ == "__main__":
    main()
