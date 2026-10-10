"""Idempotent seed of roles, demo tenants, and demo users.

Run: ``nimbrix-seed`` (requires NIMBRIX_DEMO_PASSWORD). With ``--if-enabled`` it does
nothing unless NIMBRIX_DEMO_SEED=true, so production images never seed by accident.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.core.config import Settings, load_settings
from nimbrix_api.core.logging import configure_logging
from nimbrix_api.core.permissions import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS
from nimbrix_api.core.security import hash_password, verify_password
from nimbrix_api.db.session import Database, build_engine
from nimbrix_api.models import Membership, Role, Tenant, User
from nimbrix_api.modules.demo import seed_demo_data

logger = logging.getLogger("nimbrix.seed")


@dataclass(frozen=True)
class DemoUser:
    email: str
    display_name: str
    tenant_slug: str
    role: str


DEMO_TENANTS = {"acme-demo": "Acme Corp (demo)", "globex-demo": "Globex Inc (demo)"}
DEMO_USERS = (
    DemoUser("viewer@acme.demo", "Vera Viewer", "acme-demo", "viewer"),
    DemoUser("analyst@acme.demo", "Ana Analyst", "acme-demo", "analyst"),
    DemoUser("approver@acme.demo", "Avery Approver", "acme-demo", "approver"),
    DemoUser("auditor@acme.demo", "Audrey Auditor", "acme-demo", "auditor"),
    DemoUser("admin@acme.demo", "Adam Admin", "acme-demo", "admin"),
    DemoUser("admin@globex.demo", "Gia Globex", "globex-demo", "admin"),
)


def seed_roles(session: Session) -> dict[str, Role]:
    roles = {r.name: r for r in session.scalars(select(Role))}
    for name, permissions in ROLE_PERMISSIONS.items():
        perms = sorted(p.value for p in permissions)
        role = roles.get(name)
        if role is None:
            role = Role(name=name, description=ROLE_DESCRIPTIONS[name], permissions=perms)
            session.add(role)
            roles[name] = role
        else:
            role.description = ROLE_DESCRIPTIONS[name]
            role.permissions = perms
    session.flush()
    return roles


def seed_identities(session: Session, password: str) -> dict[str, Tenant]:
    roles = seed_roles(session)
    tenants = {t.slug: t for t in session.scalars(select(Tenant))}
    for slug, name in DEMO_TENANTS.items():
        if slug not in tenants:
            tenants[slug] = Tenant(slug=slug, name=name)
            session.add(tenants[slug])
    session.flush()

    for demo in DEMO_USERS:
        user = session.scalar(select(User).where(User.email == demo.email))
        if user is None:
            user = User(email=demo.email, display_name=demo.display_name, is_active=True)
            session.add(user)
        if not user.password_hash or not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)
        session.flush()
        tenant = tenants[demo.tenant_slug]
        membership = session.scalar(
            select(Membership).where(
                Membership.user_id == user.id, Membership.tenant_id == tenant.id
            )
        )
        if membership is None:
            session.add(
                Membership(user_id=user.id, tenant_id=tenant.id, role_id=roles[demo.role].id)
            )
        else:
            membership.role_id = roles[demo.role].id
    session.flush()
    return tenants


def run(settings: Settings, *, with_data: bool) -> None:
    if settings.demo_password is None:
        raise SystemExit("NIMBRIX_DEMO_PASSWORD must be set to seed demo users")
    database = Database(build_engine(settings))
    try:
        with database.session_factory() as session:
            tenants = seed_identities(session, settings.demo_password.get_secret_value())
            session.commit()
            logger.info("seeded_identities", extra={"tenants": sorted(tenants)})
            if with_data:
                seed_demo_data(session, settings, tenants)
    finally:
        database.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--if-enabled", action="store_true", help="No-op unless NIMBRIX_DEMO_SEED=true"
    )
    parser.add_argument("--no-data", action="store_true", help="Seed identities only")
    args = parser.parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level.value)
    if args.if_enabled and not settings.demo_seed:
        logger.info("demo_seed_skipped", extra={"reason": "NIMBRIX_DEMO_SEED is not true"})
        return
    run(settings, with_data=not args.no_data)


if __name__ == "__main__":
    main(sys.argv[1:])
