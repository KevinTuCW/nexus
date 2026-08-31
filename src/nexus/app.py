"""nexus application wiring."""

import os

from fastapi import FastAPI

from nexus.config import get_settings
from nexus.console.api import router as console_router
from nexus.ingress.admin_auth import ADMIN_KEYS_ENV
from nexus.ingress.api import router
from nexus.ingress.passthrough import router as passthrough_router
from nexus.ingress.usage_api import router as usage_router


def admin_is_available() -> bool:
    """Both prerequisites for a control plane, checked before mounting one.

    No administrators means nobody could act on it. No database means every
    change it accepted would die at the next restart, and a control plane
    whose changes do not survive is a lie -- the same judgement this
    repository already makes when it refuses to call an empty ledger a pass.

    Not mounted, rather than mounted and refusing everything. A 401 tells a
    scanner the surface exists and is worth a dictionary; a 404 says there is
    nothing here, which is true.
    """
    return bool(
        get_settings().database_url and os.environ.get(ADMIN_KEYS_ENV, "").strip()
    )


def create_app() -> FastAPI:
    """Assemble the application.

    A factory rather than module-level mounting because whether `/admin`
    exists is decided by the environment, and a decision made once at import
    time cannot be exercised by a test. The conditional mount is the whole
    security property here, so it has to be reachable.
    """
    application = FastAPI(title="nexus", version="0.1.0")
    application.include_router(router)
    application.include_router(passthrough_router)
    application.include_router(usage_router)
    application.include_router(console_router)

    if admin_is_available():
        from nexus.admin.api import router as admin_router

        application.include_router(admin_router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
