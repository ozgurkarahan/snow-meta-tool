<p align="center">
  <img src="https://img.shields.io/badge/MCP-Streamable_HTTP-blue?style=for-the-badge" alt="MCP">
  <img src="https://img.shields.io/badge/ServiceNow-Table_API-6DB33F?style=for-the-badge" alt="ServiceNow">
  <img src="https://img.shields.io/badge/Azure-APIM_OBO-0078D4?style=for-the-badge" alt="Azure APIM">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

# 🔧 ServiceNow MCP Meta-Tool

MCP server exposing ServiceNow as **3 generic tools** (`discover`, `query`, `write`) with **per-user identity propagation**. An AI agent discovers tables and fields at runtime, then queries and writes records **as the authenticated user** — no service account, no shared credentials.

---

## 💡 Why a Meta-Tool?

Most MCP integrations build **one tool per API endpoint** — `list_incidents`, `create_change_request`, `get_user`, etc. This creates a combinatorial explosion: ServiceNow has **4,000+ tables**, each with dozens of fields. You'd need thousands of tools, and the agent would struggle to pick the right one.

The meta-tool pattern flips this: **3 generic tools cover the entire ServiceNow data model.** The agent discovers the schema at runtime and constructs queries dynamically:

| ❌ Traditional Approach | ✅ Meta-Tool Approach |
|---|---|
| `list_incidents(priority, state)` | `query(table="incident", query="priority=1^state=2")` |
| `create_change_request(title, ...)` | `write(table="change_request", operation="create", ...)` |
| `get_incident_fields()` | `discover(table="incident")` |
| 100+ hardcoded tools | **3 tools for any table** |

> 🧠 This works because LLMs are good at composing queries from natural language — they don't need pre-built tool signatures for every table.

---

## 🔐 Why Identity Propagation?

Most AI integrations use a **shared service account** — every user's actions appear as "API Bot" in the audit log. This creates three problems:

| | Problem | Impact |
|---|---|---|
| 👤 | **No accountability** | Can't tell who did what — audit trail shows the service account, not the user |
| 🔓 | **Over-privileged access** | Service account needs permissions for everything any user might do |
| ⚖️ | **Compliance risk** | SOX, HIPAA, ITIL require actions traceable to individual users |

This project solves all three with **On-Behalf-Of (OBO) identity propagation:**

```
User "alice@company.com" asks agent: "Create a P2 incident for the network outage"

❌ Traditional:  ServiceNow sees → sys_created_by: "api_service_account"
✅ This project: ServiceNow sees → sys_created_by: "alice@company.com"
```

> 🛡️ The APIM policy exchanges the user's Azure AD token for a ServiceNow token scoped to that specific user. Every API call runs with that user's permissions and appears in the audit log under their name.

---

## 🏗️ Architecture

```
                          Azure AD
                            │
                            │ Bearer token (user identity)
                            ▼
                    ┌───────────────┐
                    │  Azure APIM   │
                    │  OBO Policy   │
                    └───────┬───────┘
                            │
           1. Validate Azure AD token
           2. Extract user email (preferred_username / upn)
           3. Build JWT Bearer assertion (RS256, per-user sub)
           4. Exchange at ServiceNow oauth_token.do
           5. Cache SN token per user (25 min)
           6. Forward request with SN Bearer token
                            │
                            ▼
                ┌────────────────────────┐
                │  servicenow-mcp        │
                │  (Container App)       │
                │                        │
                │  FastMCP + 3 tools:    │
                │  🔍 discover           │
                │  📊 query              │
                │  ✏️ write              │
                └────────┬───────────────┘
                         │
                         │ ServiceNow Table API
                         │ (Bearer token per user)
                         ▼
                ┌──────────────────┐
                │  ServiceNow      │
                │  Instance        │
                └──────────────────┘
```

**🔄 Identity flow:** Azure AD user → APIM extracts email from JWT → builds ServiceNow JWT Bearer assertion with `sub=email` → exchanges for ServiceNow access token → all API calls run as that ServiceNow user.

**Two auth modes:**
- 🌐 **Passthrough (APIM):** Bearer token injected by Azure API Management. The APIM policy handles the Azure AD → ServiceNow token exchange. No secrets on the MCP server.
- 💻 **Self-managed (local dev):** JWT Bearer exchange at `oauth_token.do` using a local RSA private key. Useful for development and testing without APIM.

---

## 🛠️ Tools

### 🔍 `discover` — Table & Field Discovery

Two modes:
- **Table search:** `discover(filter="incident")` — search `sys_db_object` by name/label
- **Field metadata:** `discover(table="incident")` — query `sys_dictionary` for field definitions
- **+ Picklists:** `discover(table="incident", include_choices=True)` — adds choice values from `sys_choice`

### 📊 `query` — Read Records, Search, Aggregate

