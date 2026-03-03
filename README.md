# ServiceNow MCP Meta-Tool

> **Work in Progress** -- The MCP server (3 tools) is implemented and smoke-tested. Still missing: APIM OBO policy, IaC (Bicep), CI/CD pipeline. See [Project Status](#project-status) for details.

MCP server exposing ServiceNow as 3 generic tools (`discover`, `query`, `write`) with JWT Bearer identity propagation. Same architecture as `salesforce-meta-tool-id-prop` — an AI agent discovers tables and fields at runtime, then queries and writes records as the authenticated user.

## Architecture

```
Agent (Claude)
  |
  | MCP (Streamable HTTP)
  v
+----------------------------+
| servicenow-mcp (FastMCP)   |
| 3 tools: discover / query  |
|           / write           |
+----------------------------+
  |                    ^
  | Table API          | Bearer token
  v                    | (passthrough from APIM
+----------------+     |  or self-managed JWT)
| ServiceNow     |     |
| Instance       |  +------+
| (Table API)    |  | APIM |  <-- future
+----------------+  +------+
```

**Two auth modes:**
- **Passthrough (APIM):** Bearer token injected by Azure API Management via middleware `ContextVar`. No retry on 401.
- **Self-managed (local dev):** JWT Bearer exchange at `oauth_token.do` using RSA private key. Retries once on 401.

## Project Status

### Done

| Component | Status | Notes |
|---|---|---|
| JWT Bearer feasibility test | PASS | Token exchange + identity propagation verified (2026-03-03) |
| `servicenow_client.py` | DONE | Async httpx client, JWT auth, Table API, Stats API, 15-min schema caching |
| `app.py` | DONE | FastMCP server, 3 tools, BearerTokenMiddleware, health check |
| `requirements.txt` | DONE | mcp[http], httpx, uvicorn, cryptography, azure-monitor-opentelemetry |
| `Dockerfile` | DONE | Multi-stage build, non-root user, health check |
| Smoke test (local) | PASS | All 3 tools tested against dev instance (see below) |

### Smoke Test Results (2026-03-04)

| Test | Tool Call | Result |
|---|---|---|
| Health check | `GET /health` | PASS |
| MCP handshake | `initialize` | PASS |
| Table search | `discover(filter="incident")` | PASS -- found incident, incident_fact_table, incident_task |
| Field metadata | `discover(table="incident")` | 403 -- expected, needs `personalize_dictionary` role |
| Record query | `query(table="incident", fields="number,short_description,priority", limit=3)` | PASS -- 68 incidents returned with display_value + value |
| Text search | `query(table="incident", text_search="password")` | PASS |
| Aggregate | `query(table="incident", aggregate=True, group_by="priority")` | PASS -- 5 priority groups with counts |
| Create record | `write(table="incident", operation="create", ...)` | PASS -- identity propagation confirmed (`sys_created_by` = test user) |
| Update record | `write(table="incident", operation="update", ...)` | PASS -- short_description updated |
| Delete record | `write(table="incident", operation="delete", ...)` | 403 -- expected, `itil` role lacks delete ACL |

### Not Yet Done

| Component | Description |
|---|---|
| APIM OBO policy | Adapt Salesforce APIM policy for ServiceNow JWT Bearer token exchange |
| IaC (Bicep) | Container App, APIM, Key Vault for cert -- reuse modules from SF project |
| CI/CD | GitHub Actions pipeline for build + deploy |
| `personalize_dictionary` role | Grant to test user so `discover(table=...)` and write field validation work |
| Delete ACL | Grant delete permission (or accept as limitation for `itil` users) |

## ServiceNow Roles Required

The MCP tools depend on ServiceNow ACLs. The table below shows which roles are needed for each tool to work fully.

| Tool | Minimum Role | Full Feature Role | What Breaks Without It |
|---|---|---|---|
| `discover(filter=...)` | `itil` | `itil` | Nothing -- `sys_db_object` is readable |
| `discover(table=...)` | **`personalize_dictionary`** | `personalize_dictionary` | Returns 403 -- cannot read `sys_dictionary` for field metadata |
| `discover(include_choices=True)` | `itil` + `personalize_dictionary` | same | `sys_choice` needs read access (usually available), but `sys_dictionary` is the bottleneck |
| `query(...)` | `itil` | `itil` | Nothing -- reads whatever tables the user's role allows |
| `query(aggregate=True)` | `itil` | `itil` | Nothing -- Stats API follows same ACLs |
| `write(create/update)` | `itil` | `itil` + `personalize_dictionary` | Without `personalize_dictionary`: field name validation skipped (graceful fallback), Table API still validates server-side |
| `write(delete)` | Table-specific delete ACL | Varies by table | `itil` alone cannot delete incidents (403) |

### Role Summary

| Role | Purpose | Required For |
|---|---|---|
| `itil` | Base ITSM role -- read/write incidents, changes, problems, etc. | All basic operations |
| `personalize_dictionary` | Read `sys_dictionary` table (field metadata) | `discover(table=...)`, pre-flight field validation in `write` |
| `admin` | Full access | **BLOCKED** -- ServiceNow rejects JWT Bearer tokens for admin users |

### Recommended Role Assignment

For MCP users, assign: **`itil` + `personalize_dictionary`**

This enables all 3 tools including schema introspection. The server gracefully degrades if `personalize_dictionary` is missing (skips field validation, returns 403 on `discover(table=...)`).

## Tools

### `discover` -- Table & Field Discovery

Two modes:
- **Table search:** `discover(filter="incident")` -- search `sys_db_object` by name/label
- **Field metadata:** `discover(table="incident")` -- query `sys_dictionary` for field definitions

Optional: `include_choices=True` fetches picklist values from `sys_choice`.

### `query` -- Read Records, Search, Aggregate

Three modes:
- **Record query:** `query(table="incident", query="priority=1^state!=6", fields="number,short_description")` -- encoded query with auto-pagination
- **Text search:** `query(table="incident", text_search="password reset")` -- uses TEXTQUERY operator
- **Aggregate:** `query(table="incident", aggregate=True, group_by="priority")` -- counts/sums/averages via Stats API

All queries use `sysparm_display_value=all` so reference fields return both `sys_id` and human-readable names.

### `write` -- Create, Update, Delete

Three operations:
- **Create:** `write(table="incident", operation="create", field_values={...})`
- **Update:** `write(table="incident", operation="update", sys_id="...", field_values={...})`
- **Delete:** `write(table="incident", operation="delete", sys_id="...")`

Approvals are just table writes: update `sysapproval_approver` with `state=approved/rejected`.

## Quick Start (Local Dev)

### Prerequisites

- Python 3.12+
- RSA private key for JWT signing (in `certs/sn-jwt-bearer.key`)
- ServiceNow instance with OAuth JWT Bearer app configured

### Run

```bash
cd src/servicenow-mcp
pip install -r requirements.txt

export SN_INSTANCE_URL=https://<instance>.service-now.com
export SN_CLIENT_ID=<oauth_client_id>
export SN_JWT_KID=<kid_from_jwt_verifier_map>
export SN_JWT_KEY_PATH=../../certs/sn-jwt-bearer.key
export SN_JWT_SUB=<user_email>

python app.py
# Server starts on http://localhost:8000
```

### Docker

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

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SN_INSTANCE_URL` | Yes | -- | ServiceNow instance URL |
| `SN_CLIENT_ID` | Self-managed | -- | OAuth JWT Bearer client_id |
| `SN_JWT_KID` | Self-managed | -- | kid from `jwt_verifier_map` |
| `SN_JWT_KEY_PATH` | Self-managed | -- | Path to RSA private key (.pem) |
| `SN_JWT_SUB` | Self-managed | -- | JWT sub claim (user email) |
| `PORT` | No | `8000` | HTTP listen port |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | No | -- | Azure Monitor telemetry |

In passthrough mode (behind APIM), only `SN_INSTANCE_URL` is required. The bearer token comes from the `Authorization` header.

## Key Paths

| Path | Description |
|---|---|
| `src/servicenow-mcp/` | MCP server source code |
| `src/servicenow-mcp/app.py` | FastMCP server + 3 tools |
| `src/servicenow-mcp/servicenow_client.py` | Async ServiceNow REST client |
| `certs/` | RSA keys for JWT signing (gitignored) |
| `scripts/` | Setup & automation scripts |
| `infra/` | IaC (Bicep) -- not yet created |

## ServiceNow Instance Setup

Required configuration on the ServiceNow instance (done via Table API in feasibility test):

1. **X.509 Certificate** -- upload to `sys_certificate` (type: `trust_store_cert`)
2. **OAuth JWT App** -- create in `oauth_jwt` table with:
   - `inbound_grant_type = "jwt"`
   - `default_grant_type = "urn:ietf:params:oauth:grant-type:jwt-bearer"`
   - `public_client = true`
   - `user_field = "email"` (maps JWT `sub` to user)
3. **JWT Verifier Map** -- create in `jwt_verifier_map` table linking `kid` to certificate
4. **User** -- non-admin user with `itil` + `personalize_dictionary` roles

See `.claude/docs/project-reference.md` for detailed setup instructions and table schemas.
