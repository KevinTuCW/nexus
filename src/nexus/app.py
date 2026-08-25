"""nexus application wiring."""

from fastapi import FastAPI

from nexus.ingress.api import router

app = FastAPI(title="nexus", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
