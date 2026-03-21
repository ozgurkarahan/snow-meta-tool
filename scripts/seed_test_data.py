"""Seed meaningful test data into ServiceNow for demo purposes.

Usage:
  python scripts/seed_test_data.py --instance https://devXXXXX.service-now.com --admin-password <pw>
"""
import argparse
import httpx
import sys


def main():
    parser = argparse.ArgumentParser(description="Seed test data into ServiceNow")
    parser.add_argument("--instance", required=True, help="ServiceNow instance URL")
    parser.add_argument("--admin-user", default="admin", help="Admin username")
    parser.add_argument("--admin-password", required=True, help="Admin password")
    args = parser.parse_args()

    client = httpx.Client(
        base_url=args.instance.rstrip("/"),
        auth=(args.admin_user, args.admin_password),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )

    # Look up ozgurkarahan
    r = client.get("/api/now/table/sys_user", params={
        "sysparm_query": "user_name=ozgurkarahan",
        "sysparm_fields": "sys_id",
        "sysparm_limit": "1",
    })
    user_id = r.json()["result"][0]["sys_id"]
    print(f"User sys_id: {user_id}")

    # --- Incidents ---
    incidents = [
        {
            "short_description": "VPN disconnects every 30 minutes for remote workers",
            "description": "Multiple remote employees in the Paris office report GlobalProtect VPN disconnects exactly every 30 minutes. Affecting ~25 users. Started after the firewall upgrade last Friday.",
            "impact": "2", "urgency": "2", "category": "Network",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "SAP production server running at 98% CPU",
            "description": "SAP ERP production server (sap-prod-01) at 98% CPU for 2 hours. Response times degraded from 200ms to 8s. Nightly batch job for financial reconciliation appears stuck. Users cannot process purchase orders.",
            "impact": "1", "urgency": "1", "category": "Software",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "Email attachments over 5MB failing to send",
            "description": "Users unable to send email attachments >5MB via Outlook. Error: 'attachment size exceeds allowable limit.' Started after Exchange Online migration. Previous limit was 25MB.",
            "impact": "2", "urgency": "3", "category": "Software",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "Badge access not working for Building C - 3rd floor",
            "description": "All badge readers on 3rd floor of Building C returning 'Access Denied' for all employees. Physical security propped doors open. Access control panel may need firmware update.",
            "impact": "2", "urgency": "2", "category": "Hardware",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "Salesforce dashboards loading blank for sales team",
            "description": "Entire sales team (40+ users) reports Salesforce dashboards loading blank. Standard record views work fine. Started at 09:00 today. Salesforce Status shows no outages. Suspect permission set change.",
            "impact": "2", "urgency": "1", "category": "Software",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "Printer on Floor 2 jamming continuously",
            "description": "HP LaserJet Pro MFP M428 on Floor 2 (PRINT-2F-001) jamming every 3-4 pages. Paper tray reloaded with correct size. Fuser unit may need replacement. 15 users affected.",
            "impact": "3", "urgency": "3", "category": "Hardware",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "Azure DevOps pipelines failing with timeout errors",
            "description": "All CI/CD pipelines in Platform Engineering project failing with timeout. Build times went from 12 min to 90+ min after dependency update PR merged yesterday.",
            "impact": "2", "urgency": "2", "category": "Software",
            "assigned_to": user_id, "caller_id": user_id,
        },
        {
            "short_description": "New hire laptop not configured - starting Monday",
            "description": "New employee Maria Santos (Engineering) starts Monday March 24. Dell Latitude 5540 delivered but not configured. Need standard image, VPN, MS Office, VS Code, Docker Desktop, Azure CLI.",
            "impact": "3", "urgency": "2", "category": "Hardware",
            "assigned_to": user_id, "caller_id": user_id,
        },
    ]

    print(f"\nCreating {len(incidents)} incidents...")
    for inc in incidents:
        r = client.post("/api/now/table/incident", json=inc)
        if r.status_code == 201:
            result = r.json()["result"]
            print(f"  {result['number']}: {inc['short_description'][:65]}")
        else:
            print(f"  FAIL: {r.status_code}")

    # --- Change Requests ---
    changes = [
        {
            "short_description": "Upgrade Azure Kubernetes cluster to v1.30",
            "description": "Upgrade production AKS cluster from v1.28 to v1.30. Includes node pool OS upgrade and API deprecation fixes. Window: Saturday 02:00-06:00. Rollback: scale old pool, drain new nodes.",
            "type": "Normal", "impact": "2", "risk": "2",
            "assigned_to": user_id, "requested_by": user_id,
        },
        {
            "short_description": "Migrate DNS from GoDaddy to Azure DNS",
            "description": "Move 47 DNS zones from GoDaddy to Azure DNS for Azure Front Door and Private Endpoint integration. TTL lowered to 60s before cutover. Expected zero downtime.",
            "type": "Normal", "impact": "2", "risk": "3",
            "assigned_to": user_id, "requested_by": user_id,
        },
        {
            "short_description": "Enable MFA enforcement for all admin accounts",
            "description": "Security compliance: enforce Azure AD MFA for all admin roles (Global Admin, User Admin, Security Admin). 7-day grace period for enrollment. 12 accounts affected.",
            "type": "Normal", "impact": "3", "risk": "2",
            "assigned_to": user_id, "requested_by": user_id,
        },
    ]

    print(f"\nCreating {len(changes)} change requests...")
    for chg in changes:
        r = client.post("/api/now/table/change_request", json=chg)
        if r.status_code == 201:
            result = r.json()["result"]
            print(f"  {result['number']}: {chg['short_description'][:65]}")
        else:
            print(f"  FAIL: {r.status_code}")

    # --- Problems ---
    problems = [
        {
            "short_description": "Recurring memory leak in payment processing service",
            "description": "payment-gateway microservice leaks ~50MB/hour, requiring restart every 18 hours. Suspected connection pool not releasing DB connections on timeout. Three P2 incidents linked in past month.",
            "impact": "2", "urgency": "2",
            "assigned_to": user_id,
        },
        {
            "short_description": "Intermittent 502 errors on API gateway during peak hours",
            "description": "Between 09:00-11:00 and 14:00-16:00, API gateway returns 502 for ~2% of requests. Backend services healthy. Suspect connection pool exhaustion. Correlates with mobile app traffic.",
            "impact": "2", "urgency": "2",
            "assigned_to": user_id,
        },
    ]

    print(f"\nCreating {len(problems)} problems...")
    for prob in problems:
        r = client.post("/api/now/table/problem", json=prob)
        if r.status_code == 201:
            result = r.json()["result"]
            print(f"  {result['number']}: {prob['short_description'][:65]}")
        else:
            print(f"  FAIL: {r.status_code}")

    # --- Service Catalog Request + Items ---
    r = client.post("/api/now/table/sc_request", json={
        "requested_for": user_id,
        "short_description": "IT equipment and access requests for Q1",
    })
    if r.status_code == 201:
        req_id = r.json()["result"]["sys_id"]
        req_num = r.json()["result"]["number"]
        print(f"\nCreated request: {req_num}")

        ritms = [
            {
                "short_description": "Request for 27-inch external monitor",
                "description": "Dell UltraSharp 27 4K USB-C Monitor (U2723QE) for home office. Justification: dual-monitor needed for code review and design work.",
                "request": req_id, "requested_for": user_id, "assigned_to": user_id,
            },
            {
                "short_description": "Access to Azure production subscription",
                "description": "Need Contributor role on subscription prod-platform for infrastructure deployments. Manager approval: Jean Dupont. Project: Platform Modernization.",
                "request": req_id, "requested_for": user_id, "assigned_to": user_id,
            },
        ]

        print(f"Creating {len(ritms)} request items...")
        for ritm in ritms:
            r = client.post("/api/now/table/sc_req_item", json=ritm)
            if r.status_code == 201:
                result = r.json()["result"]
                print(f"  {result['number']}: {ritm['short_description'][:65]}")
            else:
                print(f"  FAIL: {r.status_code}")

    client.close()
    print("\nDone! Test data created.")


if __name__ == "__main__":
    main()
