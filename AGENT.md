# ServiceNow MCP Meta-Tool

## Overview

MCP server exposing ServiceNow as 3 generic tools (`discover`, `query`, `write`) with per-user identity propagation via JWT Bearer OBO flow through Azure APIM. Agents discover tables and fields at runtime, then read/write records as the authenticated Azure AD user.

## Architecture

```
Browser Login (SAML SSO):
  User -> ServiceNow Login -> "Use external login" -> Azure AD SAML
    -> Azure AD authenticates -> SAML Response -> ServiceNow ACS
    -> JIT: create/update sys_user -> Session established

API Flow (MCP):
  Azure AD user -> APIM (validate token, JWT Bearer OBO exchange) -> Container App (FastMCP) -> ServiceNow Table API
```

- **SAML 2.0 SSO** enables browser-based login with JIT user provisioning (Azure AD Enterprise App)
- **APIM OBO policy** exchanges Azure AD token for a per-user ServiceNow token via JWT Bearer (RS256, cached 25 min)
- **FastMCP server** exposes 3 tools over MCP Streamable HTTP
- **ServiceNow client** handles auth (passthrough or self-managed), caching, pagination
- **AI Foundry connection** registered as RemoteTool with UserEntraToken auth

## Quick Reference

### Setup
```bash
# Local dev
cd src/servicenow-mcp && pip install -r requirements.txt
export SN_INSTANCE_URL=https://<instance>.service-now.com
export SN_CLIENT_ID=<client_id> SN_JWT_KID=<kid> SN_JWT_KEY_PATH=../../certs/sn-jwt-bearer.key SN_JWT_SUB=<email>
python app.py

# Deploy to Azure
azd up
```

### Common Commands
```bash
azd provision          # Create/update infrastructure
azd deploy             # Build + deploy container image
azd env get-values     # Show all environment variables
curl /health           # Container App health check
```

## Key Paths

| Path | Description |
|------|-------------|
| `src/servicenow-mcp/app.py` | FastMCP server, 3 tools, BearerTokenMiddleware |
| `src/servicenow-mcp/servicenow_client.py` | Async SN REST client, JWT auth, caching |
| `infra/main.bicep` | Root IaC (references existing shared resources) |
| `infra/modules/` | Container App, APIM API, APIM cert, Foundry connection |
| `infra/policies/sn-mcp-obo-policy.xml` | APIM OBO token exchange policy |
| `infra/policies/sn-mcp-obo-prm-policy.xml` | RFC 9728 Protected Resource Metadata |
| `hooks/postprovision.py` | Post-deploy: cert, APIM, connection, agent, app, bot, Teams publish (8 steps) |
| `hooks/requirements.txt` | Python dependencies for postprovision hook (azure-ai-projects, azure-identity) |
| `assets/teams/` | Teams app icons (color.png 192x192, outline.png 32x32) |
| `teams-app/` | Generated Teams app package (.zip with manifest + icons) |
| `scripts/test_jwt_bearer.py` | Automated SN instance setup + JWT Bearer test |
| `scripts/setup_saml_sso.py` | SAML 2.0 SSO + JIT provisioning setup (Azure AD -> SN) |

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SN_INSTANCE_URL` | ServiceNow instance URL | Yes |
| `SN_CLIENT_ID` | OAuth client_id (self-managed mode) | Local dev only |
| `SN_JWT_KID` | kid from jwt_verifier_map | Local dev only |
| `SN_JWT_KEY_PATH` | Path to RSA private key | Local dev only |
| `SN_JWT_SUB` | User email for JWT sub claim | Local dev only |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Azure Monitor | No |

## Deployment

```bash
# Prerequisites: PFX cert at certs/sn-jwt-bearer.pfx, SN instance configured
azd init --environment <env-name>
azd env set AZURE_RESOURCE_GROUP rg-sf-mcp-obo
azd env set SN_INSTANCE_URL "https://<instance>.service-now.com"
azd env set SN_OAUTH_CLIENT_ID "<client_id>"
azd env set SN_JWT_BEARER_KID "<kid>"
# + shared resource names: APIM_NAME, CONTAINER_APPS_ENV_NAME, AZURE_CONTAINER_REGISTRY_NAME, etc.
azd up
```

Post-provision hook automatically (8 steps): uploads PFX to Key Vault, creates APIM cert binding, updates Named Values, creates Foundry connection, creates `servicenow-assistant` agent with MCP + Memory tools, provisions Agent Application, creates Agent Deployment, bootstraps Bot Service + Teams/DirectLine channels, and publishes Teams app to org catalog (requires `AppCatalog.ReadWrite.All`).

## Key Constraints

- **SAML SSO** is browser-only; does not affect MCP API auth (JWT Bearer OBO via APIM)
- ServiceNow **blocks JWT Bearer for admin users** -- always use non-admin accounts
- `sys_dictionary` requires `personalize_dictionary` role -- server gracefully degrades without it
- XML comments in APIM policies must not contain `--` (XML spec)
- APIM Named Values must exist before policy references them (even if code path is unreached)
- Azure AD v1 tokens use `upn` not `preferred_username` -- policy checks both
