from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()


@router.get("/api/health")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/ready")
def readiness() -> dict[str, str]:
    # When real persistence + LLM checks land, ping them here.
    return {"status": "ready"}
