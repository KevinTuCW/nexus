"""nexus application wiring."""

from fastapi import FastAPI

from nexus.config import get_settings
from nexus.console.api import router as console_router
from nexus.ingress.api import router
from nexus.ingress.passthrough import router as passthrough_router
from nexus.ingress.usage_api import router as usage_router


def admin_is_available() -> bool:
    """The one prerequisite for a control plane: somewhere to write.

    A control plane whose changes die at the next restart is a lie -- the
    same judgement this repository already makes when it refuses to call an
    empty ledger a pass. So no database means no `/admin` at all, and a 404
    rather than a 401: a 401 tells a scanner the surface exists and is worth
    a dictionary.

    Accounts are *not* part of this condition, unlike the shared keys they
    replaced. With no accounts the login form is there and nothing can get
    past it, which is the same closed door -- and hiding the surface would
    also hide the only page that tells a fresh operator how to create the
    first account.
    """
    return bool(get_settings().database_url)


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
