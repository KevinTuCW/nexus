"""nexus application wiring."""

from fastapi import FastAPI

from nexus.console.api import router as console_router
from nexus.ingress.api import router
from nexus.ingress.passthrough import router as passthrough_router
from nexus.ingress.usage_api import router as usage_router

app = FastAPI(title="nexus", version="0.1.0")
app.include_router(router)
app.include_router(passthrough_router)
app.include_router(usage_router)
app.include_router(console_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
