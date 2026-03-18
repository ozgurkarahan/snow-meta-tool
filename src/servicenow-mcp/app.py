"""Metadata-driven ServiceNow MCP server.

Dynamically discovers tables/fields and exposes discover, query, and write
tools via the Model Context Protocol (MCP).
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx

# --- Azure Monitor OpenTelemetry ---
_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _conn_str:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=_conn_str)
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _root.addHandler(_h)
    logging.getLogger("azure").setLevel(logging.WARNING)
    print("Azure Monitor OpenTelemetry configured for servicenow-mcp")
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from servicenow_client import ServiceNowClient, _request_token, _request_user_email

log = logging.getLogger("servicenow_mcp")

sn = ServiceNowClient()

port = int(os.environ.get("PORT", "8000"))


@asynccontextmanager
async def lifespan(app):
    # Recreate httpx client on startup -- Container App revision updates trigger
    # ASGI shutdown (closing the client) then restart, but the module-level
    # singleton persists in Python's module cache with a closed client.
    sn._client = httpx.AsyncClient(timeout=30.0)
    yield
    await sn.close()


mcp = FastMCP(
    "ServiceNow Meta Tool - MCP Server",
    lifespan=lifespan,
    instructions="""\
ServiceNow MCP server -- dynamically discovers tables and fields via the ServiceNow Table API.

## Recommended workflow
1. **discover** -- Find the table name and its fields before querying or writing.
   - discover(filter="incident") -> find tables matching "incident"
   - discover(table="incident") -> get field metadata for the incident table
   MUST call discover(table=...) before any write operation to learn valid field names.
2. **query** -- Read records, search text, or aggregate stats.
3. **write** -- Create, update, or delete records (including approvals).

## Encoded query syntax
ServiceNow uses encoded query strings (not SQL). Key operators:
- Equals: field=value
- Not equals: field!=value
- Contains: fieldLIKEvalue
- Starts with: fieldSTARTSWITHvalue
- Greater than: field>value
- Less than: field<value
- In list: fieldINvalue1,value2,value3
- Empty: fieldISEMPTY
- Not empty: fieldISNOTEMPTY
- AND (default): join with ^  (e.g., priority=1^state=2)
- OR: join with ^OR  (e.g., priority=1^ORpriority=2)
- Order: ^ORDERBYfield or ^ORDERBYDESCfield
- Encoded queries are case-sensitive for operators but values are case-insensitive.

Example: "priority=1^state!=6^assignment_groupLIKEnetwork^ORDERBYDESCsys_created_on"

## Text search
Use the query tool with text_search parameter for full-text keyword searches.
Under the hood this uses ServiceNow's TEXTQUERY operator.

## Approvals
Approvals in ServiceNow are records in the `sysapproval_approver` table.
- To find pending approvals: query(table="sysapproval_approver", query="state=requested")
- To approve: write(table="sysapproval_approver", operation="update", sys_id="...", field_values={"state": "approved"})
- To reject: write(table="sysapproval_approver", operation="update", sys_id="...", field_values={"state": "rejected"})

## Important conventions
- Table and field names are lowercase with underscores: incident, sys_created_on, short_description.
- Record IDs (sys_id) are 32-character hex strings.
- Reference fields return both value (sys_id) and display_value (human name) when queried.
- Date/time format: "YYYY-MM-DD HH:MM:SS" in the instance timezone.
- Always use the fields parameter to limit returned columns for performance.

## Error handling
- 403 = ACL restriction -- the user lacks permission for this table/record.
- 404 = table or record not found -- verify the table name with discover.
- Invalid field name = call discover(table=...) to see valid fields.
- ServiceNow errors return {"error": {"message": "...", "detail": "..."}}.

