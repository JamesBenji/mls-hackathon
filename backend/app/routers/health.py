"""Health routes. TODO(router task):
public_router:    GET /health          liveness only {"status": "ok"}, no data
protected_router: GET /health/models   (X-API-Key) per-model status via
                                       ollama_client.probe_models()
"""
from fastapi import APIRouter

public_router = APIRouter()
protected_router = APIRouter()
