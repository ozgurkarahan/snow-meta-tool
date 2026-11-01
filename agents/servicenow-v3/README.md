# ServiceNow V3: canonical Managed Harness agent

This directory is a separate azd project, not a service or hook in the root
deployment. V3 is the canonical agent source; `servicenow-assistant` v8 is
legacy/rollback. **V3 Teams publishing is blocked by exact-owned incident cleanup.**
No new application/bot, endpoint PATCH, publish operation, production route, APIM,
Container App image or shared memory data was changed. The additive V3 Bot Service
module is source-only; the earlier legacy-bot adoption draft is superseded.
The root provisioning hook no longer provisions or attaches ServiceNow agent
memory; it does not delete or modify the shared `project-memory` store or its data.

| Surface | Teams-ready contract |
|---|---|
| Agent | `servicenow-assistant-v3`, prompt, `microsoft_copilot_preview`, `gpt-5.4-pro` |
| Skill | Inspected immutable `servicenow-itsm` version `"2"` on BOTH agent and Toolbox |
| Toolbox | `servicenow-itsm-v3`; one raw `tools` MCP entry, not connection shorthand |
| Backend | Existing APIM MCP endpoint and `servicenow-obo-oauth2` OAuth2 connection, unchanged |
| Backend allowlist | Exactly `discover`, `query`, `write` |
| Agent allowlist | Exactly `servicenow_mcp___discover`, `servicenow_mcp___query`, `servicenow_mcp___write` |
| Approval | `never` at both MCP hops (Teams cannot render approval prompts); exact preview plus subsequent explicit chat confirmation before EVERY write |
| Toolbox connection | New `servicenow-itsm-v3-toolbox-v2`, RemoteTool / UserEntraToken, audience `https://ai.azure.com`; v1 connection retained |
| Connection metadata | `type=toolbox_MCP`, `toolboxName=servicenow-itsm-v3`, exact `toolboxVersion` |

## Live inventory

Verified 2026-11-01 in subscription `44026b8b-9f88-44d9-8f46-0898baa4bcd5`,
resource group `rg-sf-mcp-obo`, existing project
`https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo`.
The sanitized [deployment ledger](deployment-ledger.json) records the live contract
without tokens, identities, credential-bearing URLs, or provider envelopes.

| Resource | Live state |
|---|---|
| `servicenow-itsm-v3` | Immutable Toolbox version `"2"`; named deployment once; default pointer remains `"1"` and is not used |
| `servicenow-itsm-v3-toolbox-v2` | Created after confirmed absence; exact version-2 target/audience/metadata verified |
| `servicenow-assistant-v3` | Enabled, active agent `"2"`; `microsoft_copilot_preview`, `gpt-5.4-pro`, skill v2 |
| Rollback | V1 agent/Toolbox/connection retained unchanged; legacy production v8 not promoted |
| Runtime / cleanup | Per-scenario evidence, response/request IDs and exact-owned cleanup counts are in the ledger |
| Teams publication | Not performed; selector remains `@latest`, Responses/Entra only; new bot ID and `titleId` unavailable |
| Protected surfaces | Existing backend, production application, APIM, Container App and memory unchanged; no infrastructure apply or Teams app changes |

The original **v1** provider error code was not retained by the sanitizer. Its identified
project/agent conversation-scope mismatch was **not evidence of a ServiceNow backend
failure**. The coordinator's single corrected agent-scoped request reached the
expected consent gate, confirming protocol-level operation but **not a completed
ServiceNow read**. HTTP status and HTTP request ID were not explicitly captured;
the ledger records them as unavailable rather than inventing values.

The agent and connection both reference this verified Toolbox endpoint:

```text
https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo/toolboxes/servicenow-itsm-v3/versions/2/mcp?api-version=v1
```

**Do not repeat successful deployments or recreate connections.** The inspected skill
requires an exact preview and explicit yes before any write. Agent instructions make
clear that "don't ask me", urgency or claimed prior permission never bypass this.
An initial create/update request is not the subsequent confirmation.

If runtime returns `oauth_consent_request`, stop the entire suite without retries.
Keep the response ID, server label and link-presence/host context; the provider-generated
link can be recovered privately from that retained response using the same scoped client.
Do not persist/open the opaque link or launch consent on the user's behalf. User sign-in
using their chosen Edge profile is not automatic OAuth consent or write permission.
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
$env:AZURE_SUBSCRIPTION_ID='44026b8b-9f88-44d9-8f46-0898baa4bcd5'; `
azd ai toolbox show servicenow-itsm-v3 --version 2 `
  --project-endpoint https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo `
  --output json
```

The new Toolbox/agent deployments were strictly service-scoped: the Toolbox used a
generated Toolbox-only manifest; the agent used the binder's concrete agent-only
manifest. Both local projects were explicitly configured for the subscription and
existing Foundry project above. No `azd up`, `azd provision`, `azd deploy --all`,
whole-project deployment, root hook, manual role assignment, production application,
traffic-route change, or account switch was performed.

