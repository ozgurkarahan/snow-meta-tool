# 🎯 Demo Data Setup Guide

To run the joint Salesforce + ServiceNow scenarios, both systems need **correlated data** — the same companies, contacts, and issues referenced across CRM and ITSM. The AI agent links them by matching company names, email domains, and keywords in descriptions.

> **Key principle:** There's no direct integration between the systems. The agent correlates data by recognizing that "Acme Corp" in a Salesforce Account is the same "Acme Corp" mentioned in a ServiceNow incident description or caller's company field.

---

## 🔗 The Linking Strategy

| Link Method | Salesforce Field | ServiceNow Field | Used By |
|---|---|---|---|
| Company name | `Account.Name` | `incident.company` or description text | Most prompts |
| Email domain | `Contact.Email` | `sys_user.email` or caller email | Customer lookup |
| Keywords | `Case.Subject`, `Opportunity.Description` | `incident.short_description` | Pattern matching |
| Reference IDs | `Case.CaseNumber` | `incident.correlation_id` or work notes | Traceability |

---

## ☁️ Salesforce — Required Data

### 1. Accounts (3-5 demo companies)

Create these accounts in Salesforce so they can be referenced from ServiceNow:

| Account Name | Industry | Annual Revenue | Type | Support Tier |
|---|---|---|---|---|
| Acme Corp | Technology | $5,000,000 | Customer | Premium |
| Northwind Traders | Retail | $2,500,000 | Customer | Standard |
| Contoso Ltd | Financial Services | $12,000,000 | Customer | Enterprise |
| Fabrikam Inc | Manufacturing | $800,000 | Prospect | — |
| Adventure Works | Healthcare | $3,200,000 | Customer | Premium |

```
Use the Salesforce MCP agent:

Create the following accounts in Salesforce:
1. Acme Corp - Technology, $5M revenue, Customer type
2. Northwind Traders - Retail, $2.5M revenue, Customer type
3. Contoso Ltd - Financial Services, $12M revenue, Customer type
4. Fabrikam Inc - Manufacturing, $800K revenue, Prospect type
5. Adventure Works - Healthcare, $3.2M revenue, Customer type
```

### 2. Contacts (1-2 per account)

| Name | Account | Email | Title | Phone |
|---|---|---|---|---|
| Sarah Chen | Acme Corp | sarah.chen@acmecorp.com | VP of Engineering | +1-555-0101 |
| James Wilson | Acme Corp | james.wilson@acmecorp.com | IT Director | +1-555-0102 |
| Maria Garcia | Northwind Traders | maria.garcia@northwind.com | CTO | +1-555-0201 |
| David Kim | Contoso Ltd | david.kim@contoso.com | Head of Operations | +1-555-0301 |
| Lisa Zhang | Contoso Ltd | lisa.zhang@contoso.com | CISO | +1-555-0302 |
| Tom Brown | Adventure Works | tom.brown@adventureworks.com | IT Manager | +1-555-0501 |

```
Use the Salesforce MCP agent:

Create contacts for our demo accounts:
- Sarah Chen (VP of Engineering) and James Wilson (IT Director) at Acme Corp
- Maria Garcia (CTO) at Northwind Traders
- David Kim (Head of Operations) and Lisa Zhang (CISO) at Contoso Ltd
- Tom Brown (IT Manager) at Adventure Works
Use their company domain for email addresses.
```

### 3. Opportunities (active deals to show revenue at risk)

| Opportunity | Account | Amount | Stage | Close Date | Description |
|---|---|---|---|---|---|
| Acme Corp - Platform Expansion | Acme Corp | $450,000 | Negotiation | Next month | Expanding API integration and adding 500 users |
| Northwind POS Upgrade | Northwind Traders | $180,000 | Proposal | Next month | Point-of-sale system upgrade with payment module |
| Contoso Enterprise License | Contoso Ltd | $1,200,000 | Closed Won | Last month | 3-year enterprise license renewal |
| Contoso Analytics Add-on | Contoso Ltd | $350,000 | Qualification | In 2 months | Real-time analytics dashboard for trading desk |
| Adventure Works HIPAA Module | Adventure Works | $280,000 | Negotiation | In 3 weeks | HIPAA-compliant patient data module |

