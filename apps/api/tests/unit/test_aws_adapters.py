"""AWS adapters against botocore stubs: pagination, freshness flags, errors, credentials."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from botocore.stub import ANY

from nimbrix_api.core.config import Settings
from nimbrix_api.models import CloudConnection
from nimbrix_api.modules.ingestion import aws
from nimbrix_api.modules.jobs.queue import PermanentJobError, RetryableJobError
from tests.aws_stubs import StubbedSession


def _connection(**overrides: Any) -> CloudConnection:
    values = {
        "account_id": "123456789012",
        "role_arn": "arn:aws:iam::123456789012:role/NimbrixReadOnly",
        "external_id": "ext-123",
        "regions": ["us-east-1"],
    }
    values.update(overrides)
    return cast(CloudConnection, SimpleNamespace(**values))


def _ce_page(day: str, amount: str, *, estimated: bool, token: str | None) -> dict[str, Any]:
    page: dict[str, Any] = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": day, "End": day},
                "Total": {},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Compute Cloud - Compute", "123456789012"],
                        "Metrics": {
                            "UnblendedCost": {"Amount": amount, "Unit": "USD"},
                            "UsageQuantity": {"Amount": "24", "Unit": "Hrs"},
                        },
                    }
                ],
                "Estimated": estimated,
            }
        ],
        "DimensionValueAttributes": [],
    }
    if token:
        page["NextPageToken"] = token
    return page


def test_cost_explorer_paginates_and_flags_estimates() -> None:
    session = StubbedSession("ce")
    ce = session.stub("ce")
    ce.add_response(
        "get_cost_and_usage", _ce_page("2026-09-01", "12.5", estimated=False, token="t1")
    )
    ce.add_response(
        "get_cost_and_usage",
        _ce_page("2026-09-02", "13.25", estimated=True, token=None),
        expected_params={
            "TimePeriod": {"Start": "2026-09-01", "End": "2026-09-03"},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost", "UsageQuantity"],
            "GroupBy": ANY,
            "NextPageToken": "t1",
        },
    )
    records = list(aws.fetch_cost_explorer(session, date(2026, 9, 1), date(2026, 9, 3)))
    session.assert_no_pending()
    assert [r.amount for r in records] == [Decimal("12.5"), Decimal("13.25")]
    assert records[0].resource_id is None  # Cost Explorer has no resource IDs
    assert [r.provenance["estimated"] for r in records] == [False, True]
    assert (
        records[1].source_record_id
        == "ce:2026-09-02:123456789012:Amazon Elastic Compute Cloud - Compute"
    )


def test_cost_explorer_throttling_is_retryable() -> None:
    session = StubbedSession("ce")
    session.stub("ce").add_client_error(
        "get_cost_and_usage", service_error_code="ThrottlingException"
    )
    with pytest.raises(RetryableJobError, match="throttled"):
        list(aws.fetch_cost_explorer(session, date(2026, 9, 1), date(2026, 9, 2)))


def test_access_denied_is_permanent_and_redacted() -> None:
    session = StubbedSession("ce")
    session.stub("ce").add_client_error(
        "get_cost_and_usage",
        service_error_code="AccessDeniedException",
        service_message="User arn:aws:sts::1:x is not authorized",
    )
    with pytest.raises(PermanentJobError) as info:
        list(aws.fetch_cost_explorer(session, date(2026, 9, 1), date(2026, 9, 2)))
    assert "AccessDeniedException" in str(info.value)
    assert "arn:aws" not in str(info.value)


def test_ec2_inventory_with_partial_metrics() -> None:
    session = StubbedSession("ec2", "cloudwatch")
    ec2, cw = session.stub("ec2"), session.stub("cloudwatch")
    launch = datetime(2026, 1, 1, tzinfo=UTC)
    ec2.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1",
                            "InstanceType": "m5.large",
                            "State": {"Name": "running", "Code": 16},
                            "LaunchTime": launch,
                            "Tags": [{"Key": "environment", "Value": "dev"}],
                        },
                        {
                            "InstanceId": "i-2",
                            "InstanceType": "x9.mystery",
                            "State": {"Name": "stopped", "Code": 80},
                            "LaunchTime": launch,
                        },
                    ]
                }
            ]
        },
    )
    cw.add_response(
        "get_metric_data",
        {
            "MetricDataResults": [
                {
                    "Id": "avg0",
                    "Label": "a",
                    "Timestamps": [launch, launch],
                    "Values": [1.0, 3.0],
                    "StatusCode": "Complete",
                },
                {
                    "Id": "p950",
                    "Label": "p",
                    "Timestamps": [launch, launch],
                    "Values": [2.0, 4.0],
                    "StatusCode": "Complete",
                },
                {
                    "Id": "avg1",
                    "Label": "a",
                    "Timestamps": [],
                    "Values": [],
                    "StatusCode": "Complete",
                },
                {
                    "Id": "p951",
                    "Label": "p",
                    "Timestamps": [],
                    "Values": [],
                    "StatusCode": "Complete",
                },
            ]
        },
    )
    ec2.add_response(
        "describe_volumes",
        {
            "Volumes": [
                {
                    "VolumeId": "vol-1",
                    "VolumeType": "gp3",
                    "Size": 50,
                    "State": "available",
                    "Attachments": [],
                }
            ]
        },
    )
    ec2.add_response(
        "describe_addresses",
        {"Addresses": [{"AllocationId": "eipalloc-1", "PublicIp": "198.51.100.1"}]},
    )
    resources = {
        r.resource_id: r for r in aws.fetch_ec2_inventory(session, "us-east-1", "123456789012")
    }
    session.assert_no_pending()
    assert resources["i-1"].utilization is not None
    assert resources["i-1"].utilization["statistic_avg_pct"] == 2.0
    assert resources["i-1"].hourly_cost == Decimal("0.096")
    assert resources["i-2"].utilization is None  # missing metrics stays missing
    assert resources["i-2"].hourly_cost is None  # unknown price stays unknown
    assert resources["vol-1"].monthly_cost == Decimal("4.000000")
    assert resources["eipalloc-1"].state == "unassociated"


def test_cur_athena_rejects_unsafe_identifiers() -> None:
    with pytest.raises(PermanentJobError, match="simple identifiers"):
        list(
            aws.fetch_cur_athena(
                StubbedSession("athena"),
                database="cur; DROP",
                table="t",
                workgroup="w",
                start=date(2026, 9, 1),
                end=date(2026, 9, 2),
            )
        )


def test_cur_athena_reads_paginated_results() -> None:
    session = StubbedSession("athena")
    athena = session.stub("athena")
    athena.add_response("start_query_execution", {"QueryExecutionId": "q-1"})
    athena.add_response(
        "get_query_execution",
        {"QueryExecution": {"QueryExecutionId": "q-1", "Status": {"State": "SUCCEEDED"}}},
    )
    header = {"Data": [{"VarCharValue": c} for c in ["id"] * 12]}
    row = {
        "Data": [
            {"VarCharValue": v}
            for v in [
                "line-1",
                "2026-09-01 00:00:00",
                "2026-09-01 01:00:00",
                "123456789012",
                "AmazonEC2",
                "us-east-1",
                "i-1",
                "BoxUsage:m5.large",
                "1",
                "Hrs",
                "0.096",
                "USD",
            ]
        ]
    }
    athena.add_response("get_query_results", {"ResultSet": {"Rows": [header, row]}})
    records = list(
        aws.fetch_cur_athena(
            session,
            database="cur",
            table="cur_table",
            workgroup="primary",
            start=date(2026, 9, 1),
            end=date(2026, 9, 2),
            poll_seconds=0,
        )
    )
    assert len(records) == 1
    assert records[0].resource_id == "i-1"
    assert records[0].amount == Decimal("0.096")


def test_assume_role_requires_aws_enabled(make_settings: Any) -> None:
    factory = aws.AwsSessionFactory(make_settings())
    with pytest.raises(aws.AwsDisabledError):
        factory.for_connection(_connection())


def test_assume_role_passes_external_id_and_keeps_creds_in_memory(make_settings: Any) -> None:
    base = StubbedSession("sts")
    base.stub("sts").add_response(
        "assume_role",
        {
            "Credentials": {
                "AccessKeyId": "ASIAEXAMPLEEXAMPLE00",
                "SecretAccessKey": "secret" * 6,
                "SessionToken": "token",
                "Expiration": datetime(2030, 1, 1, tzinfo=UTC),
            }
        },
        expected_params={
            "RoleArn": "arn:aws:iam::123456789012:role/NimbrixReadOnly",
            "RoleSessionName": "nimbrix-readonly",
            "DurationSeconds": 900,
            "ExternalId": "ext-123",
        },
    )
    settings: Settings = make_settings(aws_enabled=True)
    session = aws.AwsSessionFactory(settings, base_session=base).for_connection(_connection())
    assert session.get_credentials().access_key == "ASIAEXAMPLEEXAMPLE00"
    base.assert_no_pending()


def test_connection_without_role_is_refused(make_settings: Any) -> None:
    factory = aws.AwsSessionFactory(
        make_settings(aws_enabled=True), base_session=StubbedSession("sts")
    )
    with pytest.raises(PermanentJobError, match="role_arn"):
        factory.for_connection(_connection(role_arn=None))


def test_validate_connection_detects_wrong_account() -> None:
    session = StubbedSession("sts", "ec2")
    session.stub("sts").add_response(
        "get_caller_identity",
        {
            "Account": "999999999999",
            "Arn": "arn:aws:sts::999999999999:assumed-role/x/y",
            "UserId": "u",
        },
    )
    session.stub("ec2").add_response("describe_instances", {"Reservations": []})
    checks = aws.validate_connection(session, _connection(), check_cost_explorer=False)
    assert checks[0].name == "assume_role"
    assert not checks[0].passed
    assert checks[1].passed
