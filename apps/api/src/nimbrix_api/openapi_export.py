"""Write the OpenAPI document to disk without a database or environment config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import create_engine

from nimbrix_api.core.config import Settings
from nimbrix_api.db.session import Database
from nimbrix_api.main import create_app

# Schema generation never connects; the engine is created lazily and never used.
_PLACEHOLDER_URL = "postgresql+psycopg://openapi:unused@localhost:5432/openapi"


def build_openapi() -> dict[str, object]:
    settings = Settings(database_url=SecretStr(_PLACEHOLDER_URL), _env_file=None)
    app = create_app(settings, Database(create_engine(_PLACEHOLDER_URL)))
    return app.openapi()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Destination openapi.json path")
    args = parser.parse_args()
    document = json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n"
    args.output.write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