```
Use the Salesforce MCP agent:

Create these opportunities:
1. "Acme Corp - Platform Expansion" - $450K, Negotiation stage, closing next month.
   Description: "Expanding API integration and adding 500 users"
2. "Northwind POS Upgrade" - $180K, Proposal stage, closing next month.
   Description: "Point-of-sale system upgrade with payment module"
3. "Contoso Enterprise License" - $1.2M, Closed Won, closed last month.
   Description: "3-year enterprise license renewal"
4. "Contoso Analytics Add-on" - $350K, Qualification, closing in 2 months.
   Description: "Real-time analytics dashboard for trading desk"
5. "Adventure Works HIPAA Module" - $280K, Negotiation, closing in 3 weeks.
   Description: "HIPAA-compliant patient data module"
```

### 4. Cases (open support cases that correlate with ServiceNow incidents)

| Case Subject | Account | Status | Priority | Description |
|---|---|---|---|---|
| API gateway timeout errors | Acme Corp | Open | High | Customer reporting intermittent 504 errors on the API gateway since Tuesday |
| Payment processing failures | Northwind Traders | Open | Critical | Payment module returning errors during checkout — affecting store operations |
| Report generation slow | Contoso Ltd | Open | Medium | Monthly risk reports taking 45+ minutes to generate, was under 5 minutes |
| Report generation slow | Contoso Ltd | Open | Medium | Trading desk analytics dashboard timeout during market hours |
| SSO login failures | Adventure Works | Open | High | Users unable to authenticate via SSO since the certificate update |
| Data export not working | Acme Corp | Open | Medium | Scheduled data exports failing silently — no error notification |

```
Use the Salesforce MCP agent:

Create these support cases:
1. Acme Corp - "API gateway timeout errors" - High priority, Open.
   "Customer reporting intermittent 504 errors on the API gateway since Tuesday"
2. Northwind Traders - "Payment processing failures" - Critical, Open.
   "Payment module returning errors during checkout - affecting store operations"
3. Contoso Ltd - "Report generation slow" - Medium, Open.
   "Monthly risk reports taking 45+ minutes to generate, was under 5 minutes"
4. Contoso Ltd - "Report generation slow" - Medium, Open.
   "Trading desk analytics dashboard timeout during market hours"
5. Adventure Works - "SSO login failures" - High, Open.
   "Users unable to authenticate via SSO since the certificate update"
6. Acme Corp - "Data export not working" - Medium, Open.
   "Scheduled data exports failing silently - no error notification"
```

---

## ☁️ ServiceNow — Required Data

### 1. Incidents (matching the Salesforce cases)

Create incidents that mirror or relate to the Salesforce support cases. The agent will correlate them by keywords and company names.

```
Use the ServiceNow MCP agent:

Create these incidents:

1. P2 incident: "API gateway returning 504 timeout errors - multiple customers affected"
   Category: Software, Subcategory: Application
   Description: "Intermittent 504 errors on the API gateway since Tuesday morning.
   Affected customers include Acme Corp and several other enterprise accounts.
   Impact: API-dependent integrations failing for ~15% of requests."

2. P1 incident: "Payment processing service degraded - transaction failures"
   Category: Software, Subcategory: Application
   Description: "Payment processing module returning errors during high-volume periods.
   Customer reports from Northwind Traders confirm checkout failures.
   Business impact: Direct revenue loss for affected merchants."

3. P3 incident: "Database performance degradation - slow report generation"
   Category: Software, Subcategory: Database
   Description: "Report generation queries running 10x slower than baseline.
   Likely related to the index rebuild scheduled last weekend.
   Multiple customers reporting slow dashboards including Contoso Ltd."

4. P2 incident: "SSO certificate rotation caused authentication failures"
   Category: Network, Subcategory: Security
   Description: "After the scheduled SSL certificate rotation, some customers
   are experiencing SSO login failures. Adventure Works confirmed affected.
   Root cause likely: old certificate not fully revoked in IdP."

5. P3 incident: "Scheduled data export jobs failing silently"
   Category: Software, Subcategory: Jobs
   Description: "Automated data export cron jobs failing without alerting.
   Discovered during review after customer complaint from Acme Corp.
   Affects nightly export pipeline for ~20 accounts."

6. P2 incident: "Network latency spike on EU-West region"
   Category: Network, Subcategory: Infrastructure
   Description: "Monitoring shows 3x latency increase on EU-West load balancers
   since last night. Could be affecting European customers including Contoso Ltd."
```

