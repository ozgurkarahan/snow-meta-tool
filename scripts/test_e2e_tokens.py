"""End-to-end token measurement: call ServiceNow agent via Responses API.

Sends realistic user queries through the full Foundry agent stack
(instructions + memory injection + MCP tool calls + context accumulation)
and reports per-turn token usage from the LLM response.

Usage:
    python scripts/test_e2e_tokens.py
"""

import json
import os
import subprocess
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def load_azd_env():
    result = subprocess.run(
        "azd env get-values", capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            value = value.strip('"').strip("'")
            os.environ.setdefault(key, value)


def dump_output_items(output_items):
    """Print output items summary."""
    for i, item in enumerate(output_items):
        item_type = getattr(item, "type", "unknown")
        if item_type == "message":
            content = getattr(item, "content", [])
            for c in content:
                if hasattr(c, "text"):
                    text = c.text
                    print(f"  [message] {text[:300]}{'...' if len(text) > 300 else ''}")
        elif item_type == "mcp_call":
            name = getattr(item, "name", "?")
            server = getattr(item, "server_label", "?")
            args = getattr(item, "arguments", "")
            print(f"  [mcp_call] {server}.{name}({args[:150]})")
        elif item_type == "mcp_approval_request":
            name = getattr(item, "name", "?")
            print(f"  [mcp_approval] {name}")
        elif item_type == "oauth_consent_request":
            print(f"  [oauth_consent] {getattr(item, 'consent_link', '')[:80]}")
        else:
            print(f"  [{item_type}] {str(item)[:200]}")


def print_usage(label, response, elapsed):
    """Print token usage from response."""
    usage = getattr(response, "usage", None)
    if usage:
        input_t = getattr(usage, "input_tokens", 0)
        output_t = getattr(usage, "output_tokens", 0)
        total_t = getattr(usage, "total_tokens", 0)
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        print(f"\n  TOKEN USAGE ({label}):")
        print(f"    Input tokens:  {input_t:,}")
        print(f"    Output tokens: {output_t:,}")
        print(f"    Total tokens:  {total_t:,}")
        if cached:
            print(f"    Cached tokens: {cached:,}")
        print(f"    Elapsed:       {elapsed:.1f}s")
        return {"input": input_t, "output": output_t, "total": total_t, "cached": cached}
    else:
        print(f"\n  TOKEN USAGE ({label}): not available")
        print(f"    Elapsed: {elapsed:.1f}s")
        return None


def handle_approval(openai_client, response, agent_name):
    """Auto-approve MCP tool calls if needed."""
    output_items = getattr(response, "output", [])
    approval_items = [
        item for item in output_items
        if getattr(item, "type", "") == "mcp_approval_request"
    ]
    if not approval_items:
        return response

    print(f"\n  Auto-approving {len(approval_items)} MCP tool call(s)...")
    for item in approval_items:
        print(f"    Approving: {getattr(item, 'name', '?')}")

    approval_input = [
        {
            "type": "mcp_approval_response",
            "approve": True,
            "approval_request_id": item.id,
        }
        for item in approval_items
    ]

    t0 = time.monotonic()
    response = openai_client.responses.create(
        previous_response_id=response.id,
        input=approval_input,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    elapsed = time.monotonic() - t0

    output_items = getattr(response, "output", [])
    output_types = [getattr(item, "type", "unknown") for item in output_items]
    print(f"  Output types: {output_types}")
    dump_output_items(output_items)
    print_usage("after approval", response, elapsed)

    # Recursive: there might be more approvals
    return handle_approval(openai_client, response, agent_name)


def main():
    print("=" * 70)
    print("  E2E Token Measurement: ServiceNow Agent via Responses API")
    print("=" * 70)
    print()

    load_azd_env()

    project_endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        print("ERROR: AI_FOUNDRY_PROJECT_ENDPOINT not set")
        sys.exit(1)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
    except ImportError:
        print("ERROR: pip install azure-ai-projects azure-identity")
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = client.get_openai_client()

    # Find agent
    agent_name = "servicenow-assistant"
    agents = list(client.agents.list())
    agent = None
    for a in agents:
        name = getattr(a, "name", "")
        if name == agent_name or name.startswith(f"{agent_name}-"):
            agent = a
            break

    if not agent:
        names = [getattr(a, "name", "?") for a in agents]
        print(f"ERROR: {agent_name} not found (agents: {names})")
        sys.exit(1)

    actual_name = getattr(agent, "name", agent_name)
    print(f"Agent:    {actual_name}")
    print(f"Endpoint: {project_endpoint}")
    print()

    # Define test scenarios (multi-turn conversation)
    scenarios = [
        {
            "label": "Turn 1: Simple query (5 incidents)",
            "query": "Show me the 5 most recent open incidents with their number, short description, priority, and assigned to.",
        },
        {
            "label": "Turn 2: Follow-up query (context accumulation)",
            "query": "Now show me the 3 most recent change requests with their number, short description, and state.",
        },
        {
            "label": "Turn 3: Aggregation (lightweight)",
            "query": "How many incidents are there grouped by priority?",
        },
    ]

    # Create conversation
    conversation = openai_client.conversations.create()
    print(f"Conversation: {conversation.id}")
    print()

    all_usage = []
    prev_response_id = None

    for i, scenario in enumerate(scenarios):
        print(f"\n{'='*70}")
        print(f"  {scenario['label']}")
        print(f"  Query: {scenario['query']}")
        print(f"{'='*70}")

        t0 = time.monotonic()
        try:
            kwargs = {
                "input": scenario["query"],
                "extra_body": {"agent_reference": {"name": actual_name, "type": "agent_reference"}},
            }
            if prev_response_id:
                kwargs["previous_response_id"] = prev_response_id
            else:
                kwargs["conversation"] = conversation.id

            response = openai_client.responses.create(**kwargs)
            elapsed = time.monotonic() - t0

            output_items = getattr(response, "output", [])
            output_types = [getattr(item, "type", "unknown") for item in output_items]
            print(f"\n  Response ID: {response.id}")
            print(f"  Output types: {output_types}")
            dump_output_items(output_items)

            # Handle OAuth consent (first time)
            consent_items = [
                item for item in output_items
                if getattr(item, "type", "") == "oauth_consent_request"
            ]
            if consent_items:
                consent_link = getattr(consent_items[0], "consent_link", "")
                print(f"\n  OAuth consent required: {consent_link[:100]}")
                import webbrowser
                try:
                    webbrowser.open(consent_link)
                    print("  (Opening in browser...)")
                except Exception:
                    pass
                input("\n  Press ENTER after completing authentication...")

                t0 = time.monotonic()
                response = openai_client.responses.create(
                    previous_response_id=response.id,
                    input=scenario["query"],
                    extra_body={"agent_reference": {"name": actual_name, "type": "agent_reference"}},
                )
                elapsed = time.monotonic() - t0
                output_items = getattr(response, "output", [])
                output_types = [getattr(item, "type", "unknown") for item in output_items]
                print(f"  After consent - Output types: {output_types}")
                dump_output_items(output_items)

            # Handle MCP approvals
            response = handle_approval(openai_client, response, actual_name)

            usage = print_usage(scenario["label"], response, elapsed)
            if usage:
                all_usage.append({"turn": i + 1, "label": scenario["label"], **usage})

            prev_response_id = response.id

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"\n  ERROR ({elapsed:.1f}s): {e}")
            # Try to continue with next turn
            if hasattr(e, "response") and hasattr(e.response, "text"):
                print(f"  Response: {e.response.text[:500]}")

    # Summary
    print(f"\n\n{'='*70}")
    print("  SUMMARY: Token Usage Per Turn")
    print(f"{'='*70}")
    print(f"  {'Turn':<6} {'Input':>8} {'Output':>8} {'Total':>8} {'Cached':>8}  Label")
    print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8}  {'-'*30}")

    cumulative_total = 0
    for u in all_usage:
        cumulative_total += u["total"]
        print(f"  {u['turn']:<6} {u['input']:>8,} {u['output']:>8,} {u['total']:>8,} {u['cached']:>8,}  {u['label']}")

    if all_usage:
        print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
        print(f"  {'TOTAL':<6} {sum(u['input'] for u in all_usage):>8,} {sum(u['output'] for u in all_usage):>8,} {cumulative_total:>8,}")

        if len(all_usage) >= 2:
            growth = all_usage[-1]["input"] / all_usage[0]["input"] if all_usage[0]["input"] else 0
            print(f"\n  Context growth factor (last/first input): {growth:.1f}x")

    print()


if __name__ == "__main__":
    main()
