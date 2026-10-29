"""Minimal local API bootstrap; clinical workflows are deliberately not enabled yet."""

import os

from fastapi import FastAPI
from pydantic import BaseModel

from services.api.app.middleware.request_context import RequestContextMiddleware
from services.api.app.routers.triage import router as triage_router
from services.api.app.routers.records import router as records_router
from services.api.app.routers.scribe import router as scribe_router

if os.getenv("SYNTHETIC_DATA_ONLY", "true").lower() != "true":
    raise RuntimeError("This bootstrap API is restricted to synthetic data")

app = FastAPI(
    title="Sahyog Care Companion API",
    version="0.1.0",
    description="Synthetic-only foundation bootstrap; no clinical decisions are performed.",
)
app.add_middleware(RequestContextMiddleware)
app.include_router(triage_router)
app.include_router(records_router)
app.include_router(scribe_router)


class HealthStatus(BaseModel):
    status: str
    environment: str
    synthetic_data_only: bool


@app.get("/health/live", response_model=HealthStatus, tags=["health"])
def liveness() -> HealthStatus:
    return HealthStatus(
        status="alive",
        environment=os.getenv("APP_ENV", "local"),
        synthetic_data_only=os.getenv("SYNTHETIC_DATA_ONLY", "true").lower() == "true",
    )


@app.get("/health/ready", response_model=HealthStatus, tags=["health"])
def readiness() -> HealthStatus:
    # Database and auth readiness checks will be added with the schema/auth steps.
    return HealthStatus(
        status="ready",
        environment=os.getenv("APP_ENV", "local"),
        synthetic_data_only=os.getenv("SYNTHETIC_DATA_ONLY", "true").lower() == "true",
    )