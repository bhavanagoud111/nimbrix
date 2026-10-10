from __future__ import annotations

from decimal import Decimal

import pytest

from nimbrix_api.modules.ingestion.csv_source import CsvFormatError, parse_csv
from tests.fixtures import fixture_bytes


def test_valid_file() -> None:
    result = parse_csv(fixture_bytes("billing_valid.csv"))
    assert result.rows_received == 8
    assert result.rejected == 0
    assert len(result.records) == 8
    first = result.records[0]
    assert first.source_record_id == "csv:li-0001"
    assert first.amount == Decimal("2.304")
    assert first.tags == {"team": "payments", "environment": "prod"}
    credit = next(r for r in result.records if r.source_record_id == "csv:li-0008")
    assert credit.amount == Decimal("-5.00")
    bare_date = next(r for r in result.records if r.source_record_id == "csv:li-0006")
    assert bare_date.usage_start.tzinfo is not None
    assert str(result.period_start) == "2026-09-01"
    assert str(result.period_end) == "2026-09-02"


def test_malformed_rows_are_rejected_individually() -> None:
    result = parse_csv(fixture_bytes("billing_malformed.csv"))
    assert result.rows_received == 12
    assert len(result.records) == 2
    assert result.rejected == 10
    codes = {e.code for e in result.errors}
    assert codes == {
        "invalid_timestamp",
        "invalid_account",
        "invalid_number",
        "invalid_currency",
        "invalid_window",
        "unsupported_granularity",
        "invalid_tags",
        "invalid_region",
        "duplicate_in_file",
        "malformed_row",
    }
    assert all(e.row is not None and e.row >= 2 for e in result.errors)
    formula = next(r for r in result.records if r.source_record_id == "csv:formula")
    assert formula.service.startswith("=HYPERLINK")  # stored verbatim as data


def test_mixed_currency_is_preserved() -> None:
    result = parse_csv(fixture_bytes("billing_mixed_currency.csv"))
    assert result.currencies == {"EUR", "USD"}


def test_empty_file_has_no_records() -> None:
    result = parse_csv(fixture_bytes("billing_empty.csv"))
    assert result.rows_received == 0
    assert result.records == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "no header"),
        (b"a,b,c\n1,2,3\n", "missing required columns"),
        (b"\xff\xfe\x00bad", "not valid UTF-8"),
    ],
)
def test_whole_file_errors(content: bytes, message: str) -> None:
    with pytest.raises(CsvFormatError, match=message):
        parse_csv(content)


def test_field_length_limit() -> None:
    header = b"line_item_id,usage_start,usage_end,account_id,service,amount,currency\n"
    row = b"x," + b"2026-09-01," * 2 + b"123456789012," + b"S" * 5000 + b",1,USD\n"
    result = parse_csv(header + row)
    assert result.errors[0].code == "field_too_long"
