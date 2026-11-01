import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "agents" / "servicenow-v3"
PROJECT_ENDPOINT = "https://foundry-test.services.ai.azure.com/api/projects/test-project"
VERSION = "3"


@pytest.fixture
def binding():
    spec = importlib.util.spec_from_file_location("bind_toolbox", STAGING / "bind_toolbox.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source():
    return yaml.safe_load((STAGING / "azure.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def toolbox(source):
    return {
        "endpoint": f"{PROJECT_ENDPOINT}/toolboxes/servicenow-itsm-v3/versions/{VERSION}/mcp?api-version=v1",
        "toolbox": {"name": "servicenow-itsm-v3", "default_version": "99"},
        "version": {
            "name": "servicenow-itsm-v3",
            "version": VERSION,
            "tools": copy.deepcopy(source["services"]["servicenow-itsm-v3"]["tools"]),
            "skills": copy.deepcopy(source["services"]["servicenow-itsm-v3"]["skills"]),
        },
    }


def test_isolated_manifest_has_exact_managed_harness_and_toolbox_contract(source):
    root_manifest = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    assert set(root_manifest["services"]) == {"servicenow-mcp"}
    assert root_manifest["hooks"]["postprovision"]["run"] == "hooks/postprovision.sh"
    assert set(source["services"]) == {"servicenow-itsm-v3", "servicenow-assistant-v3"}
    assert "infra" not in source
    assert "hooks" not in source
    toolbox_service = source["services"]["servicenow-itsm-v3"]
    assert toolbox_service["host"] == "azure.ai.toolbox"
    assert "connections" not in toolbox_service
    assert toolbox_service["skills"] == [{"name": "servicenow-itsm", "version": "2"}]
    assert toolbox_service["tools"] == [{
        "type": "mcp",
        "server_label": "servicenow_mcp",
        "server_url": "https://apim-sf-mcp-obo.azure-api.net/servicenow-mcp-obo/mcp",
        "project_connection_id": "servicenow-obo-oauth2",
        "require_approval": "never",
        "allowed_tools": ["discover", "query", "write"],
    }]
    agent = source["services"]["servicenow-assistant-v3"]
    assert agent["host"] == "azure.ai.agent"
    assert agent["kind"] == "prompt"
    assert agent["name"] == "servicenow-assistant-v3"
    assert agent["model"] == "gpt-5.4-pro"
    assert agent["harness"] == {"type": "microsoft_copilot_preview"}
    assert agent["skills"] == [{"name": "servicenow-itsm", "version": "2"}]
    assert agent["tools"] == [{
        "type": "mcp",
        "server_label": "servicenow_toolbox",
        "server_url": "${SERVICENOW_V3_TOOLBOX_ENDPOINT}",
        "project_connection_id": "servicenow-itsm-v3-toolbox",
        "require_approval": "never",
        "allowed_tools": [
            "servicenow_mcp___discover", "servicenow_mcp___query", "servicenow_mcp___write",
        ],
    }]
    assert "memory" not in json.dumps(agent).lower()


def test_staging_prompt_preserves_explicit_writes_and_read_only_identity(source):
    instructions = source["services"]["servicenow-assistant-v3"]["instructions"]
    assert "ALWAYS confirm with the user before create, update, or delete." in instructions
    assert "Wait for explicit user confirmation of that exact action" in instructions
    assert "Confirmation must be a subsequent user message" in instructions
    assert '"don\'t ask me"' in instructions
    assert "Never waive this rule." in instructions
    assert "MCP approval prompts are disabled because Teams cannot render them" in instructions
    assert "Identity lookup is read-only" in instructions
    assert "Never create, update, or delete sys_user, assign roles, or impersonate" in instructions
    assert "On 401 or 403, report the authorization failure" in instructions
    assert "Do not switch accounts, use service credentials" in instructions


def test_binding_uses_requested_version_not_default_pointer(binding, toolbox):
    manifest, connection = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
    assert set(manifest["services"]) == {"servicenow-assistant-v3"}
    agent = manifest["services"]["servicenow-assistant-v3"]
    assert agent["tools"][0]["server_url"] == toolbox["endpoint"]
    assert connection == {
        "properties": {
            "authType": "UserEntraToken",
            "category": "RemoteTool",
            "target": toolbox["endpoint"],
            "audience": "https://ai.azure.com",
            "metadata": {
                "type": "toolbox_MCP", "toolboxName": "servicenow-itsm-v3", "toolboxVersion": VERSION,
            },
            "isSharedToAll": False,
        },
    }
    assert "${" not in yaml.safe_dump(manifest)
    assert agent["skills"] == [{"name": "servicenow-itsm", "version": "2"}]


@pytest.mark.parametrize("version", ["latest", "", "0", "03", "3/other", 3, None])
def test_binding_rejects_non_explicit_versions(binding, toolbox, version):
    with pytest.raises(ValueError, match="explicit positive integer"):
        binding.prepare_binding(toolbox, version, PROJECT_ENDPOINT)


@pytest.mark.parametrize("endpoint", [
    "https://apim.example/api/projects/test",
    "http://foundry-test.services.ai.azure.com/api/projects/test",
    "https://foundry-test.services.ai.azure.com.evil.example/api/projects/test",
    "https://foundry-test.services.ai.azure.com/api/projects/test?extra=1",
    "https://foundry-test.services.ai.azure.com/api/projects/test#fragment",
    "https://user@foundry-test.services.ai.azure.com/api/projects/test",
])
def test_binding_rejects_non_first_party_project_endpoint(binding, toolbox, endpoint):
    with pytest.raises(ValueError, match="exact https"):
        binding.prepare_binding(toolbox, VERSION, endpoint)


@pytest.mark.parametrize("field,value", [
    ("endpoint", f"{PROJECT_ENDPOINT}/toolboxes/servicenow-itsm-v3/mcp?api-version=v1"),
    ("endpoint", f"{PROJECT_ENDPOINT}/toolboxes/servicenow-itsm-v3/versions/99/mcp?api-version=v1"),
    ("endpoint", f"{PROJECT_ENDPOINT}/toolboxes/servicenow-itsm-v3/versions/{VERSION}/mcp?api-version=v1&extra=1"),
    ("toolbox", {"name": "another-toolbox"}),
    ("version", {"name": "servicenow-itsm-v3", "version": "99"}),
])
def test_binding_fails_closed_on_endpoint_name_or_version_mismatch(binding, toolbox, field, value):
    toolbox[field] = value
    with pytest.raises(ValueError):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)


@pytest.mark.parametrize("field,value", [
    ("server_url", "https://another-backend.example/mcp"),
    ("project_connection_id", "servicenow-obo"),
    ("require_approval", "always"),
    ("allowed_tools", ["discover", "query"]),
    ("allowed_tools", ["discover", "query", "write", "other"]),
])
def test_binding_checks_deployed_backend_identity_approval_and_allowlist(binding, toolbox, field, value):
    toolbox["version"]["tools"][0][field] = value
    with pytest.raises(ValueError, match="OAuth2 backend"):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)


def test_binding_rejects_missing_data_additional_tools_and_toolbox_skills(binding, toolbox):
    with pytest.raises(ValueError, match="JSON object"):
        binding.prepare_binding(None, VERSION, PROJECT_ENDPOINT)
    missing = copy.deepcopy(toolbox)
    del missing["version"]["tools"][0]["allowed_tools"]
    with pytest.raises(ValueError, match="OAuth2 backend"):
        binding.prepare_binding(missing, VERSION, PROJECT_ENDPOINT)
    additional = copy.deepcopy(toolbox)
    additional["version"]["tools"].append(copy.deepcopy(additional["version"]["tools"][0]))
    with pytest.raises(ValueError, match="exactly one"):
        binding.prepare_binding(additional, VERSION, PROJECT_ENDPOINT)
    toolbox["version"]["skills"] = [{"name": "servicenow-itsm", "version": "1"}]
    with pytest.raises(ValueError, match="pinned servicenow-itsm"):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)


