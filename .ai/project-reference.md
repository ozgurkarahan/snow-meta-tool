# Project Reference

Technical details and implementation caveats. Referenced from [`.claude/CLAUDE.md`](../CLAUDE.md).

---

## ServiceNow Data Model (OAuth)

### Table Inheritance
```
oauth_entity (parent)
  +-- oauth_jwt (child, extends oauth_entity)
        JWT-specific: user_field, enable_jti_verification, clock_skew, jti_claim
```

### Key Tables
| Table | Purpose |
|---|---|
| `sys_certificate` | X.509 certificate storage (type=trust_store_cert) |
| `oauth_jwt` | OAuth App Registry for JWT Bearer (extends oauth_entity) |
| `jwt_verifier_map` | Links kid -> certificate, FK to oauth_jwt |
| `oauth_jwt_claims` | Custom claim validations |
| `sys_user` | User records (mapped via user_field from JWT sub claim) |

### OAuth Entity Type Choices
`snc_instance`, `oauth_provider`, `auth_server`, `client`

### Inbound Grant Type Choices
`jwt`, `oidc`, `authz_code`, `client_credential`, `ropc`, `internal`, `multiple`, `default`, `not_applicable`, `uncertain`

---

## JWT Bearer Flow

### Token Endpoint
```
POST https://<instance>.service-now.com/oauth_token.do
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
&client_id=<CLIENT_ID>
&assertion=<SIGNED_JWT>
```

### JWT Structure
```json
Header: {"alg": "RS256", "typ": "JWT", "kid": "<from jwt_verifier_map>"}
Payload: {
  "iss": "<client_id>",
  "sub": "<user email matching user_field>",
  "aud": "<client_id>",
  "iat": <now>,
  "exp": <now + 300>,
  "jti": "<unique uuid>"
}
```

### Constraints
- Algorithm: RS256 only
- Admin users BLOCKED (ServiceNow restriction)
- No refresh token returned
- JTI must be unique per request (if verification enabled)
- Content-Type MUST be `application/x-www-form-urlencoded`

---

## Test Instance (Dev)

- Instance: https://dev330554.service-now.com (since 2026-10-31; Zurich PDI — REST basic auth is rejected for `admin`, so admin setup runs through an authenticated UI session with `X-UserToken`)
- OAuth Client ID: `eeaab49dc8ec4462abfa9a418b9c3c7a`
- JWT kid: `6b9b7db09df54b58ac0675aa2d139bb2`
- JWT Bearer cert thumbprint: `947043BB0892B46FDDADD410576A19C879F3E5CD` (same PFX as dev194081 — reused across PDI migrations)
- Test users: `jwt.test@example.com` (user_name=jwt.test), `ozgurkarahan@MngEnvMCAP549101.onmicrosoft.com` (user_name=ozgurkarahan and full-UPN alias)
- All test users have `itil` + `personalize_dictionary` roles
- APIM cache key is versioned: `sn-token-{{SnOboInstanceUrl}}-<email>` — switching `SnOboInstanceUrl` automatically orphans cached tokens

### Previous instances (released back to PDI pool)
- `dev434731` (lost after ~14 idle days; last APIM 200 on 2026-10-17, JWT bearer `access_denied` for every user on 2026-10-31; replaced by dev330554 — `scripts/repair_sn_pdi.py` documents the cert/app/verifier/user setup, then update APIM Named Values `SnOboInstanceUrl`/`SnOboClientId`/`SnJwtBearerKid`, `ca-sn-mcp` `SN_INSTANCE_URL`, and azd env `SN_INSTANCE_URL`/`SN_OAUTH_CLIENT_ID`/`SN_JWT_BEARER_KID`)
- `dev300704` (lost by 2026-10-07: JWT ****** returned `access_denied` for every client; replaced by dev434731 — re-run `scripts/test_jwt_bearer.py` setup steps + seed `seed_demo_data.py --sn-only`, then update APIM Named Values `SnOboInstanceUrl`/`SnOboClientId`/`SnJwtBearerKid` and `ca-sn-mcp` `SN_INSTANCE_URL`)
- `dev194081` (hibernated 2026-05-17, released when dev300704 was created)
- `dev281447` (only ever a placeholder in bicep defaults)

## Foundry Agent

- Canonical agent: `servicenow-assistant-v3` v2 (`gpt-5.4-pro`, Managed Harness).
- Legacy/rollback: `servicenow-assistant` v8. Its Agent Application route is
  unchanged. Managed Harness V3 publishes through its agent endpoint, not that
  application. V3 was published Shared `1.0.0` after the version-2 E2E gate passed;
  its endpoint serves immutable version 2 at 100%.