Three modes:
- **Record query:** `query(table="incident", query="priority=1^state!=6", fields="number,short_description")` — encoded query with auto-pagination
- **Text search:** `query(table="incident", text_search="password reset")` — full-text search via TEXTQUERY operator
- **Aggregate:** `query(table="incident", aggregate=True, group_by="priority")` — counts/sums/averages via Stats API

### ✏️ `write` — Create, Update, Delete

Three operations:
- **Create:** `write(table="incident", operation="create", field_values={...})`
- **Update:** `write(table="incident", operation="update", sys_id="...", field_values={...})`
- **Delete:** `write(table="incident", operation="delete", sys_id="...")`

> 📋 **Approvals** are just table writes: update `sysapproval_approver` with `state=approved/rejected`.

---

## 🚀 Quick Start

### 📋 Prerequisites

- 🐍 Python 3.12+
- 🔑 RSA key pair for JWT signing
- ☁️ ServiceNow instance with OAuth JWT Bearer app configured ([setup guide](#-servicenow-instance-setup))

### 💻 Local Development

```bash
cd src/servicenow-mcp
pip install -r requirements.txt

export SN_INSTANCE_URL=https://<instance>.service-now.com
export SN_CLIENT_ID=<oauth_client_id>
export SN_JWT_KID=<kid_from_jwt_verifier_map>
export SN_JWT_KEY_PATH=../../certs/sn-jwt-bearer.key
export SN_JWT_SUB=<user_email>

python app.py
# 🟢 Server starts on http://localhost:8000
# Health check: GET /health
# MCP endpoint: POST /mcp
```

### 🐳 Docker

```bash
cd src/servicenow-mcp
docker build -t servicenow-mcp .
docker run -p 8000:8000 \
  -e SN_INSTANCE_URL=https://<instance>.service-now.com \
  -e SN_CLIENT_ID=<client_id> \
  -e SN_JWT_KID=<kid> \
  -e SN_JWT_KEY_PATH=/app/certs/key.pem \
  -e SN_JWT_SUB=<user_email> \
  -v ./certs:/app/certs:ro \
  servicenow-mcp
```

### ☁️ Deploy to Azure

```bash
# Generate PFX from existing key + cert
openssl pkcs12 -export -out certs/sn-jwt-bearer.pfx \
  -inkey certs/sn-jwt-bearer.key -in certs/sn-jwt-bearer.crt -passout pass:

# Initialize and configure
azd init
azd env set SN_INSTANCE_URL "https://<instance>.service-now.com"
azd env set SN_OAUTH_CLIENT_ID "<client_id>"
azd env set SN_JWT_BEARER_KID "<kid>"
azd env set AZURE_RESOURCE_GROUP "<existing-resource-group>"
# Set shared resource names (APIM, ACR, KV, CAE, etc.)

# 🚀 Provision infrastructure + deploy
azd up
```

> The `azd provision` step creates: Container App, APIM API + backend + OBO policy, Named Values, Key Vault cert, and AI Foundry connection. The post-provision hook uploads the PFX certificate to Key Vault and configures the APIM certificate binding.

---

## ⚙️ Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SN_INSTANCE_URL` | ✅ Yes | — | ServiceNow instance URL |
| `SN_CLIENT_ID` | 💻 Self-managed | — | OAuth JWT Bearer client_id |
| `SN_JWT_KID` | 💻 Self-managed | — | kid from `jwt_verifier_map` |
| `SN_JWT_KEY_PATH` | 💻 Self-managed | — | Path to RSA private key (.pem) |
| `SN_JWT_SUB` | 💻 Self-managed | — | JWT sub claim (user email) |
| `PORT` | ❌ No | `8000` | HTTP listen port |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | ❌ No | — | Azure Monitor telemetry |

> In passthrough mode (behind APIM), only `SN_INSTANCE_URL` is required. The bearer token comes from the APIM policy via the `Authorization` header.

---

## 📁 Key Paths

| Path | Description |
|---|---|
| 📄 `src/servicenow-mcp/app.py` | FastMCP server + 3 tools + middleware |
| 📄 `src/servicenow-mcp/servicenow_client.py` | Async ServiceNow REST client (JWT auth, caching) |
| 🐳 `src/servicenow-mcp/Dockerfile` | Multi-stage Docker build |
| 🏗️ `infra/main.bicep` | Root IaC module (resource-group scoped) |
| 🏗️ `infra/modules/` | Bicep modules: Container App, APIM API, cert, Foundry connection |
| 📜 `infra/policies/` | APIM XML policies: OBO token exchange, PRM metadata |
| 🔧 `hooks/postprovision.py` | Post-deploy: cert upload, APIM binding, Named Values, Foundry |
| 🧪 `scripts/test_jwt_bearer.py` | Automated SN instance setup + feasibility test |
| 🔑 `certs/` | RSA keys for JWT signing (gitignored) |

---

## 🏢 Infrastructure

The project deploys into an **existing** resource group alongside the Salesforce MCP tool, sharing:

| Resource | Shared With |
|---|---|
| 🌐 Azure API Management (APIM) | SF MCP |
| 📦 Container Apps Environment | SF MCP |
| 🐳 Azure Container Registry (ACR) | SF MCP |
| 🔑 Key Vault | SF MCP |
| 📊 Application Insights | SF MCP |
| 🤖 AI Foundry project | SF MCP |

**SN-specific resources** created by `azd provision`:
- 📦 **Container App** (`ca-sn-mcp`) — runs the FastMCP server
- 🌐 **APIM API** (`servicenow-mcp-obo`) — native MCP type with OBO policy
- 🏷️ **APIM Named Values** — SN OAuth client ID, instance URL, JWT kid, cert thumbprint
- 🔐 **APIM Certificate** — JWT Bearer signing cert (from Key Vault)
- 🤖 **Foundry Connection** (`servicenow-obo`) — RemoteTool with UserEntraToken auth

### 🔄 APIM OBO Policy

The APIM policy (`infra/policies/sn-mcp-obo-policy.xml`) handles the full token exchange:

| Step | Action |
|---|---|
| 1️⃣ | **Validate** Azure AD token (v1 + v2 issuers, audience: `https://ai.azure.com`) |
| 2️⃣ | **Extract** user email from `preferred_username` / `upn` / `unique_name` claims |
| 3️⃣ | **Build** RS256 JWT Bearer assertion with `sub=email`, `kid`, unique `jti` |
| 4️⃣ | **Exchange** assertion at ServiceNow `oauth_token.do` for a user-scoped access token |
| 5️⃣ | **Cache** the ServiceNow token per user for 25 min (5-min safety margin on 30-min lifetime) |
| 6️⃣ | **Forward** the request with the ServiceNow Bearer token + `X-User-Email` header |

> 📡 A separate PRM endpoint (`/.well-known/oauth-protected-resource`) advertises Azure AD as the authorization server per RFC 9728.

---

## ☁️ ServiceNow Instance Setup

Required configuration on the ServiceNow instance:

| Step | What | Details |
|---|---|---|
| 1️⃣ | 🔐 **X.509 Certificate** | Upload to `sys_certificate` (type: `trust_store_cert`) |
| 2️⃣ | 🔑 **OAuth JWT App** | Create in `oauth_jwt` with `inbound_grant_type="jwt"`, `public_client=true`, `user_field="email"` |
| 3️⃣ | 🗺️ **JWT Verifier Map** | Create in `jwt_verifier_map` linking `kid` to certificate |
| 4️⃣ | 👤 **User** | Non-admin user with `itil` + `personalize_dictionary` roles |

> 🤖 The `scripts/test_jwt_bearer.py` script **automates all of this:**
> ```bash
> python scripts/test_jwt_bearer.py \
>   --instance https://<instance>.service-now.com \
>   --admin-password "<admin_password>"
> ```

### 👥 ServiceNow Roles

| Role | Purpose | Required For |
|---|---|---|
| 🟢 `itil` | Base ITSM role — read/write incidents, changes, problems | All basic operations |
| 🟡 `personalize_dictionary` | Read `sys_dictionary` table (field metadata) | `discover(table=...)`, field validation in `write` |
| 🔴 `admin` | Full access | **BLOCKED** — ServiceNow rejects JWT Bearer for admin users |

> 💡 **Recommended:** Assign `itil` + `personalize_dictionary` to MCP users. The server gracefully degrades if `personalize_dictionary` is missing.

---

## 📖 ServiceNow Encoded Query Syntax

ServiceNow uses encoded query strings (not SQL). Key operators:

| Operator | Syntax | Example |
|---|---|---|
| Equals | `field=value` | `priority=1` |
| Not equals | `field!=value` | `state!=6` |
| Contains | `fieldLIKEvalue` | `short_descriptionLIKEpassword` |
| Starts with | `fieldSTARTSWITHvalue` | `numberSTARTSWITHINC` |
| Greater than | `field>value` | `priority>2` |
| In list | `fieldINvalue1,value2` | `priorityIN1,2,3` |
| Empty | `fieldISEMPTY` | `assigned_toISEMPTY` |
| AND | `^` | `priority=1^state=2` |
| OR | `^OR` | `priority=1^ORpriority=2` |
| Order by | `^ORDERBYfield` | `^ORDERBYDESCsys_created_on` |

---

## 🧪 Testing & Demo

| Document | Description |
|---|---|
| 📋 [TEST_PROMPTS.md](TEST_PROMPTS.md) | 10 business scenarios with 40+ agent prompts — service desk triage, change management, major incidents, executive dashboards, and Salesforce CRM + ServiceNow ITSM joint workflows |
| 🎯 [DEMO_DATA_SETUP.md](DEMO_DATA_SETUP.md) | Step-by-step guide to populate both Salesforce and ServiceNow with correlated demo data for cross-platform scenarios |

---

## 📄 License

MIT
