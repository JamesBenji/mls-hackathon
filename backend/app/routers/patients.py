"""Dashboard data routes (X-API-Key). TODO(router task):
GET /patients, GET /patients/{id}, PATCH /patients/{id},
GET/POST /patients/{id}/vitals, GET /patients/{id}/reports."""
from fastapi import APIRouter

router = APIRouter()