def test_matching_existing_connection_is_validated_without_changes(binding, toolbox):
    _, expected = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
    existing = {"name": binding.toolbox_connection_name(VERSION), **copy.deepcopy(expected)}
    original = copy.deepcopy(existing)
    manifest, connection = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT, existing)
    assert existing == original
    assert connection == expected
    assert manifest["services"]["servicenow-assistant-v3"]["tools"][0]["server_url"] == existing["properties"]["target"]


@pytest.mark.parametrize("field,value", [
    ("authType", "OAuth2"),
    ("category", "Generic"),
    ("target", f"{PROJECT_ENDPOINT}/toolboxes/servicenow-itsm-v3/versions/99/mcp?api-version=v1"),
    ("audience", "https://management.azure.com"),
    ("metadata", {"type": "custom_MCP", "toolboxName": "servicenow-itsm-v3", "toolboxVersion": VERSION}),
    ("metadata", {"type": "toolbox_MCP", "toolboxName": "servicenow-itsm-v3", "toolboxVersion": "99"}),
])
def test_existing_connection_mismatch_is_an_error_not_an_overwrite(binding, toolbox, field, value):
    _, connection = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
    existing = {"name": binding.toolbox_connection_name(VERSION), **connection}
    existing["properties"][field] = value
    original = copy.deepcopy(existing)
    with pytest.raises(ValueError, match="leave it untouched"):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT, existing)
    assert existing == original


