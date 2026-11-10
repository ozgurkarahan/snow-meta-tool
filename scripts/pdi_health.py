"""Classify ServiceNow PDI health (non-interactive, CI friendly).

Probes <instance>/stats.do, then (when JWT config is available) the JWT bearer
exchange at /oauth_token.do and a 1-row Table API read. Prints ONE JSON line and
exits with the state's code. Never prints secrets, assertions or tokens.

States / exit codes:
    UP                          0   reachable (+ JWT bearer and table read OK when configured)
    HIBERNATING_OR_OFFLINE      10  502/503/504 or `snow_adc` / hibernation page
    RECLAIMED_OR_MISCONFIGURED  11  oauth_token.do 400/401/403 (config wiped) or table 401/403
    NETWORK_ERROR               12  DNS/connect/TLS/timeout
    UNEXPECTED                  13  any other HTTP answer
    NOT_CONFIGURED              20  no instance URL (CI secret missing): nothing checked

Config: env SN_INSTANCE_URL, SN_CLIENT_ID, SN_KID, SN_SUB, SN_JWT_PRIVATE_KEY,
falling back to certs/. With only SN_INSTANCE_URL the auth stage is skipped
("auth_checked": false), so a reclaimed instance can still look UP.

Usage:
    python scripts/pdi_health.py [--instance URL] [--output result.json]
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sn_jwt  # noqa: E402

UP = "UP"
HIBERNATING_OR_OFFLINE = "HIBERNATING_OR_OFFLINE"
RECLAIMED_OR_MISCONFIGURED = "RECLAIMED_OR_MISCONFIGURED"
NETWORK_ERROR = "NETWORK_ERROR"
UNEXPECTED = "UNEXPECTED"
NOT_CONFIGURED = "NOT_CONFIGURED"

EXIT_CODES = {UP: 0, HIBERNATING_OR_OFFLINE: 10, RECLAIMED_OR_MISCONFIGURED: 11,
              NETWORK_ERROR: 12, UNEXPECTED: 13, NOT_CONFIGURED: 20}

NEXT_ACTIONS = {
    UP: "None.",
    HIBERNATING_OR_OFFLINE: (
        "Sign in to https://developer.servicenow.com and wake the instance (ServiceNow ID + MFA). "
        "If the portal shows Status 'Offline' with no Wake option, it is a ServiceNow-side outage: "
        "wait for ServiceNow to finish; nothing to repair on our side."),
    RECLAIMED_OR_MISCONFIGURED: (
        "Check the instance in the developer portal. If it was reclaimed/reset, run "
        "`python scripts/repair_sn_pdi.py`, then update APIM named values SnOboInstanceUrl / "
        "SnOboClientId / SnJwtBearerKid and ca-sn-mcp SN_INSTANCE_URL (and the SN_* GitHub secrets) "
        "with the printed values. If the instance is fine, the SN_* secrets are wrong."),
    NETWORK_ERROR: "Transient network/DNS/TLS failure from the runner; re-run. Investigate if it persists.",
    UNEXPECTED: "Unexpected HTTP answer; open the instance in a browser and check the workflow logs.",
    NOT_CONFIGURED: "Set the SN_INSTANCE_URL secret (and SN_CLIENT_ID/SN_KID/SN_SUB/SN_JWT_PRIVATE_KEY).",
}

HIBERNATION_MARKERS = ("snow_adc", "hibernat", "instance is not available", "instance is offline")
OUTAGE_STATUSES = {502, 503, 504}
AUTH_FAILURE_STATUSES = {400, 401, 403}


def _is_hibernation(resp: httpx.Response) -> bool:
    if resp.status_code in OUTAGE_STATUSES:
        return True
    if "snow_adc" in resp.headers.get("server", "").lower() and resp.status_code >= 500:
        return True
    location = resp.headers.get("location", "").lower()
    if "developer.servicenow.com" in location:
        return True
    body = resp.text[:4000].lower() if resp.status_code < 300 or resp.status_code >= 500 else ""
    return resp.status_code != 404 and any(m in body for m in HIBERNATION_MARKERS) and "<form" not in body


def _oauth_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts = [f"{k}={str(data[k])[:80]}" for k in ("error", "error_description") if data.get(k)]
    return " ".join(parts)


def _result(state, cfg, stage, status=None, detail="", auth_checked=False, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    # reachable = stats.do passed and the instance itself answered later stages (auth may still fail)
    reachable = state == UP or (stage in ("token", "table") and state in (RECLAIMED_OR_MISCONFIGURED, UNEXPECTED))
    return {"state": state, "exit_code": EXIT_CODES[state], "instance": cfg.instance or None,
            "stage": stage, "http_status": status, "detail": detail, "auth_checked": auth_checked,
            "reachable": reachable,
            "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "next_action": NEXT_ACTIONS[state]}


def classify(cfg: sn_jwt.SnConfig, client: httpx.Client, now=None) -> dict:
    if not cfg.instance:
        return _result(NOT_CONFIGURED, cfg, "config", detail="SN_INSTANCE_URL not set", now=now)

    try:
        r = client.get(f"{cfg.instance}/stats.do", follow_redirects=False)
    except httpx.TransportError as exc:
        return _result(NETWORK_ERROR, cfg, "reachability", detail=type(exc).__name__, now=now)
    if _is_hibernation(r):
        marker = "snow_adc" if "snow_adc" in (r.headers.get("server", "") + r.text[:4000]).lower() else ""
        return _result(HIBERNATING_OR_OFFLINE, cfg, "reachability", r.status_code, marker, now=now)
    if r.status_code >= 500:
        return _result(UNEXPECTED, cfg, "reachability", r.status_code, now=now)

    if not cfg.has_jwt:
        return _result(UP, cfg, "reachability", r.status_code, "JWT config absent; auth not checked", now=now)

    try:
        tok = sn_jwt.exchange_token(client, cfg)
    except httpx.TransportError as exc:
        return _result(NETWORK_ERROR, cfg, "token", detail=type(exc).__name__, now=now)
    if _is_hibernation(tok):
        return _result(HIBERNATING_OR_OFFLINE, cfg, "token", tok.status_code, now=now)
    if tok.status_code in AUTH_FAILURE_STATUSES:
        return _result(RECLAIMED_OR_MISCONFIGURED, cfg, "token", tok.status_code, _oauth_error(tok), True, now)
    if tok.status_code != 200:
        return _result(UNEXPECTED, cfg, "token", tok.status_code, now=now)
    try:
        access_token = tok.json()["access_token"]
    except (ValueError, KeyError, TypeError):
        return _result(UNEXPECTED, cfg, "token", 200, "no access_token in response", True, now)

    try:
        tbl = sn_jwt.read_one_incident(client, cfg.instance, access_token)
    except httpx.TransportError as exc:
        return _result(NETWORK_ERROR, cfg, "table", detail=type(exc).__name__, now=now)
    if _is_hibernation(tbl):
        return _result(HIBERNATING_OR_OFFLINE, cfg, "table", tbl.status_code, now=now)
    if tbl.status_code in (401, 403):
        return _result(RECLAIMED_OR_MISCONFIGURED, cfg, "table", tbl.status_code,
                       "token issued but table read denied (roles wiped?)", True, now)
    if tbl.status_code != 200:
        return _result(UNEXPECTED, cfg, "table", tbl.status_code, auth_checked=True, now=now)
    return _result(UP, cfg, "table", 200, "JWT bearer + table read OK", True, now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance", default=None, help="Override SN_INSTANCE_URL / config instance")
    parser.add_argument("--output", type=Path, default=None, help="Also write the JSON result to this file")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    cfg = sn_jwt.load_config(instance_override=args.instance)
    with httpx.Client(timeout=args.timeout, headers={"User-Agent": "snow-meta-tool-pdi-health"}) as client:
        result = classify(cfg, client)
    line = json.dumps(result, separators=(",", ":"))
    print(line)
    if args.output:
        args.output.write_text(line + "\n", encoding="utf-8")
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
