# Operations: Retention, Deletion, Backup, and Recovery

## Data retention

| Data | Retention | Mechanism |
| --- | --- | --- |
| Cost line items, inventory, recommendations, anomalies, forecasts, investigations | Until the tenant is deleted | — |
| Raw uploaded CSV bytes (`import_files`) | `NIMBRIX_RAW_UPLOAD_RETENTION_DAYS` (30) | Daily retention job in the worker |
| Idempotency records | 24 h after creation | Daily retention job |
| Expired or revoked sessions | `NIMBRIX_SESSION_RETENTION_DAYS` (7) after expiry/revocation | Daily retention job |
| Audit events | Indefinitely by the application (append-only) | Remove only by an operator with table-owner rights, after export, per your compliance policy |
| Job rows | Indefinitely (small) | — |

The retention job (`modules/maintenance.py`) is covered by `test_retention_purges_only_expired_data`.

## Tenant deletion

All tenant-owned tables reference `tenants.id` with `ON DELETE CASCADE`, except
`audit_events`, which is deliberately not cascaded. Procedure:

1. Export the tenant's audit events if required (`GET /api/v1/audit-events`, paginated, as an auditor).
2. As the database owner: `DELETE FROM tenants WHERE slug = '<slug>';` This removes users'
   memberships, sessions, imports, line items, inventory, analysis, approvals, and executions.
   It fails while audit events reference the tenant — by design.
3. Decide on audit events per policy: either keep them (and keep the tenant row as a tombstone)
   or remove them with the trigger temporarily disabled
   (`ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable;` … re-enable), recording
   the action in your change log.
4. Users without remaining memberships can then be deleted from `users`.

There is no self-service deletion API yet.

## Backup and restore

PostgreSQL is the only stateful component. For the local stack:

```bash
# Backup (custom format, compressed)
docker compose exec -T postgres pg_dump -U nimbrix -d nimbrix -Fc > nimbrix-$(date +%F).dump

# Restore into an empty database
docker compose exec -T postgres dropdb -U nimbrix --if-exists nimbrix_restore
docker compose exec -T postgres createdb -U nimbrix nimbrix_restore
docker compose exec -T postgres pg_restore -U nimbrix -d nimbrix_restore --no-owner < nimbrix-YYYY-MM-DD.dump
```

After a restore, run `alembic upgrade head` (the migrate service does this) before starting the
API; readiness stays `503` until migrations match. In AWS, use RDS automated backups with
point-in-time recovery plus periodic snapshots copied to a second region (see deployment docs).

## Disaster recovery: what is and is not covered

- **RPO/RTO** depend entirely on the database backup strategy; Nimbrix adds no replication.
- Jobs are durable rows; after a restore, queued/running jobs resume (leases expire and are reclaimed).
- Synthetic demo data can be regenerated deterministically; real CSV uploads older than the raw
  retention window cannot be re-parsed (their line items remain).
- Rate-limit counters and in-memory caches are lost on restart (acceptable).
- Single-region by default; multi-region failover is not implemented.

## Least-privilege IAM

**Customer read-only role** (what Nimbrix assumes): see the policy in
[ingestion.md](ingestion.md#aws-access-model-and-limitations). Trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::<NIMBRIX_RUNTIME_ACCOUNT>:role/nimbrix-runtime"},
    "Action": "sts:AssumeRole",
    "Condition": {"StringEquals": {"sts:ExternalId": "<per-tenant external id>"}}
  }]
}
```

**Nimbrix runtime role** (ECS task role for API/worker):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::*:role/NimbrixReadOnly"},
    {"Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:aws:secretsmanager:<region>:<account>:secret:nimbrix/*"}
  ]
}
```

No role grants EC2 write actions. A future live action requires its own role (see
[remediation.md](remediation.md#a-real-aws-action-not-implemented-not-enabled)).

## Observability

- `docker compose --profile observability up -d` starts Prometheus (`127.0.0.1:9290`) and
  Grafana (`127.0.0.1:3290`, anonymous viewer) with the provisioned "Nimbrix" dashboard:
  HTTP rate/latency/errors, 429s, jobs by outcome, import duration and rows, agent calls,
  recommendations by status, remediation outcomes, policy denials, authentication.
- Tracing: set `NIMBRIX_OTEL_ENABLED=true` and `NIMBRIX_OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP/HTTP).
  Spans: HTTP routes (health/metrics excluded), SQL statements (no parameters), one span per job
  and per agent node. Logs include `trace_id`/`span_id` when a span is active.
- `/api/v1/metrics` is unauthenticated on the internal network and blocked at the web proxy;
  in production restrict it with network policy.
