# From Salesforce to ServiceNow: The Identity-Propagated Meta-Tool Pattern Goes Universal

In my [previous article](https://www.linkedin.com/pulse/from-theory-production-salesforce-meta-tools-identity-ozgur-karahan-ch30e/), I demonstrated how three meta-tools could replace hundreds of Salesforce-specific integrations while preserving end-to-end identity propagation. Two principles anchored that work:

1. **An agent should discover what it can do at runtime, not at build time.**
2. **Every action an agent takes must carry the real user's identity -- never a shared service account.**

The natural question was: *does this pattern generalize?*

The answer is yes. I rebuilt the entire system for **ServiceNow** -- and it took a fraction of the effort.

---

## Same Pattern, Different Giant

ServiceNow has over **4,000 tables** spanning ITSM, HR, SecOps, and more. The traditional integration approach would mean building and maintaining tool definitions for each table -- an exponential maintenance burden that doesn't scale.

Instead, the same three meta-tools handle everything:

| Tool | Purpose | How it works |
|------|---------|--------------|
| **discover** | Find tables and their schemas | Searches `sys_db_object` for tables, queries `sys_dictionary` for field metadata, fetches choice lists from `sys_choice` |
| **query** | Read and aggregate records | Encoded query syntax, auto-pagination, full-text search, Stats API for aggregations |
| **write** | Create, update, delete records | Pre-validates fields against schema, supports approvals workflow |

**Three tools. All of ServiceNow. Runtime discovery, not build-time configuration.**

The agent doesn't know about incidents, change requests, or catalog items at build time. It discovers them dynamically, reads the field definitions, and acts -- all while carrying the authenticated user's identity.

---

## The Double Authentication Story

What makes this implementation interesting is the **two distinct auth paths** that coexist:

### Path 1: Browser SSO (SAML 2.0)

When users access ServiceNow directly through their browser, they authenticate via **SAML 2.0 SSO** with Azure AD as the Identity Provider.

The flow:
1. User navigates to ServiceNow
2. ServiceNow redirects to Azure AD SAML endpoint
3. Azure AD authenticates the user (seamless with Windows SSO)
4. SAML assertion flows back to ServiceNow
5. **JIT Provisioning**: if the user doesn't exist in ServiceNow yet, their account is created automatically from the SAML claims

*[Screenshot: Azure AD "Pick an account" page -- ServiceNow redirected to Azure AD for SAML authentication]*

*[Screenshot: ServiceNow home page -- "Hello Ozgur!" -- SSO login successful, user session established]*

This is the **human** path. Users browse ServiceNow as themselves, see their dashboards, manage their incidents.

### Path 2: API Identity Propagation (JWT Bearer OBO)

When the AI agent acts on behalf of the user, a different mechanism kicks in -- but the result is the same: **ServiceNow sees the real user**.

The flow:
1. User authenticates to Azure AI Foundry (Azure AD token)
2. Agent sends request through Azure APIM
3. **APIM OBO Policy** intercepts the request:
   - Validates the Azure AD token
   - Extracts the user's email from JWT claims
   - Constructs a new RS256-signed JWT assertion with the user as `sub`
   - Exchanges it at ServiceNow's `oauth_token.do` endpoint
   - Caches the resulting ServiceNow token per-user (25-minute TTL)
4. Request reaches ServiceNow's Table API as the authenticated user

**The same user. Two paths. One audit trail.**

Every incident created by the agent, every approval action, every record update -- it all appears in ServiceNow's audit log under the real user's name. Not a service account. Not an integration user. The actual person who asked the agent to act.

---

## The Agent in Action

In **Azure AI Foundry**, the agent is configured with:
- **Model**: GPT-5.4
- **MCP Tool**: `servicenow-obo` -- pointing to our APIM endpoint
- **Memory**: per-user conversational memory (remembers past interactions and discovered schemas)

*[Screenshot: Foundry Agent Playground -- servicenow-assistant with instructions, MCP tool connection, and memory store]*

When a user asks "What ServiceNow tables are available for incident management?", the agent:

1. Checks **memory** for previously discovered schemas
2. Calls **discover(filter="incident")** via MCP -- which flows through APIM, exchanges the user's token, and queries ServiceNow
3. Returns a structured response with table names, labels, and descriptions

*[Screenshot: Agent response showing discovered incident tables -- all data fetched as the authenticated user]*

The metadata bar tells the story: `servicenow_mcp` + `memory_search_call` -- the agent orchestrated multiple tools, all authenticated as the real user.

---

## From One to Many: The Repeatable Pattern

Here's what the Salesforce and ServiceNow implementations share:

| Component | Salesforce | ServiceNow |
|-----------|-----------|-------------|
| Meta-tools | discover, query, write (6 tools total) | discover, query, write (3 tools) |
| Token exchange | JWT Bearer (RS256) via APIM | JWT Bearer (RS256) via APIM |
| User mapping | Azure AD email -> SF username | Azure AD email -> SN user email |
| Schema discovery | Describe API | sys_dictionary + sys_db_object |
| Caching | Token + metadata | Token (25 min) + schema (15 min) |
| Infrastructure | Container App + APIM + Key Vault | Container App + APIM + Key Vault |
| Deployment | `azd up` + postprovision hook | `azd up` + postprovision hook |
| Surface | Teams + Web | Teams + Web |

The **infrastructure** is identical. The **pattern** is identical. Only the **SaaS-specific details** change -- the token endpoint URL, the metadata API, the query syntax.

This is the key insight: **identity-propagated meta-tools are a pattern, not a product**. Any SaaS with a REST API, a table/object model, and an OAuth token exchange can be integrated this way.

---

## What's Different About ServiceNow

A few ServiceNow-specific details that made this implementation unique:

1. **SAML SSO requires UI creation**: ServiceNow's SAML Identity Provider records *must* be created through the UI metadata import dialog. API-created records fail silently during SAML response validation -- a Java initialization step is skipped. This was a hard-won lesson.

2. **Admin users are blocked from JWT Bearer**: ServiceNow explicitly blocks admin users from the JWT Bearer flow. A deliberate security decision that forces proper RBAC separation.

3. **Encoded query syntax**: ServiceNow uses its own query language (`priority=1^state!=6^ORDERBYDESCsys_created_on`) rather than SOQL or SQL. The agent learns this through its instructions and applies it at runtime.

4. **Graceful degradation**: If the user lacks the `personalize_dictionary` role for schema access, the agent falls back to well-known standard fields and lets the Table API validate. It doesn't fail -- it adapts.

---

## The Deployment is Fully Automated

One `azd up` command provisions everything. The postprovision hook handles 8 steps:

0. Upload JWT signing certificate to Key Vault + create APIM cert binding
1. Configure APIM Named Values for ServiceNow connection parameters
2. Create Foundry OBO connection (RemoteTool, UserEntraToken)
3. Create/version the agent with MCP tools + conversational memory
4. Provision Agent Application (managed identity)
5. Create Agent Deployment with Activity Protocol
6. Bootstrap Bot Service + Teams/DirectLine channels
7. Publish Teams app to org catalog

From zero to a working agent in Teams -- fully automated, fully auditable.

---

## What This Proves

Two articles. Two enterprise SaaS platforms. The same three principles hold:

1. **Meta-tools scale where specific tools don't.** Three tools cover 4,000+ ServiceNow tables. The same pattern covered all of Salesforce's objects.

2. **Identity propagation is non-negotiable.** In regulated environments (SOX, HIPAA, ITIL), every agent action must trace back to a real person. The OBO pattern makes this possible without sacrificing the agent's capabilities.

3. **The pattern is portable.** Swap the SaaS-specific details -- token endpoint, metadata API, query syntax -- and the architecture transfers wholesale. SAP, Workday, Jira, Dynamics -- they're all candidates.

The code is open-source: [ServiceNow MCP Meta-Tool](https://github.com/ozgurkarahan/snow-meta-tool) | [Salesforce MCP Meta-Tool](https://github.com/ozgurkarahan/salesforce-meta-tool-id-prop)

**What enterprise system would you connect next?**

---

#AI #EnterpriseAI #AzureAIFoundry #ServiceNow #Azure #MCP #AIAgents #IdentityPropagation #SAML #SSO #ITSM
