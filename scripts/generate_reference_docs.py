"""Regenerate docs/api.md endpoint tables and docs/database.md from code.

Usage (from repo root): cd apps/api && uv run python ../../scripts/generate_reference_docs.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from nimbrix_api import models  # noqa: F401 - registers tables
from nimbrix_api.db.base import Base

ROOT = Path(__file__).resolve().parents[1]
API_HEADER = (ROOT / "docs" / "api.header.md").read_text()


def api_doc() -> str:
    spec = json.loads((ROOT / "packages" / "contracts" / "openapi.json").read_text())
    by_tag: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            success = next((code for code in op["responses"] if code.startswith("2")), "")
            by_tag[op.get("tags", ["other"])[0]].append((method.upper(), path, op.get("summary", ""), success))
    lines = [API_HEADER.rstrip(), "", "## Endpoints (generated from the OpenAPI document)", ""]
    for tag in sorted(by_tag):
        lines += [f"### {tag}", "", "| Method | Path | Summary | Success |", "| --- | --- | --- | --- |"]
        lines += [f"| `{m}` | `{p}` | {s} | {c} |" for m, p, s, c in sorted(by_tag[tag], key=lambda r: (r[1], r[0]))]
        lines.append("")
    return "\n".join(lines)


def database_doc() -> str:
    tables = Base.metadata.sorted_tables
    lines = [
        "# Database Schema",
        "",
        "Generated from the SQLAlchemy models (`scripts/generate_reference_docs.py`); migrations in",
        "`apps/api/src/nimbrix_api/migrations/versions/` are the source of truth for DDL and are",
        "checked for drift with `alembic check` in CI.",
        "",
        "Conventions: UUID primary keys (`gen_random_uuid()`), `timestamptz` UTC timestamps,",
        "`NUMERIC(18,6)` money with an ISO currency column, `tenant_id` on every tenant-owned table,",
        "CHECK constraints for status vocabularies, and an append-only trigger on `audit_events`.",
        "",
        "```mermaid",
        "erDiagram",
    ]
    for table in tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent != table.name:
                lines.append(f"  {parent} ||--o{{ {table.name} : \"{fk.parent.name}\"")
    lines += ["```", ""]
    for table in tables:
        lines += [f"## `{table.name}`", "", "| Column | Type | Null | Notes |", "| --- | --- | --- | --- |"]
        for col in table.columns:
            notes = []
            if col.primary_key:
                notes.append("PK")
            notes += [f"FK → `{fk.column.table.name}.{fk.column.name}`" for fk in col.foreign_keys]
            lines.append(f"| `{col.name}` | {col.type} | {'yes' if col.nullable else 'no'} | {', '.join(notes)} |")
        uniques = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        checks = [c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"]
        if uniques:
            lines.append("")
            lines.append("Unique: " + "; ".join("(" + ", ".join(c.name for c in u.columns) + ")" for u in uniques))
        if checks:
            lines.append("")
            lines.append("Checks: " + "; ".join(f"`{c.sqltext}`" for c in checks))
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    (ROOT / "docs" / "api.md").write_text(api_doc() + "\n")
    (ROOT / "docs" / "database.md").write_text(database_doc() + "\n")
    print("wrote docs/api.md and docs/database.md")  # noqa: T201
