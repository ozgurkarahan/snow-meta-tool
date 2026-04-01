# Lessons Learned

Project-specific debugging history and corrections. Update after every mistake or discovery.
Workflow rules live in `~/.ai/workflow.md` (global) -- do NOT duplicate them here.

---

## Project-Specific Lessons

### 2026-03-03 -- ServiceNow OAuth JWT Bearer Setup via Table API

**Mistake:** Created OAuth app in `oauth_entity` table (got type=`client`), used wrong table `oauth_entity_jwt_verifier` for JWT Verifier Map, missed `inbound_grant_type` field.

**Root cause:** ServiceNow uses table inheritance (`oauth_jwt` extends `oauth_entity`). JWT-specific fields live on the child table. JWT Verifier Map table has a different name than expected.

**Rule:**
- Always create OAuth JWT Bearer apps in `oauth_jwt` table (not `oauth_entity`)
- Always set `inbound_grant_type = "jwt"` and `default_grant_type = "urn:ietf:params:oauth:grant-type:jwt-bearer"`
- JWT Verifier Map table = `jwt_verifier_map`, FK field = `oauth_jwt`
- Use `public_client = true` (client_secret is encrypted/unreadable via Table API)

### 2026-03-03 -- ServiceNow Admin Users Blocked from JWT Bearer

**Mistake:** N/A (discovered proactively).

**Root cause:** ServiceNow explicitly blocks JWT Bearer tokens for users with the `admin` role (returns `invalid_grant`).

**Rule:** Always create non-admin test/service users for JWT Bearer flows.

### 2026-03-03 -- sys_dictionary Requires Role Beyond itil

**Mistake:** `describe_table` test returned 403 for user with only `itil` role.

**Root cause:** `sys_dictionary` table has ACL restrictions; `itil` role doesn't include dictionary read access.

**Rule:** Users needing schema introspection (describe_table) require `personalize_dictionary` role or equivalent. Alternative: use service account for metadata queries.

### 2026-03-04 -- Write Tool Field Validation Must Handle sys_dictionary 403

**Mistake:** `write` tool called `describe_fields()` for pre-flight field validation, but this hits `sys_dictionary` which returns 403 for users without `personalize_dictionary` role. Write tool failed even though the actual Table API write would have succeeded.

**Root cause:** Pre-flight field name validation depends on schema access that the user may not have.

**Rule:** Wrap `describe_fields()` call in write tool with try/except for 403. If schema access is denied, skip validation and let the Table API itself validate field names. Log the skip for debugging.

### 2026-03-18 -- APIM XML Policy Double-Hyphen in Comments

**Mistake:** APIM policy XML had `--` inside XML comments (e.g., `preferred_username -- no intermediate`). Deployment failed with `An XML comment cannot contain '--'`.

**Root cause:** XML spec forbids `--` anywhere inside a comment body (`<!-- ... -->`). APIM's XML parser enforces this strictly.

**Rule:** Never use `--` inside XML comments in APIM policies. Use `;` or `,` or rephrase instead.

### 2026-03-18 -- APIM Named Values Must Exist Before Policy References Them

**Mistake:** `SnJwtBearerCertThumbprint` Named Value was conditionally created (`if (!empty(thumbprint))`) but the policy always referenced `{{SnJwtBearerCertThumbprint}}`. First deploy (no thumbprint yet) failed.

**Root cause:** APIM validates Named Value references at policy deployment time. If the NV doesn't exist, deployment fails even if the code path is never reached at runtime.

**Rule:** If an APIM policy references `{{NV}}`, the Named Value MUST exist before the policy is deployed. Use placeholder values with unconditional creation, not conditional Bicep resources.

### 2026-03-18 -- Azure AD v1 Tokens Use `upn` Not `preferred_username`

**Mistake:** APIM policy extracted only `preferred_username` claim from Azure AD JWT. Tokens from `az account get-access-token` are v1 tokens (issuer: `sts.windows.net`) which use `upn` instead.

**Root cause:** Azure AD v1 and v2 tokens have different claim names for the user email. `preferred_username` is v2-only; v1 uses `upn` and `unique_name`.

**Rule:** Always check claims in this fallback order: `preferred_username` -> `upn` -> `unique_name`. This handles both v1 and v2 tokens.

### 2026-03-18 -- azd resourceGroup in azure.yaml Insufficient for --no-prompt

**Mistake:** `azure.yaml` had `infra.resourceGroup: rg-sf-mcp-obo` but `azd provision --no-prompt` still prompted for resource group selection.

**Root cause:** `azd` requires `AZURE_RESOURCE_GROUP` env var when using `--no-prompt`, even if `azure.yaml` specifies the resource group.

