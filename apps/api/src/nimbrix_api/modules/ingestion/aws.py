"""Read-only AWS adapters: STS role assumption, Cost Explorer, CUR via Athena, EC2 + CloudWatch.

Credential model: Nimbrix never receives or stores AWS keys. The process uses the
default boto3 credential chain of its own runtime (instance/task role, or environment
for local experiments) only to call ``sts:AssumeRole`` into the customer's read-only
role with an external ID. The temporary credentials live in memory for one job.

Billing data characteristics this module does not hide (see docs/ingestion.md):

* Cost Explorer is **not real time**: data refreshes several times a day and the most
  recent days are estimates (``Estimated: true``) that can change.
* Cost Explorer API requests are billed per paginated request (USD 0.01 at the time of
  writing); the adapter uses DAILY granularity with at most two GroupBy dimensions and
  returns no resource IDs or tags.
* CUR files land in S3 with hours-to-a-day delay and are restated until month close.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from nimbrix_api.core.config import Settings
from nimbrix_api.modules.ingestion.records import CostRecord, ResourceRecord
from nimbrix_api.modules.jobs.queue import PermanentJobError, RetryableJobError
from nimbrix_api.modules.pricing import PUBLIC_IPV4_HOURLY_USD, ebs_monthly, ec2_hourly

if TYPE_CHECKING:
    from nimbrix_api.models import CloudConnection

logger = logging.getLogger(__name__)

SESSION_NAME = "nimbrix-readonly"
_THROTTLING = {
    "Throttling",
    "ThrottlingException",
    "RequestLimitExceeded",
    "TooManyRequestsException",
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BOTO_CONFIG = BotoConfig(
    retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30
)


class AwsDisabledError(PermanentJobError):
    pass


def classify(exc: Exception) -> Exception:
    """Map boto errors to retryable vs permanent job errors without leaking request details."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in _THROTTLING:
            return RetryableJobError(f"AWS throttled the request ({code})")
        return PermanentJobError(f"AWS rejected the request ({code})")
    if isinstance(exc, BotoCoreError):
        return RetryableJobError(f"AWS connectivity error ({type(exc).__name__})")
    return exc


@dataclass
class AwsSessionFactory:
    """Builds boto3 sessions scoped to a connection's read-only role."""

    settings: Settings
    base_session: Any = None  # injectable for tests

    def _base(self) -> Any:
        if not self.settings.aws_enabled:
            raise AwsDisabledError("AWS access is disabled (NIMBRIX_AWS_ENABLED=false)")
        return self.base_session or boto3.Session(region_name=self.settings.aws_default_region)

    def for_connection(self, connection: CloudConnection) -> Any:
        base = self._base()
        if not connection.role_arn:
            raise PermanentJobError("connection has no role_arn; Nimbrix only uses assumed roles")
        params: dict[str, Any] = {
            "RoleArn": connection.role_arn,
            "RoleSessionName": SESSION_NAME,
            "DurationSeconds": 900,
        }
        if connection.external_id:
            params["ExternalId"] = connection.external_id
        try:
            creds = base.client("sts", config=_BOTO_CONFIG).assume_role(**params)["Credentials"]
        except (ClientError, BotoCoreError) as exc:
            raise classify(exc) from None
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=(connection.regions or [self.settings.aws_default_region])[0],
        )


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    detail: str


