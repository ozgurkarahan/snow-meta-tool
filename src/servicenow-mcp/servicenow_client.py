"""ServiceNow REST API client with JWT Bearer auth, Table API, and caching."""

import base64
import contextvars
import json as _json
import os
import time
import uuid

import httpx

# Per-request bearer token for passthrough mode (set by middleware).
# When set, _request() uses this token directly and does NOT retry on 401 --
# the upstream caller (APIM) owns the token lifecycle.
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_request_token", default=None
)


class ServiceNowClient:
    """Async ServiceNow REST API client.

    Supports two auth modes:
    - Passthrough (APIM): Bearer token from middleware via ContextVar.
    - Self-managed (local dev): JWT Bearer exchange at oauth_token.do.
    """

    def __init__(self):
        self.instance_url = os.environ.get("SN_INSTANCE_URL", "").rstrip("/")

        # Self-managed JWT Bearer config
        self.client_id = os.environ.get("SN_CLIENT_ID", "")
        self.jwt_kid = os.environ.get("SN_JWT_KID", "")
        self.jwt_key_path = os.environ.get("SN_JWT_KEY_PATH", "")
        self.jwt_sub = os.environ.get("SN_JWT_SUB", "")

        # Self-managed token state
        self.access_token: str | None = None

        # Schema caches (instance-wide, not user-specific) -- 15-min TTL
        self._table_list_cache: dict[str, tuple[float, list]] = {}
        self._field_cache: dict[str, tuple[float, list]] = {}
        self._choice_cache: dict[str, tuple[float, list]] = {}
        self._cache_ttl = 900

        self._client = httpx.AsyncClient(timeout=30.0)

    # --- JWT Bearer Auth (self-managed mode) ---

    def _build_jwt(self) -> str:
        """Build and sign an RS256 JWT assertion for ServiceNow token exchange."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, utils

        with open(self.jwt_key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": self.jwt_kid}
        payload = {
            "iss": self.client_id,
            "sub": self.jwt_sub,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
        }

        def _b64(data: dict) -> bytes:
            return base64.urlsafe_b64encode(
                _json.dumps(data, separators=(",", ":")).encode()
            ).rstrip(b"=")

        header_b64 = _b64(header)
        payload_b64 = _b64(payload)
        signing_input = header_b64 + b"." + payload_b64

        signature = private_key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
        return (signing_input + b"." + sig_b64).decode()

    async def _authenticate(self) -> None:
        """Exchange JWT assertion for access token at oauth_token.do."""
        assertion = self._build_jwt()
        resp = await self._client.post(
            f"{self.instance_url}/oauth_token.do",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "client_id": self.client_id,
                "assertion": assertion,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]

    # --- Core Request ---

    async def _request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        """Make an authenticated request to the ServiceNow REST API.

        Passthrough mode: uses per-request token from middleware (no retry).
        Self-managed mode: JWT Bearer exchange, retries once on 401.
        """
        passthrough_token = _request_token.get()
        if passthrough_token:
            url = f"{self.instance_url}{path}"
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {passthrough_token}"
            resp = await self._client.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp

        # Self-managed mode
        if not self.access_token:
            await self._authenticate()

        url = f"{self.instance_url}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        resp = await self._client.request(method, url, headers=headers, **kwargs)

        if resp.status_code == 401:
            await self._authenticate()
            headers["Authorization"] = f"Bearer {self.access_token}"
            resp = await self._client.request(method, url, headers=headers, **kwargs)

        resp.raise_for_status()
        return resp

    # --- Table Discovery & Schema ---

    async def list_tables(self, query: str = "") -> list[dict]:
        """Search for tables by name or label via sys_db_object. Cached 15 min."""
        now = time.time()
        if query in self._table_list_cache:
            ts, data = self._table_list_cache[query]
            if now - ts < self._cache_ttl:
                return data

        params = {
            "sysparm_fields": "name,label,super_class,sys_id",
            "sysparm_limit": "100",
        }
        if query:
            params["sysparm_query"] = (
                f"nameLIKE{query}^ORlabelLIKE{query}^ORDERBYname"
            )
        else:
            params["sysparm_query"] = "ORDERBYname"

        resp = await self._request("GET", "/api/now/table/sys_db_object", params=params)
        result = resp.json().get("result", [])
        self._table_list_cache[query] = (now, result)
        return result

    async def describe_fields(self, table: str) -> list[dict]:
        """Get field metadata from sys_dictionary for a table. Cached 15 min."""
        now = time.time()
        if table in self._field_cache:
            ts, data = self._field_cache[table]
            if now - ts < self._cache_ttl:
                return data

        params = {
            "sysparm_query": f"name={table}^elementISNOTEMPTY^ORDERBYelement",
            "sysparm_fields": (
                "element,column_label,internal_type,mandatory,"
                "max_length,default_value,reference,read_only,active"
            ),
            "sysparm_limit": "500",
        }
        resp = await self._request("GET", "/api/now/table/sys_dictionary", params=params)
        result = resp.json().get("result", [])
        self._field_cache[table] = (now, result)
        return result

    async def get_choices(self, table: str, field: str) -> list[dict]:
        """Get picklist/choice values for a table field from sys_choice. Cached 15 min."""
        cache_key = f"{table}.{field}"
        now = time.time()
        if cache_key in self._choice_cache:
            ts, data = self._choice_cache[cache_key]
            if now - ts < self._cache_ttl:
                return data

        params = {
            "sysparm_query": f"name={table}^element={field}^inactive=false^ORDERBYsequence",
            "sysparm_fields": "value,label,sequence",
            "sysparm_limit": "200",
        }
        resp = await self._request("GET", "/api/now/table/sys_choice", params=params)
        result = resp.json().get("result", [])
        self._choice_cache[cache_key] = (now, result)
        return result

    # --- Record Operations ---

    async def query_records(
        self,
        table: str,
        query: str = "",
        fields: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Query records from a table. Returns (records, total_count)."""
        params: dict[str, str] = {
            "sysparm_query": query,
            "sysparm_limit": str(limit),
            "sysparm_offset": str(offset),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_suppress_pagination_header": "false",
        }
        if fields:
            params["sysparm_fields"] = fields

        resp = await self._request("GET", f"/api/now/table/{table}", params=params)
        records = resp.json().get("result", [])

        # Total count from X-Total-Count header (if available)
        total = int(resp.headers.get("X-Total-Count", len(records)))

        return records, total

    async def aggregate(
        self,
        table: str,
        query: str = "",
        group_by: str = "",
        count: bool = True,
        avg_fields: str = "",
        sum_fields: str = "",
    ) -> list[dict]:
        """Get aggregate stats via the Stats API."""
        params: dict[str, str] = {}
        if query:
            params["sysparm_query"] = query
        if group_by:
            params["sysparm_group_by"] = group_by

        agg_parts = []
        if count:
            agg_parts.append("COUNT")
        if avg_fields:
            for f in avg_fields.split(","):
                agg_parts.append(f"AVG:{f.strip()}")
        if sum_fields:
            for f in sum_fields.split(","):
                agg_parts.append(f"SUM:{f.strip()}")

        if agg_parts:
            params["sysparm_avg_fields"] = avg_fields
            params["sysparm_sum_fields"] = sum_fields
            params["sysparm_count"] = str(count).lower()

        if group_by:
            params["sysparm_display_value"] = "all"

        resp = await self._request("GET", f"/api/now/stats/{table}", params=params)
        data = resp.json().get("result", {})

        # Stats API returns different shapes depending on group_by
        if group_by:
            return data if isinstance(data, list) else [data]
        return [data]

    async def create_record(self, table: str, fields: dict) -> dict:
        """Create a new record. Returns the created record."""
        resp = await self._request(
            "POST",
            f"/api/now/table/{table}",
            json=fields,
            headers={"Content-Type": "application/json"},
        )
        return resp.json().get("result", {})

    async def update_record(self, table: str, sys_id: str, fields: dict) -> dict:
        """Partial update of a record by sys_id. Returns the updated record."""
        resp = await self._request(
            "PATCH",
            f"/api/now/table/{table}/{sys_id}",
            json=fields,
            headers={"Content-Type": "application/json"},
        )
        return resp.json().get("result", {})

    async def delete_record(self, table: str, sys_id: str) -> None:
        """Delete a record by sys_id. Returns None on success (204)."""
        await self._request("DELETE", f"/api/now/table/{table}/{sys_id}")

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