**Rule:** Always `azd env set AZURE_RESOURCE_GROUP <name>` when deploying with `--no-prompt` to an existing resource group.

### 2026-03-18 -- ServiceNow Dev Instance Hibernation

**Discovery:** SN dev instances hibernate after ~10 days of inactivity and may lose OAuth apps, certs, JWT verifier maps, and user configurations.

**Rule:** Before testing JWT Bearer flows against a SN dev instance, verify the instance is awake and OAuth configuration still exists. Re-run the feasibility test script if instance was likely hibernated.

### 2026-03-18 -- Foundry Agent Reuses Shared Project Resources

**Discovery:** The `project-memory` store is shared across agents in the same Foundry project. When creating `servicenow-assistant` in the same project as `salesforce-assistant`, the memory store already existed — no need to recreate.

**Rule:** `create_memory_store()` should always be idempotent (get-or-create). The `user_profile_details` field is set at store creation time and won't update on subsequent calls — if it needs to change, the store must be deleted and recreated.

### 2026-03-18 -- AI_FOUNDRY_PROJECT_ENDPOINT Not Set by Bicep

**Discovery:** `AI_FOUNDRY_PROJECT_ENDPOINT` is not automatically set by `azd provision` or Bicep outputs. It must be manually set via `azd env set` with the format: `https://{account}.services.ai.azure.com/api/projects/{project}`.

**Rule:** Before running agent creation steps, ensure `AI_FOUNDRY_PROJECT_ENDPOINT` is set. Can derive it from `COGNITIVE_ACCOUNT_NAME` and `AI_FOUNDRY_PROJECT_NAME` if needed.

### 2026-03-18 -- sys_choice Table 403 for OBO Users

**Discovery:** When the `discover` tool fetches field choices via `sys_choice` table, some fields return 403 for OBO users even with `personalize_dictionary` role. The `business_stc` field on `incident` was one example.

**Rule:** The MCP server handles this gracefully (skips choices for that field). Not all `sys_choice` records are accessible to all roles — some are restricted by field-level ACLs. This is a ServiceNow-side issue, not a token/auth problem.

### 2026-03-19 -- Single sys_choice 403 Killed Entire Discover Call

**Mistake:** The `discover(table=..., include_choices=True)` tool had no try/except around per-field `get_choices()` calls. A 403 on `sys_choice` for one field (e.g., `business_stc`) raised `HTTPStatusError` that bubbled up to the tool's catch-all handler, discarding all successfully-fetched field metadata from `sys_dictionary`.

**Root cause:** The per-field choice loop (`app.py` lines 207-211) lacked individual error handling. The outer `except HTTPStatusError` treated the choices error as a total tool failure.

**Rule:** Always wrap per-item API calls in loops with individual try/except. A failure on one item should not discard results from prior successful items. Return partial results with metadata about what was skipped (`choices_skipped`, `choices_note`).

### 2026-03-19 -- Write Tool Field Validation vs Row-Level ACLs

**Mistake:** The `write` tool's field validation compared submitted fields against `sys_dictionary` results, but `sys_dictionary` can return 200 OK with partial results when row-level ACLs filter out field definitions. Standard fields like `priority` and `short_description` were flagged as "Invalid field names" because they were missing from the ACL-filtered response.

**Root cause:** The 403 handler only caught table-level denial. Row-level ACL filtering returns 200 with incomplete data — the validation treated all submitted fields not in the partial list as invalid.

**Rule:** Never hard-fail on field validation when the schema source may be incomplete. Log a warning and let the target API validate instead. The Table API uses record-level ACLs that may grant access even when `sys_dictionary` row ACLs don't.

### 2026-03-19 -- Agent Instructions Need Error Recovery Paths

**Mistake:** Agent instructions said "ALWAYS call discover(table=...) before writes" with no fallback. When discover failed (even partially), the agent refused to proceed and told the user it couldn't create the record.

**Root cause:** Hard prerequisite rules without escape hatches cause the agent to give up on recoverable errors.

**Rule:** Agent instructions for MCP tools should always include an "Error recovery" section with fallback behavior for common failure modes (403, partial results, timeouts). For ServiceNow: allow fallback to well-known standard fields when discover fails.

### 2026-03-19 -- ServiceNow Priority Auto-Calculation

**Discovery:** ServiceNow auto-calculates `priority` from `impact × urgency` via a business rule. Setting `priority=3` directly on create was overridden to `priority=5` because the default `impact=3, urgency=3` maps to `priority=5` in the priority lookup matrix.

**Rule:** To set a specific priority on incident creation, set `impact` and `urgency` instead (or in addition). The priority matrix varies by instance configuration.

