"""Bind staging source to one verified Toolbox version without Azure writes."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parent
TOOLBOX_NAME = "servicenow-itsm-v3"
AGENT_NAME = "servicenow-assistant-v3"
CONNECTION_NAME = "servicenow-itsm-v3-toolbox"


def prepare_binding(toolbox_document, version, project_endpoint, existing_connection=None):
    if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("An explicit positive integer Toolbox version is required; latest is not supported.")
    if not isinstance(project_endpoint, str):
        raise ValueError("A first-party Foundry project endpoint is required.")
    project_endpoint = project_endpoint.rstrip("/")
    project_url = urlsplit(project_endpoint)
    if (
        project_url.scheme != "https"
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.services\.ai\.azure\.com", project_url.netloc)
        or not re.fullmatch(r"/api/projects/[A-Za-z0-9_-]+", project_url.path)
        or project_url.query
        or project_url.fragment
    ):
        raise ValueError("Expected an exact https://<account>.services.ai.azure.com/api/projects/<project> endpoint.")
    endpoint = f"{project_endpoint}/toolboxes/{TOOLBOX_NAME}/versions/{version}/mcp?api-version=v1"

    if not isinstance(toolbox_document, dict):
        raise ValueError("Toolbox show output must be a JSON object.")
    toolbox = toolbox_document.get("toolbox")
    deployed_version = toolbox_document.get("version")
    if not isinstance(toolbox, dict) or toolbox.get("name") != TOOLBOX_NAME:
        raise ValueError(f"Toolbox show output must identify {TOOLBOX_NAME}.")
    if (
        not isinstance(deployed_version, dict)
        or deployed_version.get("name") != TOOLBOX_NAME
        or deployed_version.get("version") != version
    ):
        raise ValueError("Returned Toolbox version does not match the explicitly requested version.")
    if toolbox_document.get("endpoint") != endpoint:
        raise ValueError("Returned Toolbox MCP endpoint does not exactly match the project and requested version.")

    manifest = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    expected_tools = manifest["services"][TOOLBOX_NAME]["tools"]
    deployed_tools = deployed_version.get("tools")
    if not isinstance(deployed_tools, list) or len(deployed_tools) != 1:
        raise ValueError("The Toolbox must contain exactly one raw ServiceNow MCP tool.")
    deployed_tool = deployed_tools[0]
    if not isinstance(deployed_tool, dict) or any(
        deployed_tool.get(key) != value for key, value in expected_tools[0].items()
    ):
        raise ValueError("Toolbox tools must match the staged OAuth2 backend, approval policy, and exact allowlist.")
    if deployed_version.get("skills") != []:
        raise ValueError("Toolbox skills must be empty; servicenow-itsm version 2 belongs on the agent.")

    connection = {
        "properties": {
            "authType": "UserEntraToken",
            "category": "RemoteTool",
            "target": endpoint,
            "audience": "https://ai.azure.com",
            "metadata": {
                "type": "toolbox_MCP",
                "toolboxName": TOOLBOX_NAME,
                "toolboxVersion": version,
            },
            "isSharedToAll": False,
        },
    }
    if existing_connection is not None:
        if not isinstance(existing_connection, dict) or existing_connection.get("name") != CONNECTION_NAME:
            raise ValueError(f"Existing connection must identify {CONNECTION_NAME}; never replace another connection.")
        properties = existing_connection.get("properties")
        if not isinstance(properties, dict):
            raise ValueError("Existing Toolbox connection has no properties.")
        for key in ("authType", "category", "target", "audience"):
            if properties.get(key) != connection["properties"][key]:
                raise ValueError(f"Existing Toolbox connection {key} mismatch; leave it untouched.")
        metadata = properties.get("metadata")
        if not isinstance(metadata, dict) or any(
            metadata.get(key) != value for key, value in connection["properties"]["metadata"].items()
        ):
            raise ValueError("Existing Toolbox connection metadata mismatch; leave it untouched.")

    # Deploy the bound agent alone so its immutable Toolbox version is not recreated.
    agent = manifest["services"][AGENT_NAME]
    agent["tools"][0]["server_url"] = endpoint
    manifest["services"] = {AGENT_NAME: agent}
    return manifest, connection


def write_binding(manifest, connection, output_dir):
    output_dir = Path(output_dir)
    contents = {
        "azure.yaml": yaml.safe_dump(manifest, sort_keys=False, allow_unicode=False),
        "toolbox-connection.json": json.dumps(connection, indent=2) + "\n",
    }
    for filename, content in contents.items():
        path = output_dir / filename
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Refusing to overwrite a different binding at {path}. Use a new version-specific output directory.")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in contents.items():
        path = output_dir / filename
        if not path.exists():
            with path.open("x", encoding="utf-8") as output:
                output.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolbox-json", type=Path, required=True, help="Exact-version azd ai toolbox show JSON output.")
    parser.add_argument("--version", required=True, help="Explicit immutable Toolbox version; not the default pointer.")
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--connection-json", type=Path, help="ARM GET result for the existing staging Toolbox connection, if present.")
    parser.add_argument("--output-dir", type=Path, help="New binding directory under this agent's .local folder.")
    args = parser.parse_args()
    try:
        toolbox = json.loads(args.toolbox_json.read_text(encoding="utf-8-sig"))
        existing = None
        if args.connection_json is not None:
            existing = json.loads(args.connection_json.read_text(encoding="utf-8-sig"))
            if not isinstance(existing, dict):
                raise ValueError("Existing connection GET result must be a JSON object, not an absence marker.")
        manifest, connection = prepare_binding(toolbox, args.version, args.project_endpoint, existing)
        output_dir = (args.output_dir or ROOT / ".local" / f"version-{args.version}").resolve()
        if not output_dir.is_relative_to((ROOT / ".local").resolve()):
            raise ValueError("Binding output must stay under agents/servicenow-v3/.local.")
        write_binding(manifest, connection, output_dir)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.error(str(error))
    print(f"Verified Toolbox endpoint: {connection['properties']['target']}")
    print(f"Bound agent manifest and new connection payload written to {output_dir}")
    print("No Azure resources were changed. Reuse a matching connection unchanged; create only if absent.")


if __name__ == "__main__":
    main()