def validate_connection(
    session: Any, connection: CloudConnection, *, check_cost_explorer: bool
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    try:
        identity = session.client("sts", config=_BOTO_CONFIG).get_caller_identity()
        same_account = identity.get("Account") == connection.account_id
        checks.append(
            ValidationCheck(
                "assume_role",
                same_account,
                "assumed role in the expected account"
                if same_account
                else "role belongs to a different account",
            )
        )
    except (ClientError, BotoCoreError) as exc:
        checks.append(ValidationCheck("assume_role", False, str(classify(exc))))
        return checks
    region = (connection.regions or ["us-east-1"])[0]
    try:
        session.client("ec2", region_name=region, config=_BOTO_CONFIG).describe_instances(
            MaxResults=5
        )
        checks.append(
            ValidationCheck("ec2_read", True, f"ec2:DescribeInstances allowed in {region}")
        )
    except (ClientError, BotoCoreError) as exc:
        checks.append(ValidationCheck("ec2_read", False, str(classify(exc))))
    if check_cost_explorer:
        end = datetime.now(UTC).date()
        try:
            session.client("ce", region_name="us-east-1", config=_BOTO_CONFIG).get_cost_and_usage(
                TimePeriod={"Start": str(end - timedelta(days=1)), "End": str(end)},
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
            )
            checks.append(
                ValidationCheck(
                    "cost_explorer_read", True, "ce:GetCostAndUsage allowed (one billed request)"
                )
            )
        except (ClientError, BotoCoreError) as exc:
            checks.append(ValidationCheck("cost_explorer_read", False, str(classify(exc))))
    return checks


def fetch_cost_explorer(session: Any, start: date, end: date) -> Iterator[CostRecord]:
    """Daily unblended cost by service and linked account for [start, end)."""
    client = session.client("ce", region_name="us-east-1", config=_BOTO_CONFIG)
    token: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost", "UsageQuantity"],
            "GroupBy": [
                {"Type": "DIMENSION", "Key": "SERVICE"},
                {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            ],
        }
        if token:
            params["NextPageToken"] = token
        try:
            response = client.get_cost_and_usage(**params)
        except (ClientError, BotoCoreError) as exc:
            raise classify(exc) from None
        pages += 1
        for result in response.get("ResultsByTime", []):
            day = date.fromisoformat(result["TimePeriod"]["Start"])
            day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            for group in result.get("Groups", []):
                service, account = group["Keys"]
                cost = group["Metrics"]["UnblendedCost"]
                usage = group["Metrics"].get("UsageQuantity", {})
                yield CostRecord(
                    source_record_id=f"ce:{day}:{account}:{service}",
                    provider="aws",
                    account_id=account,
                    service=service[:128],
                    usage_start=day_start,
                    usage_end=day_start + timedelta(days=1),
                    amount=Decimal(cost["Amount"]),
                    currency=cost.get("Unit", "USD").upper(),
                    usage_quantity=Decimal(usage["Amount"]) if usage.get("Amount") else None,
                    usage_unit=usage.get("Unit") or None,
                    provenance={
                        "source": "aws_cost_explorer",
                        "estimated": bool(result.get("Estimated", False)),
                        "granularity": "DAILY",
                    },
                )
        token = response.get("NextPageToken")
        if not token:
            logger.info("cost_explorer_fetched", extra={"pages": pages})
            return


def fetch_cur_athena(
    session: Any,
    *,
    database: str,
    table: str,
    workgroup: str,
    start: date,
    end: date,
    poll_seconds: float = 2.0,
    max_wait_seconds: float = 300.0,
) -> Iterator[CostRecord]:
    """Query a CUR (legacy CUR column names) table in Athena for [start, end)."""
    for identifier in (database, table):
        if not _IDENTIFIER.match(identifier):
            raise PermanentJobError("Athena database/table names must be simple identifiers")
    client = session.client("athena", config=_BOTO_CONFIG)
    # Identifiers are validated above; the date range is passed as bound parameters.
    query = (
        "SELECT identity_line_item_id, line_item_usage_start_date, "  # noqa: S608
        "line_item_usage_end_date, "
        "line_item_usage_account_id, line_item_product_code, product_region, "
        "line_item_resource_id, "
        "line_item_usage_type, line_item_usage_amount, pricing_unit, line_item_unblended_cost, "
        "line_item_currency_code "
        f'FROM "{database}"."{table}" '
        "WHERE line_item_usage_start_date >= from_iso8601_date(?) "
        "AND line_item_usage_start_date < from_iso8601_date(?)"
    )
    try:
        execution_id = client.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": database},
            WorkGroup=workgroup,
            ExecutionParameters=[f"'{start.isoformat()}'", f"'{end.isoformat()}'"],
        )["QueryExecutionId"]
        waited = 0.0
        while True:
            status = client.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"][
                "Status"
            ]
            state = status["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                raise PermanentJobError(f"Athena query {state.lower()}")
            if waited >= max_wait_seconds:
                raise RetryableJobError("Athena query did not finish in time")
            time.sleep(poll_seconds)
            waited += poll_seconds
        paginator = client.get_paginator("get_query_results")
        first = True
        for page in paginator.paginate(QueryExecutionId=execution_id):
            rows = page["ResultSet"]["Rows"]
            if first:
                rows = rows[1:]  # header row
                first = False
            for row in rows:
                v = [cell.get("VarCharValue") for cell in row["Data"]]
                usage_start = datetime.fromisoformat(v[1].replace(" ", "T")).replace(tzinfo=UTC)
                usage_end = datetime.fromisoformat(v[2].replace(" ", "T")).replace(tzinfo=UTC)
                yield CostRecord(
                    source_record_id=f"cur:{v[0]}:{v[1]}",
                    provider="aws",
                    account_id=v[3],
                    service=(v[4] or "unknown")[:128],
                    region=v[5] or None,
                    resource_id=v[6] or None,
                    usage_type=v[7] or None,
                    usage_quantity=Decimal(v[8]) if v[8] else None,
                    usage_unit=v[9] or None,
                    usage_start=usage_start,
                    usage_end=usage_end,
                    amount=Decimal(v[10] or "0"),
                    currency=(v[11] or "USD").upper(),
                    provenance={"source": "aws_cur", "athena_query_id": execution_id},
                )
    except (ClientError, BotoCoreError) as exc:
        raise classify(exc) from None


def _tags(raw: list[dict[str, str]] | None) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in raw or []}