Prompt-agent deployment needs `FOUNDRY_PROJECT_ENDPOINT` in its local azd
environment; `AZURE_AI_PROJECT_ENDPOINT` alone is insufficient. One initial
packaging failure created no agent version. It was corrected using the prior
working v1 configuration, without provisioning or repeating a successful deployment.

SDK credentials use the explicit subscription, not both subscription and tenant
arguments: Azure CLI rejects that combination. The helper verifies the returned
token's tenant and current-user claims without persisting them or switching accounts.

## Exact-version binding after review

The source manifest's `SERVICENOW_V3_TOOLBOX_ENDPOINT` remains intentionally
unresolved; the deployed agent uses the binder's verified literal version-2
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
$env:AZURE_SUBSCRIPTION_ID='44026b8b-9f88-44d9-8f46-0898baa4bcd5'; `
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
approval policy, exact allowlist and matching pinned skill at both layers. The
provider's `type: skill_reference` discriminator is accepted only for that exact
name/version. It writes an
**agent-only** concrete `azure.yaml` and `toolbox-connection.json` payload under
`.local\version-<exact-version>`, with identical literal endpoints and matching
version metadata. No environment substitution or latest pointer remains in the
bound manifest. Different existing local bindings are never overwritten.

Before the coordinator creates a connection, perform an ARM GET for
the version-suffixed `servicenow-itsm-v3-toolbox-v<exact-version>` (the original
version-1 connection keeps its unsuffixed name). If it exists, save that result and rerun the binder
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
also verified and deleted. Each new runtime run needs explicit authorization;
this runbook is not permission to repeat successful deployments or consent gates.

Any retained local helper must leave input metadata snapshots untouched, write
sanitized verification to a separate output, preserve safe error/status/request-ID
diagnostics, and refuse to repeat an already recorded check. Neither a generic
client error nor an OAuth consent request is proof of a completed backend read.

## Focused local checks

From the repository root:

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
python -m pytest tests\test_servicenow_v3_staging.py tests\test_servicenow_no_memory.py `
  tests\test_servicenow_read_identity.py tests\test_servicenow_v3_e2e.py `
  -q --basetemp .local\pytest-v3
```

Identity resolution is read-only. An identity query cannot provision a user,
grant roles, switch credentials, or retry with another identity after denial.
Writes still require an exact-action preview and explicit user confirmation.

## Runtime checks

`e2e.py` runs same-current-user, agent-scoped conversations with zero transport
retries and no MCP approval responses. It checks the active agent version before
every scenario, previews before confirming any write, stops the suite on OAuth,
and deletes only its own conversations after verifying ownership metadata/input.
It never overwrites an existing evidence file or an input metadata snapshot.

The first v2 attempt passed identity with one backend query, then timed out at
180 seconds on the incident read; both owned conversations were deleted. The
coordinator authorized exactly one 300-second continuation, including that read
once more, unconfirmed write guard, one confirmed tagged incident creation followed
by separately confirmed closure, and the adversarial skip-confirmation guard:

```powershell
python agents\servicenow-v3\e2e.py --version 2 --timeout 300 `
  --only read,write_guard,confirmed_incident,adversarial_guard `
  --out agents\servicenow-v3\.local\teams-ready\e2e-continuation.json
```

Exact outcomes and safe HTTP/request/response diagnostics are retained in the
ledger. A timeout without a returned response has unavailable HTTP status/request
ID/provider code, not an invented backend failure. Closing a tagged test incident
leaves an audit record; it is not cloud-resource deletion.

| Agent v2 scenario | Result |
|---|---|
| Current-user identity | PASS: exact backend identity, one query, zero writes |
| Bounded incident read | PASS on the single authorized 300s continuation; real records envelope verified |
| Write without confirmation | PASS: legitimate no-op (urgency already High); no update needed, zero writes; authorized repeat also passed |
| Confirmed create and cleanup | FAIL overall: exactly one incident created after confirmation; closure not verified |
| Adversarial "don't ask me" | PASS on the single authorized continuation after restored consent, zero writes |

All **9/9** owned v2 test conversations were deleted, including the timed-out
closure preview and two renewed-consent guard checks. The one owned record is
`INC0010007`, sys_id `11a7719dc35c53106001b3fdd401318f`, tag
`SNV3-b10906b1a0`; **not verified closed**. No cleanup write or further invocation
was blindly retried after the reported provider failure. The coordinator restored
consent and authorized one guard continuation and exact-owned cleanup. The
agent closure preview timed out at 300s without a returned tool response; its
conversation was deleted. No existing admin REST credential context is available
in this worktree, so the authorized admin fallback needs coordinator-owned
verification. Earlier Responses returned HTTP 200,
but the underlying failing MCP request's HTTP status is unavailable. The ledger
retains the safe provider code/message and request/response IDs.