def test_binding_cannot_replace_backend_connection(binding, toolbox):
    existing = {"name": "servicenow-obo-oauth2", "properties": {"authType": "OAuth2"}}
    original = copy.deepcopy(existing)
    with pytest.raises(ValueError, match="never replace another connection"):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT, existing)
    assert existing == original


def test_binding_outputs_are_idempotent_and_not_overwritten(binding, toolbox, tmp_path):
    manifest, connection = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
    binding.write_binding(manifest, connection, tmp_path)
    binding.write_binding(manifest, connection, tmp_path)
    assert yaml.safe_load((tmp_path / "azure.yaml").read_text(encoding="utf-8")) == manifest
    assert json.loads((tmp_path / "toolbox-connection.json").read_text(encoding="utf-8")) == connection
    original = (tmp_path / "azure.yaml").read_bytes()
    different = copy.deepcopy(connection)
    different["properties"]["target"] += "&different=1"
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        binding.write_binding(manifest, different, tmp_path)
    assert (tmp_path / "azure.yaml").read_bytes() == original
    assert json.loads((tmp_path / "toolbox-connection.json").read_text(encoding="utf-8")) == connection


def test_binding_cli_generates_concrete_agent_only_files_locally(toolbox, tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    for filename in ("bind_toolbox.py", "azure.yaml"):
        (staging / filename).write_bytes((STAGING / filename).read_bytes())
    show_path = staging / "toolbox.json"
    show_path.write_text(json.dumps(toolbox), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(staging / "bind_toolbox.py"), "--toolbox-json", str(show_path),
         "--version", VERSION, "--project-endpoint", PROJECT_ENDPOINT],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "No Azure resources were changed." in result.stdout
    bound = staging / ".local" / f"version-{VERSION}"
    manifest = yaml.safe_load((bound / "azure.yaml").read_text(encoding="utf-8"))
    connection = json.loads((bound / "toolbox-connection.json").read_text(encoding="utf-8"))
    assert set(manifest["services"]) == {"servicenow-assistant-v3"}
    assert manifest["services"]["servicenow-assistant-v3"]["tools"][0]["server_url"] == connection["properties"]["target"] == toolbox["endpoint"]

    result = subprocess.run(
        [sys.executable, str(staging / "bind_toolbox.py"), "--toolbox-json", str(show_path),
         "--version", "99", "--project-endpoint", PROJECT_ENDPOINT],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert not (staging / ".local" / "version-99").exists()


def test_binding_cli_rejects_null_existing_connection(binding, toolbox, tmp_path, monkeypatch, capsys):
    show_path = tmp_path / "toolbox.json"
    show_path.write_text(json.dumps(toolbox), encoding="utf-8")
    connection_path = tmp_path / "connection.json"
    connection_path.write_text("null", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "bind_toolbox.py", "--toolbox-json", str(show_path), "--version", VERSION,
        "--project-endpoint", PROJECT_ENDPOINT, "--connection-json", str(connection_path),
    ])
    with pytest.raises(SystemExit) as error:
        binding.main()
    assert error.value.code != 0
    assert "not an absence marker" in capsys.readouterr().err


def test_container_image_preservation_and_memory_sizing_remain():
    parameters = (ROOT / "infra" / "main.bicepparam").read_text(encoding="utf-8")
    container = (ROOT / "infra" / "modules" / "servicenow-mcp-app.bicep").read_text(encoding="utf-8")
    assert "param servicenowMcpImageName = readEnvironmentVariable('SERVICE_SERVICENOW_MCP_IMAGE_NAME', '')" in parameters
    assert "memory: '0.5Gi'" in container


def test_live_ledger_retains_exact_staging_contract_without_credentials(source):
    ledger = json.loads((STAGING / "deployment-ledger.json").read_text(encoding="utf-8"))
    toolbox = ledger["toolbox"]
    connection = ledger["connection"]
    agent = ledger["agent"]
    expected_endpoint = (
        f"{ledger['project_endpoint']}/toolboxes/{toolbox['name']}/versions/{toolbox['version']}/mcp?api-version=v1"
    )
    assert toolbox["endpoint"] == connection["target"] == agent["toolbox_endpoint"] == expected_endpoint
    assert connection["metadata"] == {
        "type": "toolbox_MCP", "toolboxName": toolbox["name"], "toolboxVersion": toolbox["version"],
    }
    assert connection["auth_type"] == "UserEntraToken"
    assert connection["category"] == "RemoteTool"
    assert connection["audience"] == "https://ai.azure.com"
    assert connection["created_only_after_absence"] is True
    assert toolbox["require_approval"] == agent["require_approval"] == "never"
    assert toolbox["allowed_tools"] == source["services"]["servicenow-itsm-v3"]["tools"][0]["allowed_tools"]
    assert toolbox["backend_connection"] == "servicenow-obo-oauth2"
    assert toolbox["backend_auth_type"] == "OAuth2"
    assert toolbox["skills"] == source["services"]["servicenow-itsm-v3"]["skills"]
    assert agent["allowed_tools"] == source["services"]["servicenow-assistant-v3"]["tools"][0]["allowed_tools"]
    assert agent["model"] == source["services"]["servicenow-assistant-v3"]["model"]
    assert agent["harness_type"] == source["services"]["servicenow-assistant-v3"]["harness"]["type"]
    assert agent["skills"] == source["services"]["servicenow-assistant-v3"]["skills"]
    assert ledger["runtime_check"]["consent_started"] is False
    assert ledger["runtime_check"]["tool_approvals_granted"] == 0
    assert ledger["runtime_check"]["conversations_deleted"] == ledger["runtime_check"]["conversations_created"]
    assert ledger["safeguards"]["manual_permission_grants"] is False
    assert ledger["safeguards"]["foundry_managed_agent_identities_generated"] is True

    def check_keys(value):
        if isinstance(value, dict):
            assert not set(value).intersection({
                "credentials", "client_id", "principal_id", "oid", "user_email",
                "access_token", "refresh_token", "authorization", "consent_link", "consent_url",
            })
            for child in value.values():
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    check_keys(ledger)
    runbook = (STAGING / "README.md").read_text(encoding="utf-8")
    assert "$env:AZURE_DEV_USER_AGENT='microsoft_foundry_skill'" in runbook
    assert "$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS='true'" in runbook
    assert "$env:AZD_AGENT_SKIP_ROLE_ASSIGNMENTS='true'" in runbook
    assert 'agent_name="servicenow-assistant-v3"' in runbook
    assert '"Foundry-Features": "MicrosoftCopilot=V1Preview"' in runbook


def test_binding_generates_new_connection_name_without_rewriting_v1(binding, toolbox):
    manifest, _ = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
    name = manifest["services"]["servicenow-assistant-v3"]["tools"][0]["project_connection_id"]
    assert name == f"servicenow-itsm-v3-toolbox-v{VERSION}"
    assert binding.toolbox_connection_name("1") == "servicenow-itsm-v3-toolbox"
    old = {"name": "servicenow-itsm-v3-toolbox", "properties": {"target": "old-endpoint"}}
    original = copy.deepcopy(old)
    with pytest.raises(ValueError, match="never replace another"):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT, old)
    assert old == original


def test_binding_accepts_provider_skill_reference_discriminator(binding, toolbox):
    toolbox["version"]["skills"][0]["type"] = "skill_reference"
    manifest, _ = binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
    assert manifest["services"]["servicenow-assistant-v3"]["skills"] == [
        {"name": "servicenow-itsm", "version": "2"}
    ]
    toolbox["version"]["skills"][0]["type"] = "other"
    with pytest.raises(ValueError, match="pinned servicenow-itsm"):
        binding.prepare_binding(toolbox, VERSION, PROJECT_ENDPOINT)
