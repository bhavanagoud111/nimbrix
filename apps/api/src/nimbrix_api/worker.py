"""Worker entrypoint: ``python -m nimbrix_api.worker`` (or ``nimbrix-worker``)."""

from __future__ import annotations

from nimbrix_api.core.config import get_settings
from nimbrix_api.core.logging import configure_logging
from nimbrix_api.db.session import Database, build_engine
from nimbrix_api.modules.jobs.runner import run_forever


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level.value)
    database = Database(build_engine(settings))
    try:
        run_forever(database.session_factory, settings)
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