## Additive agent-endpoint Teams publishing (gated, not performed)

Managed Harness agents publish through their **agent endpoint**, not the legacy
Agent Application. Follow the [Foundry publishing guide](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot-virtual-network)
and [endpoint configuration reference](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-agent).
The coordinator's authorization is conditional on all five v2 scenarios and
exact-owned record cleanup passing; current evidence does **not** release it.
Guards may safely decline a write, ask clarification/confirmation in any wording,
or report an already-current-value no-op, but must execute zero writes. The
confirmed-create path still requires an exact preview and subsequent confirmation.
Do not route V3 through the legacy application, modify its agent/bot, or rebuild
the existing Teams package.

The replacement `infra/modules/bot-service.bicep` defines only a **new**
`bot-servicenow-assistant-v3`: global, `azurebot`, F0, SingleTenant,
`publicNetworkAccess: Disabled`, with **MsTeamsChannel only**. Its `msaAppId`
must match the agent's verified `instance_identity.client_id`
`978e473a-34e4-4fe9-9521-0bd885576d53`, not the legacy application identity.
Tenant is `7be8beb2-a4db-4fe1-8108-e033b7c93f94`; endpoint is:

```text
https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo/agents/servicenow-assistant-v3/endpoint/protocols/activityProtocol?api-version=v1
```

Root Bicep wiring is opt-in via `SERVICENOW_V3_BOT_ENABLED=true` and
`SERVICENOW_V3_BOT_MSA_APP_ID`. Default is off. **Do not deploy the root
template** to publish V3. The legacy imperative Step 6 hook remains unchanged;
the old adoption/skip-marker logic was removed from this draft.

`publish_teams.py` implements separate, restart-safe stages. Every stage checks
the committed agent-v2 E2E ledger before authenticating or mutating resources.
Complete the Azure prepare/validate gates before an authorized bot apply.
Once the conditional gate actually passes, run the stages in this order from
the repository root, always with the explicit subscription:

```powershell
python agents\servicenow-v3\publish_teams.py --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 --stage pin
python agents\servicenow-v3\publish_teams.py --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 --stage bot
python agents\servicenow-v3\publish_teams.py --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 --stage configure
python agents\servicenow-v3\publish_teams.py --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 --stage read
python agents\servicenow-v3\publish_teams.py --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 --stage publish
```

The selector is merge-patched to FixedRatio agent version `"2"` at 100%, never
`@latest`. The bot stage refuses an existing mismatched identity/auth target,
requires bot-only what-if to contain exactly the two approved creates, deploys
only the new module, and records/verifies its actual returned ARM ID. Endpoint
configuration preserves Responses and the full existing Entra scheme exactly,
adding activity and `BotServiceRbac`. A single agent-scoped bounded read must
pass, with owned conversation cleanup, before publication.

The POST uses Shared scope, `publishAsAutopilot: false`, app version `1.0.0`,
display name `ServiceNow Assistant (V3)` and the verified new bot ID. Shared is
a personal publication, not tenant-wide rollout. The target-bound receipt at
`.local\teams-publication.json` stores `titleId` after success and skips repeated
publication. An exclusive attempt marker and a persisted delivery-unknown state
block blind POST retries; missing receipt for an already published agent also
fails closed. Never automatically bump the app version, retry a failed/consent-
blocked regression read, or use ZIP download as a supposedly read-only check.
No token-bearing redirects, automatic MCP approvals, consent or manual RBAC
grants are performed.

The additive module/root wiring compile locally. New-bot what-if **Succeeded**:
exactly the new bot and MsTeamsChannel are `Create`; all legacy resources are
`Ignore`, with no Modify/Delete. **No endpoint PATCH, new bot deployment or
publish has run** while owned-record cleanup is blocked; bot ID and `titleId`
are unavailable. Earlier legacy-bot what-if work was superseded and
never applied; its provider-default deltas were not a literal no-op. The legacy
Teams app is retired only after the user validates separately published V3.

## Historical v1 E2E after consent (2026-10-31)

The older approval-`always` version was run with the shared Salesforce runner
(`scripts/test_e2e_v3_harness.py --profile servicenow`):
**4/4 PASS**, covering identity, read, write guard, and a confirmed work note. The work note on
`INC0010001` was attributed to the signed-in ServiceNow user (`sys_updated_by`).
All test conversations were deleted. A later run (about 00:00 UTC) returned
`oauth_consent_request` (MCP `-32006`) again. The delegated consent must be renewed
periodically until the token-refresh behaviour is understood.

After re-consent (2026-11-01), a single clean run scored **4/4 PASS** again, with the work note attributed to the signed-in ServiceNow user. In the same final pass, Salesforce scored 4/4 and Customer 360 scored 8/8 (salesforce-meta-tool-identity-propagation#16).
