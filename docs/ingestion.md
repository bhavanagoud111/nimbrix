# Ingestion

Nimbrix ingests billing records and resource inventory through adapters that all emit
the same normalized shapes (`CostRecord`, `ResourceRecord`). Every import runs as a
background job; the API only records the request and returns `202 Accepted`.

## Sources

| Source | Mode label | Adapter | Resource IDs | Tags | Freshness |
| --- | --- | --- | --- | --- | --- |
| Synthetic demo (`POST /imports/demo`) | `demo` | `synthetic.generate` | Yes | Yes | Generated on demand, deterministic |
| CSV upload (`POST /imports/upload`) | `live` | `csv_source.parse_csv` | If provided | `tags` JSON or `tag:<key>` columns | As of the file |
| AWS Cost Explorer (`POST /connections/{id}/sync`) | `live` | `aws.fetch_cost_explorer` | **No** | **No** | Refreshed several times per day; recent days are estimates |
| AWS CUR via Athena | `live` | `aws.fetch_cur_athena` | Yes | Not selected by default | Hours to a day; restated until month close |
| EC2 + CloudWatch inventory | `live` | `aws.fetch_ec2_inventory` | Yes | Yes | Point-in-time at sync; CPU window 14 days, hourly |

The CUR adapter is implemented and tested against stubs but is not exposed through an
endpoint yet; it needs per-connection Athena settings (database, table, workgroup) that
the UI does not collect. Cost Explorer is the supported live path.

## Synthetic demo dataset

`modules/ingestion/synthetic.py` (generator `nimbrix-synthetic-v1`) builds 120 days (configurable
30–400) of daily records for three accounts (prod `111111111111`, staging `222222222222`, dev
`333333333333`), four regions, and ten services. Teams are `payments`, `search`, `platform`, `data`;
some spend is deliberately untagged. The same `(end_date, days, seed)` always produces identical
records with identical source IDs, so re-imports are idempotent.

Labeled scenarios: an 8-node `c5.4xlarge` fleet (`search-reindex-batch-*`) launched four days
before the end date in the dev account (≈ +$130/day of EC2), an idle dev instance
(`dev-build-runner-legacy`, 0.9% average CPU), an underutilized prod instance, business-hours dev
instances, an unattached 500 GB volume, an unassociated Elastic IP, a `nimbrix:protected=true` dev
instance, and a staging instance with no CloudWatch data. Budgets are sized from the pre-spike
baseline. Prices come from the reference table in `modules/pricing.py`.

## Normalization rules

- **Time:** all timestamps are converted to UTC. `usage_date` is the UTC date of `usage_start`.
  Records may span at most 24 hours; monthly rows are rejected rather than smeared.
- **Money:** exact decimals (`NUMERIC(18,6)`), negative amounts allowed (credits). The ISO currency
  code is kept on every row; currencies are never converted or summed together.
- **Allocation:** `environment` and `team` are derived from tags (`environment`/`env`/`stage`,
  `team`/`owner-team`, case-insensitive; `production`→`prod`, `development`→`dev`, …). Missing
  tags leave them `NULL`, which analytics reports as *unallocated*.
- **Provenance:** every line item stores the import ID and source details (`estimated` for Cost
  Explorer, row number for CSV, generator and seed for demo data).

## Idempotency, deduplication, and late data

| Mechanism | Effect |
| --- | --- |
| `Idempotency-Key` header | A retried request returns the stored response |
| File/parameter checksum | Re-importing identical data creates a `duplicate` import that points at the original; no job runs |
| `(tenant, provider, source_record_id)` unique key + upsert | New IDs are inserted; changed rows are **updated** (late-arriving corrections); identical rows are counted as `unchanged` |
| Inventory `observed_at` guard | An older observation never overwrites a newer one |
| Job `dedupe_key` | The same import cannot be queued twice while active |

Every import reports `rows_received = inserted + updated + unchanged + rejected`.

## Import states

`queued → running → succeeded | partially_failed | failed`, or `duplicate` immediately.
`partially_failed` means some rows were rejected; their row numbers, codes, and messages are
returned by `GET /imports/{id}` (first 200 errors stored). Whole-file problems (not UTF-8, no
header, missing required columns, empty dataset) fail the import.

## Jobs and retries

Jobs live in the `jobs` table ([ADR-0003](adr/0003-db-backed-jobs.md)). The worker claims with
`FOR UPDATE SKIP LOCKED` and a 120 s lease extended by heartbeats. Throttling, connectivity, and
database errors retry with exponential backoff (2ⁿ s, max 300 s, 3 attempts); validation errors
fail immediately. A job whose worker crashes is reclaimed after its lease expires; a crash on the
final attempt marks it failed. `GET /jobs/{id}` shows state, attempts, result, and error.

## AWS access model and limitations

- **Disabled by default** (`NIMBRIX_AWS_ENABLED=false`). The offline demo never calls AWS.
- Nimbrix never accepts or stores access keys. Connections hold only an account ID, a role ARN,
  an optional external ID, and regions. Requests containing key-like values are rejected.
- The Nimbrix runtime uses its own credential chain (task/instance role) only to call
  `sts:AssumeRole` (900 s sessions, external ID) into the customer role. Temporary credentials
  live in memory for one job.
- `POST /connections/aws/validate` checks role assumption, account match, and `ec2:DescribeInstances`.
  The optional Cost Explorer check is off by default because **each Cost Explorer API request is
  billed** (USD 0.01 per paginated request at the time of writing).
- Cost Explorer returns daily totals by service and linked account only: **no resource IDs, no
  tags, no region** in this grouping. Resource-level analysis needs CUR or inventory.
- Cost Explorer's latest days are **estimates** (`estimated` provenance flag) and are re-imported
  on later syncs, which updates them in place.
- CloudWatch CPU is the only utilization signal collected; memory requires the CloudWatch agent and
  is not assumed. Instances without datapoints keep `utilization = null`, and rules treat that as
  insufficient evidence.
- Prices for EC2/EBS/IPv4 come from a reference list-price table, not the Pricing API, and exclude
  discounts (Savings Plans, RIs, EDP). Unknown instance types get no price.

Minimum IAM policy for the customer role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "NimbrixReadOnly",
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeAddresses",
        "cloudwatch:GetMetricData"
      ],
      "Resource": "*"
    }
  ]
}
```

Trust policy: allow `sts:AssumeRole` from the Nimbrix runtime role with the condition
`"StringEquals": {"sts:ExternalId": "<external id>"}`.

## CSV format

See [`data/fixtures/README.md`](../data/fixtures/README.md) for examples. Required columns:
`line_item_id, usage_start, usage_end, account_id, service, amount, currency`. Optional:
`region, resource_id, usage_type, usage_quantity, usage_unit, tags` and `tag:<key>` columns.
Uploads are limited to `NIMBRIX_UPLOAD_MAX_BYTES` (10 MiB default) and 500,000 rows. Cell
values are stored as text and never evaluated.
