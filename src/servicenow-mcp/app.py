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
ServiceNow MCP server. Encoded query syntax: field=value (equals), fieldLIKEvalue (contains), \
fieldSTARTSWITHvalue, field>value, field<value, fieldINval1,val2, fieldISEMPTY, fieldISNOTEMPTY. \
AND: ^ | OR: ^OR | Order: ^ORDERBYfield / ^ORDERBYDESCfield. \
Example: priority=1^state!=6^ORDERBYDESCsys_created_on. \
Approvals: sysapproval_approver table (state=requested/approved/rejected). \
Always specify fields param on query. Use aggregate=True for counts.
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
    mode: str = "compact",
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
        mode: Field detail level -- "compact" (default: element, type, mandatory, reference),
            "names" (field names only), or "full" (all sys_dictionary attributes).

    Returns:
        JSON with either a list of tables or field metadata for the specified table.
    """
    log.info("tool=discover filter=%s table=%s include_choices=%s mode=%s", filter, table, include_choices, mode)
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
            # Compact table results: strip link URLs
            compact_tables = [
                {k: v for k, v in t.items() if k != "super_class" or v}
                for t in tables
            ]
            log.info("tool=discover mode=list count=%d elapsed=%.1fs", len(tables), time.monotonic() - t0)
            return json.dumps({"tables": compact_tables, "count": len(compact_tables)})

        # Mode 2: field metadata
        raw_fields = await sn.describe_fields(table)

        if mode == "names":
            fields_out = [f.get("element", "") for f in raw_fields]
        elif mode == "full":
            fields_out = raw_fields
        else:  # compact (default)
            fields_out = sn.compact_fields(raw_fields)

        result = {
            "table": table,
            "fields": fields_out,
            "fieldCount": len(raw_fields),
        }

        if include_choices:
            # Find choice-type fields and fetch their values
            choice_fields = [
                f.get("element", "") if isinstance(f, dict) else f
                for f in (raw_fields if mode != "names" else [])
                if isinstance(f, dict) and (
                    f.get("internal_type", {}).get("value", "") in ("choice", "integer")
                    or f.get("internal_type", "") in ("choice", "integer")
                )
            ]
            # If mode=names, use raw_fields for choice detection
            if mode == "names":
                choice_fields = [
                    f["element"] for f in raw_fields
                    if f.get("internal_type", {}).get("value", "") in ("choice", "integer")
                    or f.get("internal_type", "") in ("choice", "integer")
                ]
            choices = {}
            skipped = []
            for field_name in choice_fields[:20]:  # Cap at 20 to avoid excessive calls
                try:
                    vals = await sn.get_choices(table, field_name)
                    if vals:
                        # Strip sequence from choice values
                        choices[field_name] = [
                            {"value": v.get("value", ""), "label": v.get("label", "")}
                            for v in vals[:10]  # Cap at 10 values per field
                        ]
                except httpx.HTTPStatusError as choice_err:
                    if choice_err.response.status_code in (401, 403):
                        log.warning("discover: sys_choice %d for %s.%s -- skipping",
                                    choice_err.response.status_code, table, field_name)
                        skipped.append(field_name)
                    else:
                        raise
            result["choices"] = choices
            if skipped:
                result["choices_skipped"] = skipped
                result["choices_note"] = "Some choice fields were inaccessible (403). Field metadata is still complete."

        log.info("tool=discover mode=describe fields=%d elapsed=%.1fs", len(raw_fields), time.monotonic() - t0)
        return json.dumps(result)

    except httpx.HTTPStatusError as e:
        return _sn_error_response(e)


@mcp.tool()
async def query(
    table: str,
    query: str = "",
    fields: str = "",
    limit: int = 20,
    offset: int = 0,
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
    1. Record query (default): Fetch records with encoded query filters.
       Example: query(table="incident", query="priority=1^state!=6", fields="number,short_description,priority", limit=10)

    2. Text search: Set text_search to search for keywords in a field.
       Example: query(table="incident", text_search="password reset", fields="number,short_description")

    3. Aggregate: Set aggregate=True for counts, averages, and sums by group.
       Example: query(table="incident", aggregate=True, group_by="priority")

    IMPORTANT: Always specify the fields parameter to limit returned columns.

    Args:
        table: ServiceNow table name (e.g., "incident", "sc_req_item", "change_request").
        query: Encoded query string. See server instructions for syntax.
        fields: Comma-separated field names to return (e.g., "number,short_description,priority").
            ALWAYS specify this -- omitting returns all columns and wastes tokens.
        limit: Maximum total records to return (default 20, max 200).
        offset: Starting record offset for pagination.
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
    log.info("tool=query table=%s aggregate=%s limit=%d", table, aggregate, limit)
    t0 = time.monotonic()

    # Cap total records to prevent runaway token usage
    max_total = min(limit, 200)

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

        # Fetch records (paginate only up to max_total)
        page_size = min(max_total, 100)
        records, total_count = await sn.query_records(
            table, query=final_query, fields=fields, limit=page_size, offset=offset
        )

        # Auto-paginate only if user requested more than first page
        fetched = len(records)
        current_offset = offset + page_size
        while fetched < max_total and fetched < total_count:
            next_page = min(page_size, max_total - fetched)
            page_records, _ = await sn.query_records(
                table, query=final_query, fields=fields,
                limit=next_page, offset=current_offset
            )
            if not page_records:
                break
            records.extend(page_records)
            fetched += len(page_records)
            current_offset += len(page_records)

        records = records[:max_total]
        log.info(
            "tool=query done total=%d returned=%d elapsed=%.1fs",
            total_count, len(records), time.monotonic() - t0,
        )
        return json.dumps({
            "totalCount": total_count,
            "records": records,
            "returned": len(records),
            "hasMore": total_count > len(records),
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
                        # Schema may be partial due to row-level ACLs (200 with
                        # filtered results) -- log warning but let Table API validate
                        log.warning(
                            "write: field validation found unrecognized fields %s for %s "
                            "(may be ACL-filtered schema -- skipping validation)",
                            sorted(invalid), table,
                        )
            except httpx.HTTPStatusError as schema_err:
                if schema_err.response.status_code == 403:
                    log.info("write: sys_dictionary 403, skipping field validation for %s", table)
                else:
                    raise

        if op == "create":
            record = await sn.create_record(table, field_values)
            result = {
                "success": True,
                "sys_id": record.get("sys_id", ""),
                "number": record.get("number", ""),
            }
        elif op == "update":
            record = await sn.update_record(table, sys_id, field_values)
            result = {"success": True, "sys_id": record.get("sys_id", sys_id)}
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
