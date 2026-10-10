"""Reference list prices used for savings estimates.

These are public AWS on-demand list prices for Linux in us-east-1 (USD), recorded as
reference values. They exclude Savings Plans, Reserved Instances, enterprise discounts,
taxes, and regional differences. Every estimate that uses them says so in its
assumptions, and a missing price yields "insufficient evidence" rather than a guess.
"""

from __future__ import annotations

from decimal import Decimal

PRICE_SOURCE = "AWS public on-demand list price, Linux, us-east-1 (reference table v1)"
HOURS_PER_MONTH = Decimal(730)  # AWS convention: 8,760 hours / 12 months

EC2_HOURLY_USD: dict[str, Decimal] = {
    "t3.medium": Decimal("0.0416"),
    "t3.large": Decimal("0.0832"),
    "m5.large": Decimal("0.096"),
    "m5.xlarge": Decimal("0.192"),
    "m5.2xlarge": Decimal("0.384"),
    "m5.4xlarge": Decimal("0.768"),
    "c5.large": Decimal("0.085"),
    "c5.xlarge": Decimal("0.17"),
    "c5.2xlarge": Decimal("0.34"),
    "c5.4xlarge": Decimal("0.68"),
    "r5.large": Decimal("0.126"),
    "r5.xlarge": Decimal("0.252"),
    "r5.2xlarge": Decimal("0.504"),
}

# One size down within the same family (used for rightsizing estimates).
EC2_DOWNSIZE: dict[str, str] = {
    "t3.large": "t3.medium",
    "m5.xlarge": "m5.large",
    "m5.2xlarge": "m5.xlarge",
    "m5.4xlarge": "m5.2xlarge",
    "c5.xlarge": "c5.large",
    "c5.2xlarge": "c5.xlarge",
    "c5.4xlarge": "c5.2xlarge",
    "r5.xlarge": "r5.large",
    "r5.2xlarge": "r5.xlarge",
}

EBS_GB_MONTH_USD: dict[str, Decimal] = {
    "gp3": Decimal("0.08"),
    "gp2": Decimal("0.10"),
    "io1": Decimal("0.125"),
    "st1": Decimal("0.045"),
    "sc1": Decimal("0.015"),
}

PUBLIC_IPV4_HOURLY_USD = Decimal("0.005")


def ec2_hourly(instance_type: str | None) -> Decimal | None:
    return EC2_HOURLY_USD.get(instance_type or "")


def ebs_monthly(volume_type: str | None, size_gb: int | float | None) -> Decimal | None:
    price = EBS_GB_MONTH_USD.get(volume_type or "")
    if price is None or size_gb is None:
        return None
    return (price * Decimal(str(size_gb))).quantize(Decimal("0.000001"))
