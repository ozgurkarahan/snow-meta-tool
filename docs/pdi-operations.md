# ServiceNow PDI operations: health check and keep-alive

The V3 ServiceNow and Customer 360 agents depend on a free ServiceNow Personal
Developer Instance (PDI). The call path is Foundry, then APIM `apim-sf-mcp-obo`,
then `ca-sn-mcp`, then the ServiceNow JWT bearer exchange (`/oauth_token.do`).
When the PDI is unavailable, every tool call fails. This runbook explains how we
detect that early and what to do.

## Failure modes

| State (`scripts/pdi_health.py`) | What you see | Cause | Fix |
|---|---|---|---|
| `HIBERNATING_OR_OFFLINE` (exit 10) | `502 Bad Gateway`, `Server: snow_adc`, on every URL | **Hibernation**: the PDI is asleep. | Sign in to <https://developer.servicenow.com> (ServiceNow ID + MFA) and wake the instance. This cannot be automated safely. |
| `HIBERNATING_OR_OFFLINE` (exit 10) | Same 502 `snow_adc`. The portal shows Status **Offline** and has no Wake option. | **ServiceNow infrastructure outage** or maintenance. | Wait. It clears when ServiceNow finishes. Nothing to repair on our side. |
| `RECLAIMED_OR_MISCONFIGURED` (exit 11) | The instance answers, but `oauth_token.do` returns `401 access_denied` (or 400/403). | **Reclamation/reset** after 10 days without an interactive login wipes the OAuth JWT app, the verifier, the certificate and the users. It can also mean the `SN_*` secrets are wrong. | Run the [reclaim recovery](#reclaim-recovery) procedure. |
| `NETWORK_ERROR` (exit 12) | DNS, connect, TLS or timeout failure | Runner or network problem | Re-run. Investigate only if it persists. |
| `UNEXPECTED` (exit 13) | Any other HTTP answer | Unknown | Open the instance in a browser and check the workflow logs. |
| `UP` (exit 0) | `stats.do` is reachable, JWT bearer works and a 1-row incident read succeeds | None | None |
| `NOT_CONFIGURED` (exit 20) | No `SN_INSTANCE_URL` | Secrets not set | The workflow is skipped. |

If only `SN_INSTANCE_URL` is configured, the auth stage is skipped (`"auth_checked": false`).
That still catches hibernation and outages, but a reclaimed instance can then look `UP`.

## Why REST pings and Foundry routines do not prevent reclamation

ServiceNow KB3140725 (October 2026) says that **only an explicit interactive login to
the PDI itself** (`devNNNNNN.service-now.com`) counts as activity for the 10-day
reclamation policy. These do **not** count:

- REST or Table API calls, including our JWT bearer traffic and `keepalive_ping.py`
- integrations and scheduled jobs
- logins to the developer portal

So `scripts/pdi_health.py`, `scripts/keepalive_ping.py` and any Foundry routine can only
**detect** problems. Only `scripts/pdi_ui_login.py`, which runs a headless browser login,
resets the reclamation clock. Hibernation always needs a person to wake the instance in
the portal.

## Automation: `.github/workflows/pdi-health.yml`

- Runs every 4 hours (`23 */4 * * *`), daily at 07:47 UTC (`47 7 * * *`) and on
  manual `workflow_dispatch`. Scheduled runs only fire from the **default branch**, and
  GitHub pauses schedules in public repos after 60 days without repository activity.
- Steps: `pdi_health.py`, then `pdi_issue.py`, which keeps a **single** open issue
  labelled `pdi-down`:
  - It creates the issue when the state is not `UP`.
  - Repeated failures edit the issue body without sending notifications. A state
    change adds a comment.
  - It closes the issue automatically when the instance is `UP` again.
- When the instance is **reachable** (`stats.do` answers, even if JWT auth fails) and the
  keep-alive secrets exist, the daily run (or a dispatch with `ui_login: true`) also runs
  `pdi_ui_login.py`. Wrong `SN_*` JWT secrets therefore never block the anti-reclamation login.
- The workflow uses only `GITHUB_TOKEN` with `contents: read` and `issues: write`.
  Actions are pinned by SHA. Every step no-ops when its secrets are missing.

### Secrets (repository Actions secrets; names only)

| Secret | Used by | Value |
|---|---|---|
| `SN_INSTANCE_URL` | all | `https://devNNNNNN.service-now.com`. Without it, the workflow is skipped. |
| `SN_CLIENT_ID` | health (auth stage) | OAuth JWT app `client_id` (`certs/sn-oauth-config.json`) |
| `SN_KID` | health (auth stage) | `jwt_verifier_map` kid |
| `SN_SUB` | health (auth stage) | non-admin JWT user email, e.g. `jwt.test@example.com` |
| `SN_JWT_PRIVATE_KEY` | health (auth stage) | PEM of `certs/sn-jwt-bearer.key`: `gh secret set SN_JWT_PRIVATE_KEY < certs/sn-jwt-bearer.key` |
| `SN_KEEPALIVE_USER` | UI keep-alive | `pdi.keepalive` (never `admin`) |
| `SN_KEEPALIVE_PASSWORD` | UI keep-alive | printed once by `repair_sn_pdi.py --keepalive-user-only` |

### One-time keep-alive user setup

Run this when the PDI is awake:

```bash
python scripts/repair_sn_pdi.py --keepalive-user-only
```

This creates (idempotently) `pdi.keepalive` with no roles, active, unlocked and with
`password_needs_reset=false`. It refuses if the user holds `admin`, `security_admin`
or `maint`. The generated password is printed **once** and never written to disk.
Store it with the printed `gh secret set` commands. If the secret is lost, use
`--rotate-keepalive-password`.

Zurich PDIs may reject REST basic auth for `admin`. In that case, create the user in
the UI instead: no roles, a strong password, and **Password needs reset** unchecked.

## Local use

```bash
python scripts/pdi_health.py                # reads certs/ (gitignored) unless SN_* env vars are set
SN_KEEPALIVE_USER=pdi.keepalive SN_KEEPALIVE_PASSWORD=... python scripts/pdi_ui_login.py
```

Both print one JSON line and never print keys, assertions, tokens or passwords.

## Reclaim recovery

1. Confirm in the developer portal that the instance is yours and awake. **Do not**
   request a new PDI while the old one is only hibernating: that releases the old one
   (see `.ai/lessons-learned.md`).
2. Run `python scripts/repair_sn_pdi.py --keepalive-user`. It is interactive (admin
   password via getpass) and idempotent. It rebuilds the certificate, the OAuth JWT
   app, the verifier, the JWT users and the keep-alive user, verifies the JWT bearer,
   then rewrites `certs/sn-oauth-config.json`.
3. Update Azure with the printed `instance` / `client_id` / `kid`:
   - APIM named values `SnOboInstanceUrl`, `SnOboClientId`, `SnJwtBearerKid`
   - `ca-sn-mcp` env `SN_INSTANCE_URL`
   - azd env `SN_INSTANCE_URL` / `SN_OAUTH_CLIENT_ID` / `SN_JWT_BEARER_KID`
4. Update the `SN_CLIENT_ID` / `SN_KID` GitHub secrets if they changed, and
   `SN_KEEPALIVE_PASSWORD` if a new password was printed.
5. Run the workflow manually. When it reports `UP`, it closes the `pdi-down` issue.