### 2. Problem Records (for pattern-based scenarios)

```
Use the ServiceNow MCP agent:

Create a problem record:
"Recurring database performance degradation during report generation.
Multiple incidents reported over the past 2 weeks (slow queries,
dashboard timeouts). Suspected root cause: missing indexes after
the database migration on March 1st. Affecting customers with
large datasets including Contoso Ltd."
Set impact to high and urgency to medium.
```

### 3. Change Requests (for change risk assessment scenarios)

```
Use the ServiceNow MCP agent:

Create these change requests:

1. "Upgrade billing system to v4.2"
   Description: "Upgrade the billing and invoicing platform from v4.1 to v4.2.
   Includes payment gateway API changes and new tax calculation engine.
   Maintenance window: Saturday 2AM-6AM UTC.
   Rollback plan: Revert to v4.1 container image."
   Risk: Moderate, Category: Software

2. "CRM system database migration"
   Description: "Migrate CRM database from PostgreSQL 14 to PostgreSQL 16.
   Includes schema changes for new analytics features.
   Estimated downtime: 2 hours. Affects all CRM integrations."
   Risk: High, Category: Software
```

### 4. ServiceNow Users (matching company context)

The demo users in ServiceNow should have company fields that match the Salesforce accounts. This enables the agent to correlate "who reported the incident" with "which CRM account they belong to."

```
Use the ServiceNow MCP agent:

First, discover what fields are available on sys_user.
Then check if there's a "company" field.
```

> **Note:** ServiceNow's `sys_user.company` field references the `core_company` table. For the demo, you can either:
> - Create matching companies in `core_company` and assign users to them
> - Or rely on description-based correlation (the incident descriptions above already mention customer names)

---

## ✅ Data Validation

After creating the data, verify the cross-references work:

### From the Salesforce side
```
Show me all open support cases. For each one, tell me the account name,
priority, and a brief description.
```

### From the ServiceNow side
```
Show me all open incidents with priority P1 or P2. Include the description
so I can see which customers are mentioned.
```

### Cross-platform test
```
I need a combined view: Check both Salesforce and ServiceNow for anything
related to "Acme Corp". Show me their account details from Salesforce
and any open incidents from ServiceNow that mention them.
```

---

## 📋 Prompt-to-Data Mapping

Which demo data each joint scenario needs:

| Scenario | Salesforce Data | ServiceNow Data |
|---|---|---|
| Customer outage → incident | Acme Corp account + contacts | — (creates new incident) |
| Revenue at risk escalation | Opportunities with amounts | P1 incidents with customer names in description |
| Case-to-incident correlation | Cases (report generation slow) | Incidents (database performance) |
| Change risk + renewals | Opportunities with close dates | Change requests (billing upgrade) |
| SLA breach + account value | Accounts with revenue | P1/P2 incidents open > 24 hours |
| Onboarding provisioning | Northwind Traders opportunity (Closed Won) | — (creates provisioning incident) |
| Customer health dashboard | Top 3 accounts by revenue | Incidents mentioning those account names |
| Defect tracking | Cases (payment error) | Problem record (payment processing) |
| Executive board report | All opportunities (pipeline value) | All P1 incidents |
