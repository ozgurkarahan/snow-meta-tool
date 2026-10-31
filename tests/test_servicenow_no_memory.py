import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hook(monkeypatch):
    monkeypatch.setenv("AZURE_ENV_NAME", "local-test")
    monkeypatch.delenv("SN_OBO_CONNECTION_NAME", raising=False)
    monkeypatch.setenv("AI_FOUNDRY_PROJECT_ENDPOINT", "https://foundry.example/api/projects/test")
    monkeypatch.setenv("APIM_SN_MCP_OBO_ENDPOINT", "https://apim.example/servicenow-mcp-obo/mcp")
    spec = importlib.util.spec_from_file_location("postprovision", ROOT / "hooks" / "postprovision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_has_no_memory_provisioning(hook):
    assert not hasattr(hook, "create_memory_store")
    source = (ROOT / "hooks" / "postprovision.py").read_text(encoding="utf-8")
    assert "MemorySearchPreviewTool" not in source
    assert "memory_stores" not in source


def test_legacy_agent_keeps_oauth2_and_confirmation_without_memory(hook, monkeypatch):
    import azure.ai.projects
    import azure.ai.projects.models
    import azure.identity

    client = Mock()
    client.agents.create_version.return_value = SimpleNamespace(
        name="servicenow-assistant", version="8", id="test-agent"
    )
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", Mock(return_value=client))
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", Mock())
    monkeypatch.setattr(azure.ai.projects.models, "MCPTool", lambda **kwargs: kwargs)
    monkeypatch.setattr(azure.ai.projects.models, "PromptAgentDefinition", lambda **kwargs: kwargs)
    monkeypatch.setattr(hook.time, "sleep", Mock())

    assert hook.create_agent() == "8"
    call = client.agents.create_version.call_args.kwargs
    assert call["agent_name"] == "servicenow-assistant"
    definition = call["definition"]
    assert definition["model"] == "gpt-5.4"
    assert definition["tools"] == [{
        "server_label": "servicenow_mcp",
        "server_url": "https://apim.example/servicenow-mcp-obo/mcp",
        "project_connection_id": "servicenow-obo-oauth2",
        "require_approval": "never",
        "allowed_tools": ["discover", "query", "write"],
    }]
    assert "memory" not in definition["instructions"].lower()
    assert "ALWAYS confirm with the user before create, update, or delete." in definition["instructions"]
    assert client.memory_stores.mock_calls == []


@pytest.mark.parametrize("auth_type", ["OAuth2", "UserEntraToken"])
def test_existing_backend_connection_is_never_rewritten(hook, monkeypatch, auth_type):
    monkeypatch.setenv("COGNITIVE_ACCOUNT_NAME", "test-account")
    monkeypatch.setenv("AI_FOUNDRY_PROJECT_NAME", "test-project")
    calls = []

    def run(command, parse_json=False):
        calls.append(command)
        if command == "az account show --query id -o tsv":
            return "test-subscription"
        assert "--method GET" in command
        assert "/connections/servicenow-obo-oauth2?" in command
        return {"properties": {"authType": auth_type}}

    monkeypatch.setattr(hook, "run", run)
    hook.ensure_obo_connection()
    assert len(calls) == 2
    assert not any("--method PUT" in command or "--method DELETE" in command for command in calls)
