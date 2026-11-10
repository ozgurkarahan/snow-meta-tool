"""Open / update / close the single `pdi-down` GitHub issue from a pdi_health result.

Pure decision logic (decide/render_body) plus thin `gh` CLI calls. Uses GH_TOKEN
(the workflow's GITHUB_TOKEN); needs only `issues: write`.

    python scripts/pdi_issue.py --result result.json [--repo owner/name] [--dry-run]

Decision:
    NOT_CONFIGURED                       -> none
    UP + open issue                      -> close (with comment)
    UP + no open issue                   -> none
    not UP + no open issue               -> create
    not UP + open issue, same state      -> update (body only; no notification)
    not UP + open issue, state changed   -> update + comment
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LABEL = "pdi-down"
TITLE_PREFIX = "ServiceNow PDI not UP"
STATE_MARKER = re.compile(r"<!--\s*pdi-health-state:\s*([A-Z_]+)\s*-->")
SILENT_STATES = {"NOT_CONFIGURED"}


@dataclass(frozen=True)
class Decision:
    action: str  # none | create | update | close
    issue_number: int | None = None
    comment: str | None = None


def previous_state(issue: dict | None) -> str | None:
    if not issue:
        return None
    m = STATE_MARKER.search(issue.get("body") or "")
    return m.group(1) if m else None


def decide(result: dict, open_issues: list[dict]) -> Decision:
    state = result.get("state")
    issue = min(open_issues, key=lambda i: i["number"]) if open_issues else None
    if state in SILENT_STATES or not state:
        return Decision("none")
    if state == "UP":
        if issue:
            return Decision("close", issue["number"],
                            f"PDI is UP again (checked {result.get('checked_at')}, "
                            f"auth_checked={result.get('auth_checked')}). Closing automatically.")
        return Decision("none")
    if not issue:
        return Decision("create")
    prev = previous_state(issue)
    comment = None
    if prev != state:
        comment = f"State changed: `{prev or 'unknown'}` -> `{state}` at {result.get('checked_at')}."
    return Decision("update", issue["number"], comment)


def render_title(result: dict) -> str:
    return f"{TITLE_PREFIX}: {result['state']}"


def render_body(result: dict, run_url: str | None = None) -> str:
    lines = [
        f"<!-- pdi-health-state: {result['state']} -->",
        "Automated by `.github/workflows/pdi-health.yml` (`scripts/pdi_health.py`). "
        "Closes automatically when the instance is UP again.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| State | `{result['state']}` |",
        f"| Instance | {result.get('instance')} |",
        f"| Failing stage | `{result.get('stage')}` |",
        f"| HTTP status | {result.get('http_status')} |",
        f"| Detail | {result.get('detail') or '-'} |",
        f"| Auth checked | {result.get('auth_checked')} |",
        f"| Last checked (UTC) | {result.get('checked_at')} |",
    ]
    if run_url:
        lines.append(f"| Workflow run | {run_url} |")
    lines += ["", "### Next action", "", result.get("next_action") or "-", "",
              "Runbook: [docs/pdi-operations.md](../blob/main/docs/pdi-operations.md)"]
    return "\n".join(lines)


def _gh(args, runner=subprocess.run, input_text=None):
    proc = runner(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                  input=input_text, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def apply(decision: Decision, result: dict, repo: str, run_url=None, runner=subprocess.run, dry_run=False):
    if decision.action == "none":
        return
    base = ["--repo", repo]
    calls = []
    if decision.action == "create":
        calls.append((["label", "create", LABEL, "--color", "B60205", "--force",
                       "--description", "ServiceNow PDI health check failing", *base], None))
        calls.append((["issue", "create", "--title", render_title(result), "--label", LABEL,
                       "--body-file", "-", *base], render_body(result, run_url)))
    elif decision.action == "update":
        calls.append((["issue", "edit", str(decision.issue_number), "--title", render_title(result),
                       "--body-file", "-", *base], render_body(result, run_url)))
        if decision.comment:
            calls.append((["issue", "comment", str(decision.issue_number), "--body-file", "-", *base],
                          decision.comment))
    elif decision.action == "close":
        calls.append((["issue", "close", str(decision.issue_number), "--comment", decision.comment, *base], None))
    for args, stdin in calls:
        if dry_run:
            print("DRY-RUN gh " + " ".join(args))
        else:
            _gh(args, runner, stdin)


def list_open_issues(repo: str, runner=subprocess.run) -> list[dict]:
    out = _gh(["issue", "list", "--repo", repo, "--label", LABEL, "--state", "open",
               "--json", "number,body,title", "--limit", "20"], runner)
    return json.loads(out or "[]")


def main(argv=None, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--run-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result = json.loads(args.result.read_text(encoding="utf-8").strip().splitlines()[-1])
    if result.get("state") in SILENT_STATES:
        print(f"pdi_issue: state {result.get('state')}; nothing to do")
        return 0
    issues = list_open_issues(args.repo, runner)
    decision = decide(result, issues)
    print(f"pdi_issue: state={result.get('state')} action={decision.action} issue={decision.issue_number}")
    apply(decision, result, args.repo, args.run_url, runner, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
