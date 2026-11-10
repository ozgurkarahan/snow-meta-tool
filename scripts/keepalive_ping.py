"""Keepalive: authenticated ServiceNow API ping (JWT bearer + 1-row incident read).

NOTE: per ServiceNow KB3140725 (Oct 2026) REST/API traffic does NOT count as
activity for PDI reclamation; only an interactive login to the instance does.
This ping verifies the JWT bearer path end to end; use scripts/pdi_health.py for
state classification and scripts/pdi_ui_login.py for the reclamation keep-alive.

Config: env (SN_INSTANCE_URL, SN_CLIENT_ID, SN_KID, SN_SUB, SN_JWT_PRIVATE_KEY)
falling back to certs/sn-oauth-config.json + certs/sn-jwt-bearer.key.

Usage:
    python scripts/keepalive_ping.py                  # uses "instance" from config
    python scripts/keepalive_ping.py --instance ...   # override

Exit code 0 if the instance is awake and responding to authenticated API calls.
Exit code != 0 if hibernating, network error, or auth fails.
"""
import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sn_jwt  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance", default=None, help="SN instance URL (defaults to config, then dev330554)")
    parser.add_argument("--quiet", action="store_true", help="Only print summary line")
    args = parser.parse_args()

    cfg = sn_jwt.load_config(instance_override=args.instance, default_instance=sn_jwt.DEFAULT_INSTANCE)
    if not cfg.has_jwt:
        print("[FAIL] Missing JWT config (env SN_* or certs/sn-oauth-config.json + certs/sn-jwt-bearer.key)",
              file=sys.stderr)
        sys.exit(2)

    t0 = time.time()
    with httpx.Client(timeout=30) as client:
        r = sn_jwt.exchange_token(client, cfg)
        if r.status_code != 200:
            body = r.text[:200] if not args.quiet else ""
            print(f"[FAIL] Token exchange: HTTP {r.status_code} {body}")
            sys.exit(1)
        r2 = sn_jwt.read_one_incident(client, cfg.instance, r.json()["access_token"])
    elapsed = time.time() - t0
    if r2.status_code != 200:
        print(f"[FAIL] Table API: HTTP {r2.status_code}")
        sys.exit(1)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[OK] {ts} keepalive {cfg.instance} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
