import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pdi_issue  # noqa: E402
import pdi_ui_login  # noqa: E402


def result(state, **kw):
    base = {"state": state, "instance": "https://dev000001.service-now.com", "stage": "reachability",
            "http_status": 502, "detail": "snow_adc", "auth_checked": False,
            "checked_at": "2026-11-10T15:00:00Z", "next_action": "Wake it."}
    return {**base, **kw}


def issue(number, state):
    return {"number": number, "title": "x", "body": f"<!-- pdi-health-state: {state} -->\nbody"}


@pytest.mark.parametrize("state,issues,expected", [
    ("UP", [], ("none", None)),
    ("UP", [issue(7, "HIBERNATING_OR_OFFLINE")], ("close", 7)),
    ("HIBERNATING_OR_OFFLINE", [], ("create", None)),
    ("HIBERNATING_OR_OFFLINE", [issue(7, "HIBERNATING_OR_OFFLINE")], ("update", 7)),
    ("RECLAIMED_OR_MISCONFIGURED", [issue(9, "X"), issue(7, "HIBERNATING_OR_OFFLINE")], ("update", 7)),
    ("NOT_CONFIGURED", [issue(7, "HIBERNATING_OR_OFFLINE")], ("none", None)),
    ("NETWORK_ERROR", [], ("create", None)),
])
def test_decide(state, issues, expected):
    d = pdi_issue.decide(result(state), issues)
    assert (d.action, d.issue_number) == expected


def test_update_comments_only_on_state_change():
    same = pdi_issue.decide(result("HIBERNATING_OR_OFFLINE"), [issue(7, "HIBERNATING_OR_OFFLINE")])
    changed = pdi_issue.decide(result("RECLAIMED_OR_MISCONFIGURED"), [issue(7, "HIBERNATING_OR_OFFLINE")])
    assert same.comment is None
    assert "HIBERNATING_OR_OFFLINE` -> `RECLAIMED_OR_MISCONFIGURED" in changed.comment


def test_body_roundtrips_state_and_next_action():
    body = pdi_issue.render_body(result("RECLAIMED_OR_MISCONFIGURED", next_action="Run repair_sn_pdi.py"),
                                 run_url="https://github.com/o/r/actions/runs/1")
    assert pdi_issue.previous_state({"body": body}) == "RECLAIMED_OR_MISCONFIGURED"
    assert "Run repair_sn_pdi.py" in body and "actions/runs/1" in body


class FakeGh:
    def __init__(self, issues):
        self.issues, self.calls = issues, []

    def __call__(self, cmd, **kw):
        assert cmd[0] == "gh"
        self.calls.append((cmd[1:], kw.get("input")))
        out = json.dumps(self.issues) if cmd[1:3] == ["issue", "list"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def run_main(tmp_path, res, issues):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(res) + "\n")
    gh = FakeGh(issues)
    assert pdi_issue.main(["--result", str(f), "--repo", "o/r"], runner=gh) == 0
    return [c[0][:2] for c in gh.calls], gh.calls


def test_main_creates_labelled_issue(tmp_path):
    verbs, calls = run_main(tmp_path, result("HIBERNATING_OR_OFFLINE"), [])
    assert verbs == [["issue", "list"], ["label", "create"], ["issue", "create"]]
    args, body = calls[-1]
    assert "pdi-down" in args and "HIBERNATING_OR_OFFLINE" in body


def test_main_closes_when_up(tmp_path):
    verbs, calls = run_main(tmp_path, result("UP"), [issue(4, "HIBERNATING_OR_OFFLINE")])
    assert verbs == [["issue", "list"], ["issue", "close"]] and "4" in calls[-1][0]


def test_main_update_with_state_change_comment(tmp_path):
    verbs, _ = run_main(tmp_path, result("RECLAIMED_OR_MISCONFIGURED"), [issue(4, "HIBERNATING_OR_OFFLINE")])
    assert verbs == [["issue", "list"], ["issue", "edit"], ["issue", "comment"]]


def test_main_not_configured_makes_no_gh_calls(tmp_path):
    verbs, _ = run_main(tmp_path, result("NOT_CONFIGURED"), [])
    assert verbs == []


def test_gh_failure_raises():
    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP 403")
    with pytest.raises(RuntimeError, match="403"):
        pdi_issue.list_open_issues("o/r", runner=failing)


# --- pdi_ui_login ------------------------------------------------------------

def test_ui_login_skips_without_secrets(capsys):
    called = []
    assert pdi_ui_login.main(env={}, login=lambda *a: called.append(a)) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "SKIPPED" and not called


@pytest.mark.parametrize("user", ["admin", "Admin", "maint"])
def test_ui_login_refuses_privileged_accounts(user, capsys):
    env = {"SN_KEEPALIVE_USER": user, "SN_KEEPALIVE_PASSWORD": "pw", "SN_INSTANCE_URL": "https://x.service-now.com"}
    assert pdi_ui_login.main(env=env, login=lambda *a: (True, "")) == 2
    assert json.loads(capsys.readouterr().out)["result"] == "REFUSED"


def test_ui_login_success_never_prints_password(capsys):
    env = {"SN_KEEPALIVE_USER": "pdi.keepalive", "SN_KEEPALIVE_PASSWORD": "Sup3r-Secret!",
           "SN_INSTANCE_URL": "https://x.service-now.com/"}
    seen = []
    assert pdi_ui_login.main(env=env, login=lambda i, u, p: seen.append((i, u)) or (True, "signed in")) == 0
    out = capsys.readouterr().out
    assert seen == [("https://x.service-now.com", "pdi.keepalive")]
    assert "Sup3r-Secret!" not in out and json.loads(out)["result"] == "OK"


def test_ui_login_failure_and_browser_error(capsys):
    env = {"SN_KEEPALIVE_USER": "pdi.keepalive", "SN_KEEPALIVE_PASSWORD": "pw",
           "SN_INSTANCE_URL": "https://x.service-now.com"}
    assert pdi_ui_login.main(env=env, login=lambda *a: (False, "still on the login form")) == 1

    def boom(*a):
        raise TimeoutError("page text with pw")
    assert pdi_ui_login.main(env=env, login=boom) == 1
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [x["result"] for x in lines] == ["FAILED", "FAILED"]
    assert lines[1]["reason"] == "browser error: TimeoutError"


@pytest.mark.parametrize("url,pw_visible,err,ok", [
    ("https://x.service-now.com/now/nav/ui/home", False, None, True),
    ("https://x.service-now.com/sp", False, None, True),
    ("https://x.service-now.com/login.do", True, "User name or password invalid", False),
    ("https://x.service-now.com/login.do", True, None, False),
    ("https://x.service-now.com/sys_user_change_password.do", False, None, False),
    ("https://developer.servicenow.com/dev.do", False, None, False),
])
def test_assess_landing(url, pw_visible, err, ok):
    assert pdi_ui_login.assess_landing(url, pw_visible, err)[0] is ok
