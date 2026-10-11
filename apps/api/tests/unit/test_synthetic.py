from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from nimbrix_api.modules.ingestion import synthetic

END = date(2026, 9, 30)


@pytest.fixture(scope="module")
def dataset() -> synthetic.DemoDataset:
    return synthetic.generate(END, days=120, seed=42)


def test_generation_is_reproducible(dataset: synthetic.DemoDataset) -> None:
    again = synthetic.generate(END, days=120, seed=42)
    assert again.cost_records == dataset.cost_records
    assert again.resources == dataset.resources
    other_seed = synthetic.generate(END, days=120, seed=7)
    assert other_seed.cost_records != dataset.cost_records


def test_source_ids_are_unique_and_stable(dataset: synthetic.DemoDataset) -> None:
    ids = [r.source_record_id for r in dataset.cost_records]
    assert len(ids) == len(set(ids))
    shifted = synthetic.generate(END + timedelta(days=1), days=120, seed=42)
    overlap = set(ids) & {r.source_record_id for r in shifted.cost_records}
    assert len(overlap) > len(ids) * 0.95  # a later run overlaps all but the dropped first day


def test_coverage_spans_accounts_regions_services_and_days(dataset: synthetic.DemoDataset) -> None:
    records = dataset.cost_records
    assert {r.usage_date for r in records} == {END - timedelta(days=i) for i in range(120)}
    assert {r.account_id for r in records} == set(synthetic.ACCOUNTS)
    assert len({r.region for r in records}) >= 3
    assert len({r.service for r in records}) >= 8
    assert any(not r.tags.get("team") for r in records), "needs unallocated spend"
    assert all(r.currency == "USD" and isinstance(r.amount, Decimal) for r in records)
    assert all(r.usage_end - r.usage_start == timedelta(days=1) for r in records)


def test_compute_spike_is_present(dataset: synthetic.DemoDataset) -> None:
    daily: dict[date, Decimal] = defaultdict(Decimal)
    for r in dataset.cost_records:
        if r.service == "AmazonEC2":
            daily[r.usage_date] += r.amount
    spike_start = END - timedelta(days=synthetic.SPIKE_LAUNCH_DAYS_BEFORE_END)
    before = daily[spike_start - timedelta(days=1)]
    after = daily[spike_start]
    assert after - before > Decimal(120)  # 8 x c5.4xlarge x 24h = $130.56/day
    labels = {(a["scope_type"], a["scope_value"]) for a in dataset.labeled_anomalies}
    assert ("service", "AmazonEC2") in labels


def test_scenario_resources(dataset: synthetic.DemoDataset) -> None:
    by_name = {r.name: r for r in dataset.resources}
    idle = by_name[synthetic.IDLE_INSTANCE]
    assert idle.tags["environment"] == "dev"
    assert idle.utilization
    assert idle.utilization["statistic_avg_pct"] < 5
    assert by_name["staging-search-canary"].utilization is None  # missing metrics case
    assert by_name["search-staging-old-data"].state == "available"
    assert by_name["old-bastion-ip"].state == "unassociated"
    assert by_name["dev-vpn-gateway"].tags["nimbrix:protected"] == "true"


def test_budgets_are_sized_from_baseline(dataset: synthetic.DemoDataset) -> None:
    names = {b["name"]: b for b in dataset.budgets}
    assert set(names) == {"Total cloud spend", "Search team", "Development environment"}
    assert all(b["amount"] > 0 for b in dataset.budgets)


def test_rejects_too_short_windows() -> None:
    with pytest.raises(ValueError, match="at least 30 days"):
        synthetic.generate(END, days=10)


def test_inventory_is_never_observed_in_the_future() -> None:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    dataset = synthetic.generate(yesterday, days=30)
    assert all(r.observed_at <= datetime.now(UTC) for r in dataset.resources)