### 2026-03-19 -- ServiceNow SSO Data Model (saml2_update1_properties)

**Discovery:** Multi-Provider SSO plugin uses table inheritance for SAML IdP config:
- `sso_properties` (parent) -- just `name`, `user_field`, `active`, etc.
- `saml2_update1_properties` (child) -- all SAML fields (`issuer`, `idp`, `x509_certificate`, `auto_provision`, etc.)

**Mistake:** Initially tried to create IdP record in `sys_auth_profile` which only has `name` + `sys_id` fields. SAML config was silently ignored.

**Rule:** Create SAML IdP records in `saml2_update1_properties` (same pattern as `oauth_jwt` extends `oauth_entity`). The parent `sso_properties` record is auto-created via table inheritance.

### 2026-03-19 -- glide.authenticate.multisso.enabled Protected by Business Rule

**Discovery:** `glide.authenticate.multisso.enabled` system property is protected by Business Rule "Check ACR and SSO". Attempts to update via Table API return 403.

**Rule:** This property must be enabled through the ServiceNow UI: Multi-Provider SSO > Administration > Properties. Cannot be automated via API.

### 2026-03-19 -- az ad app create Rejects Non-Verified Domains for identifierUris

**Mistake:** `az ad app create --identifier-uris "https://dev194081.service-now.com"` failed because `service-now.com` is not a verified domain in our Azure AD tenant.

**Rule:** Create the app WITHOUT `--identifier-uris`, create the SP, set `preferredSingleSignOnMode = "saml"` on the SP, THEN use Graph API `PATCH /applications/{id}` to set `identifierUris`. The Graph API allows non-verified domains for SAML apps after the SP is configured for SAML.

### 2026-03-19 -- SN Plugin Activation via CICD API

**Discovery:** Plugin activation can be triggered via `POST /api/sn_cicd/plugin/{plugin_id}/activate`. Progress is tracked via `GET /api/sn_cicd/progress/{id}`. Takes 3-5 minutes on dev instances.

**Rule:** Use `v_plugin` table (not `sys_plugins`) to check plugin status -- `sys_plugins` returns 403 for admin users. CICD API is available on dev instances and handles activation asynchronously.

### 2026-03-21 -- SAML IdP Records MUST Be Created via UI Metadata Import

**Mistake:** Created `saml2_update1_properties` records via Table API. The records looked correct (all fields populated, cert referenced) but the Java `SAML2IdpConfig` object was always `null` during SAML response validation, causing silent authentication failures.

