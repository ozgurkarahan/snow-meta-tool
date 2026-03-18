# 🧪 Business Scenario Prompts

Real-world business prompts for testing the ServiceNow MCP agent. Organized by ITSM workflow — each scenario reflects how IT teams, service desk analysts, managers, and executives actually interact with ServiceNow.

> **How to use:** Point an MCP-compatible agent (Claude in AI Foundry, Claude Desktop, etc.) at the ServiceNow MCP endpoint and run these prompts. The agent should discover tables and fields dynamically — no pre-configuration needed.

---

## 🏢 Scenario 1: Service Desk Analyst — Daily Triage

*Persona: L1 Service Desk Analyst starting their shift*

### Morning Queue Review
```
Good morning. Show me my open incident queue — I need to see what came in overnight,
sorted by priority. Include the incident number, description, who reported it,
and when it was created.
```

### Prioritization Check
```
How many P1 and P2 incidents are currently open across all teams?
Which ones have been open the longest without being assigned?
```

### Quick Incident Logging
```
A user just called — they can't access the VPN from their home office.
Their laptop shows "authentication failed" when connecting to GlobalProtect.
Log this as a new incident with medium urgency.
```

### Incident Categorization
```
I have incident INC0010001 but I'm not sure how to categorize it.
What categories and subcategories are available for incidents?
It's a software issue related to email.
```

### Escalation
```
Incident INC0000060 about email connectivity has been open for 3 days
with no resolution. Escalate it to priority 2 and add a work note saying
"Escalating due to extended resolution time — affecting multiple users
in the finance department."
```

---

## 📊 Scenario 2: IT Manager — Operational Reporting

*Persona: IT Service Delivery Manager preparing for a weekly review*

### Weekly Incident Summary
```
Give me a summary of this week's incident volume: total created,
total resolved, and a breakdown by priority level.
How does this compare to what's still open?
```

### SLA Performance
```
How many high-priority incidents (P1 and P2) were created this month?
Of those, how many are still open? Show me the ones that have been
open the longest — I need to flag these in my leadership meeting.
```

### Team Workload Analysis
```
Show me the current incident distribution across assignment groups.
Which teams have the most open incidents? Are there any unassigned
incidents that need attention?
```

### Trend Analysis
```
What are the top 5 most common types of incidents we're seeing?
Search for patterns in recent incident descriptions — are there
recurring issues we should address with a problem record?
```

### Category Deep Dive
```
Break down all open incidents by category. I want to understand
if we're seeing more network issues vs. software issues vs. hardware
issues this month.
```

---

## 🔄 Scenario 3: Change Management — End-to-End Workflow

*Persona: Change Manager overseeing the weekly Change Advisory Board (CAB)*

### CAB Preparation
```
What change requests are scheduled for this week? Show me the ones
that still need approval, along with their risk level and implementation plan.
```

### Change Impact Assessment
```
We're planning a network switch upgrade this weekend. Before I approve it,
find any recent incidents related to network switches or network connectivity
in the last 30 days. I want to understand the current risk profile.
```

### Change Approval
```
I've reviewed change request CHG0000001 and it looks good.
Approve it and add a comment: "Approved by CAB — implementation
window confirmed for Saturday 2AM-6AM."
```

### Post-Implementation Review
```
The network upgrade change was completed last weekend.
Check if there were any new incidents created since then
related to network connectivity. I need to know if the change
caused any issues.
```

---

## 🔍 Scenario 4: Problem Management — Root Cause Investigation

*Persona: Problem Manager investigating a recurring issue*

### Pattern Detection
```
We keep getting incidents about slow application performance.
Search for all incidents mentioning "slow" or "performance" or "timeout"
in the last month. Group them by assignment group and category
to see if there's a pattern.
```

### Problem Record Creation
```
Based on the pattern of performance incidents, create a problem record
with the description: "Recurring application performance degradation
reported across multiple business units. Initial analysis suggests
database connection pool exhaustion during peak hours."
Set the impact to high and urgency to medium.
```

### Related Incident Correlation
```
I'm investigating a major outage that happened yesterday.
Find all P1 and P2 incidents from the last 48 hours and show them
sorted by creation time. I need to build a timeline of what happened.
```

---

## 👤 Scenario 5: Employee Self-Service

*Persona: Business user who needs IT help*

### Report an Issue
```
My laptop keeps freezing every time I open more than 3 Excel spreadsheets.
It started happening after the Windows update last Tuesday.
This is affecting my ability to complete the quarterly financial report
which is due Friday. Please log this for me.
```

