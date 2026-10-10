"""ORM models. Import every model here so Alembic sees the full metadata."""

from nimbrix_api.models.tenant import Tenant

__all__ = ["Tenant"]
