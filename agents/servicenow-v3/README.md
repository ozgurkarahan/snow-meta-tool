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

## Exact-version binding after review

No V3 Toolbox version or connection is presumed to exist. The source manifest's
`SERVICENOW_V3_TOOLBOX_ENDPOINT` is intentionally unresolved. Do not deploy the
source agent or the whole staging project: create the Toolbox service first,
then bind and deploy only the agent. No deployment is performed by the binder.

After the coordinator deploys **only** the `servicenow-itsm-v3` Toolbox service,
read that exact version (not latest/default) from the chosen Foundry project:

```powershell
# Run from agents\servicenow-v3 in an explicitly configured staging environment.
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