### Check Request Status
```
I submitted a request for a new monitor last week.
Can you check the status of any recent requests or incidents
logged under my name?
```

### Software Access Request
```
I need access to Tableau for a new analytics project starting next month.
What's the process — is there a catalog item or should I create
an incident? What tables handle software requests?
```

---

## 🏥 Scenario 6: Major Incident Management

*Persona: Major Incident Manager during a live outage*

### Situation Assessment
```
We have a major outage — email is down for the entire company.
Show me any existing incidents related to email, Exchange,
or mail in the last 2 hours. Has anyone already logged this?
```

### Major Incident Declaration
```
No existing incident covers this. Create a P1 incident:
"Complete email outage — Microsoft Exchange Online service unavailable.
All users in EMEA and North America affected. Business impact:
critical communications blocked including customer-facing operations."
Set impact and urgency both to 1.
```

### Stakeholder Update
```
Update the major email outage incident with a work note:
"15:30 UTC Update — Root cause identified as misconfigured
mail flow rule deployed at 14:00 UTC. Engineering team is
rolling back the change. Estimated resolution: 30 minutes.
Affected users: approximately 5,000 across 3 regions."
```

### Resolution
```
The email outage is resolved. Update the incident to reflect that
the issue was fixed by rolling back the mail flow rule.
Add the resolution notes and close the incident.
```

---

## ⚖️ Scenario 7: Approvals and Governance

*Persona: Department head reviewing pending approvals*

### Approval Queue
```
Do I have any pending approvals? Show me what's waiting
for my sign-off — changes, requests, anything that needs
my attention.
```

### Informed Approval Decision
```
I have a change request pending my approval for a database migration.
Before I approve it, show me any recent incidents related to databases
or migrations. I want to assess the risk.
```

### Bulk Approval Review
```
Show me all pending change approvals for this week's maintenance window.
List them with their risk level, affected services, and who requested them.
```

---

## 📈 Scenario 8: Executive Dashboard

*Persona: CIO/VP of IT wanting a quick operational pulse*

### Operational Health Check
```
Give me a quick health check of our IT operations:
How many open P1 and P2 incidents do we have right now?
Any major incidents in the last 24 hours?
What's our total open incident count?
```

### Month-over-Month Comparison
```
How many incidents were created this month compared to
how many are still open? Break it down by priority.
Are we keeping up with the volume or falling behind?
```

### Service Quality Metrics
```
What percentage of our incidents are P1 or P2?
I want to understand if we're seeing too many critical issues
or if our priority distribution is healthy.
```

---

## 🔗 Scenario 9: Cross-Process Workflows

*These scenarios span multiple ITSM processes — incident, change, problem — showing the meta-tool's ability to work across the entire ServiceNow data model.*

### Incident-to-Problem Escalation
```
Find all incidents about VPN connectivity issues in the last 2 weeks.
If there are more than 5, create a problem record to investigate
the root cause. Include the count and a summary in the problem description.
```

### Change Risk Assessment from Incident History
```
We're planning to upgrade the CRM system next weekend.
Before the change, pull all incidents related to CRM
from the last 90 days. Summarize the most common failure modes
so we can plan our rollback strategy.
```

### Full ITSM Lifecycle
```
We've been having printer issues across the 3rd floor for two weeks.
First, find all related incidents. Then create a problem record
linking to the pattern. Finally, draft a change request to replace
the print server that keeps failing.
```

### Onboarding New ITSM Staff
```
I'm new to this ServiceNow instance. Walk me through the main tables
we use — incidents, changes, problems, and requests. For each one,
tell me what fields are available and what the key statuses are.
```

---

## 🤝 Scenario 10: Salesforce CRM + ServiceNow ITSM Joint Use Cases

*These scenarios require both the **Salesforce MCP** and **ServiceNow MCP** tools connected to the same agent. They demonstrate cross-platform workflows where CRM and ITSM data converge — a single AI agent bridging customer-facing and internal operations.*

> **Prerequisites:** The agent must have both MCP servers connected — the Salesforce meta-tool for CRM data and the ServiceNow meta-tool for ITSM data. Both use identity propagation, so actions in each system are attributed to the authenticated user.

### Customer-Reported Outage → IT Incident

```
A major customer, Acme Corp, just called our sales team reporting
that they can't access our customer portal. Check Salesforce for
the Acme Corp account details and their support tier, then create
a P2 incident in ServiceNow with the customer context:
account name, support tier, and their primary contact.
```

