# Project Instructions

> Read `AGENT.md` for project overview, architecture, and key paths.

## Workflow Rules

Read `~/.ai/workflow.md` for the full set of global workflow rules. Key rules summarized here:

1. **Plan Before Coding** — For any task with 3+ steps, outline the approach first. Get approval before implementing.
2. **Verify Before Done** — Never mark a task complete without proving it works.
3. **Learn From Mistakes** — After any correction, update `.ai/lessons-learned.md`. Review at session start.
4. **No Blind Retries** — Diagnose root cause on failure. Don't retry non-transient errors.
5. **Keep It Simple** — Don't add features, refactor code, or make improvements beyond what was asked.

## Platform & Environment

Read `~/.ai/platform.md` for full platform preferences and domain knowledge index.

- Windows 11 + Git Bash (MSYS)
- Python 3.11+ (`python` not `python3`)
- Always `encoding="utf-8", errors="replace"` for subprocess on Windows

## Reference Documents

| Document | Contents |
|----------|----------|
| `.ai/lessons-learned.md` | Debugging history, project-specific lessons |
| `.ai/project-reference.md` | Technical details, implementation caveats |
| `~/.ai/knowledge/*.md` | Cross-project domain knowledge |

## What NOT To Do

- Do not create files unless absolutely necessary — prefer editing existing files.
- Do not add comments, docstrings, or type annotations to code you didn't change.
- Do not over-engineer simple solutions.
- Do not commit secrets, credentials, or `.env` files.
- Do not skip tests or verification steps.
