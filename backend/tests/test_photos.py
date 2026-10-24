"""Small offline smoke/security checks; no real FCM or PostgreSQL required."""

import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.photos import push
from app.photos.auth import issue_credential
from app.photos.models import PhotoAsset, PhotoRequest


@compiles(ARRAY, "sqlite")
@compiles(JSONB, "sqlite")
def sqlite_test_types(_type, _compiler, **_kwargs):
    return "JSON"


sqlite3.register_adapter(list, json.dumps)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from app.photos import quality
    async def technical_quality(_images):
        return quality.QualityResult(status="clear_enough")
    monkeypatch.setattr(quality, "assess_photo_quality", technical_quality)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "photo_storage_dir", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "photo_push_enabled", False)
    with Session(engine) as db:
        db.add_all(
            [
                models.Doctor(id=1, name="A", email="a@example.test"),
                models.Doctor(id=2, name="B", email="b@example.test"),
            ]
        )
        db.flush()
        db.add_all(
            [
                models.Patient(id=1, doctor_id=1, name="Patient A", phone="1"),
                models.Patient(id=2, doctor_id=2, name="Patient B", phone="2"),
            ]
        )
        db.flush()
        tokens = {}
        for name, role, subject in [
            ("doctor", "doctor", 1),
            ("other_doctor", "doctor", 2),
            ("patient", "patient", 1),
            ("other_patient", "patient", 2),
        ]:
            token, _ = issue_credential(db, role, subject)
            tokens[name] = {"Authorization": f"Bearer {token}"}
        db.commit()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, tokens, engine
    app.dependency_overrides.clear()
    engine.dispose()


def create(client, tokens):
    return client.post(
        "/patients/1/photo-requests",
        json={"instructions": "Take a photo"},
        headers={**tokens["doctor"], "Idempotency-Key": "request-1"},
    )


def upload(client, tokens, request_id, content=None, mime="image/png", role="patient"):
    if content is None:
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10), "red").save(buffer, format="PNG")
        content = buffer.getvalue()
    return client.post(
        f"/me/photo-requests/{request_id}/photos",
        headers={**tokens[role], "Idempotency-Key": "upload-1"},
        files={"file": ("../../unsafe.png", content, mime)},
    )


def test_advisory_quality_and_optional_retake(api, monkeypatch):
    from app.photos import quality
    async def uncertain(_images):
        return quality.QualityResult(status="uncertain", issues=["blur"])
    monkeypatch.setattr(quality, "assess_photo_quality", uncertain)
    client, tokens, _engine = api
    request_id = create(client, tokens).json()["id"]
    original = upload(client, tokens, request_id)
    assert original.status_code == 201
    request = client.get(f"/me/photo-requests/{request_id}", headers=tokens["patient"]).json()
    assert request["quality"]["status"] == "uncertain"
    assert request["quality"]["technical_feedback"] == ["blur"]
    assert client.get(original.json()["content_url"], headers=tokens["patient"]).status_code == 200
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(buffer, format="PNG")
    retake = client.post(f"/me/photo-requests/{request_id}/photos",
                         headers={**tokens["patient"], "Idempotency-Key": "retake-2"},
                         files={"file": ("retake.png", buffer.getvalue(), "image/png")})
    assert retake.status_code == 201
    assert retake.json()["request_id"] == request_id


def test_pairing_upload_review_and_idempotent_retries(api):
    client, tokens, engine = api
    pairing = client.post("/patients/1/pairing", headers=tokens["doctor"])
    assert pairing.status_code == 201
    paired = {"Authorization": f"Bearer {pairing.json()['access_token']}"}
    assert client.get("/me", headers=paired).json()["id"] == 1
    assert (
        client.put(
            "/me/devices/install-1",
            headers=paired,
            json={"platform": "android", "fcm_token": "test-token"},
        ).status_code
        == 200
    )
    request = create(client, tokens).json()
    assert create(client, tokens).json()["id"] == request["id"]
    assert client.get("/me/photo-requests", headers=paired).json()[0]["id"] == request["id"]
    result = upload(client, tokens, request["id"])
    assert result.status_code == 201, result.text
    photo = result.json()
    assert upload(client, tokens, request["id"]).json() == photo
    assert client.get("/patients/1/photos", headers=tokens["doctor"]).json() == [photo]
    content = client.get(photo["content_url"], headers=tokens["doctor"])
    assert content.status_code == 200 and content.headers["content-type"] == "image/jpeg"
    assert content.headers["cache-control"] == "private, no-store"
    assert (
        client.get(f"/me/photo-requests/{request['id']}", headers=paired).json()["status"]
        == "completed"
    )
    with Session(engine) as db:
        assert len(db.scalars(select(PhotoAsset)).all()) == 1
        assert len(db.scalars(select(models.PhotoUpload)).all()) == 1
    assert client.delete("/patients/1/pairing", headers=tokens["doctor"]).status_code == 204
    assert client.get("/me", headers=paired).status_code == 401


def test_authentication_and_ownership(api):
    client, tokens, _ = api
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer patient-1"}).status_code == 401
    assert client.get("/me", headers=tokens["doctor"]).status_code == 403
    assert client.post("/patients/1/pairing", headers=tokens["other_doctor"]).status_code == 404
    assert client.get("/patients/1/photos", headers=tokens["other_doctor"]).status_code == 404
    request_id = create(client, tokens).json()["id"]
    assert (
        client.get(f"/me/photo-requests/{request_id}", headers=tokens["other_patient"]).status_code
        == 404
    )
    assert upload(client, tokens, request_id, role="other_patient").status_code == 404
    photo = upload(client, tokens, request_id).json()
    assert client.get(photo["content_url"]).status_code == 401
    for role in ["other_patient", "other_doctor"]:
        assert client.get(photo["content_url"], headers=tokens[role]).status_code == 404


def test_upload_validation(api, monkeypatch):
    client, tokens, engine = api
    request_id = create(client, tokens).json()["id"]
    assert upload(client, tokens, request_id, b"not an image").status_code == 415
    assert upload(client, tokens, request_id, mime="image/jpeg").status_code == 415
    monkeypatch.setattr(settings, "photo_max_bytes", 20)
    assert upload(client, tokens, request_id).status_code == 413
    with Session(engine) as db:
        assert db.get(PhotoRequest, request_id).status == "pending"
        assert db.scalar(select(PhotoAsset)) is None


def test_push_failure_keeps_request_pollable(api, monkeypatch):
    client, tokens, _ = api
    monkeypatch.setattr(settings, "photo_push_enabled", True)
    client.put(
        "/me/devices/a",
        headers=tokens["patient"],
        json={"platform": "android", "fcm_token": "test-token"},
    )

    def fail(*_args):
        raise RuntimeError("Firebase unavailable")

    monkeypatch.setattr(push, "send_notification", fail)
    request = create(client, tokens).json()
    assert request["push_status"] == "failed"
    assert (
        client.get(f"/me/photo-requests/{request['id']}", headers=tokens["patient"]).json()[
            "status"
        ]
        == "pending"
    )