def fetch_ec2_inventory(
    session: Any, region: str, account_id: str, *, window_days: int = 14
) -> list[ResourceRecord]:
    """Instances (with CloudWatch CPU evidence), EBS volumes, and Elastic IPs in one region."""
    observed_at = datetime.now(UTC)
    ec2 = session.client("ec2", region_name=region, config=_BOTO_CONFIG)
    cloudwatch = session.client("cloudwatch", region_name=region, config=_BOTO_CONFIG)
    resources: list[ResourceRecord] = []
    try:
        instances = [
            instance
            for page in ec2.get_paginator("describe_instances").paginate()
            for reservation in page["Reservations"]
            for instance in reservation["Instances"]
        ]
        utilization = _cpu_utilization(
            cloudwatch, [i["InstanceId"] for i in instances], window_days, observed_at
        )
        for inst in instances:
            tags = _tags(inst.get("Tags"))
            resources.append(
                ResourceRecord(
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_id=inst["InstanceId"],
                    resource_type="ec2_instance",
                    state=inst["State"]["Name"],
                    observed_at=observed_at,
                    name=tags.get("Name"),
                    tags=tags,
                    attributes={
                        "instance_type": inst["InstanceType"],
                        "launch_time": inst["LaunchTime"].isoformat()
                        if inst.get("LaunchTime")
                        else None,
                        "platform": inst.get("PlatformDetails", "linux"),
                    },
                    hourly_cost=ec2_hourly(inst["InstanceType"]),
                    utilization=utilization.get(inst["InstanceId"]),
                )
            )
        for page in ec2.get_paginator("describe_volumes").paginate():
            for vol in page["Volumes"]:
                tags = _tags(vol.get("Tags"))
                attachments = vol.get("Attachments") or []
                resources.append(
                    ResourceRecord(
                        provider="aws",
                        account_id=account_id,
                        region=region,
                        resource_id=vol["VolumeId"],
                        resource_type="ebs_volume",
                        state=vol["State"],
                        observed_at=observed_at,
                        name=tags.get("Name"),
                        tags=tags,
                        attributes={
                            "volume_type": vol["VolumeType"],
                            "size_gb": vol["Size"],
                            "attached_instance_id": attachments[0]["InstanceId"]
                            if attachments
                            else None,
                        },
                        monthly_cost=ebs_monthly(vol["VolumeType"], vol["Size"]),
                    )
                )
        for addr in ec2.describe_addresses().get("Addresses", []):
            tags = _tags(addr.get("Tags"))
            associated = bool(addr.get("AssociationId"))
            resources.append(
                ResourceRecord(
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_id=addr.get("AllocationId") or addr["PublicIp"],
                    resource_type="elastic_ip",
                    state="associated" if associated else "unassociated",
                    observed_at=observed_at,
                    name=tags.get("Name"),
                    tags=tags,
                    attributes={
                        "public_ip": addr.get("PublicIp"),
                        "association_id": addr.get("AssociationId"),
                    },
                    hourly_cost=PUBLIC_IPV4_HOURLY_USD,
                )
            )
    except (ClientError, BotoCoreError) as exc:
        raise classify(exc) from None
    return resources


def _cpu_utilization(
    cloudwatch: Any, instance_ids: list[str], window_days: int, now: datetime
) -> dict[str, dict[str, Any]]:
    """Hourly CPU average and p95 over the window. Instances without datapoints get no entry."""
    result: dict[str, dict[str, Any]] = {}
    period = 3600
    expected = window_days * 24
    for offset in range(0, len(instance_ids), 250):  # 500 queries per call, 2 per instance
        chunk = instance_ids[offset : offset + 250]
        queries = []
        for index, instance_id in enumerate(chunk):
            for stat, label in (("Average", "avg"), ("p95", "p95")):
                queries.append(
                    {
                        "Id": f"{label}{index}",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/EC2",
                                "MetricName": "CPUUtilization",
                                "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                            },
                            "Period": period,
                            "Stat": stat,
                        },
                    }
                )
        values: dict[str, list[float]] = {}
        for page in cloudwatch.get_paginator("get_metric_data").paginate(
            MetricDataQueries=queries, StartTime=now - timedelta(days=window_days), EndTime=now
        ):
            for series in page["MetricDataResults"]:
                values.setdefault(series["Id"], []).extend(series.get("Values", []))
        for index, instance_id in enumerate(chunk):
            avg_values = values.get(f"avg{index}", [])
            p95_values = sorted(values.get(f"p95{index}", []))
            if not avg_values:
                continue
            result[instance_id] = {
                "metric": "CPUUtilization",
                "statistic_avg_pct": round(sum(avg_values) / len(avg_values), 2),
                "statistic_p95_pct": round(p95_values[int(0.95 * (len(p95_values) - 1))], 2)
                if p95_values
                else None,
                "window_days": window_days,
                "period_seconds": period,
                "datapoints": len(avg_values),
                "expected_datapoints": expected,
                "business_hours_ratio": None,
                "source": "CloudWatch GetMetricData",
            }
    return result
