"""Idempotent, E2E-gated publishing through the V3 agent endpoint, not an application."""

import argparse
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

from e2e import AGENT, PROJECT, SUBSCRIPTION, TENANT, current_user, redact


ROOT = Path(__file__).resolve().parent
BOT_NAME = "bot-servicenow-assistant-v3"
VERSION = "2"
EXPECTED_MSA_APP_ID = "978e473a-34e4-4fe9-9521-0bd885576d53"
ACTIVITY_ENDPOINT = f"{PROJECT}/agents/{AGENT}/endpoint/protocols/activityProtocol?api-version=v1"
SELECTOR = {
    "version_selection_rules": [{"type": "FixedRatio", "agent_version": VERSION, "traffic_percentage": 100}]
}
BOT_AUTH = {"type": "BotServiceRbac"}


def validate_e2e(ledger):
    if ledger.get("agent", {}).get("name") != AGENT or ledger["agent"].get("version") != VERSION:
        raise ValueError("Publishing requires the reviewed V3 agent version 2")
    check = ledger.get("runtime_check", {})
    cases = check.get("results", [])
    expected_cases = {"identity", "read", "write_guard", "confirmed_incident", "adversarial_guard"}
    if (
        check.get("passed") != 5 or check.get("total") != 5
        or len(cases) != 5
        or {case.get("scenario") for case in cases} != expected_cases
        or any(case.get("status") != "PASS" for case in cases)
        or check.get("record_cleanup_complete") is not True
        or not isinstance(check.get("conversations_created"), int) or check["conversations_created"] < 1
        or check.get("conversations_created") != check.get("conversations_deleted")
        or check.get("consent_started") is not False or check.get("tool_approvals_granted") != 0
    ):
        raise ValueError("Publishing gate is closed: all five E2E scenarios and owned cleanup must pass")
    guarded = [case for case in cases if case["scenario"] in {"write_guard", "adversarial_guard"}]
    if any(sum(turn.get("write_calls", 0) for turn in case.get("turns", [])) != 0 for case in guarded):
        raise ValueError("An unconfirmed/adversarial write occurred; publishing is forbidden")
    records = check.get("writes_created", [])
    confirmed = next(case for case in cases if case["scenario"] == "confirmed_incident")
    if len(records) != 1 or records[0].get("closed") is not True or confirmed.get("records_created") != 1:
        raise ValueError("Exactly one confirmed created record and its verified closure are required")


def endpoint_patches(agent):
    if agent.get("name") != AGENT or agent.get("instance_identity", {}).get("client_id") != EXPECTED_MSA_APP_ID:
        raise ValueError("Agent or instance identity changed; do not create a bot for a different identity")
    latest = agent.get("versions", {}).get("latest", {})
    if latest.get("version") != VERSION:
        raise ValueError("Agent version changed since E2E")
    endpoint = agent.get("agent_endpoint", {})
    schemes = endpoint.get("authorization_schemes", [])
    entra = [scheme for scheme in schemes if scheme.get("type") == "Entra"]
    if len(entra) != 1 or any(scheme.get("type") == "BotServiceTenant" for scheme in schemes):
        raise ValueError("Unexpected direct-client or tenant-wide authorization; leave it unchanged")
    protocols = copy.deepcopy(endpoint.get("protocol_configuration", {}))
    if "responses" not in protocols:
        raise ValueError("Responses protocol is missing; do not replace an unexpected endpoint")
    protocols.setdefault("activity", {})
    desired_schemes = copy.deepcopy(schemes)
    if BOT_AUTH not in desired_schemes:
        desired_schemes.append(copy.deepcopy(BOT_AUTH))
    return (
        {"agent_endpoint": {"version_selector": copy.deepcopy(SELECTOR)}},
        {"agent_endpoint": {"protocol_configuration": protocols, "authorization_schemes": desired_schemes}},
    )


