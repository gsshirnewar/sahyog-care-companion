"""Minimal local API bootstrap; clinical workflows are deliberately not enabled yet."""

import os

from fastapi import FastAPI
from pydantic import BaseModel

if os.getenv("SYNTHETIC_DATA_ONLY", "true").lower() != "true":
    raise RuntimeError("This bootstrap API is restricted to synthetic data")

app = FastAPI(
    title="Sahyog Care Companion API",
    version="0.1.0",
    description="Synthetic-only foundation bootstrap; no clinical decisions are performed.",
)


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