"""ASGI entrypoint: ``uvicorn nimbrix_api.asgi:app``."""

from nimbrix_api.main import create_app

app = create_app()