- V3 has no agent-memory tools or provisioning. The shared `project-memory`
  metadata/data remains untouched.
- Agent Application clientId: `4d7fd750-bf31-4c67-9e18-cea9d02fb205`
- Agent Application ID: `6942944a-3b19-4ecb-96f7-e4a9cfd4fbb6`
- Project endpoint: `https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo`
- Shared Foundry project with `salesforce-assistant` agent

### V3 production/Teams readiness (2026-11-01)

- Isolated manifest: `agents/servicenow-v3/azure.yaml`; never included in root `azure.yaml`.
- Additive agent: `servicenow-assistant-v3`, `kind: prompt`, Managed Harness
  `microsoft_copilot_preview`, model `gpt-5.4-pro`.
- Toolbox: `servicenow-itsm-v3`, one raw MCP tool using the existing
  `servicenow-obo-oauth2` OAuth2 backend connection. Both MCP hops use approval
  `never` because Teams cannot render MCP approval prompts. Every write still
  requires a subsequent explicit user confirmation of the exact preview.
- Skill: immutable `servicenow-itsm` version `"2"` on BOTH Toolbox and agent.
  Its deployed contents were inspected: before any write, preview table,
  target/operation/field values and wait for explicit yes. Agent instructions
  also prohibit "don't ask me", urgency or claimed permission from bypassing it.
- First-party Toolbox connection: `servicenow-itsm-v3-toolbox-v2`, RemoteTool /
  UserEntraToken, audience `https://ai.azure.com`. Target and metadata must match
  the exact deployed Toolbox version; never use an unversioned/latest endpoint.
- Identity resolution is read-only; writes need explicit user confirmation.
- Only the new V3 bot/Teams module was deployed; no legacy application/bot/agent,
  production route, root infrastructure or shared memory-store change. Endpoint
  selector/protocol/auth configuration was updated as approved and published
  Shared. Legacy bot adoption is superseded; Container Apps `memory` remains sizing.
- Live Toolbox `servicenow-itsm-v3` and active agent `servicenow-assistant-v3`
  are both version `"2"`; the new UserEntraToken connection has the exact
  version-2 Toolbox endpoint and metadata. Each new version was deployed once;
  one initial agent packaging failure created no version. Toolbox default pointer
  remains version `"1"` and is not used for binding. V1 agent/Toolbox/connection
  are retained for rollback.
- Mandatory inline flags: `AZURE_DEV_USER_AGENT=microsoft_foundry_skill`,
  `AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true`,
  `AZD_AGENT_SKIP_ROLE_ASSIGNMENTS=true`. Never use whole-project deployment or
  root provisioning/hooks for V3. Named prompt-agent deployment resolves
  `FOUNDRY_PROJECT_ENDPOINT`; `AZURE_AI_PROJECT_ENDPOINT` alone is insufficient.
- The initial bounded current-user attempt failed before tools: a project-scoped
  conversation was supplied to the agent-scoped Managed Harness endpoint. Provider
  code was not retained; this is not evidence of a ServiceNow backend failure.
  Its exact-owned project conversation was deleted.
- The coordinator's corrected agent-scoped diagnostic completed at
  `oauth_consent_request` for `servicenow_toolbox`, with zero tool calls and
  `errors: []`; its verified owned conversation was deleted. Backend read remains
  unverified until separately approved consent/tool approval. No consent, approval,
  manual permission grant, or account switch occurred in that initial check.
  Subsequent coordinator v1 E2E passed 4/4 after re-consent; this is historical
  evidence, not proof of the new Teams-ready version. V2 results are in the ledger.
- Runtime client must use `AIProjectClient(..., allow_preview=True)` and
  `project.get_openai_client(agent_name="servicenow-assistant-v3")` with
  `Foundry-Features: MicrosoftCopilot=V1Preview`, timeout 90 and zero automatic
  retries. A single coordinator-authorized v2 continuation uses timeout 300,
  without a retry loop. Conversation creation/invocation/inspection/cleanup must use that same
  agent-scoped client.
- Foundry generated managed per-agent Entra identity dependencies for the persistent
  V3 agent; these are not test artifacts and must not be removed during cleanup.
