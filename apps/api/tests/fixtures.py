"""Paths to the shared fixture files in /data/fixtures."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