**Root cause:** ServiceNow's Java SAML framework requires internal initialization that only happens when the IdP record is created through the SN UI's "Import Identity Provider Metadata" dialog. API-created records are missing this initialization (likely Java-level Business Rules or internal state setup that the Table API doesn't trigger).

**Rule:** NEVER create SAML IdP records (`saml2_update1_properties`) via the Table API. Always use the SN UI:
1. Navigate to Multi-Provider SSO > Identity Providers > New
2. Use the "Import Identity Provider Metadata" popup (URL or XML)
3. Then update additional fields (name, NameID policy, user_field, etc.) via API

### 2026-03-21 -- SAML SSO Script Must Be MultiSSOv2_SAML2_internal

**Mistake:** Initially set `sso_script` to `MultiSSO_SAML2_Update1` which reads from legacy system properties (e.g., `idp.ssocircle.com`), not from the per-IdP record.

**Root cause:** `MultiSSO_SAML2_Update1` is the legacy single-provider script. `MultiSSOv2_SAML2_internal` is the Multi-Provider SSO v2 script that reads config from the per-IdP GlideRecord.

**Rule:** For Multi-Provider SSO, always use `MultiSSOv2_SAML2_internal` (sys_id: `055e19b20b21230001d36c4d37673ae9`) as the `sso_script`.

### 2026-03-21 -- SAML IdP issuer Field = SP Entity ID (Not IdP Entity ID)

**Mistake:** Set the `issuer` field on the SN IdP record to the IdP's entity ID (`https://sts.windows.net/{tenant}/`). Azure AD returned error AADSTS700016 because it couldn't find an app with that identifier.

**Root cause:** In SN's `saml2_update1_properties`, the `issuer` field is the **SP Entity ID** (what ServiceNow sends as its own identifier in the AuthnRequest), NOT the IdP's entity ID.

**Rule:** Set `issuer` = SP Entity ID (e.g., `https://dev194081.service-now.com`), which must match `identifierUris` in Azure AD.

### 2026-03-21 -- PEM Certificates Need 64-Char Line Wrapping

**Mistake:** Stored X.509 certificates in `sys_certificate.pem_certificate` with all base64 on a single line. SN's Java SAML handler silently failed signature validation.

**Root cause:** Java's PEM parser requires 64-character line wrapping in base64 data. A single-line PEM is technically valid but not accepted by SN's Java SAML implementation.

**Rule:** Always use `textwrap.fill(b64_data, 64)` when creating PEM certificates for ServiceNow.

---

### 2026-03-21 -- APIM MCP API Not Visible in az CLI (Already Known!)

**Mistake:** `az apim api list` didn't show the `servicenow-mcp-obo` MCP API. Concluded it was missing and almost redeployed. The API was working the entire time.

**Root cause:** Already documented in `~/.claude/knowledge/azure-apim.md`: "`az apim api list` (GA version) does NOT show MCP-type APIs -- use `az rest` with preview API version." Failed to check the knowledge base before acting.

**Rule:** Check `~/.claude/knowledge/` BEFORE diagnosing infrastructure issues. A simple `curl` to the endpoint (401 = exists, 404 = missing) would have avoided this entirely.

### 2026-03-26 -- Teams/Copilot bot fails: "Sorry, I wasn't able to respond"

**Problem:** ServiceNow Assistant appeared in Copilot agent picker and Teams Apps but always returned "Sorry, I wasn't able to respond to that." Web chat and Portal "Test in Web Chat" worked fine.

**Debugging timeline (what we checked and eliminated):**
1. Bot Service config (endpoint, msaAppId, channels) -- identical to working SF bot
2. Agent Application + Deployment -- provisioned, Succeeded state
3. Foundry connection (`servicenow-obo`) -- identical config to `salesforce-obo`
4. APIM OBO endpoint -- working (401/406 as expected)
5. Container App MCP server -- healthy, serving requests from web chat
6. Agent via Responses API -- works correctly
7. Traffic routing mismatch (stale deploymentId) -- fixed but didn't resolve
8. DirectLine test -- bot sends "Waiting for foundry login" but no APIM calls from Teams

**Root causes (two issues):**

1. **Teams app not published to org catalog.** Postprovision Step 7 failed silently because the `az` session lacked `AppCatalog.ReadWrite.All` permission. The agent appeared in Copilot (Foundry discovery) but SSO couldn't work without a proper Teams app installation.

2. **Foundry-generated Teams manifest has wrong defaults.** When publishing via AI Foundry portal, the generated manifest has:
   - `validDomains: []` -- should be `["token.botframework.com"]` (required for Bot Framework SSO token exchange)
   - `webApplicationInfo.resource: "api://example.com"` -- should be `"api://botid-{msaAppId}"` (correct OAuth audience)

**Fix applied:**
- Generated corrected manifest with proper `validDomains`, `webApplicationInfo`, and Foundry's `copilotAgents` section
- Published via Teams Admin Center (https://admin.teams.microsoft.com -> Manage Apps -> Upload)
- Added `_update_traffic_routing()` to postprovision to prevent stale deployment routing

**Rules:**
1. After `azd up`, verify Teams app is in the org catalog. If postprovision Step 7 fails, publish manually via Teams Admin Center or AI Foundry portal.
2. Never trust Foundry-generated Teams manifests blindly -- always verify `validDomains` includes `token.botframework.com` and `webApplicationInfo.resource` is `api://botid-{msaAppId}`, not `api://example.com`.
3. When comparing a working bot (SF) vs broken bot (SN), check the PUBLISHED manifest first -- the issue may not be in Azure resources at all.
4. After creating/updating an Agent Deployment, always update the Agent Application's `trafficRoutingPolicy` to point to the new `deploymentId`. Without this, the Activity Protocol routes to a stale deployment.
5. "Sorry, I wasn't able to respond" with NO MCP tool calls in logs = the failure is before agent execution (auth/SSO/routing), not in the agent itself.

---

### 2026-03-28 -- MemorySearchOptions(max_memories=N) Is Buggy

**Mistake:** Added `search_options=MemorySearchOptions(max_memories=1)` to `MemorySearchTool` config. The class exists in the SDK source (`azure.ai.projects.models`) and passes type checking, but does not work correctly at runtime.

**Root cause:** The SDK class is defined but the Foundry backend does not honor it properly. User reported bugs.

**Rule:** Do NOT use `MemorySearchOptions` or `search_options` parameter on `MemorySearchTool`. Only use `memory_store_name`, `scope`, and `update_delay`.

### 2026-03-28 -- Foundry MemorySearchTool Auto-Injects on Every Turn

**Discovery:** The Foundry runtime automatically searches memory and injects results into the LLM context on EVERY user turn. This is not agent-initiated — there is no `auto_retrieve=False` or on-demand mode. The only controls are:
- `update_delay` (seconds of inactivity before memory extraction, default 300)
- Memory store options (`chat_summary_enabled`, `user_profile_enabled`)

**Rule:** Memory injection cannot be disabled per-turn. To reduce its token impact: increase `update_delay` (reduces write frequency), or disable `chat_summary_enabled` on the memory store (keeps only user profile, which is small).

### 2026-03-28 -- 429 Rate Limits from Foundry Agent = Azure OpenAI TPM Exhaustion

**Mistake:** Initially investigated APIM, Container App, and ServiceNow as sources of `too_many_requests` error in Teams. All were clean.

**Root cause:** The error format (`ConversationId`, `activityId`) is from the Foundry agent runtime, which surfaces 429 from the Azure OpenAI deployment. The gpt-5.4 deployment had only 120K TPM capacity while a 2-turn agent conversation consumes 83K-120K tokens due to quadratic context growth.

**Rule:** When diagnosing `too_many_requests` from a Foundry agent in Teams:
1. Check `az cognitiveservices account deployment list` for TPM capacity
2. Check `az monitor metrics list --metric TokenTransaction` for actual usage
3. The error is almost always Azure OpenAI TPM, not APIM or backend throttling

### 2026-03-28 -- ConnectedAgentTool NOT Compatible with PromptAgentDefinition

**Discovery:** `ConnectedAgentTool` (subagent delegation) exists in `azure.ai.agents` SDK but is NOT available in `azure.ai.projects.models`. It requires the classic Assistants API pattern (`create_agent` + `create_thread_and_run`), not the `PromptAgentDefinition` pattern used by our agents.

**Rule:** For multi-agent patterns with `PromptAgentDefinition`, use Workflows (portal/YAML) or A2ATool (A2A protocol), not ConnectedAgentTool.

### 2026-03-28 -- MCP Response Compaction: 60% Token Reduction

**Problem:** 3-turn agent conversation consumed 65K tokens (hitting 120K TPM limit). Root causes: `display_value=all` doubling every field, `limit` param being page size not total cap (auto-pagination fetched 10K records), discover returning 9 fields with link URLs, write returning full records.

**Fix applied:**
1. `display_value=all` → `display_value=true` (plain strings)
2. Compact discover: 5 fields per definition, no link URLs, names/compact/full modes
3. `limit` now caps total records (default 20, max 200)
4. Write returns `{success, sys_id}` only
5. MCP instructions trimmed from 2.4K to ~500 chars
6. Agent instructions: skip unnecessary discover calls, enforce fields param

**Rule:** For MCP tools returning data to LLM agents, always compact responses server-side:
- Return display values OR raw values, never both
- Default to compact field metadata; offer full mode for edge cases
- Cap record limits at the tool level (agents will request 10K if allowed)
- Write responses should return identifiers only, not full records
- Deduplicate instructions between MCP server and agent prompt

### 2026-03-28 -- E2E Token Testing via Foundry Responses API

**Discovery:** `openai_client.responses.create()` returns `response.usage.input_tokens` and `output_tokens` per turn. Multi-turn via `previous_response_id`. This is the only way to measure real token consumption including memory injection, instructions, and context accumulation.

**Rule:** Use `scripts/test_e2e_tokens.py` pattern to measure token impact of changes. MCP payload size alone underestimates the real cost (memory injection + mcp_list_tools + instructions add ~3-5K overhead per turn).

### 2026-03-28 -- query limit vs max_records: Pagination Bug

**Mistake:** `limit=5` parameter meant "page size 5" with `max_records=10000` default. A "show me 5 incidents" query auto-paginated and fetched all 92 records (548K bytes).

**Root cause:** API design confused page size with total record limit. The agent always passed `limit` thinking it capped results, but auto-pagination fetched up to `max_records`.

**Rule:** For MCP tools consumed by LLM agents, `limit` must mean total records returned, not page size. Agents don't understand pagination internals. Default should be low (20) with a hard cap (200).

---

**Graduated to cross-project knowledge:**
- SAML IdP UI-only creation, SSO script selection, issuer field semantics, PEM formatting, plugin management, OAuth JWT patterns, schema ACL handling → `~/.claude/knowledge/servicenow.md`
- Azure AD SAML app registration (identifierUris workaround, addTokenSigningCertificate, claims mapping) → `~/.claude/knowledge/azure-identity.md`
- MCP pagination for LLM agents (limit = total cap, not page size; display_value=true not all) → `~/.claude/knowledge/mcp-python-sdk.md`
