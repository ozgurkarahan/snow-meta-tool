# ServiceNow V3 staging

This directory is a separate azd project, not a service or hook in the root
deployment. Source changes are additive: no staging application, bot, production
routing, APIM, infrastructure, Container App image, or shared memory-store changes.
The root provisioning hook no longer provisions or attaches ServiceNow agent
memory; it does not delete or modify the shared `project-memory` store or its data.

| Surface | Staging contract |
|---|---|
| Agent | `servicenow-assistant-v3`, prompt, `microsoft_copilot_preview`, `gpt-5.4-pro` |
| Skill | Agent-level `servicenow-itsm` version `"2"`; never in Toolbox |
| Toolbox | `servicenow-itsm-v3`; one raw `tools` MCP entry, not connection shorthand |
| Backend | Existing APIM MCP endpoint and `servicenow-obo-oauth2` OAuth2 connection, unchanged |
| Backend allowlist | Exactly `discover`, `query`, `write` |
| Agent allowlist | Exactly `servicenow_mcp___discover`, `servicenow_mcp___query`, `servicenow_mcp___write` |
| Approval | `always` at both MCP hops, plus explicit user confirmation before each write |
| Toolbox connection | New `servicenow-itsm-v3-toolbox`, RemoteTool / UserEntraToken, audience `https://ai.azure.com` |
| Connection metadata | `type=toolbox_MCP`, `toolboxName=servicenow-itsm-v3`, exact `toolboxVersion` |

## Live inventory

Verified 2026-10-31 in subscription `44026b8b-9f88-44d9-8f46-0898baa4bcd5`,
resource group `rg-sf-mcp-obo`, existing project
`https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo`.
The sanitized [deployment ledger](deployment-ledger.json) records the live contract
without tokens, identities, credential-bearing URLs, or provider envelopes.

| Resource | Live state |
|---|---|
| `servicenow-itsm-v3` | Immutable Toolbox version `"1"`; created by one named deployment |
| `servicenow-itsm-v3-toolbox` | Created after confirmed absence; RemoteTool / UserEntraToken with exact version-1 target and metadata |
| `servicenow-assistant-v3` | Enabled, active agent version `"1"`; `microsoft_copilot_preview`, `gpt-5.4-pro`, skill v2 |
| Runtime read | Corrected agent-scoped request completed at `oauth_consent_request` for `servicenow_toolbox`; zero tool calls and no response errors |
| Cleanup | Both exact-owned test conversations deleted; no consent or tool approval was granted |
| Protected surfaces | Existing backend connection, production agent/application, APIM, Container App and source infrastructure unchanged |

The initial provider error code was not retained by the sanitizer. Its identified
project/agent conversation-scope mismatch was **not evidence of a ServiceNow backend
failure**. The coordinator's single corrected agent-scoped request reached the
expected consent gate, confirming protocol-level operation but **not a completed
ServiceNow read**. HTTP status and HTTP request ID were not explicitly captured;
the ledger records them as unavailable rather than inventing values.

The agent and connection both reference this verified Toolbox endpoint:

```text
https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo/toolboxes/servicenow-itsm-v3/versions/1/mcp?api-version=v1
```

**Do not repeat the successful deployments or recreate the connection.** Runtime
testing stopped at the consent gate. Completing a backend read requires a separate
user-approved consent and tool-approval workflow; neither was started or granted.
Never treat user unavailability as consent. Browser interaction is not required
for this API-only staging; leave an in-use browser profile untouched.

Foundry automatically provisioned managed per-agent Entra identity dependencies for
the persistent V3 agent. No manual permission grants were made. These dependencies
belong to the staged agent and must not be deleted as test-conversation cleanup.

## Mandatory process flags and deployment boundary

For **every azd command**, set `AZURE_DEV_USER_AGENT=microsoft_foundry_skill`
inline in that process. Agent work also requires
`AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true`; all staging commands use
`AZD_AGENT_SKIP_ROLE_ASSIGNMENTS=true`. Do not rely on flags in a persisted
environment or another shell. A read-only example:

```powershell
$env:AZURE_DEV_USER_AGENT='microsoft_foundry_skill'; `
$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS='true'; `
$env:AZD_AGENT_SKIP_ROLE_ASSIGNMENTS='true'; `
azd ai toolbox show servicenow-itsm-v3 --version 1 `
  --project-endpoint https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo `
  --output json
```

The two successful deployments were strictly service-scoped: the Toolbox used a
generated Toolbox-only manifest; the agent used the binder's concrete agent-only
manifest. Both local projects were explicitly configured for the subscription and
existing Foundry project above. No `azd up`, `azd provision`, `azd deploy --all`,
whole-project deployment, root hook, manual role assignment, production application,
traffic-route change, or account switch was performed.

## Exact-version binding after review

The source manifest's `SERVICENOW_V3_TOOLBOX_ENDPOINT` remains intentionally
unresolved; the deployed agent used the binder's verified literal version-1
endpoint. The following procedure explains the binding contract for an explicitly
approved future change; it is **not** authorization to rerun the existing deployment.
Never deploy the source agent or the whole staging project. No deployment is
performed by the binder.

After the coordinator deploys **only** the `servicenow-itsm-v3` Toolbox service,
read that exact version (not latest/default) from the chosen Foundry project:

```powershell
# Run from agents\servicenow-v3 in an explicitly configured staging environment.
$env:AZURE_DEV_USER_AGENT='microsoft_foundry_skill'; `
$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS='true'; `
$env:AZD_AGENT_SKIP_ROLE_ASSIGNMENTS='true'; `
azd ai toolbox show servicenow-itsm-v3 --version <exact-version> `
  --project-endpoint https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo `
  --output json
```

