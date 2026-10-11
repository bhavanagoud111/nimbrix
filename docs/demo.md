# Demo Script (≈ 4 minutes)

Setup (once): `cp .env.example .env && make demo`, wait for `scripts/wait-for-demo.sh` to print
"demo data ready", open <http://127.0.0.1:3200>. Password for all demo users: the value of
`NIMBRIX_DEMO_PASSWORD` in `.env` (default `nimbrix-demo-password`).

Everything shown is synthetic demo data, labeled **Demo data**; every action is a
**simulation**. No AWS account or LLM key is used.

| Time | Screen | What to show and say |
| --- | --- | --- |
| 0:00 | Login | Sign in as `analyst@acme.demo`. "Demo auth with five roles; production would use OIDC." |
| 0:15 | Overview | Spend +10% vs prior period, EC2 is the top driver, 4 open anomalies, ~$560/month estimated savings, budgets at risk. The chart shows the step change five days ago. |
| 0:45 | Cost Explorer | Group by service → click **AmazonEC2** to drill into resources. Point out UTC and the excluded partial day. |
| 1:10 | Anomalies → service AmazonEC2 | Observed vs expected chart with the anomaly window, score, severity; contributors are 8 resources new in the window. |
| 1:35 | **Investigate** | Graph progress across 7 nodes and 5 agent roles; root cause: "8 new c5.4xlarge resources (team search, dev) … 130.56/day, 98% of the excess". Evidence chips link to cited facts. Mode: deterministic. Numbers are computed, not generated. |
| 2:15 | Recommendations → idle `dev-build-runner-legacy` | Eligibility checks (CPU 0.9% avg, 14 days, coverage), assumptions, formula `0.384 × 730 = $280.32`. **Simulate change**: current vs projected cost, range, excluded EBS storage — "not a realized saving". |
| 2:50 | Resource Inventory → `dev-build-runner-legacy` → **Request approval** | Policy check passes (dev, not protected, running, fresh inventory). Show the denial for `payments-batch-01` (prod) as contrast. |
| 3:15 | Sign in as `approver@acme.demo` → Approval Center | Exact payload and SHA-256 hash, 10 policy checks, impact, rollback plan. **Approve simulation**, then **Execute simulation**: before *running* → after *stopped (simulated)*, verified, cloud API called: false. |
| 3:45 | Audit trail / Audit Timeline (approver or `auditor@acme.demo`) | requested → approved → remediation.requested → remediation.succeeded, append-only. |
| 4:00 | Close | "Ingest → aggregate → detect → correlate → recommend → simulate → approve → audit, offline, with every number traceable." |