*Tests: Salesforce query (Account, Contact) → ServiceNow write (incident creation with CRM context)*

### Escalation with Customer Revenue Context

```
We have a P1 incident in ServiceNow about API gateway failures.
Check which Salesforce opportunities or accounts are affected —
look for any open deals or active contracts that mention
"API" or "integration" in their description.
I need to understand the revenue at risk before our leadership call.
```

*Tests: ServiceNow query (incident) → Salesforce query (Opportunity, Account) → business impact analysis*

### Support Case to Incident Correlation

```
Our customer success team in Salesforce has logged 8 support cases
this week about slow report generation. Check ServiceNow to see
if there's already an incident or problem record covering this.
If not, create one and reference the Salesforce case count
in the description.
```

*Tests: Salesforce query (Case) → ServiceNow query (incident search) → ServiceNow write (incident/problem creation)*

### Change Risk Assessment with Customer Impact

```
We're planning a maintenance window to upgrade our billing system
this Saturday. Before we approve the change in ServiceNow, check
Salesforce for any enterprise accounts with renewal dates in the
next 2 weeks. I don't want billing issues during a critical
renewal period. Summarize the risk.
```

*Tests: ServiceNow query (change_request) → Salesforce query (Opportunity with close dates) → risk summary*

### SLA Breach Alert with Account Context

```
Find all P1 and P2 incidents in ServiceNow that have been open
for more than 24 hours. For each one, check if the affected user
or description mentions a company name that matches a Salesforce
account. Flag any that involve accounts with deals worth over $100K.
```

*Tests: ServiceNow query (incident, time filter) → Salesforce query (Account, Opportunity amount) → prioritized list*

### New Customer Onboarding — IT Provisioning

```
We just closed a deal with Northwind Traders in Salesforce.
Check the opportunity details for the products they purchased,
then create a ServiceNow incident to provision their environment:
tenant setup, API credentials, and SSO configuration.
Include the Salesforce opportunity number in the incident for traceability.
```

*Tests: Salesforce query (Opportunity, OpportunityLineItem) → ServiceNow write (incident for provisioning)*

### Customer Health Dashboard

```
Give me a combined health view for our top 3 accounts by revenue.
For each account:
1. From Salesforce: account name, annual revenue, open opportunities,
   recent activity
2. From ServiceNow: open incidents, average priority, any P1s in
   the last 30 days
I need this for our quarterly business review.
```

*Tests: Salesforce query (Account by revenue) → ServiceNow aggregate (incidents per customer) → consolidated report*

### Product Defect Tracking Across Systems

```
Our engineering team fixed a bug in the payment module.
Find all Salesforce cases tagged with "payment error" that are
still open, and check ServiceNow for the related problem record.
Update the ServiceNow problem with a work note that the fix has
been deployed, and give me the list of Salesforce cases that
should be notified.
```

*Tests: Salesforce query (Case) → ServiceNow query + write (problem update) → cross-reference for notification*

### Executive Revenue-at-Risk Report

```
I'm preparing for the board meeting. Give me a single view:
- Total open P1 incidents from ServiceNow
- For each P1, identify if it impacts a Salesforce account
  with open opportunities
- Calculate the total pipeline value at risk
- Recommend which incidents to prioritize based on revenue impact
```

*Tests: ServiceNow query (P1 incidents) → Salesforce query (Account, Opportunity pipeline) → revenue-weighted prioritization*

---

## ✅ Verification Checklist

After running these scenarios, verify:

- [ ] Agent discovers tables and fields dynamically without hardcoded knowledge
- [ ] Queries use ServiceNow encoded query syntax correctly
- [ ] Records are created with `sys_created_by` matching the authenticated user (identity propagation)
- [ ] Multi-step workflows maintain context across discover → query → write
- [ ] Aggregate queries return meaningful business metrics
- [ ] Error messages from ServiceNow are surfaced clearly to the user
- [ ] Approval workflows correctly update `sysapproval_approver` records
- [ ] Text search finds relevant incidents by keyword
- [ ] The agent suggests appropriate categories, priorities, and fields
- [ ] Cross-table operations (incident → problem → change) work seamlessly
- [ ] **Cross-platform:** Agent uses both Salesforce and ServiceNow MCP tools in a single workflow
- [ ] **Cross-platform:** CRM data (accounts, opportunities, cases) enriches ITSM actions
- [ ] **Cross-platform:** Identity propagation works independently in both systems
