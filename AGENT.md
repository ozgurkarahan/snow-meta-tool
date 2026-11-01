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
- **AI Foundry connection** `servicenow-obo-oauth2` -- RemoteTool with **OAuth2 identity passthrough** (shared Entra app `MCP_OAUTH_CLIENT_ID`, scope `api://<appId>/access_as_user`). Ensured create-only by the postprovision hook, never by Bicep. UserEntraToken is rejected by Foundry for custom MCP endpoints -- do not reintroduce it.
- **Image preservation**: `azd provision` passes `SERVICE_SERVICENOW_MCP_IMAGE_NAME` to Bicep so `ca-sn-mcp` keeps its deployed image (helloworld only on first provision).
- **Canonical agent**: `agents/servicenow-v3/azure.yaml`, `servicenow-assistant-v3` v2,
  Managed Harness `microsoft_copilot_preview`, `gpt-5.4-pro`, exact Toolbox v2.
  Skill `servicenow-itsm` v2 is pinned on both agent and Toolbox. Both MCP hops use
  `require_approval: never` for Teams compatibility; explicit subsequent chat
  confirmation of the exact preview remains mandatory before ANY write.
  `servicenow-assistant` v8 is legacy/rollback. V3 Teams publishing uses an
  identity-matched bot and the agent endpoint, not the legacy Agent Application.
  The current E2E/owned-incident-cleanup gate blocks publishing.

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
| `infra/modules/` | Container App, APIM API, APIM cert, opt-in additive V3 Bot Service |
| `infra/policies/sn-mcp-obo-policy.xml` | APIM OBO token exchange policy |
| `infra/policies/sn-mcp-obo-prm-policy.xml` | RFC 9728 Protected Resource Metadata |
| `hooks/postprovision.py` | Post-deploy: cert, APIM, connection, agent, app, bot, Teams publish (8 steps) |
| `hooks/requirements.txt` | Python dependencies for postprovision hook (azure-ai-projects, azure-identity) |
| `agents/servicenow-v3/azure.yaml` | Canonical Managed Harness agent + Toolbox; deployed separately from root |
| `agents/servicenow-v3/e2e.py` | Same-user, agent-scoped E2E; stop on consent, no approvals/retries, owned conversation cleanup |
| `agents/servicenow-v3/publish_teams.py` | E2E-gated agent-endpoint publishing; new bot only, exact version pin, durable publish receipt |
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

## Infrastructure / legacy bootstrap deployment

For the canonical V3 agent, use the separate [V3 runbook](agents/servicenow-v3/README.md).
Root deployment executes legacy bootstrap/app/Teams hooks and is NOT a V3 deployment
or publishing workflow. Never run it to update an agent or deploy only the new bot.

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

The retained legacy post-provision hook uploads PFX to Key Vault, creates APIM cert
binding, updates Named Values, ensures the create-only OAuth2 connection, and
bootstraps legacy agent/application/bot/Teams integration; it remains unchanged
in this phase. New V3 bot wiring is off by default, with
`SERVICENOW_V3_BOT_ENABLED` / `SERVICENOW_V3_BOT_MSA_APP_ID` opt-in parameters.
The new F0/global/SingleTenant bot has Teams only and uses the V3 agent's
instance identity and Activity Protocol endpoint. Deploy only its module after
the E2E gate and create-only bot what-if pass, never the root template. No
infrastructure apply or V3 publishing was performed while this gate is blocked.

ServiceNow agent-memory provisioning and attachment have been removed from source.
Do not delete or modify the shared `project-memory` store or its data. V3 is
additive and lives under `agents/servicenow-v3`; root deployment and production
routing do not include it. The custom APIM backend still uses OAuth2. Only the
first-party, exact-version Toolbox endpoint uses a separate UserEntraToken
connection; see the V3 README for the binding contract. Never overwrite a previous
version's connection: v2 uses `servicenow-itsm-v3-toolbox-v2`.

## Key Constraints

- **SAML SSO** is browser-only; does not affect MCP API auth (JWT Bearer OBO via APIM)
- ServiceNow **blocks JWT Bearer for admin users** -- always use non-admin accounts
- `sys_dictionary` requires `personalize_dictionary` role -- server gracefully degrades without it
- XML comments in APIM policies must not contain `--` (XML spec)
- APIM Named Values must exist before policy references them (even if code path is unreached)
- Azure AD v1 tokens use `upn` not `preferred_username` -- policy checks both

---

## Workflow Rules

Read `~/projects/memory/agent-config/workflow.md` for the full set of global workflow rules. Key rules summarized here:

1. **Plan Before Coding** — For any task with 3+ steps, outline the approach first. Get approval before implementing.
2. **Verify Before Done** — Never mark a task complete without proving it works.
3. **Learn From Mistakes** — After any correction, update `.ai/lessons-learned.md`. Review at session start.
4. **No Blind Retries** — Diagnose root cause on failure. Don't retry non-transient errors.
5. **Keep It Simple** — Don't add features, refactor code, or make improvements beyond what was asked.

## Platform & Environment

Read `~/projects/memory/agent-config/platform.md` for full platform preferences and domain knowledge index.

- Windows 11 + Git Bash (MSYS)
- Python 3.11+ (`python` not `python3`)
- Always `encoding="utf-8", errors="replace"` for subprocess on Windows

## Reference Documents

| Document | Contents |
|----------|----------|
| `.ai/lessons-learned.md` | Debugging history, project-specific lessons |
| `.ai/project-reference.md` | Technical details, implementation caveats |
| `~/projects/memory/agent-config/knowledge/*.md` | Cross-project domain knowledge |

## What NOT To Do

- Do not create files unless absolutely necessary — prefer editing existing files.
- Do not add comments, docstrings, or type annotations to code you didn't change.
- Do not over-engineer simple solutions.
- Do not commit secrets, credentials, or `.env` files.
- Do not skip tests or verification steps.
