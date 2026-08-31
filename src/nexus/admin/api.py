"""The control plane's HTTP surface. Phase 4a mounts identity, not function.

Every endpoint here is behind `_admin()`, and in this phase there are only
two: one that says who you are, and the page shell. The panels that read and
write policy land in Phase 4b and 4c, on top of an identity that was checked
first rather than added afterwards -- which is the opposite of how the
console got its authorisation, and the reason it shipped a hole.

Mounting is conditional and lives in `app.py`: no administrators, or no
database, and this router is never registered. A 404 rather than a 401 is
deliberate. A 401 tells a scanner the surface exists and is worth a
dictionary; a 404 says there is nothing here, which is true.
"""

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from nexus.ingress.admin_auth import Admin, AdminAuthError, authenticate_admin
from nexus.state import get_state

router = APIRouter()


def _admin(authorization: str) -> Admin:
    """Authenticate an administrator, or refuse.

    403 rather than 401 for a valid tenant credential: the caller proved who
    they are and the answer is still no. Saying 401 would invite them to try
    again with the same key, which will never work and which the audit trail
    would fill up with.
    """
    state = get_state()
    try:
        return authenticate_admin(authorization, state.admin_index)
    except AdminAuthError as exc:
        try:
            from nexus.ingress.auth import authenticate

            tenant = authenticate(authorization, state.key_index)
        except Exception:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{tenant}' is a tenant credential. The control plane is a "
                "separate namespace; a data-plane key never carries "
                "administrative power."
            ),
        ) from exc


def _require_write(admin: Admin) -> None:
    """Guard for the mutating endpoints Phase 4b adds."""
    if not admin.may_write:
        raise HTTPException(
            status_code=403,
            detail=f"administrator '{admin.name}' is read-only",
        )


@router.get("/admin/whoami")
def whoami(authorization: str = Header(default="")) -> dict:
    """Who the control plane thinks you are. Named, never "admin"."""
    admin = _admin(authorization)
    return {"admin": admin.name, "role": admin.role}


@router.get("/admin")
def admin_page(authorization: str = Header(default="")) -> FileResponse:
    """The page shell. Empty until Phase 4c.

    Unlike the console, this shell authenticates. The console's shell is
    public because every panel it fetches is not, and a page that renders
    nothing without a key leaks nothing. This page will eventually carry
    write controls, and a login form is not something to bolt on later.
    """
    _admin(authorization)
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin.html")
