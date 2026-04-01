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

- Instance: https://dev194081.service-now.com
- OAuth Client ID: `d3d36c11036b4d1b94876ed27cbe81ae`
- JWT kid: `82a6615d905f4b0f8b46e936fcb4f43c`
- Test users: `jwt.test@snow-meta-tool.dev`, `ozgurkarahan@MngEnvMCAP549101.onmicrosoft.com`
- Both users have itil + personalize_dictionary roles

## Foundry Agent

- Agent: `servicenow-assistant` v7 (gpt-5.4)
- Tools: MCPTool (discover, query, write via `servicenow-obo` connection) + MemorySearchTool (project-memory, per-user scope)
- Agent Application clientId: `4d7fd750-bf31-4c67-9e18-cea9d02fb205`
- Agent Application ID: `6942944a-3b19-4ecb-96f7-e4a9cfd4fbb6`
- Project endpoint: `https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo`
- Shared Foundry project with `salesforce-assistant` agent

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
