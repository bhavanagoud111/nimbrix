"""CSV billing upload parser.

Required columns: ``line_item_id, usage_start, usage_end, account_id, service, amount,
currency``. Optional: ``region, resource_id, usage_type, usage_quantity, usage_unit,
tags`` (JSON object) and any number of ``tag:<key>`` columns.

Rules (each violation rejects only that row, with a row-level error):

* timestamps are ISO 8601; a bare date or a naive timestamp is interpreted as UTC;
* a record may span at most 24 hours (hourly or daily granularity);
* ``amount`` is an exact decimal (negative values are credits); ``currency`` is ISO 4217;
* ``account_id`` is a 12-digit AWS account ID;
* a ``line_item_id`` repeated within one file is rejected after its first occurrence.

Cell contents are stored as data and never evaluated (no formula semantics).
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from nimbrix_api.modules.ingestion.records import CostRecord, RowError

REQUIRED_COLUMNS = (
    "line_item_id",
    "usage_start",
    "usage_end",
    "account_id",
    "service",
    "amount",
    "currency",
)
MAX_ROWS = 500_000
MAX_FIELD = 2_048
MAX_STORED_ERRORS = 200
_ACCOUNT = re.compile(r"^\d{12}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$|^global$")
_MAX_SPAN = timedelta(hours=24)
_AMOUNT_LIMIT = Decimal("1e12")


class CsvFormatError(ValueError):
    """The file as a whole is unusable (encoding, header, size)."""


@dataclass
class CsvParseResult:
    records: list[CostRecord] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    rows_received: int = 0
    rejected: int = 0
    currencies: set[str] = field(default_factory=set)
    period_start: date | None = None
    period_end: date | None = None


def _parse_timestamp(value: str) -> datetime:
    value = value.strip()
    if not value:
        raise ValueError("empty timestamp")
    if len(value) == 10:
        parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_decimal(value: str, *, allow_empty: bool = False) -> Decimal | None:
    value = value.strip()
    if not value:
        if allow_empty:
            return None
        raise ValueError("empty number")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("not a decimal number") from None
    if not number.is_finite() or abs(number) >= _AMOUNT_LIMIT:
        raise ValueError("number out of range")
    return number


def _tags_from_row(row: dict[str, str]) -> dict[str, str]:
    tags: dict[str, str] = {}
    raw = (row.get("tags") or "").strip()
    if raw:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tags must be a JSON object")
        for key, value in parsed.items():
            tags[str(key)[:128]] = str(value)[:256]
    for column, value in row.items():
        if column and column.lower().startswith("tag:") and value and value.strip():
            tags[column[4:][:128]] = value.strip()[:256]
    return tags


def decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvFormatError("file is not valid UTF-8 text") from None


def parse_csv(
    content: bytes, *, provider: str = "aws", source_label: str = "csv"
) -> CsvParseResult:
    text = decode(content)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip() for h in (reader.fieldnames or [])]
    if not header:
        raise CsvFormatError("file has no header row")
    reader.fieldnames = header
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise CsvFormatError(f"missing required columns: {', '.join(missing)}")

    result = CsvParseResult()
    seen_ids: set[str] = set()
    for line_number, row in enumerate(reader, start=2):
        result.rows_received += 1
        if result.rows_received > MAX_ROWS:
            raise CsvFormatError(f"file exceeds the {MAX_ROWS:,} row limit")
        try:
            record = _parse_row(row, provider, source_label, line_number, seen_ids)
        except _RowRejectedError as rejected:
            result.rejected += 1
            if len(result.errors) < MAX_STORED_ERRORS:
                result.errors.append(rejected.error)
            continue
        result.records.append(record)
        result.currencies.add(record.currency)
        day = record.usage_date
        result.period_start = min(result.period_start or day, day)
        result.period_end = max(result.period_end or day, day)
    return result


class _RowRejectedError(Exception):
    def __init__(self, error: RowError) -> None:
        super().__init__(error.message)
        self.error = error


def _reject(row: int, code: str, message: str, column: str | None = None) -> _RowRejectedError:
    return _RowRejectedError(RowError(row=row, code=code, message=message, field=column))


def _parse_row(  # noqa: PLR0912 - one branch per validation rule, kept linear on purpose
    row: dict[str, Any], provider: str, source_label: str, line: int, seen_ids: set[str]
) -> CostRecord:
    if None in row:  # more cells than headers
        raise _reject(line, "malformed_row", "row has more cells than the header")
    for column, value in row.items():
        if value is not None and len(value) > MAX_FIELD:
            raise _reject(line, "field_too_long", f"value exceeds {MAX_FIELD} characters", column)
    values = {k: (v or "").strip() for k, v in row.items()}

    line_item_id = values["line_item_id"]
    if not line_item_id:
        raise _reject(line, "missing_value", "line_item_id is required", "line_item_id")
    if line_item_id in seen_ids:
        raise _reject(
            line, "duplicate_in_file", "line_item_id repeated in this file", "line_item_id"
        )

    try:
        start = _parse_timestamp(values["usage_start"])
    except ValueError:
        raise _reject(
            line, "invalid_timestamp", "usage_start is not ISO 8601", "usage_start"
        ) from None
    try:
        end = _parse_timestamp(values["usage_end"])
    except ValueError:
        raise _reject(line, "invalid_timestamp", "usage_end is not ISO 8601", "usage_end") from None
    if end < start:
        raise _reject(line, "invalid_window", "usage_end is before usage_start", "usage_end")
    if end - start > _MAX_SPAN:
        raise _reject(
            line, "unsupported_granularity", "records may span at most 24 hours", "usage_end"
        )

    account = values["account_id"]
    if not _ACCOUNT.match(account):
        raise _reject(line, "invalid_account", "account_id must be 12 digits", "account_id")
    service = values["service"]
    if not service:
        raise _reject(line, "missing_value", "service is required", "service")
    region = values.get("region") or None
    if region and not _REGION.match(region):
        raise _reject(line, "invalid_region", "region is not a valid AWS region", "region")

    currency = values["currency"].upper()
    if not _CURRENCY.match(currency):
        raise _reject(line, "invalid_currency", "currency must be a 3-letter ISO code", "currency")
    try:
        amount = _parse_decimal(values["amount"])
        quantity = _parse_decimal(values.get("usage_quantity", ""), allow_empty=True)
    except ValueError as exc:
        raise _reject(line, "invalid_number", str(exc), "amount") from None
    assert amount is not None  # noqa: S101

    try:
        tags = _tags_from_row(values)
    except (ValueError, json.JSONDecodeError):
        raise _reject(line, "invalid_tags", "tags must be a JSON object", "tags") from None

    seen_ids.add(line_item_id)
    return CostRecord(
        source_record_id=f"{source_label}:{line_item_id}",
        provider=provider,
        account_id=account,
        service=service[:128],
        usage_start=start,
        usage_end=end,
        amount=amount,
        currency=currency,
        region=region,
        resource_id=values.get("resource_id") or None,
        usage_type=values.get("usage_type") or None,
        usage_quantity=quantity,
        usage_unit=values.get("usage_unit") or None,
        tags=tags,
        provenance={"source": source_label, "row": line},
    )
