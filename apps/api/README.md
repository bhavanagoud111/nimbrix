# nimbrix-api

FastAPI modular monolith for Nimbrix. See the repository [README](../../README.md),
[architecture](../../docs/architecture.md), and [API reference](../../docs/api.md).

```bash
uv sync                                   # install (Python 3.12 via uv)
uv run pytest tests/unit                  # unit tests
uv run alembic upgrade head               # needs NIMBRIX_DATABASE_URL
uv run uvicorn nimbrix_api.asgi:app --reload --port 8200
uv run nimbrix-export-openapi ../../packages/contracts/openapi.json
```
