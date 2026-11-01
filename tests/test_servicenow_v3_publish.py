import copy
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def publisher(monkeypatch):
    directory = ROOT / "agents" / "servicenow-v3"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("servicenow_publisher", directory / "publish_teams.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def agent(publisher):
    return {
        "name": publisher.AGENT,
        "instance_identity": {"client_id": publisher.EXPECTED_MSA_APP_ID},
        "versions": {"latest": {"version": "2"}},
        "agent_endpoint": {
            "version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": "@latest", "traffic_percentage": 100},
            ]},
            "protocol_configuration": {"responses": {}},
            "authorization_schemes": [{"type": "Entra", "isolation_key_source": {"kind": "Entra"}}],
            "publish_approval_status": "not_published",
        },
    }


def test_failed_runtime_ledger_blocks_every_mutating_stage_before_auth(publisher, monkeypatch, tmp_path):
    ledger = {"agent": {"name": publisher.AGENT, "version": "2"}, "runtime_check": {"passed": 2, "total": 5}}
    monkeypatch.setattr(publisher, "ROOT", tmp_path)
    (tmp_path / "deployment-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="gate is closed"):
        publisher.validate_e2e(ledger)
    monkeypatch.setattr(sys, "argv", [
        "publish_teams.py", "--subscription", publisher.SUBSCRIPTION, "--stage", "pin",
    ])
    with pytest.raises(ValueError, match="gate is closed"):
        publisher.main()


def test_pass_count_alone_cannot_release_publishing_gate(publisher):
    check = {
        "passed": 5, "total": 5, "record_cleanup_complete": True,
        "conversations_created": 6, "conversations_deleted": 6,
        "consent_started": False, "tool_approvals_granted": 0,
    }
    ledger = {"agent": {"name": publisher.AGENT, "version": "2"}, "runtime_check": check}
    with pytest.raises(ValueError, match="gate is closed"):
        publisher.validate_e2e(ledger)
    check["results"] = [
        {"scenario": name, "status": "PASS", "turns": [{"write_calls": 0}]}
        for name in ("identity", "read", "write_guard", "confirmed_incident", "adversarial_guard")
    ]
    check["writes_created"] = [{"closed": True}]
    check["results"][3]["records_created"] = 1
    publisher.validate_e2e(ledger)
    check["results"][-1]["turns"][0]["write_calls"] = 1
    with pytest.raises(ValueError, match="unconfirmed/adversarial"):
        publisher.validate_e2e(ledger)


def test_patches_pin_exact_version_and_preserve_full_entra_scheme_and_responses(publisher, agent):
    agent["agent_endpoint"]["authorization_schemes"][0]["extra_metadata"] = "preserve"
    original = copy.deepcopy(agent)
    pin, configure = publisher.endpoint_patches(agent)
    assert agent == original
    assert pin["agent_endpoint"]["version_selector"] == publisher.SELECTOR
    assert configure["agent_endpoint"]["protocol_configuration"] == {"responses": {}, "activity": {}}
    assert configure["agent_endpoint"]["authorization_schemes"] == (
        original["agent_endpoint"]["authorization_schemes"] + [{"type": "BotServiceRbac"}]
    )
    agent["agent_endpoint"].update(configure["agent_endpoint"])
    assert publisher.endpoint_patches(agent)[1] == configure


@pytest.mark.parametrize("mutation", ["agent", "identity", "version", "tenant_auth", "missing_responses"])
def test_unexpected_agent_or_endpoint_is_not_overwritten(publisher, agent, mutation):
    if mutation == "agent":
        agent["name"] = "servicenow-assistant"
    elif mutation == "identity":
        agent["instance_identity"]["client_id"] = "legacy-application-id"
    elif mutation == "version":
        agent["versions"]["latest"]["version"] = "3"
    elif mutation == "tenant_auth":
        agent["agent_endpoint"]["authorization_schemes"].append({"type": "BotServiceTenant"})
    else:
        agent["agent_endpoint"]["protocol_configuration"] = {}
    with pytest.raises(ValueError):
        publisher.endpoint_patches(agent)


def test_matching_endpoint_skips_patch(publisher, agent):
    pin, _ = publisher.endpoint_patches(agent)
    agent["agent_endpoint"].update(pin["agent_endpoint"])
    api = Mock(return_value=agent)
    assert publisher.patch_if_needed(api, pin) is False
    assert [call.args[0] for call in api.call_args_list] == ["GET"]


def bot_id(publisher):
    return f"/subscriptions/{publisher.SUBSCRIPTION}/resourceGroups/rg-sf-mcp-obo/providers/Microsoft.BotService/botServices/{publisher.BOT_NAME}"


def test_publish_receipt_is_saved_before_post_and_skips_duplicate_delivery(publisher, agent):
    receipt, snapshots = {}, []
    api = Mock(side_effect=[agent, {"titleId": "title-test"}])
    save = lambda value: snapshots.append(copy.deepcopy(value))
    assert publisher.publish_once(api, receipt, save, bot_id(publisher)) == "title-test"
    assert snapshots[0]["publish"]["status"] == "delivery_unknown"
    assert snapshots[-1]["publish"]["status"] == "published"
    request = api.call_args_list[1].args[2]
    assert request["publishScope"] == "Shared"
    assert request["publishAsAutopilot"] is False
    assert request["appVersion"] == "1.0.0"
    assert request["botServiceArmId"] == bot_id(publisher)
    api.reset_mock()
    assert publisher.publish_once(api, receipt, save, bot_id(publisher)) == "title-test"
    api.assert_not_called()


def test_unknown_delivery_blocks_retry_and_does_not_bump_app_version(publisher, agent):
    receipt = {}
    api = Mock(side_effect=[agent, TimeoutError("unknown delivery")])
    with pytest.raises(TimeoutError):
        publisher.publish_once(api, receipt, Mock(), bot_id(publisher))
    assert receipt["publish"]["status"] == "delivery_unknown"
    api.reset_mock()
    with pytest.raises(ValueError, match="never blindly retry"):
        publisher.publish_once(api, receipt, Mock(), bot_id(publisher))
    api.assert_not_called()


def test_already_published_without_receipt_is_not_republished(publisher, agent):
    agent["agent_endpoint"]["publish_approval_status"] = "approved"
    api = Mock(return_value=agent)
    with pytest.raises(ValueError, match="without a local receipt"):
        publisher.publish_once(api, {}, Mock(), bot_id(publisher))
    assert api.call_count == 1


def test_legacy_bot_cannot_be_published(publisher):
    with pytest.raises(ValueError, match="actual new V3 bot"):
        publisher.publish_body(bot_id(publisher).replace(publisher.BOT_NAME, "agent-bot-sn-mcp-obo"))


def test_bot_what_if_modify_or_unexpected_create_prevents_deployment(publisher, monkeypatch):
    api = Mock(return_value=None)
    az = Mock(return_value={
        "status": "Succeeded", "changes": [{"resourceId": bot_id(publisher), "changeType": "Modify"}],
    })
    monkeypatch.setattr(publisher, "az_json", az)
    with pytest.raises(ValueError, match="two approved new creates"):
        publisher.ensure_bot(api, {}, Mock())
    assert az.call_count == 1
    assert az.call_args.args[0][:3] == ["deployment", "group", "what-if"]


def test_bot_only_create_verifies_real_outputs_and_teams_channel(publisher, monkeypatch):
    bot = {
        "id": bot_id(publisher), "kind": "azurebot", "location": "global", "sku": {"name": "F0"},
        "properties": {
            "msaAppId": publisher.EXPECTED_MSA_APP_ID, "msaAppTenantId": publisher.TENANT,
            "msaAppType": "SingleTenant", "endpoint": publisher.ACTIVITY_ENDPOINT, "publicNetworkAccess": "Disabled",
        },
    }
    api = Mock(side_effect=[None, None, bot, {"properties": {"channelName": "MsTeamsChannel"}}])
    az = Mock(side_effect=[
        {"status": "Succeeded", "changes": [
            {"resourceId": bot_id(publisher), "changeType": "Create"},
            {"resourceId": bot_id(publisher) + "/channels/MsTeamsChannel", "changeType": "Create"},
        ]},
        {"state": "Succeeded", "outputs": {"botServiceId": {"value": bot_id(publisher)}}},
    ])
    monkeypatch.setattr(publisher, "az_json", az)
    receipt = {}
    assert publisher.ensure_bot(api, receipt, Mock()) == bot_id(publisher)
    command = az.call_args_list[1].args[0]
    assert command[:3] == ["deployment", "group", "create"]
    assert command[command.index("--template-file") + 1].endswith("bot-service.bicep")
    assert "main.bicep" not in str(command)
    assert api.call_args.args[1].endswith("/channels/MsTeamsChannel?api-version=2021-03-01")


def test_azure_cli_always_receives_explicit_subscription(publisher, monkeypatch):
    command = Mock(return_value=Mock(returncode=0, stdout="{}", stderr=""))
    monkeypatch.setattr(publisher.subprocess, "run", command)
    publisher.az_json(["deployment", "group", "what-if"])
    arguments = command.call_args.args[0]
    assert arguments[arguments.index("--subscription") + 1] == publisher.SUBSCRIPTION
