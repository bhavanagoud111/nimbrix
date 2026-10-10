"""Reproducible synthetic AWS-like billing and inventory for the offline demo.

Everything is derived from (seed, end_date, days), so the same inputs always yield
byte-identical records and stable source IDs (re-imports are idempotent). The dataset
contains known, labeled scenarios:

* a sudden compute spike: a new 8-node c5.4xlarge batch fleet in the dev account
  (team ``search``) launched four days before ``end_date``;
* an idle non-production instance (``dev-build-runner-legacy``, ~1% CPU for 14 days);
* an underutilized production instance, a business-hours dev notebook, an unattached
  EBS volume, an unassociated Elastic IP, a protected dev instance, and one staging
  instance with missing CloudWatch metrics;
* untagged (unallocated) spend, weekly seasonality, slow growth, and noise.

All data is labeled ``demo`` and is not real customer data.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from nimbrix_api.modules.ingestion.records import CostRecord, ResourceRecord
from nimbrix_api.modules.pricing import EC2_HOURLY_USD, PUBLIC_IPV4_HOURLY_USD, ebs_monthly

GENERATOR_VERSION = "nimbrix-synthetic-v1"
CENT = Decimal("0.000001")

ACCOUNTS = {
    "111111111111": ("acme-prod", "prod"),
    "222222222222": ("acme-staging", "staging"),
    "333333333333": ("acme-dev", "dev"),
}


@dataclass(frozen=True)
class InstanceSpec:
    name: str
    account_id: str
    region: str
    team: str
    instance_type: str
    cpu_avg: float | None
    cpu_p95: float | None
    launch_days_before_end: int | None = None  # None: running for the whole window
    business_hours_ratio: float | None = None
    extra_tags: tuple[tuple[str, str], ...] = ()

    @property
    def environment(self) -> str:
        return ACCOUNTS[self.account_id][1]


def _fleet(name: str, count: int, **kwargs: Any) -> list[InstanceSpec]:
    return [InstanceSpec(name=f"{name}-{i + 1:02d}", **kwargs) for i in range(count)]


SPIKE_FLEET = "search-reindex-batch"
SPIKE_LAUNCH_DAYS_BEFORE_END = 4
IDLE_INSTANCE = "dev-build-runner-legacy"

INSTANCES: list[InstanceSpec] = [
    *_fleet(
        "payments-api",
        4,
        account_id="111111111111",
        region="us-east-1",
        team="payments",
        instance_type="m5.xlarge",
        cpu_avg=46.0,
        cpu_p95=78.0,
    ),
    *_fleet(
        "search-api",
        6,
        account_id="111111111111",
        region="us-east-1",
        team="search",
        instance_type="c5.2xlarge",
        cpu_avg=58.0,
        cpu_p95=88.0,
    ),
    *_fleet(
        "platform-ingress",
        3,
        account_id="111111111111",
        region="eu-west-1",
        team="platform",
        instance_type="m5.large",
        cpu_avg=35.0,
        cpu_p95=64.0,
    ),
    *_fleet(
        "data-warehouse-etl",
        2,
        account_id="111111111111",
        region="us-west-2",
        team="data",
        instance_type="r5.2xlarge",
        cpu_avg=52.0,
        cpu_p95=93.0,
    ),
    InstanceSpec(
        "payments-batch-01",
        "111111111111",
        "us-east-1",
        "payments",
        "m5.2xlarge",
        cpu_avg=9.0,
        cpu_p95=22.0,
    ),
    *_fleet(
        "search-staging",
        2,
        account_id="222222222222",
        region="us-east-1",
        team="search",
        instance_type="c5.xlarge",
        cpu_avg=21.0,
        cpu_p95=55.0,
    ),
    *_fleet(
        "payments-staging",
        2,
        account_id="222222222222",
        region="us-east-1",
        team="payments",
        instance_type="m5.large",
        cpu_avg=18.0,
        cpu_p95=47.0,
    ),
    InstanceSpec(
        "staging-search-canary",
        "222222222222",
        "us-east-1",
        "search",
        "c5.large",
        cpu_avg=None,
        cpu_p95=None,
    ),
    InstanceSpec(
        IDLE_INSTANCE,
        "333333333333",
        "us-east-1",
        "platform",
        "m5.2xlarge",
        cpu_avg=0.9,
        cpu_p95=2.1,
    ),
    InstanceSpec(
        "data-notebook-dev",
        "333333333333",
        "us-east-1",
        "data",
        "r5.large",
        cpu_avg=14.0,
        cpu_p95=48.0,
        business_hours_ratio=0.93,
    ),
    InstanceSpec(
        "search-dev",
        "333333333333",
        "us-east-1",
        "search",
        "c5.large",
        cpu_avg=12.0,
        cpu_p95=41.0,
        business_hours_ratio=0.88,
    ),
    InstanceSpec(
        "dev-vpn-gateway",
        "333333333333",
        "us-east-1",
        "platform",
        "t3.medium",
        cpu_avg=2.4,
        cpu_p95=4.8,
        extra_tags=(("nimbrix:protected", "true"),),
    ),
    *_fleet(
        SPIKE_FLEET,
        8,
        account_id="333333333333",
        region="us-east-1",
        team="search",
        instance_type="c5.4xlarge",
        cpu_avg=62.0,
        cpu_p95=91.0,
        launch_days_before_end=SPIKE_LAUNCH_DAYS_BEFORE_END,
    ),
]


@dataclass(frozen=True)
class ServiceSpec:
    service: str
    account_id: str
    region: str
    team: str | None  # None => untagged, i.e. unallocated spend
    base_daily: float
    usage_type: str
    usage_unit: str
    unit_price: float
    weekend_factor: float = 1.0
    growth_per_day: float = 0.0
    noise: float = 0.03


SERVICES: list[ServiceSpec] = [
    ServiceSpec(
        "AmazonRDS",
        "111111111111",
        "us-east-1",
        "payments",
        14.0,
        "InstanceUsage:db.r5.large",
        "Hrs",
        0.25,
    ),
    ServiceSpec(
        "AmazonRDS",
        "111111111111",
        "us-west-2",
        "data",
        22.0,
        "InstanceUsage:db.r5.xlarge",
        "Hrs",
        0.5,
    ),
    ServiceSpec(
        "AmazonS3",
        "111111111111",
        "us-west-2",
        "data",
        18.0,
        "TimedStorage-ByteHrs",
        "GB-Mo",
        0.023 / 30,
        growth_per_day=0.004,
    ),
    ServiceSpec(
        "AmazonS3",
        "111111111111",
        "us-east-1",
        None,
        6.5,
        "TimedStorage-ByteHrs",
        "GB-Mo",
        0.023 / 30,
        growth_per_day=0.002,
    ),
    ServiceSpec(
        "AWSLambda",
        "111111111111",
        "us-east-1",
        "payments",
        4.0,
        "Lambda-GB-Second",
        "GB-Seconds",
        0.0000166667,
        weekend_factor=0.6,
    ),
    ServiceSpec(
        "AmazonDynamoDB",
        "111111111111",
        "us-east-1",
        "search",
        7.5,
        "ReadRequestUnits",
        "ReadRequestUnits",
        0.00000025,
        weekend_factor=0.8,
    ),
    ServiceSpec(
        "AmazonCloudWatch",
        "111111111111",
        "us-east-1",
        None,
        3.2,
        "CW:MetricMonitorUsage",
        "Metrics",
        0.01,
    ),
    ServiceSpec(
        "AWSDataTransfer",
        "111111111111",
        "us-east-1",
        None,
        9.0,
        "DataTransfer-Out-Bytes",
        "GB",
        0.09,
        weekend_factor=0.7,
    ),
    ServiceSpec(
        "AWSDataTransfer",
        "111111111111",
        "eu-west-1",
        "platform",
        3.0,
        "DataTransfer-Out-Bytes",
        "GB",
        0.09,
        weekend_factor=0.75,
    ),
    ServiceSpec(
        "AmazonRDS",
        "222222222222",
        "us-east-1",
        "payments",
        4.0,
        "InstanceUsage:db.t3.medium",
        "Hrs",
        0.068,
    ),
    ServiceSpec(
        "AmazonS3",
        "333333333333",
        "us-east-1",
        "data",
        2.0,
        "TimedStorage-ByteHrs",
        "GB-Mo",
        0.023 / 30,
    ),
    ServiceSpec(
        "AmazonCloudWatch",
        "333333333333",
        "us-east-1",
        None,
        1.1,
        "CW:MetricMonitorUsage",
        "Metrics",
        0.01,
    ),
]


@dataclass
class DemoDataset:
    end_date: date
    days: int
    seed: int
    cost_records: list[CostRecord] = field(default_factory=list)
    resources: list[ResourceRecord] = field(default_factory=list)
    budgets: list[dict[str, Any]] = field(default_factory=list)
    labeled_anomalies: list[dict[str, Any]] = field(default_factory=list)

    @property
    def start_date(self) -> date:
        return self.end_date - timedelta(days=self.days - 1)


def _rid(prefix: str, *parts: str) -> str:
    return f"{prefix}-0{hashlib.sha1('/'.join(parts).encode()).hexdigest()[:16]}"  # noqa: S324 - IDs, not security


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=UTC)
    return start, start + timedelta(days=1)


def _tags(
    team: str | None, environment: str, extra: tuple[tuple[str, str], ...] = ()
) -> dict[str, str]:
    tags = {"environment": environment}
    if team:
        tags["team"] = team
    tags.update(dict(extra))
    return tags


def generate(end_date: date, days: int = 120, seed: int = 42) -> DemoDataset:
    if days < 30:
        raise ValueError("synthetic dataset needs at least 30 days")
    rng = random.Random(seed)  # noqa: S311 - deterministic demo data, not security
    ds = DemoDataset(end_date=end_date, days=days, seed=seed)
    provenance = {"source": "demo", "generator": GENERATOR_VERSION, "seed": seed}
    observed_at = datetime.combine(end_date + timedelta(days=1), time(1), tzinfo=UTC)
    all_days = [ds.start_date + timedelta(days=i) for i in range(days)]

    for spec in INSTANCES:
        instance_id = _rid("i", spec.account_id, spec.name)
        volume_id = _rid("vol", spec.account_id, spec.name, "root")
        hourly = EC2_HOURLY_USD[spec.instance_type]
        root_monthly = ebs_monthly("gp3", 100)
        assert root_monthly is not None  # noqa: S101
        tags = _tags(spec.team, spec.environment, (("Name", spec.name), *spec.extra_tags))
        launch_day = (
            end_date - timedelta(days=spec.launch_days_before_end)
            if spec.launch_days_before_end is not None
            else ds.start_date - timedelta(days=200)
        )
        for day in all_days:
            if day < launch_day:
                continue
            start, end = _day_bounds(day)
            ds.cost_records.append(
                CostRecord(
                    source_record_id=f"demo:{day}:{instance_id}:box",
                    provider="aws",
                    account_id=spec.account_id,
                    service="AmazonEC2",
                    usage_start=start,
                    usage_end=end,
                    amount=_money(hourly * 24),
                    currency="USD",
                    region=spec.region,
                    resource_id=instance_id,
                    usage_type=f"BoxUsage:{spec.instance_type}",
                    usage_quantity=Decimal(24),
                    usage_unit="Hrs",
                    tags=tags,
                    provenance=provenance,
                )
            )
            ds.cost_records.append(
                CostRecord(
                    source_record_id=f"demo:{day}:{volume_id}:ebs",
                    provider="aws",
                    account_id=spec.account_id,
                    service="AmazonEC2",
                    usage_start=start,
                    usage_end=end,
                    amount=_money(root_monthly * 12 / 365),
                    currency="USD",
                    region=spec.region,
                    resource_id=volume_id,
                    usage_type="EBS:VolumeUsage.gp3",
                    usage_quantity=_money(Decimal(100) * 12 / 365),
                    usage_unit="GB-Mo",
                    tags=tags,
                    provenance=provenance,
                )
            )
        utilization = None
        if spec.cpu_avg is not None and spec.cpu_p95 is not None:
            utilization = {
                "metric": "CPUUtilization",
                "statistic_avg_pct": spec.cpu_avg,
                "statistic_p95_pct": spec.cpu_p95,
                "network_out_mb_per_day": round(spec.cpu_avg * 40 + rng.uniform(0, 5), 1),
                "window_days": 14,
                "period_seconds": 3600,
                "datapoints": 14 * 24,
                "expected_datapoints": 14 * 24,
                "business_hours_ratio": spec.business_hours_ratio,
                "source": "synthetic CloudWatch",
            }
        ds.resources.append(
            ResourceRecord(
                provider="aws",
                account_id=spec.account_id,
                region=spec.region,
                resource_id=instance_id,
                resource_type="ec2_instance",
                state="running",
                observed_at=observed_at,
                name=spec.name,
                tags=tags,
                attributes={
                    "instance_type": spec.instance_type,
                    "launch_time": datetime.combine(launch_day, time(9), tzinfo=UTC).isoformat(),
                    "root_volume_id": volume_id,
                    "platform": "linux",
                },
                hourly_cost=hourly,
                monthly_cost=None,
                utilization=utilization,
            )
        )
        ds.resources.append(
            ResourceRecord(
                provider="aws",
                account_id=spec.account_id,
                region=spec.region,
                resource_id=volume_id,
                resource_type="ebs_volume",
                state="in-use",
                observed_at=observed_at,
                name=f"{spec.name}-root",
                tags=tags,
                attributes={
                    "volume_type": "gp3",
                    "size_gb": 100,
                    "attached_instance_id": instance_id,
                },
                monthly_cost=root_monthly,
            )
        )

    # Unattached 500 GB volume left behind in staging 45 days ago.
    orphan_id = _rid("vol", "222222222222", "search-staging-old-data")
    orphan_monthly = ebs_monthly("gp3", 500)
    assert orphan_monthly is not None  # noqa: S101
    orphan_tags = _tags("search", "staging", (("Name", "search-staging-old-data"),))
    for day in all_days:
        start, end = _day_bounds(day)
        ds.cost_records.append(
            CostRecord(
                source_record_id=f"demo:{day}:{orphan_id}:ebs",
                provider="aws",
                account_id="222222222222",
                service="AmazonEC2",
                usage_start=start,
                usage_end=end,
                amount=_money(orphan_monthly * 12 / 365),
                currency="USD",
                region="us-east-1",
                resource_id=orphan_id,
                usage_type="EBS:VolumeUsage.gp3",
                usage_quantity=_money(Decimal(500) * 12 / 365),
                usage_unit="GB-Mo",
                tags=orphan_tags,
                provenance=provenance,
            )
        )
    ds.resources.append(
        ResourceRecord(
            provider="aws",
            account_id="222222222222",
            region="us-east-1",
            resource_id=orphan_id,
            resource_type="ebs_volume",
            state="available",
            observed_at=observed_at,
            name="search-staging-old-data",
            tags=orphan_tags,
            attributes={
                "volume_type": "gp3",
                "size_gb": 500,
                "attached_instance_id": None,
                "detached_at": datetime.combine(
                    end_date - timedelta(days=45), time(14), tzinfo=UTC
                ).isoformat(),
            },
            monthly_cost=orphan_monthly,
        )
    )

    # Elastic IP allocated in dev but not associated with anything.
    eip_id = _rid("eipalloc", "333333333333", "unused-dev-ip")
    eip_tags = _tags("platform", "dev", (("Name", "old-bastion-ip"),))
    for day in all_days:
        start, end = _day_bounds(day)
        ds.cost_records.append(
            CostRecord(
                source_record_id=f"demo:{day}:{eip_id}:ipv4",
                provider="aws",
                account_id="333333333333",
                service="AmazonVPC",
                usage_start=start,
                usage_end=end,
                amount=_money(PUBLIC_IPV4_HOURLY_USD * 24),
                currency="USD",
                region="us-east-1",
                resource_id=eip_id,
                usage_type="PublicIPv4:IdleAddress",
                usage_quantity=Decimal(24),
                usage_unit="Hrs",
                tags=eip_tags,
                provenance=provenance,
            )
        )
    ds.resources.append(
        ResourceRecord(
            provider="aws",
            account_id="333333333333",
            region="us-east-1",
            resource_id=eip_id,
            resource_type="elastic_ip",
            state="unassociated",
            observed_at=observed_at,
            name="old-bastion-ip",
            tags=eip_tags,
            attributes={"public_ip": "203.0.113.10", "association_id": None},
            hourly_cost=PUBLIC_IPV4_HOURLY_USD,
        )
    )

    # Service-level spend without resource IDs (as Cost Explorer reports it).
    for svc in SERVICES:
        environment = ACCOUNTS[svc.account_id][1]
        tags = _tags(svc.team, environment) if svc.team else {}
        key = f"{svc.account_id}:{svc.region}:{svc.service}:{svc.team or 'untagged'}"
        for index, day in enumerate(all_days):
            factor = (1 + svc.growth_per_day) ** index
            if day.weekday() >= 5:
                factor *= svc.weekend_factor
            factor *= max(0.5, rng.gauss(1.0, svc.noise))
            amount = _money(svc.base_daily * factor)
            start, end = _day_bounds(day)
            ds.cost_records.append(
                CostRecord(
                    source_record_id=f"demo:{day}:{key}",
                    provider="aws",
                    account_id=svc.account_id,
                    service=svc.service,
                    usage_start=start,
                    usage_end=end,
                    amount=amount,
                    currency="USD",
                    region=svc.region,
                    usage_type=svc.usage_type,
                    usage_quantity=_money(amount / Decimal(str(svc.unit_price))),
                    usage_unit=svc.usage_unit,
                    tags=tags,
                    provenance=provenance,
                )
            )

    _add_budgets(ds)
    spike_start = end_date - timedelta(days=SPIKE_LAUNCH_DAYS_BEFORE_END)
    ds.labeled_anomalies = [
        {
            "scope_type": "service",
            "scope_value": "AmazonEC2",
            "start": spike_start,
            "end": end_date,
        },
        {
            "scope_type": "account",
            "scope_value": "333333333333",
            "start": spike_start,
            "end": end_date,
        },
        {"scope_type": "team", "scope_value": "search", "start": spike_start, "end": end_date},
    ]
    return ds


def _add_budgets(ds: DemoDataset) -> None:
    """Budgets sized from the pre-spike baseline so the spike visibly pressures them."""
    spike_start = ds.end_date - timedelta(days=SPIKE_LAUNCH_DAYS_BEFORE_END)
    baseline = [r for r in ds.cost_records if r.usage_date < spike_start]
    baseline_days = max(1, (spike_start - ds.start_date).days)

    def monthly(predicate: Any) -> Decimal:
        total = sum((r.amount for r in baseline if predicate(r)), Decimal(0))
        return total / baseline_days * Decimal("30.4")

    def rounded(value: Decimal, step: int = 100) -> Decimal:
        return (value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step

    ds.budgets = [
        {
            "name": "Total cloud spend",
            "scope_type": "total",
            "scope_value": None,
            "amount": rounded(monthly(lambda r: True) * Decimal("1.15")),
            "owner": "finops",
        },
        {
            "name": "Search team",
            "scope_type": "team",
            "scope_value": "search",
            "amount": rounded(
                monthly(lambda r: r.tags.get("team") == "search") * Decimal("1.10"), 50
            ),
            "owner": "search",
        },
        {
            "name": "Development environment",
            "scope_type": "environment",
            "scope_value": "dev",
            "amount": rounded(
                monthly(lambda r: r.tags.get("environment") == "dev") * Decimal("1.25"), 50
            ),
            "owner": "platform",
        },
    ]
