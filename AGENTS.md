# Agent Instructions

## Shared knowledge base (memory wiki)

A compounding cross-project knowledge base lives at `~/projects/memory`
(`%USERPROFILE%\projects\memory` on Windows; resolve `~` to your home dir).
**Consult it reflexively before answering about clients, domains, patterns,
tools, colleagues, or past work** — do not answer from memory when the wiki
has the answer.

```bash
# from ANY repo — resolves the wiki root from the script's own location
python ~/projects/memory/scripts/wiki-search.py <terms>          # ranked hits
python ~/projects/memory/scripts/wiki-search.py <terms> --full   # + Summary
```

- Read the top matching page(s) in full before you answer.
- Compact catalog / router: `~/projects/memory/index.md` (per-category summaries
  in the category catalog resolved by Memory's `category_index_path`).
- When you learn something durable about this project or client, ask the user to
  run `ingest` in the memory repo so it compounds for future sessions.

## Deployment invariants (do not regress)

- The Foundry connection is `servicenow-obo-oauth2` (authType **OAuth2** identity
  passthrough). It is ensured create-only by `hooks/postprovision.py` -- never
  delete/recreate it, and never reintroduce a UserEntraToken connection (Foundry
  rejects Microsoft-audience tokens to custom MCP endpoints).
- `azd provision` must not reset `ca-sn-mcp`: the image comes from
  `SERVICE_SERVICENOW_MCP_IMAGE_NAME` via `infra/main.bicepparam`.
