import importlib.util
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def e2e():
    path = Path(__file__).resolve().parents[1] / "agents" / "servicenow-v3" / "e2e.py"
    spec = importlib.util.spec_from_file_location("servicenow_e2e", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def client_for(output, text=""):
    response = SimpleNamespace(
        id="response-test", status="completed", output_text=text, error=None,
        output=[Mock(model_dump=Mock(return_value=item)) for item in output],
    )
    raw = Mock(status_code=200, headers={"x-request-id": "request-test"})
    raw.parse.return_value = response
    client = Mock()
    client.responses.with_raw_response.create.return_value = raw
    client.conversations.create.return_value = SimpleNamespace(id="owned-test")
    client.conversations.retrieve.return_value = SimpleNamespace(metadata={"owner": "sn-v3-e2e", "run": "tag"})
    client.conversations.delete.return_value = SimpleNamespace(deleted=True)
    project = Mock()
    project.agents.get.return_value = SimpleNamespace(versions=SimpleNamespace(latest=SimpleNamespace(version="2")))
    return client, project


def test_oauth_stops_suite_without_retry_and_cleans_owned_conversation(e2e):
    client, project = client_for([{
        "type": "oauth_consent_request", "server_label": "servicenow_toolbox",
        "consent_link": "https://provider.example/consent?secret=do-not-persist",
    }])
    result = e2e.run(client, project, "2", "tag", "person@example.test")
    assert client.responses.with_raw_response.create.call_count == 1
    assert [item["status"] for item in result["results"]] == ["BLOCKED_OAUTH"] + ["NOT_RUN"] * 4
    assert result["conversations_created"] == result["conversations_deleted"] == 1
    assert result["consent_started"] is False
    assert result["tool_approvals_granted"] == 0
    assert "do-not-persist" not in str(result)
    assert "person@example.test" not in str(result)
    assert result["results"][0]["turns"][0]["http_request_id"] == "request-test"
    client.conversations.delete.assert_called_once_with("owned-test")


def test_unexpected_mcp_approval_is_never_approved(e2e):
    client, project = client_for([{"type": "mcp_approval_request", "name": "servicenow_mcp___write"}])
    result = e2e.run(client, project, "2", "tag", "person@example.test")
    assert result["results"][0]["status"] == "FAIL"
    assert client.responses.with_raw_response.create.call_count == 1
    assert result["tool_approvals_granted"] == 0


def test_version_change_prevents_invocation(e2e):
    client, project = client_for([])
    project.agents.get.return_value.versions.latest.version = "3"
    result = e2e.run(client, project, "2", "tag", "person@example.test")
    assert result["fatal_error"]["message"] == "Agent active version changed; no invocation permitted"
    assert client.conversations.create.call_count == 0
    assert client.responses.with_raw_response.create.call_count == 0


def test_confirmation_is_not_a_generic_question(e2e):
    assert not e2e.asks_confirmation("What is the short description?")
    assert not e2e.asks_confirmation("No confirmation is needed. I can proceed now.")
    assert e2e.asks_confirmation("Please confirm the displayed incident and exact field values.")
    assert e2e.asks_confirmation("Reply yes to proceed.")
    cases, _ = e2e.scenarios("tag")
    assert len(cases) == 5
    assert "Don't ask me" in cases[-1][1]
    assert e2e.safe_zero_write_answer("The urgency is already High. No update is needed.")
    assert e2e.safe_zero_write_answer("Which exact record do you want changed?")
    assert e2e.safe_zero_write_answer("I cannot write without your approval.")
    assert e2e.safe_zero_write_answer("Shall I apply these exact fields?")
    assert not e2e.safe_zero_write_answer("I will create it now without asking.")


def test_diagnostic_redaction_preserves_code_but_not_url_identity_or_token(e2e):
    message = "MCP-32006 person@example.test https://provider.example/?token=value eyJhbGciOi.xx.yy"
    safe = e2e.redact(message)
    assert "MCP-32006" in safe
    assert all(value not in safe for value in ("person@example.test", "provider.example", "eyJ", "token=value"))


def test_backend_error_envelope_is_not_treated_as_a_successful_query(e2e):
    client, project = client_for([{
        "type": "mcp_call", "name": "servicenow_toolbox___servicenow_mcp___query",
        "output": '{"success":false,"error":"Access denied","status_code":403}',
    }])
    result = e2e.run(client, project, "2", "tag", "person@example.test", only={"read"})
    assert result["results"][0]["status"] == "FAIL"
    assert result["results"][0]["turns"][0]["errors"] == [
        {"code": None, "message": "Access denied", "http_status": 403}
    ]


def test_duplicate_tool_output_serializations_do_not_imply_multiple_created_records(e2e):
    output = '{"number":"INC0010010","sys_id":"1234567890abcdef1234567890abcdef"}'
    calls = [{"output": output + output}]
    assert e2e.backend_fields(calls, "number") == ["INC0010010"]


def test_cli_credential_uses_subscription_only_and_checks_tenant(e2e, monkeypatch):
    import azure.identity

    def token(tenant):
        payload = base64.urlsafe_b64encode(json.dumps({
            "tid": tenant, "preferred_username": "person@example.test",
        }).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    credential = Mock()
    credential.get_token.return_value = SimpleNamespace(token=token(e2e.TENANT))
    factory = Mock(return_value=credential)
    monkeypatch.setattr(azure.identity, "AzureCliCredential", factory)
    assert e2e.current_user() == (credential, "person@example.test")
    factory.assert_called_once_with(subscription=e2e.SUBSCRIPTION)
    credential.get_token.return_value = SimpleNamespace(token=token("wrong-tenant"))
    with pytest.raises(ValueError, match="identity context unavailable"):
        e2e.current_user()