Save the complete JSON output under this directory's ignored `.local` folder.
The expected response has a top-level `endpoint`, `toolbox.name`, and a
`version` object containing `name`, `version`, `tools`, and `skills`.
The `toolbox.default_version` pointer is deliberately ignored.

Install this directory's `requirements.txt` only if PyYAML is missing, then run:

```powershell
python bind_toolbox.py --toolbox-json .local\toolbox.json --version <exact-version> `
  --project-endpoint https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo
```

The binder fails closed unless the returned version and endpoint exactly agree:

```text
<project-endpoint>/toolboxes/servicenow-itsm-v3/versions/<exact-version>/mcp?api-version=v1
```

It also verifies the deployed raw MCP tool's backend, OAuth2 connection,
approval policy, exact allowlist, and absence of Toolbox skills. It writes an
**agent-only** concrete `azure.yaml` and `toolbox-connection.json` payload under
`.local\version-<exact-version>`, with identical literal endpoints and matching
version metadata. No environment substitution or latest pointer remains in the
bound manifest. Different existing local bindings are never overwritten.

Before the coordinator creates a connection, perform an ARM GET for
`servicenow-itsm-v3-toolbox`. If it exists, save that result and rerun the binder
with `--connection-json .local\existing-connection.json`. Matching connections
must be reused unchanged; any name, auth, target, audience, or metadata mismatch
is an error, not permission to overwrite. Create this named connection **only
after confirmed absence**, using the documented `azd ai connection create`
flow with `--auth-type user-entra-token` and the values from the generated
payload. Do not use `--force` or Azure MCP `project_connection_create` for this
connection type. Never PUT or DELETE the backend `servicenow-obo-oauth2` or
overwrite any other existing connection. The generated JSON is a reviewable
connection contract, not an instruction to issue an unconditional ARM PUT.

Configure the bound agent-only project's environment explicitly for the same
Foundry project; do not assume the root environment transfers to the generated
directory. After verifying the matching connection, deploy only
`servicenow-assistant-v3` from the bound manifest directory. Recheck the exact
Toolbox version, agent endpoint, and live connection target before deployment.
Do not invoke the root provisioning hook, create an application, or change
production traffic routing. Runtime/identity checks happen separately after
review and deployment; the focused repository tests make no Azure calls.

For any separately approved runtime diagnostic, retain sanitized HTTP status,
error code/message, and request/response ID rather than collapsing an error to
its type. Never persist raw responses, consent links, tokens, identity data, or
unredacted tool results. Stop on `oauth_consent_request` or `mcp_approval_request`;
do not automatically approve, open consent, retry a consent gate, change accounts,
or modify RBAC. Delete only conversations created for that exact diagnostic after
verifying their owned input, and remove only their CLI/SDK test-state references.

Managed Harness conversations must be **agent-scoped**. The corrected diagnostic
used `AIProjectClient(..., allow_preview=True)` followed by:

```python
project.get_openai_client(
    agent_name="servicenow-assistant-v3",
    default_headers={"Foundry-Features": "MicrosoftCopilot=V1Preview"},
    timeout=90,
    max_retries=0,
)
```

Use that same scoped client for conversation creation, response invocation,
ownership inspection, and deletion. A conversation created by plain
`project.get_openai_client()` belongs to the project store and must not be supplied
to the agent endpoint. The initial diagnostic made that scope mistake; its owned
project conversation was deleted. The corrected agent-scoped conversation was
also verified and deleted. No further invocation is authorized by this runbook.

Any retained local helper must leave input metadata snapshots untouched, write
sanitized verification to a separate output, preserve safe error/status/request-ID
diagnostics, and refuse to repeat an already recorded check. Neither a generic
client error nor an OAuth consent request is proof of a completed backend read.

## Focused local checks

From the repository root:

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
python -m pytest tests\test_servicenow_v3_staging.py tests\test_servicenow_no_memory.py `
  tests\test_servicenow_read_identity.py -q --basetemp .local\pytest-v3
```

Identity resolution is read-only. An identity query cannot provision a user,
grant roles, switch credentials, or retry with another identity after denial.
Writes still require an exact-action preview and explicit user confirmation.

## End-to-end result after consent (2026-10-31)

Run with the shared runner in the Salesforce repo (`scripts/test_e2e_v3_harness.py --profile servicenow`):
**4/4 PASS**, covering identity, read, write guard, and a confirmed work note. The work note on
`INC0010001` was attributed to the signed-in ServiceNow user (`sys_updated_by`).
All test conversations were deleted. A later run (about 00:00 UTC) returned
`oauth_consent_request` (MCP `-32006`) again. The delegated consent must be renewed
periodically until the token-refresh behaviour is understood.