- Live inventory and sanitized contract: `agents/servicenow-v3/deployment-ledger.json`.
- V2 evidence: identity/read PASS; unconfirmed-write and adversarial guards PASS
  on one authorized continuation after consent restore, zero writes. The original
  guard was a legitimate no-op (urgency already High), not missing confirmation.
  Exactly one confirmed `INC0010007` was created and later verified state `7`
  Closed, close_code `Solution provided`, by the coordinator through agent v2
  using exact-sys_id preview/confirmation and read-back. Its conversation was deleted.
  All 9/9 child E2E conversations and the one tool-free publication check were
  deleted (10/10 child-owned this phase). Initial timeout/provider errors remain
  historical diagnostics, not current unresolved gates.
- Additive bot: `infra/modules/bot-service.bicep`, off by default; new
  `bot-servicenow-assistant-v3`, F0/global/SingleTenant, network access Disabled,
  Teams only. `msaAppId` must equal V3 `instance_identity.client_id`
  (`978e473a-34e4-4fe9-9521-0bd885576d53`); endpoint is
  `<project>/agents/servicenow-assistant-v3/endpoint/protocols/activityProtocol?api-version=v1`.
  Never adopt or modify the legacy bot or Teams package for V3.
- `publish_teams.py`: E2E-gated version-2 FixedRatio pin, create-only bot-scoped
  what-if/deploy, preserved Entra/Responses plus activity/BotServiceRbac, one
  bounded tool-free READY check, then Shared/autopilot-false/appVersion-1.0.0 publish.
  Persisted exact request/titleId prevents duplicate publication; unknown delivery
  blocks retries. Bot-only what-if passed exactly two new Creates (bot + Teams),
  no Modify/Delete. New bot module deployed once; publication completed once.
  `titleId`: `T_945978c8-7204-d12a-d120-90987255c58b`. Bot ARM ID:
  `/subscriptions/44026b8b-9f88-44d9-8f46-0898baa4bcd5/resourceGroups/rg-sf-mcp-obo/providers/Microsoft.BotService/botServices/bot-servicenow-assistant-v3`.
  Post-publish GET verified pinned v2, Responses/activity and exact Entra plus
  BotServiceRbac. User validates V3 before legacy Teams retirement.

### Foundry connection + provisioning invariants

- Connection `servicenow-obo-oauth2`: authType **OAuth2** (identity passthrough), shared Entra app `MCP_OAUTH_CLIENT_ID` (same as SF), scopes `offline_access` + `api://<appId>/access_as_user`, target `https://apim-sf-mcp-obo.azure-api.net/servicenow-mcp-obo/mcp`. Token audience is `api://<appId>`, accepted by the APIM SN policy via the SF-owned Named Value `McpOauthClientId`.
- NOT in Bicep. `hooks/postprovision.py::ensure_obo_connection()` is create-only (never deletes/overwrites), registers the connection redirect URI on the Entra app, and skips with a warning when `MCP_OAUTH_CLIENT_ID` / `MCP_OAUTH_CLIENT_SECRET` are missing.
- Legacy `servicenow-obo` (UserEntraToken) is dead since 2026-05-22: Foundry returns "Cannot pass Microsoft token to untrusted MCP endpoint" for custom MCP endpoints.
- `azd provision` keeps the live `ca-sn-mcp` image: `main.bicepparam` reads `SERVICE_SERVICENOW_MCP_IMAGE_NAME` into `servicenowMcpImageName` (helloworld placeholder only when empty).

## Continuous Evaluation (Foundry-native quality monitoring)

Enabled on `servicenow-assistant` in BOTH Foundry projects (2026-05-19). Passively scores every agent response.

| Project | Eval config ID | Eval group ID |
|---|---|---|
| `aiproj-customer-360` | `continuous-eval-servicenow-assistant-fe87301ecf26422d9ca97f4111` | `eval_f5b96862dd634d8f8a96c7b744b7e65c` |
| `aiproj-sf-mcp-obo`   | `continuous-eval-servicenow-assistant-cbb8aa4cdefd470db76d2d6d8e` | `eval_b38745a3f26749ccb6818ba3010e2d8f` |

**Evaluators** (quality, no safety): `task_completion`, `tool_call_accuracy`, `response_completeness`.
**Config**: `samplingRate=100`, `maxHourlyRuns=10`, `deploymentName=gpt-5.4`, `scenario=standard`, `enabled=true`.

**Read scores** via Azure MCP `foundry`:
```
evaluation_get with isRequestForRuns=true + evalId
```
Or in the Foundry portal → project → Evaluations → ContinuousEval-servicenow-assistant-*

**Known limitation**: passive — no traffic = no scores. First scores appear several minutes after the first agent response is logged in App Insights.

## Hibernation Prevention (dev330554)

