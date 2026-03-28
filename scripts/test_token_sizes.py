"""Test MCP tool responses and measure payload sizes for token optimization.

Calls discover, query, and write tools via the MCP Streamable HTTP endpoint
(through APIM OBO) and reports response sizes in bytes and estimated tokens.
"""

import json
import subprocess
import sys
import httpx
import asyncio


def get_azure_token():
    """Get Azure AD token for https://ai.azure.com."""
    result = subprocess.run(
        'az account get-access-token --resource "https://ai.azure.com" --query accessToken -o tsv',
        capture_output=True, text=True, shell=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip()


async def call_mcp_tool(client, endpoint, token, session_id, tool_name, arguments):
    """Call an MCP tool and return the response content + size."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    # JSON-RPC request
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    resp = await client.post(endpoint, json=payload, headers=headers)

    # Handle SSE response
    content_type = resp.headers.get("content-type", "")
    raw_text = resp.text

    if "text/event-stream" in content_type:
        # Parse SSE events
        result_data = None
        for line in raw_text.split("\n"):
            if line.startswith("data: "):
                try:
                    event_data = json.loads(line[6:])
                    if "result" in event_data:
                        result_data = event_data["result"]
                except json.JSONDecodeError:
                    pass
        return result_data, len(raw_text)
    else:
        try:
            data = resp.json()
            return data, len(raw_text)
        except Exception:
            return raw_text, len(raw_text)


async def initialize_session(client, endpoint, token):
    """Initialize MCP session."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "token-size-test", "version": "1.0"},
        },
    }
    resp = await client.post(endpoint, json=payload, headers=headers)
    session_id = resp.headers.get("mcp-session-id")

    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if "result" in data:
                        print(f"  Session initialized: {data['result'].get('serverInfo', {})}")
                except json.JSONDecodeError:
                    pass

    return session_id


def estimate_tokens(text):
    """Rough estimate: ~4 chars per token for English/JSON."""
    if isinstance(text, dict):
        text = json.dumps(text)
    elif not isinstance(text, str):
        text = str(text)
    return len(text) // 4


def print_size_report(label, data):
    """Print size report for a tool response."""
    if data is None:
        print(f"\n{'='*60}")
        print(f"  {label}: NO DATA")
        return

    json_str = json.dumps(data) if not isinstance(data, str) else data
    bytes_size = len(json_str.encode("utf-8"))
    est_tokens = estimate_tokens(json_str)

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Bytes: {bytes_size:,}")
    print(f"  Est. tokens: {est_tokens:,}")

    # Try to extract the tool result content
    if isinstance(data, dict):
        # MCP result has content array with text items
        content = data.get("content", [])
        if content:
            for item in content:
                if item.get("type") == "text":
                    text = item["text"]
                    try:
                        parsed = json.loads(text)
                        tool_json = json.dumps(parsed)
                        print(f"  Tool result bytes: {len(tool_json.encode('utf-8')):,}")
                        print(f"  Tool result tokens: {estimate_tokens(tool_json):,}")

                        # Drill into structure
                        if "fields" in parsed:
                            print(f"  Fields count: {parsed.get('fieldCount', len(parsed['fields']))}")
                            if parsed["fields"]:
                                sample = json.dumps(parsed["fields"][0])
                                print(f"  Sample field ({len(sample)} bytes): {sample[:200]}...")
                        if "records" in parsed:
                            print(f"  Records count: {parsed.get('returned', len(parsed['records']))}")
                            if parsed["records"]:
                                sample = json.dumps(parsed["records"][0])
                                print(f"  Sample record ({len(sample)} bytes): {sample[:300]}...")
                        if "record" in parsed:
                            rec_json = json.dumps(parsed["record"])
                            print(f"  Returned record: {len(rec_json):,} bytes ({estimate_tokens(rec_json):,} tokens)")
                        if "tables" in parsed:
                            print(f"  Tables count: {parsed.get('count', len(parsed['tables']))}")
                    except json.JSONDecodeError:
                        print(f"  Raw text ({len(text)} chars): {text[:200]}...")


async def main():
    token = get_azure_token()
    if not token:
        print("ERROR: Could not get Azure AD token")
        sys.exit(1)
    print(f"Azure AD token: {len(token)} chars")

    endpoint = "https://apim-sf-mcp-obo.azure-api.net/servicenow-mcp-obo/mcp"
    print(f"Endpoint: {endpoint}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Initialize session
        print("\n--- Initializing MCP session ---")
        session_id = await initialize_session(client, endpoint, token)
        print(f"  Session ID: {session_id}")

        if not session_id:
            print("ERROR: No session ID returned. Check APIM/Container App health.")
            sys.exit(1)

        # Test 1: discover(filter="incident") -- table search
        print("\n--- Test 1: discover(filter='incident') ---")
        result, raw_size = await call_mcp_tool(
            client, endpoint, token, session_id,
            "discover", {"filter": "incident"}
        )
        print_size_report("discover(filter='incident')", result)

        # Test 2: discover(table="incident") -- field metadata
        print("\n--- Test 2: discover(table='incident') ---")
        result, raw_size = await call_mcp_tool(
            client, endpoint, token, session_id,
            "discover", {"table": "incident"}
        )
        print_size_report("discover(table='incident')", result)

        # Test 3: discover(table="incident", include_choices=True) -- with choices
        print("\n--- Test 3: discover(table='incident', include_choices=True) ---")
        result, raw_size = await call_mcp_tool(
            client, endpoint, token, session_id,
            "discover", {"table": "incident", "include_choices": True}
        )
        print_size_report("discover(table='incident', include_choices=True)", result)

        # Test 4: query with all fields (no fields param)
        print("\n--- Test 4: query(table='incident', limit=5) -- ALL fields ---")
        result, raw_size = await call_mcp_tool(
            client, endpoint, token, session_id,
            "query", {"table": "incident", "limit": 5}
        )
        print_size_report("query(table='incident', limit=5) ALL FIELDS", result)

        # Test 5: query with specific fields
        print("\n--- Test 5: query(table='incident', fields='number,short_description,priority,state', limit=5) ---")
        result, raw_size = await call_mcp_tool(
            client, endpoint, token, session_id,
            "query", {
                "table": "incident",
                "fields": "number,short_description,priority,state",
                "limit": 5,
            }
        )
        print_size_report("query(table='incident', fields=4, limit=5)", result)

        # Test 6: query aggregate
        print("\n--- Test 6: query(table='incident', aggregate=True, group_by='priority') ---")
        result, raw_size = await call_mcp_tool(
            client, endpoint, token, session_id,
            "query", {"table": "incident", "aggregate": True, "group_by": "priority"}
        )
        print_size_report("query(aggregate, group_by=priority)", result)

        print(f"\n{'='*60}")
        print("DONE -- all measurements collected")


if __name__ == "__main__":
    asyncio.run(main())
