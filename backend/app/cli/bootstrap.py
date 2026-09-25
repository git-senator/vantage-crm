"""Bootstrap a workspace and its first owner.

A fresh database has permissions and roles (seeded by migration) but no
organization and no user — so nobody can log in and nobody can invite anyone.
This closes that gap.

    python -m app.cli.bootstrap --name "ROSSA CRM" --email you@example.com

Idempotent: re-running against an existing organization adds the user rather
than failing, so it is safe in a deploy script.

Deliberately a CLI and not an HTTP endpoint. A public "create the first owner"
route is a privilege-escalation hole the moment anyone forgets to disable it
after setup.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.permissions import DEFAULT_OWNER_ROLE
from app.core.security import hash_password
from app.db.session import session_scope, set_tenant_context
from app.models.organization import Organization
from app.models.user import User
from app.services.pipeline import build_default_pipeline
from app.services.rbac import RbacService

logger = get_logger(__name__)

SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = SLUG_PATTERN.sub("-", value.lower()).strip("-")
    return slug[:63] or f"workspace-{uuid.uuid4().hex[:8]}"


async def bootstrap(
    *, org_name: str, email: str, full_name: str, password: str, slug: str | None
) -> None:
    settings = get_settings()
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        raise SystemExit(
            f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters."
        )

    organization_slug = slug or slugify(org_name)

    # A brand-new organization has to be created under its own tenant context:
    # RLS applies to the very first INSERT, and the id must exist before the
    # row does. Generating it here rather than letting the default fire is what
    # makes that possible.
    organization_id = uuid.uuid4()

    async with session_scope(organization_id) as session:
        existing = (
            await session.execute(
                select(Organization).where(Organization.slug == organization_slug)
            )
        ).scalar_one_or_none()

        if existing is not None:
            organization = existing
            # Re-bind to the organization we actually found.
            await set_tenant_context(session, organization.id)
            print(f"Using existing workspace '{organization.name}'.")
        else:
            organization = Organization(
                id=organization_id, name=org_name, slug=organization_slug
            )
            session.add(organization)
            await session.flush()

            # A workspace with no pipeline can view the Deals page but cannot
            # create a deal — there is nowhere to put one. Seeded here so a
            # freshly bootstrapped workspace is immediately usable, using the
            # same builder the backfill migration uses so the two cannot drift.
            session.add(build_default_pipeline(organization.id))
            await session.flush()

            print(f"Created workspace '{org_name}' ({organization_slug}).")

        duplicate = (
            await session.execute(
                select(User)
                .where(User.email == email)
                .where(User.organization_id == organization.id)
            )
        ).scalar_one_or_none()

        if duplicate is not None:
            raise SystemExit(f"A user with email {email} already exists here.")

        user = User(
            organization_id=organization.id,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            job_title="Owner",
            status="active",
        )
        session.add(user)
        await session.flush()

        await RbacService(session).assign_role(
            user_id=user.id,
            role_key=DEFAULT_OWNER_ROLE,
            organization_id=organization.id,
        )

        print(f"Created owner {email}.")
        print("\nSign in at http://localhost:3000/login")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a workspace and its owner.")
    parser.add_argument("--name", required=True, help="Organization name")
    parser.add_argument("--email", required=True, help="Owner email address")
    parser.add_argument("--full-name", default=None, help="Owner full name")
    parser.add_argument("--slug", default=None, help="URL slug (derived if omitted)")
    parser.add_argument(
        "--password",
        default=None,
        help="Owner password. Omit to be prompted — passing it on the command "
        "line leaves it in your shell history.",
    )
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)

    password = args.password or getpass.getpass("Owner password: ")
    if not args.password and password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match.")

    asyncio.run(
        bootstrap(
            org_name=args.name,
            email=args.email,
            full_name=args.full_name or args.email.split("@")[0].replace(".", " ").title(),
            password=password,
            slug=args.slug,
        )
    )


if __name__ == "__main__":
    main()