## Performance tips
- Always specify fields parameter to avoid returning all columns.
- Prefer STARTSWITH over LIKE for indexed performance.
- Use aggregate mode (aggregate=True) for counts instead of fetching all records.
- Limit result sets with the limit parameter.
""",
    host="0.0.0.0",
    port=port,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


def _sn_error_response(e: httpx.HTTPStatusError) -> str:
    """Extract ServiceNow error details from an HTTP error response."""
    status = e.response.status_code
    try:
        body = e.response.json()
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            return json.dumps({
                "success": False,
                "error": err.get("message", str(e)),
                "detail": err.get("detail", ""),
                "httpStatus": status,
            })
    except Exception:
        pass
    return json.dumps({
        "success": False,
        "error": str(e),
        "httpStatus": status,
    })


@mcp.tool()
async def discover(
    filter: str | None = None,
    table: str | None = None,
    include_choices: bool = False,
) -> str:
    """Discover ServiceNow tables and field metadata.

    Two modes:
    1. Table search: provide `filter` to search for tables by name or label.
       Example: discover(filter="incident") -> returns matching tables.

    2. Field metadata: provide `table` to get field definitions for a specific table.
       Example: discover(table="incident") -> returns all fields with types, mandatory flags, etc.
       Set include_choices=True to also fetch picklist values for choice fields.

    MUST call discover(table=...) before write operations to learn valid field names.

    Args:
        filter: Search string to find tables by name or label (case-insensitive).
        table: Table name to get field metadata for.
        include_choices: When True and table is provided, also fetch choice/picklist
            values for fields of type "choice" or "integer" with choices. Default False.

    Returns:
        JSON with either a list of tables or field metadata for the specified table.
    """
    log.info("tool=discover filter=%s table=%s include_choices=%s", filter, table, include_choices)
    t0 = time.monotonic()

    if not filter and not table:
        return json.dumps({
            "success": False,
            "error": "Provide either 'filter' to search tables or 'table' to get field metadata.",
        })

    try:
        # Mode 1: table search
        if filter and not table:
            tables = await sn.list_tables(filter)
            log.info("tool=discover mode=list count=%d elapsed=%.1fs", len(tables), time.monotonic() - t0)
            return json.dumps({"tables": tables, "count": len(tables)})

        # Mode 2: field metadata
        fields = await sn.describe_fields(table)

        result = {
            "table": table,
            "fields": fields,
            "fieldCount": len(fields),
        }

        if include_choices:
            # Find choice-type fields and fetch their values
            choice_fields = [
                f["element"] for f in fields
                if f.get("internal_type", {}).get("value", "") in ("choice", "integer")
                or f.get("internal_type", "") in ("choice", "integer")
            ]
            choices = {}
            for field_name in choice_fields[:20]:  # Cap at 20 to avoid excessive calls
                vals = await sn.get_choices(table, field_name)
                if vals:
                    choices[field_name] = vals
            result["choices"] = choices

        log.info("tool=discover mode=describe fields=%d elapsed=%.1fs", len(fields), time.monotonic() - t0)
        return json.dumps(result)

    except httpx.HTTPStatusError as e:
        return _sn_error_response(e)


@mcp.tool()
async def query(
    table: str,
    query: str = "",
    fields: str = "",
    limit: int = 50,
    offset: int = 0,
    max_records: int = 10000,
    text_search: str | None = None,
    search_field: str = "short_description",
    aggregate: bool = False,
    group_by: str = "",
    count: bool = True,
    avg_fields: str = "",
    sum_fields: str = "",
) -> str:
    """Query ServiceNow records, search text, or get aggregate statistics.

    Three modes:
    1. Record query (default): Fetch records with encoded query filters and pagination.
       Example: query(table="incident", query="priority=1^state!=6", fields="number,short_description,priority", limit=10)

    2. Text search: Set text_search to search for keywords in a field.
       Example: query(table="incident", text_search="password reset", fields="number,short_description")
       Uses ServiceNow's TEXTQUERY operator on search_field (default: short_description).

    3. Aggregate: Set aggregate=True for counts, averages, and sums by group.
       Example: query(table="incident", aggregate=True, group_by="priority")
       Example: query(table="incident", aggregate=True, group_by="category", query="state=1")

    Results include both sys_id (value) and display names (display_value) for reference fields.
    Auto-paginates up to max_records (cap 50000).

    Args:
        table: ServiceNow table name (e.g., "incident", "sc_req_item", "change_request").
        query: Encoded query string. See server instructions for syntax.
        fields: Comma-separated field names to return (e.g., "number,short_description,priority").
            Always specify for performance -- omitting returns all columns.
        limit: Records per page (default 50, max 10000 per page).
        offset: Starting record offset for pagination.
        max_records: Maximum total records to return across all pages (default 10000, cap 50000).
        text_search: Keywords to search for. Appended as TEXTQUERY to the query.
        search_field: Field to search in when using text_search (default "short_description").
        aggregate: Set True to use the Stats API instead of fetching records.
        group_by: Comma-separated fields to group by (only with aggregate=True).
        count: Include count in aggregate results (default True).
        avg_fields: Comma-separated fields to average (only with aggregate=True).
        sum_fields: Comma-separated fields to sum (only with aggregate=True).

    Returns:
        JSON with records array and totalCount, or aggregate statistics.
    """
    log.info("tool=query table=%s aggregate=%s", table, aggregate)
    t0 = time.monotonic()
    max_records = min(max_records, 50000)

    try:
        # Aggregate mode
        if aggregate:
            stats = await sn.aggregate(
                table,
                query=query,
                group_by=group_by,
                count=count,
                avg_fields=avg_fields,
                sum_fields=sum_fields,
            )
            log.info("tool=query mode=aggregate elapsed=%.1fs", time.monotonic() - t0)
            return json.dumps({"aggregate": True, "result": stats})

        # Build final query with optional text search
        final_query = query
        if text_search:
            tq = f"{search_field}123TEXTQUERY321{text_search}"
            final_query = f"{final_query}^{tq}" if final_query else tq

        # Fetch first page
        records, total_count = await sn.query_records(
            table, query=final_query, fields=fields, limit=limit, offset=offset
        )

        # Auto-paginate if more records available
        fetched = len(records)
        current_offset = offset + limit
        while fetched < total_count and fetched < max_records:
            page_limit = min(limit, max_records - fetched)
            page_records, _ = await sn.query_records(
                table, query=final_query, fields=fields,
                limit=page_limit, offset=current_offset
            )
            if not page_records:
                break
            records.extend(page_records)
            fetched += len(page_records)
            current_offset += len(page_records)

        records = records[:max_records]
        log.info(
            "tool=query done total=%d returned=%d elapsed=%.1fs",
            total_count, len(records), time.monotonic() - t0,
        )
        return json.dumps({
            "totalCount": total_count,
            "records": records,
            "returned": len(records),
            "done": len(records) >= total_count or len(records) >= max_records,
        })

    except httpx.HTTPStatusError as e:
        return _sn_error_response(e)


@mcp.tool()
async def write(
    table: str,
    operation: str,
    field_values: dict | None = None,
    sys_id: str | None = None,
) -> str:
    """Create, update, or delete a ServiceNow record.

    IMPORTANT: Call discover(table=...) first to learn valid field names and
    required fields. Field names are validated before sending.

    Operations:
        - create: New record. Requires field_values.
        - update: Partial update. Requires sys_id + field_values.
        - delete: Permanent removal. Requires sys_id. Confirm with the user first.

    Approvals: To approve/reject, update the sysapproval_approver table:
        write(table="sysapproval_approver", operation="update",
              sys_id="<approver_sys_id>", field_values={"state": "approved"})

    Args:
        table: ServiceNow table name (e.g., "incident", "sc_req_item").
        operation: One of "create", "update", "delete".
        field_values: Field names to values. Required for create/update, ignored for delete.
            E.g., {"short_description": "New incident", "priority": "2", "assignment_group": "..."}.
        sys_id: 32-character record sys_id. Required for update/delete.

    Returns:
        JSON with success flag and the created/updated record, or deletion confirmation.
    """
    log.info("tool=write table=%s op=%s", table, operation)
    t0 = time.monotonic()
    op = operation.lower()
    valid_ops = ("create", "update", "delete")
    if op not in valid_ops:
        return json.dumps({
            "success": False,
            "error": f"Invalid operation '{operation}'. Must be one of: {', '.join(valid_ops)}.",
        })

    # Validate required parameters
    if op in ("create", "update") and not field_values:
        return json.dumps({
            "success": False,
            "error": f"field_values is required for '{op}' operation.",
        })
    if op in ("update", "delete") and not sys_id:
        return json.dumps({
            "success": False,
            "error": f"sys_id is required for '{op}' operation.",
        })

    try:
        # Validate field names against schema for create/update
        # Skip validation if sys_dictionary is inaccessible (403) -- let Table API validate
        if op in ("create", "update") and field_values:
            try:
                schema_fields = await sn.describe_fields(table)
                if schema_fields:
                    valid_field_names = {f.get("element", "") for f in schema_fields}
                    invalid = set(field_values.keys()) - valid_field_names
                    if invalid:
                        return json.dumps({
                            "success": False,
                            "error": f"Invalid field names: {', '.join(sorted(invalid))}. "
                                     f"Call discover(table=\"{table}\") to see valid field names.",
                        })
            except httpx.HTTPStatusError as schema_err:
                if schema_err.response.status_code == 403:
                    log.info("write: sys_dictionary 403, skipping field validation for %s", table)
                else:
                    raise

        if op == "create":
            record = await sn.create_record(table, field_values)
            result = {"success": True, "sys_id": record.get("sys_id", ""), "record": record}
        elif op == "update":
            record = await sn.update_record(table, sys_id, field_values)
            result = {"success": True, "record": record}
        else:  # delete
            await sn.delete_record(table, sys_id)
            result = {"success": True, "deleted": sys_id}

    except httpx.HTTPStatusError as e:
        return _sn_error_response(e)

    log.info("tool=write done elapsed=%.1fs", time.monotonic() - t0)
    return json.dumps(result)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Extract Authorization: Bearer token and X-User-Email header as per-request context vars.

    When a bearer token is present (e.g., from APIM), the ServiceNowClient uses
    it directly without retry/refresh. When absent (local dev), the existing
    self-managed JWT Bearer flow kicks in unchanged.

    X-User-Email is injected by the APIM OBO policy from the Azure AD
    preferred_username claim. Stored for future whoami tool.
    """

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        email = request.headers.get("x-user-email")
        tok = _request_token.set(token)
        em = _request_user_email.set(email)
        try:
            return await call_next(request)
        finally:
            _request_user_email.reset(em)
            _request_token.reset(tok)


if __name__ == "__main__":
    import uvicorn

    app = mcp.streamable_http_app()
    app.add_middleware(BearerTokenMiddleware)
    uvicorn.run(app, host="0.0.0.0", port=port)