PDIs auto-hibernate after ~10 days of inactivity. To keep dev330554 awake:

- **Script**: `scripts/keepalive_ping.py` — signs a JWT Bearer assertion (using `certs/sn-jwt-bearer.key` + `certs/sn-oauth-config.json`), exchanges it at `/oauth_token.do`, then calls Table API. Counts as authenticated activity.
- **Schedule**: Copilot CLI `manage_schedule` #1 — fires every 1 day, invokes the script. View/manage via `manage_schedule action=list|stop`.
- **Why every 1d (not 5d)**: Copilot's `manage_schedule` caps interval at `1d`. Daily is well under the 10-day hibernation window, so it's a safe upper bound.
- **Manual run**: `python scripts/keepalive_ping.py --quiet` from repo root.
- **If the schedule lapses** (machine off for 10+ days): instance hibernates → must be woken via developer.servicenow.com.

**Skipped evaluators (deliberate)**:
- `groundedness` / `groundedness_pro` — agent grounds in live Table API results, not retrievable docs
- `coherence` — gpt-5.4 saturates; low signal
- All safety evaluators (`violence`, `self_harm`, `hate_unfairness`, `indirect_attack`, etc.) — internal SN admin queries, near-zero risk surface

---

## Teams/Copilot Deployment

- Bot Service: `agent-bot-sn-mcp-obo` (SingleTenant, S1, created by postprovision Step 6)
- Bot endpoint: `https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo/applications/servicenow-assistant/protocols/activityprotocol?api-version=2025-11-15-preview`
- Channels: MsTeamsChannel + DirectLineChannel + WebChatChannel
- Teams manifest: `teams-app/servicenow-assistant.zip` (botId = Foundry managed identity)
- Postprovision Steps 5-7: Agent Deployment, Bot Service + Channels, Teams org catalog publish
- Graph API publish requires `AppCatalog.ReadWrite.All` (fallback: Teams Admin Center or AI Foundry portal)
- Status: WORKING in Teams/Copilot (verified 2026-03-26)

---

## Feasibility Test Results (2026-03-03)

| Test | Result |
|---|---|
| Certificate generation | PASS |
| Certificate upload via Table API | PASS |
| OAuth app creation via Table API | PASS (oauth_jwt table) |
| JWT Verifier Map creation | PASS (jwt_verifier_map table) |
| JWT Bearer token exchange | PASS |
| Table API read (incidents) | PASS |
| Identity propagation | PASS (sys_created_by = jwt.test) |
| Write record (create incident) | PASS |
| Table discovery (sys_db_object) | PASS |
| Describe table (sys_dictionary) | NEEDS_ROLE (403 with itil only) |

---

## Meta-Tool Pattern Mapping (Salesforce -> ServiceNow)

| SF Tool | SN Equivalent | SN API |
|---|---|---|
| `list_objects` | `list_tables` | `GET /api/now/table/sys_db_object` |
| `describe_object` | `describe_table` | `GET /api/now/table/sys_dictionary` + `sys_choice` |
| `soql_query` | `query_records` | `GET /api/now/table/{table}?sysparm_query=...` |
| `search_records` | `search_records` | Text Search API or `sysparm_query` with TEXTQUERY |
| `write_record` | `write_record` | `POST/PUT/PATCH/DELETE /api/now/table/{table}` |
| `process_approval` | `execute_flow` | `PUT sysapproval_approver` or Flow API |

---

## SAML 2.0 SSO (Browser Login)

### Overview

Azure AD Enterprise Application configured for SAML SSO with ServiceNow.
Complements the JWT Bearer OBO flow (API) with browser-based login + JIT user provisioning.

### Azure AD Configuration

- **Enterprise App:** "ServiceNow SSO - dev194081"
- **Identifier (Entity ID):** `https://dev194081.service-now.com`
- **Reply URL (ACS):** `https://dev194081.service-now.com/navpage.do`
- **Sign-on URL:** `https://dev194081.service-now.com/login_with_sso.do?glide_sso_id=<idp_sys_id>`
- **Logout URL:** `https://dev194081.service-now.com/navpage.do?logout`
- **SSO Mode:** `preferredSingleSignOnMode = "saml"`
- **Claims Mapping Policy:** Maps UPN, mail, givenname, surname to SAML claims

### SAML Claims

| Claim | Source | SN Field |
|---|---|---|
| NameID | user.userprincipalname | email (user matching) |
| emailaddress | user.mail | email |
| givenname | user.givenname | first_name |
| surname | user.surname | last_name |

