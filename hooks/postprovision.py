"""Post-provision hook for ServiceNow MCP deployment.

After Bicep deploys Azure resources, this hook:
0. Uploads SN JWT Bearer cert to Key Vault + creates APIM cert binding
1. Updates APIM Named Values (SnOboClientId, SnOboInstanceUrl, SnJwtBearerKid)
2. Recreates Foundry servicenow-obo connection via ARM REST

Subset of the Salesforce postprovision hook -- no Entra app, no agent, no bot.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid


def run(cmd: str, parse_json: bool = False):
    """Run a shell command and return stdout (or parsed JSON)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    if parse_json:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
    return out


def azd_env_set(key: str, value: str):
    """Set an azd environment variable."""
    subprocess.run(
        f'azd env set {key} "{value}"',
        shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    os.environ[key] = value
    print(f"  azd env set {key}={value[:20]}{'...' if len(value) > 20 else ''}")


def _write_temp_json(data):
    """Write data as JSON to a temp file and return the file path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


# ============================================================================
# Step 0: Upload SN JWT Bearer cert to Key Vault + APIM cert binding
# ============================================================================

def upload_cert_and_configure_apim():
    """Upload SN JWT Bearer cert to Key Vault and create APIM cert binding.

    On first deploy the cert isn't in KV yet (Bicep skips the cert module when
    SN_JWT_BEARER_CERT_THUMBPRINT is empty). This function:
    1. Checks for local cert at certs/sn-jwt-bearer.pfx
    2. Assigns deployer Key Vault Certificates Officer role (idempotent)
    3. Imports cert into Key Vault (with retry for RBAC propagation)
    4. Reads thumbprint and persists via azd env set
    5. Creates APIM cert binding via ARM REST
    6. Updates APIM Named Value SnJwtBearerCertThumbprint
    """
    cert_path = os.path.join(os.getcwd(), "certs", "sn-jwt-bearer.pfx")
    if not os.path.exists(cert_path):
        print("  No local cert found at certs/sn-jwt-bearer.pfx -- skipping")
        print("  Generate with: openssl pkcs12 -export -out certs/sn-jwt-bearer.pfx \\")
        print("    -inkey certs/sn-jwt-bearer.key -in certs/sn-jwt-bearer.crt -passout pass:")
        return

    kv_name = os.environ.get("KEY_VAULT_NAME", "")
    if not kv_name:
        print("  WARNING: KEY_VAULT_NAME not set -- skipping cert upload")
        return

    cert_name = os.environ.get("SN_JWT_BEARER_CERT_NAME", "sn-jwt-bearer")

    # Check if cert already exists in KV
    thumbprint = run(
        f'az keyvault certificate show --vault-name {kv_name} '
        f'--name {cert_name} --query x509ThumbprintHex -o tsv'
    )

    if thumbprint:
        print(f"  Certificate already in Key Vault (thumbprint: {thumbprint})")
    else:
        # Assign deployer Key Vault Certificates Officer role
        deployer_oid = run('az ad signed-in-user show --query id -o tsv')
        if not deployer_oid:
            print("  WARNING: Could not get deployer OID -- skipping cert upload")
            return

        sub_id = run("az account show --query id -o tsv")
        rg = os.environ.get("AZURE_RESOURCE_GROUP", "rg-sf-mcp-obo")
        kv_resource_id = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}"
            f"/providers/Microsoft.KeyVault/vaults/{kv_name}"
        )
        role_def_id = "a4417e6f-fecd-4de8-b567-7b0420556985"  # Key Vault Certificates Officer
        assignment_name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kv_resource_id}/{deployer_oid}/{role_def_id}"))

        role_url = (
            f"https://management.azure.com{kv_resource_id}"
            f"/providers/Microsoft.Authorization/roleAssignments/{assignment_name}"
            f"?api-version=2022-04-01"
        )
        role_body = {
            "properties": {
                "roleDefinitionId": f"/subscriptions/{sub_id}/providers/Microsoft.Authorization/roleDefinitions/{role_def_id}",
                "principalId": deployer_oid,
                "principalType": "User",
            }
        }
        body_file = _write_temp_json(role_body)
        try:
            print("  Assigning Key Vault Certificates Officer to deployer...")
            run(
                f'az rest --method PUT --url "{role_url}" '
                f'--headers "Content-Type=application/json" '
                f'--body "@{body_file}"',
                parse_json=True,
            )
        finally:
            os.unlink(body_file)

        # Import cert with retry (RBAC propagation can take ~30s)
        max_retries = 6
        retry_delay = 10
        for attempt in range(max_retries):
            result = run(
                f'az keyvault certificate import --vault-name {kv_name} '
                f'--name {cert_name} --file "{cert_path}" --password ""'
            )
            if result is not None:
                print("  Certificate imported to Key Vault")
                break
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: RBAC not yet propagated, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            else:
                print("  ERROR: Failed to import certificate after retries")
                return

        # Read thumbprint
        thumbprint = run(
            f'az keyvault certificate show --vault-name {kv_name} '
            f'--name {cert_name} --query x509ThumbprintHex -o tsv'
        )
        if not thumbprint:
            print("  ERROR: Could not read certificate thumbprint from Key Vault")
            return

    # Persist thumbprint for future azd up runs
    azd_env_set("SN_JWT_BEARER_CERT_THUMBPRINT", thumbprint)

    # Create APIM cert binding via ARM REST
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "rg-sf-mcp-obo")
    apim_name = os.environ.get("APIM_NAME", "")
    kv_uri = f"https://{kv_name}.vault.azure.net/"

    if not apim_name:
        print("  WARNING: APIM_NAME not set -- skipping APIM cert binding")
        return

    cert_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        f"/certificates/{cert_name}"
        f"?api-version=2024-06-01-preview"
    )
    cert_body = {
        "properties": {
            "keyVault": {
                "secretIdentifier": f"{kv_uri}secrets/{cert_name}",
            }
        }
    }
    body_file = _write_temp_json(cert_body)
    try:
        print(f"  Creating APIM certificate binding '{cert_name}'...")
        result = run(
            f'az rest --method PUT --url "{cert_url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print("  APIM certificate binding created")
        else:
            print("  WARNING: Failed to create APIM certificate binding")
    finally:
        os.unlink(body_file)

    # Update APIM Named Value for thumbprint
    nv_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        f"/namedValues/SnJwtBearerCertThumbprint"
        f"?api-version=2024-06-01-preview"
    )
    nv_body = {
        "properties": {
            "displayName": "SnJwtBearerCertThumbprint",
            "value": thumbprint,
            "secret": False,
        }
    }
    body_file = _write_temp_json(nv_body)
    try:
        print("  Updating APIM Named Value 'SnJwtBearerCertThumbprint'...")
        result = run(
            f'az rest --method PUT --url "{nv_url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print(f"  SnJwtBearerCertThumbprint = {thumbprint}")
        else:
            print("  WARNING: Failed to update SnJwtBearerCertThumbprint Named Value")
    finally:
        os.unlink(body_file)


# ============================================================================
# Step 1: Update APIM Named Values (SN-specific)
# ============================================================================

def update_apim_named_values():
    """Update SN-specific APIM Named Values from environment variables."""
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "rg-sf-mcp-obo")
    apim_name = os.environ.get("APIM_NAME", "")

    if not apim_name:
        print("  WARNING: APIM_NAME not set -- skipping Named Value updates")
        return

    named_values = {
        "SnOboClientId": os.environ.get("SN_OAUTH_CLIENT_ID", ""),
        "SnOboInstanceUrl": os.environ.get("SN_INSTANCE_URL", ""),
        "SnJwtBearerKid": os.environ.get("SN_JWT_BEARER_KID", ""),
    }

    for nv_name, nv_value in named_values.items():
        if not nv_value:
            print(f"  Skipping {nv_name} (not set)")
            continue

        nv_url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/resourceGroups/{rg}"
            f"/providers/Microsoft.ApiManagement/service/{apim_name}"
            f"/namedValues/{nv_name}"
            f"?api-version=2024-06-01-preview"
        )
        nv_body = {
            "properties": {
                "displayName": nv_name,
                "value": nv_value,
                "secret": False,
            }
        }
        body_file = _write_temp_json(nv_body)
        try:
            result = run(
                f'az rest --method PUT --url "{nv_url}" '
                f'--headers "Content-Type=application/json" '
                f'--body "@{body_file}"',
                parse_json=True,
            )
            if result:
                print(f"  {nv_name} = {nv_value[:30]}{'...' if len(nv_value) > 30 else ''}")
            else:
                print(f"  WARNING: Failed to update {nv_name}")
        finally:
            os.unlink(body_file)


# ============================================================================
# Step 2: Recreate Foundry servicenow-obo connection via ARM REST
# ============================================================================

def create_obo_connection():
    """Create/recreate the servicenow-obo Foundry connection via ARM REST.

    Bicep creates the connection but ARM REST allows updating auth properties
    that Bicep may not fully support. Delete + PUT for clean state.
    """
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "rg-sf-mcp-obo")
    cognitive_name = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
    connection_name = "servicenow-obo"

    if not all([cognitive_name, project_name, apim_gateway]):
        print("  WARNING: Missing COGNITIVE_ACCOUNT_NAME, AI_FOUNDRY_PROJECT_NAME, or APIM_GATEWAY_URL")
        print("  Skipping OBO connection creation")
        return

    target = f"{apim_gateway}/servicenow-mcp-obo/mcp"
    base_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{cognitive_name}"
        f"/projects/{project_name}"
        f"/connections/{connection_name}"
        f"?api-version=2025-04-01-preview"
    )

    # Delete existing (ignore errors)
    print(f"  Deleting existing '{connection_name}' connection (if any)...")
    run(f'az rest --method DELETE --url "{base_url}"')

    # Create connection
    conn_body = {
        "properties": {
            "authType": "UserEntraToken",
            "category": "RemoteTool",
            "target": target,
            "audience": "https://ai.azure.com",
            "metadata": {
                "type": "custom_MCP",
            },
            "isSharedToAll": True,
        }
    }
    body_file = _write_temp_json(conn_body)
    try:
        print(f"  Creating '{connection_name}' connection -> {target}")
        result = run(
            f'az rest --method PUT --url "{base_url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print(f"  Connection '{connection_name}' created successfully")
        else:
            print(f"  WARNING: Failed to create '{connection_name}' connection")
    finally:
        os.unlink(body_file)


# ============================================================================
# Main
# ============================================================================

def main():
    steps = [
        ("Step 0: Upload cert + APIM binding", upload_cert_and_configure_apim),
        ("Step 1: Update APIM Named Values", update_apim_named_values),
        ("Step 2: Create Foundry OBO connection", create_obo_connection),
    ]

    for title, func in steps:
        print(f"\n{'=' * 60}")
        print(f" {title}")
        print(f"{'=' * 60}")
        try:
            func()
        except Exception:
            print(f"  ERROR in {title}:")
            traceback.print_exc()
            print("  Continuing with next step...")


if __name__ == "__main__":
    main()