def publish_body(bot_id):
    expected_suffix = f"/providers/Microsoft.BotService/botServices/{BOT_NAME}"
    if not bot_id.startswith(f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-sf-mcp-obo/") or not bot_id.endswith(expected_suffix):
        raise ValueError("Publish target must be the actual new V3 bot ARM ID")
    return {
        "agentDisplayName": "ServiceNow Assistant (V3)",
        "botServiceArmId": bot_id,
        "publishScope": "Shared",
        "publishAsAutopilot": False,
        "appVersion": "1.0.0",
        "shortDescription": "ServiceNow ITSM with delegated user identity.",
        "fullDescription": "ServiceNow ITSM assistant. All writes require an exact preview and explicit chat confirmation.",
        "developerName": "ServiceNow MCP Meta-Tool",
        "developerWebsiteUrl": "https://github.com/ozgurkarahan/snow-meta-tool",
        "privacyUrl": "https://github.com/ozgurkarahan/snow-meta-tool#readme",
        "termsOfUseUrl": "https://github.com/ozgurkarahan/snow-meta-tool#readme",
    }


def patch_if_needed(api, desired):
    before = api("GET", f"{PROJECT}/agents/{AGENT}?api-version=v1")
    if all(before["agent_endpoint"].get(key) == value for key, value in desired["agent_endpoint"].items()):
        return False
    api("PATCH", f"{PROJECT}/agents/{AGENT}?api-version=v1", desired, "application/merge-patch+json")
    after = api("GET", f"{PROJECT}/agents/{AGENT}?api-version=v1")
    if any(after["agent_endpoint"].get(key) != value for key, value in desired["agent_endpoint"].items()):
        raise ValueError("Endpoint PATCH was not verified; do not continue")
    return True


def publish_once(api, receipt, save, bot_id):
    body = publish_body(bot_id)
    previous = receipt.get("publish")
    if previous:
        if previous.get("request") != body or previous.get("status") != "published" or not previous.get("title_id"):
            raise ValueError("Prior publish is changed, failed or delivery-unknown; never blindly retry")
        return previous["title_id"]
    agent = api("GET", f"{PROJECT}/agents/{AGENT}?api-version=v1")
    if agent["agent_endpoint"].get("publish_approval_status") != "not_published":
        raise ValueError("Remote publish already exists without a local receipt; inspect it, do not republish")
    receipt["publish"] = {"request": body, "status": "delivery_unknown", "title_id": None}
    save(receipt)
    result = api("POST", f"{PROJECT}/agents/{AGENT}/microsoft365/publish?api-version=v1", body)
    if not isinstance(result.get("titleId"), str) or not result["titleId"]:
        raise ValueError("Publish did not return titleId; retain delivery-unknown state and stop")
    receipt["publish"].update(status="published", title_id=result["titleId"])
    save(receipt)
    return result["titleId"]


def az_json(arguments):
    command = [shutil.which("az") or "az", *arguments, "--subscription", SUBSCRIPTION, "--output", "json"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise ValueError(f"Azure CLI exit {result.returncode}: {redact(result.stderr)}")
    return json.loads(result.stdout)


def ensure_bot(api, receipt, save):
    path = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-sf-mcp-obo"
        f"/providers/Microsoft.BotService/botServices/{BOT_NAME}?api-version=2022-09-15"
    )
    existing = api("GET", path, allow_absent=True)
    def verify(bot):
        if bot is None:
            raise ValueError("New V3 bot was not returned after deployment")
        p = bot["properties"]
        expected = {
            "msaAppId": EXPECTED_MSA_APP_ID, "msaAppTenantId": TENANT, "msaAppType": "SingleTenant",
            "endpoint": ACTIVITY_ENDPOINT, "publicNetworkAccess": "Disabled",
        }
        if any(p.get(key) != value for key, value in expected.items()) or bot["sku"]["name"] != "F0":
            raise ValueError("Existing V3 bot contract differs; never overwrite it")
        if bot.get("kind") != "azurebot" or bot.get("location") != "global":
            raise ValueError("Existing V3 bot kind/location differs; leave it untouched")
        api("GET", path.split("?")[0] + "/channels/MsTeamsChannel?api-version=2021-03-01")
        receipt["bot_id"] = bot["id"]
        save(receipt)
        return bot["id"]

    if existing is not None:
        return verify(existing)
    template = str(ROOT.parents[1] / "infra" / "modules" / "bot-service.bicep")
    parameters = [
        f"msaAppId={EXPECTED_MSA_APP_ID}", f"tenantId={TENANT}", f"endpoint={ACTIVITY_ENDPOINT}",
    ]
    common = ["--resource-group", "rg-sf-mcp-obo", "--name", "servicenow-v3-teams-bot",
              "--template-file", template, "--parameters", *parameters]
    what_if = az_json(["deployment", "group", "what-if", *common,
                      "--result-format", "FullResourcePayloads", "--no-pretty-print"])
    changes = [change for change in what_if.get("changes", []) if change["changeType"] != "Ignore"]
    suffixes = {f"/botServices/{BOT_NAME}", f"/botServices/{BOT_NAME}/channels/MsTeamsChannel"}
    if (
        what_if.get("status") != "Succeeded" or len(changes) != 2
        or any(change["changeType"] != "Create" for change in changes)
        or {next((suffix for suffix in suffixes if change["resourceId"].endswith(suffix)), "") for change in changes} != suffixes
    ):
        raise ValueError("Bot-only what-if is not exactly the two approved new creates")
    receipt["bot_what_if"] = [{"resource_id": change["resourceId"], "change_type": "Create"} for change in changes]
    save(receipt)
    appeared = api("GET", path, allow_absent=True)
    if appeared is not None:
        return verify(appeared)
    deployment = az_json(["deployment", "group", "create", *common, "--mode", "Incremental",
                          "--query", "{state:properties.provisioningState,outputs:properties.outputs}"])
    if deployment.get("state") != "Succeeded":
        raise ValueError("Named bot-only deployment did not succeed; inspect it without blindly retrying")
    receipt["bot_id"] = deployment["outputs"]["botServiceId"]["value"]
    save(receipt)
    verified = api("GET", path)
    if verified["id"] != receipt["bot_id"]:
        raise ValueError("New bot identity/id verification failed")
    return verify(verified)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True, choices=[SUBSCRIPTION])
    parser.add_argument("--stage", required=True, choices=["pin", "bot", "configure", "read", "publish"])
    parser.add_argument("--receipt", type=Path, default=ROOT / ".local" / "teams-publication.json")
    args = parser.parse_args()
    validate_e2e(json.loads((ROOT / "deployment-ledger.json").read_text(encoding="utf-8")))
    if not args.receipt.resolve().is_relative_to((ROOT / ".local").resolve()):
        parser.error("Publishing receipt must stay under the V3 .local directory")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8")) if args.receipt.exists() else {}
    import requests

    credential, email = current_user()

    def save(value):
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(temporary, args.receipt)

    def api(method, url, body=None, content_type="application/json", allow_absent=False):
        if url.startswith(PROJECT + "/"):
            scope = "https://ai.azure.com/.default"
            extra_headers = {"Foundry-Features": "MicrosoftCopilot=V1Preview"}
        elif url.startswith(f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/"):
            scope = "https://management.azure.com/.default"
            extra_headers = {}
        else:
            raise ValueError("Unapproved request destination")
        try:
            response = requests.request(
                method, url, json=body, timeout=90, allow_redirects=False,
                headers={"Authorization": "Bearer " + credential.get_token(scope).token,
                         "Content-Type": content_type, **extra_headers},
            )
        except requests.RequestException as error:
            raise ValueError(json.dumps({
                "type": type(error).__name__, "message": redact(error),
                "http_status": None, "request_id": None,
            })) from None
        if response.status_code == 404 and allow_absent:
            return None
        if not 200 <= response.status_code < 300:
            error = response.json().get("error", {})
            raise ValueError(json.dumps({
                "http_status": response.status_code, "code": error.get("code"),
                "message": redact(error.get("message", "Provider request failed")),
                "request_id": response.headers.get("x-request-id") or response.headers.get("apim-request-id"),
            }))
        return {} if response.status_code == 204 else response.json()

    agent = api("GET", f"{PROJECT}/agents/{AGENT}?api-version=v1")
    pin, configuration = endpoint_patches(agent)
    if args.stage == "pin":
        patch_if_needed(api, pin)
        receipt["pinned_version"] = VERSION
    else:
        if agent["agent_endpoint"].get("version_selector") != SELECTOR:
            raise ValueError("Pin the E2E-passed version before any Teams operation")
        if args.stage == "bot":
            ensure_bot(api, receipt, save)
        elif args.stage == "configure":
            if not receipt.get("bot_id"):
                raise ValueError("Create/verify the isolated V3 bot before endpoint configuration")
            patch_if_needed(api, configuration)
            receipt["endpoint_configured"] = True
        elif args.stage == "read":
            if not receipt.get("endpoint_configured") or receipt.get("regression_read"):
                raise ValueError("Regression read requires configured endpoint and may run only once")
            receipt["regression_read"] = {"status": "delivery_unknown"}
            save(receipt)
            from azure.ai.projects import AIProjectClient
            from e2e import run
            with AIProjectClient(endpoint=PROJECT, credential=credential, allow_preview=True) as project:
                with project.get_openai_client(
                    agent_name=AGENT, max_retries=0, timeout=300,
                    default_headers={"Foundry-Features": "MicrosoftCopilot=V1Preview"},
                ) as client:
                    result = run(client, project, VERSION, "SNV3-publish-read", email, only={"read"})
            receipt["regression_read"] = result
            save(receipt)
            if result.get("fatal_error") or result["cleanup_errors"] or any(
                case["status"] != "PASS" for case in result["results"]
            ):
                raise ValueError("Responses regression/consent gate failed; do not publish or retry")
        elif args.stage == "publish":
            regression = receipt.get("regression_read", {})
            if not regression.get("results") or any(case["status"] != "PASS" for case in regression["results"]):
                raise ValueError("One successful bounded Responses regression is required before publishing")
            if not receipt.get("publish"):
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                with args.receipt.with_suffix(".publish-attempt.json").open("x", encoding="utf-8") as claim:
                    json.dump(publish_body(receipt["bot_id"]), claim)
            publish_once(api, receipt, save, receipt["bot_id"])
            receipt["post_publish_verified"] = False
            save(receipt)
            published = api("GET", f"{PROJECT}/agents/{AGENT}?api-version=v1")["agent_endpoint"]
            if published.get("version_selector") != SELECTOR or any(
                published.get(key) != value for key, value in configuration["agent_endpoint"].items()
            ):
                raise ValueError("Post-publish endpoint preservation failed; retain titleId and do not republish")
            receipt["post_publish_verified"] = True
    save(receipt)
    print(json.dumps({"stage": args.stage, "pinned_version": receipt.get("pinned_version"),
                      "bot_id": receipt.get("bot_id"), "title_id": receipt.get("publish", {}).get("title_id")}))


if __name__ == "__main__":
    main()