### ServiceNow Configuration

- **Plugin:** `com.snc.integration.sso.multi` (Multi-Provider SSO)
- **IdP Name:** "Azure AD SAML SSO"
- **User field:** email (matches NameID to sys_user.email)
- **NameID Policy:** `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress`
- **JIT Provisioning:** Enabled (auto-create + auto-update users)
- **Default roles:** itil, personalize_dictionary

### Key Properties

| Property | Value |
|---|---|
| `glide.authenticate.multisso.enabled` | `true` |
| `glide.authenticate.sso.redirect.url` | `/login_with_sso.do?glide_sso_id=<idp_sys_id>` |

### Deployed Config

- **Azure App ID:** `3060c6e0-3cc1-42bc-a642-fffe966993e0`
- **Azure SP Object ID:** `c60b2451-ea99-4235-8567-2e6376b0d13c`
- **SAML Signing Cert Thumbprint:** `0F703D61E05C0A9B53AD4FBD75514A450CB19B5C`
- **Claims Mapping Policy:** `e84f0018-dd1c-4608-a356-d9e375bc869c`
- **SN IdP sys_id:** `af18aa09c3b332100607be1d050131f4` (created via UI metadata import)
- **SSO Login URL:** `https://dev194081.service-now.com/login_with_sso.do?glide_sso_id=af18aa09c3b332100607be1d050131f4`
- **Status:** WORKING (verified 2026-03-21)

### Setup Script (3-step process)

```bash
# Step 1: Azure AD + SN plugin activation + prints metadata URL
python scripts/setup_saml_sso.py \
  --instance https://<instance>.service-now.com \
  --admin-password <password>

# Step 2: MANUAL -- Import metadata URL in SN UI
#   Multi-Provider SSO > Identity Providers > New > Import URL
#   Also: Multi-Provider SSO > Administration > Properties > Enable

# Step 3: Configure the imported IdP via API
python scripts/setup_saml_sso.py \
  --instance https://<instance>.service-now.com \
  --admin-password <password> \
  --skip-azure --post-import
```

### SN Data Model (SSO)

```
sso_properties (parent: Identity Providers)
  +-- saml2_update1_properties (child: SAML 2.0 IdP config)
```

- Same inheritance pattern as `oauth_jwt` extends `oauth_entity`
- Certificate stored in `idp_certificate` table (FK to sso_properties)
- x509_certificate field on saml2_update1_properties auto-creates sys_certificate ref

### Constraints

- SSO is browser-only -- does NOT affect MCP API auth (JWT Bearer OBO via APIM)
- **SAML IdP records MUST be created via SN UI metadata import** -- API-created records fail with `idpConfig is null` (missing internal Java initialization)
- `sso_script` must be `MultiSSOv2_SAML2_internal` (not `MultiSSO_SAML2_Update1` which reads legacy properties)
- `issuer` field on IdP record = SP Entity ID (e.g., `https://dev194081.service-now.com`), NOT the IdP Entity ID
- PEM certificates must have 64-char line wrapping (Java PEM parser rejects single-line base64)
- `glide.authenticate.multisso.enabled` CANNOT be set via API (protected by Business Rule "Check ACR and SSO") -- must enable via SN UI
- Multi-Provider SSO plugin activatable via CICD API (`POST /api/sn_cicd/plugin/{id}/activate`)
- `v_plugin` table readable by admin (unlike `sys_plugins` which returns 403)
- `az ad app create --identifier-uris` rejects non-verified domains; use Graph API PATCH after SP creation to set `identifierUris`
- Local admin login remains available at `/login.do` (never disabled)
- SN dev instance hibernation may reset SSO config -- script is re-runnable

---

## Key Differences from Salesforce

| Aspect | Salesforce | ServiceNow |
|---|---|---|
| Query language | SOQL (SQL-like) | Encoded query strings |
| Schema API | `describe_global()` + `describe_object()` | `sys_db_object` + `sys_dictionary` tables |
| Admin JWT tokens | Allowed (pre-authorized) | Blocked by design |
| Certificate upload | Connected App metadata | `sys_certificate` table |
| OAuth config | Single Connected App | `oauth_jwt` + `jwt_verifier_map` (2 records) |
| User mapping field | `FederationIdentifier` | Configurable (`user_field`: email, user_name, etc.) |
| Client secret | Required | Optional (public_client mode) |
| Token caching | Not built-in | Not built-in (same) |
| CLI tooling | `sf` CLI (mature) | `snc` CLI (limited) -- use REST API directly |
