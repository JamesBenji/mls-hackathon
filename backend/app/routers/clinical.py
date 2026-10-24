"""Direct clinical-brain endpoints for testing without telephony (X-API-Key).
TODO(orchestration task): GET /clinical/{patient_id}/question-plan,
POST /clinical/{patient_id}/analyze."""
from fastapi import APIRouter

router = APIRouter()
