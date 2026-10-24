"""Twilio SMS adapter: sms_log rows for mock / sent / failed sends, inbound
signature validation, and the public webhook URL Twilio signs.

No network: real-mode tests swap the Twilio REST client for a fake through
the module's `_client_factory` seam.
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from sqlalchemy import select
from starlette.datastructures import FormData
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator

from app import models
from app.adapters import twilio_sms
from app.config import settings
from app.timeutil import utcnow
from tests.conftest import PUBLIC_BASE_URL, TWILIO_TOKEN

TO = "+15550000001"
FROM = "+15550009999"
ACCOUNT_SID = "AC" + "a" * 32
API_KEY_SID = "SK" + "b" * 32
API_KEY_SECRET = "key-secret-xyz"


class FakeTwilioClient:
    """Stands in for twilio.rest.Client: `client.messages.create(...)`."""

    def __init__(self) -> None:
        self.messages = self
        self.calls: list[dict] = []
        self.threads: list[int] = []
        self.error: Exception | None = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.threads.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        # Real MessageInstance fields we could read (we only use sid).
        return SimpleNamespace(sid="SM" + "1" * 32, status="queued", to=kwargs["to"],
                               from_=kwargs["from_"], body=kwargs["body"])


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeTwilioClient()
    fake.factory_calls = 0

    def factory():
        fake.factory_calls += 1
        return fake

    monkeypatch.setattr(twilio_sms, "_client_factory", factory)
    return fake


@pytest.fixture
def live(monkeypatch, fake_client):
    """Real (non-mock) mode with fake credentials; sends go to `fake_client`."""
    monkeypatch.setattr(settings, "mock_telephony", False)
    monkeypatch.setattr(settings, "twilio_account_sid", ACCOUNT_SID)
    monkeypatch.setattr(settings, "twilio_api_key_sid", API_KEY_SID)
    monkeypatch.setattr(settings, "twilio_api_key_secret", API_KEY_SECRET)
    monkeypatch.setattr(settings, "twilio_number", FROM)
    return fake_client


def committed_rows(db) -> list[models.SmsLog]:
    """sms_log rows that survive a rollback, i.e. were really committed."""
    db.rollback()
    return list(db.scalars(select(models.SmsLog)))


# --- send_sms ---------------------------------------------------------------

async def test_mock_send_commits_mock_row_without_api_call(db, seeded, fake_client):
    assert settings.mock_telephony is True  # conftest default
    row = await twilio_sms.send_sms(db, TO, "Guardian will call you back at 14:45.",
                                    kind="callback", patient_id=seeded["maria_id"])

    [stored] = committed_rows(db)
    assert stored.id == row.id
    assert stored.status == "mock"
    assert stored.direction == "outbound"
    assert stored.kind == "callback"
    assert stored.to_number == TO
    assert stored.body == "Guardian will call you back at 14:45."
    assert stored.patient_id == seeded["maria_id"]
    assert stored.provider_sid is None and stored.error is None
    assert stored.sent_at.tzinfo is None
    assert abs(stored.sent_at - utcnow()) < timedelta(seconds=10)  # naive UTC
    assert fake_client.factory_calls == 0 and fake_client.calls == []


async def test_mock_send_masks_number_in_logs(db, caplog):
    caplog.set_level(logging.INFO, logger=twilio_sms.__name__)
    await twilio_sms.send_sms(db, TO, "hello", kind="tip")
    assert TO not in caplog.text
    assert "+15*****0001" in caplog.text


async def test_real_send_commits_sent_row_with_twilio_sid(db, seeded, live):
    row = await twilio_sms.send_sms(db, TO, "Reminder: take your Lisinopril.",
                                    kind="reminder", patient_id=seeded["maria_id"])

    assert live.calls == [{"to": TO, "from_": FROM, "body": "Reminder: take your Lisinopril."}]
    [stored] = committed_rows(db)
    assert stored.id == row.id
    assert stored.status == "sent"
    assert stored.provider_sid == "SM" + "1" * 32
    assert stored.from_number == FROM and stored.to_number == TO
    assert stored.error is None


async def test_real_send_runs_blocking_sdk_call_in_worker_thread(db, live):
    await twilio_sms.send_sms(db, TO, "hello", kind="tip")
    assert live.threads and live.threads[0] != threading.get_ident()


@pytest.mark.parametrize("error, expected", [
    (TwilioRestException(400, "/2010-04-01/Accounts/AC1/Messages.json",
                         "The 'To' number is not a valid phone number.", code=21211,
                         method="POST"),
     ["21211", "not a valid phone number"]),
    (RuntimeError("connection reset by peer"), ["connection reset by peer"]),
])
async def test_provider_error_commits_failed_row_without_raising(db, live, error, expected):
    live.error = error
    row = await twilio_sms.send_sms(db, TO, "hello", kind="reminder")

    [stored] = committed_rows(db)
    assert stored.id == row.id
    assert stored.status == "failed"
    assert stored.provider_sid is None
    for fragment in expected:
        assert fragment in stored.error


async def test_client_construction_error_commits_failed_row(db, live, monkeypatch):
    def broken_factory():
        raise ValueError("bad credentials format")

    monkeypatch.setattr(twilio_sms, "_client_factory", broken_factory)
    row = await twilio_sms.send_sms(db, TO, "hello", kind="reminder")
    assert row.status == "failed" and "bad credentials format" in row.error
    assert [r.id for r in committed_rows(db)] == [row.id]


async def test_error_text_never_contains_secrets(db, live):
    live.error = RuntimeError(f"auth failed for {API_KEY_SECRET} / {TWILIO_TOKEN}")
    row = await twilio_sms.send_sms(db, TO, "hello", kind="reminder")
    assert row.status == "failed"
    assert API_KEY_SECRET not in row.error and TWILIO_TOKEN not in row.error


@pytest.mark.parametrize("number", ["015112345678", "12345", "not a number", "", "9" * 40, None,
                                    15550000001])
async def test_invalid_number_commits_failed_row_without_api_call(db, live, number):
    row = await twilio_sms.send_sms(db, number, "hello", kind="reminder")
    assert row.status == "failed" and "E.164" in row.error
    assert live.factory_calls == 0 and live.calls == []
    assert [r.id for r in committed_rows(db)] == [row.id]
    assert len(row.to_number or "") <= 32  # fits VARCHAR(32) on Postgres


async def test_formatted_number_is_normalized_before_sending(db, live):
    row = await twilio_sms.send_sms(db, "+1 (555) 000-0001", "hello", kind="reminder")
    assert row.status == "sent" and row.to_number == TO
    assert live.calls[0]["to"] == TO


async def test_empty_body_commits_failed_row_without_api_call(db, live):
    row = await twilio_sms.send_sms(db, TO, "   ", kind="reminder")
    assert row.status == "failed" and row.error
    assert live.calls == []


@pytest.mark.parametrize("missing", [
    "twilio_account_sid", "twilio_api_key_sid", "twilio_api_key_secret", "twilio_number"])
async def test_missing_credentials_fall_back_to_mock_with_warning(db, live, monkeypatch, caplog,
                                                                  missing):
    monkeypatch.setattr(settings, missing, "")
    caplog.set_level(logging.WARNING, logger=twilio_sms.__name__)
    assert twilio_sms.is_mock()
    row = await twilio_sms.send_sms(db, TO, "hello", kind="reminder")
    assert row.status == "mock" and live.calls == []
    assert missing.upper() in caplog.text  # names the missing env var


def test_is_mock_false_only_with_credentials_and_flag_off(live, monkeypatch):
    assert twilio_sms.is_mock() is False
    monkeypatch.setattr(settings, "mock_telephony", True)
    assert twilio_sms.is_mock() is True


def test_default_client_factory_uses_api_key_auth_with_timeout(live):
    client = twilio_sms._default_client_factory()  # builds only, no network
    assert client.username == API_KEY_SID
    assert client.password == API_KEY_SECRET
    assert client.account_sid == ACCOUNT_SID
    assert client.http_client.timeout and client.http_client.timeout > 0


# --- validate_twilio_request ---------------------------------------------------

URL = PUBLIC_BASE_URL + "/inbound/sms"
PARAMS = {"From": TO, "To": FROM, "Body": "call", "MessageSid": "SM" + "2" * 32,
          "AccountSid": ACCOUNT_SID, "NumMedia": "0"}


def twilio_signature(url: str = URL, params=PARAMS, token: str = TWILIO_TOKEN) -> str:
    """What Twilio puts in X-Twilio-Signature (computed by the official SDK)."""
    return RequestValidator(token).compute_signature(url, params)


def test_validator_accepts_genuine_signature():
    assert twilio_sms.validate_twilio_request(URL, PARAMS, twilio_signature()) is True


def test_validator_accepts_starlette_form_data():
    form = FormData(list(PARAMS.items()))
    assert twilio_sms.validate_twilio_request(URL, form, twilio_signature()) is True


def test_validator_accepts_repeated_keys_as_form_data_or_lists():
    body = "Body=photo&MediaUrl0=https%3A%2F%2Fm.test%2Fa&Tag=x&Tag=y"
    form = FormData([("Body", "photo"), ("MediaUrl0", "https://m.test/a"),
                     ("Tag", "x"), ("Tag", "y")])
    signature = twilio_signature(params=form)
    assert twilio_sms.validate_twilio_request(URL, form, signature) is True
    assert twilio_sms.validate_twilio_request(URL, parse_qs(body), signature) is True


@pytest.mark.parametrize("tampered", [
    {**PARAMS, "Body": "stop"},
    {**PARAMS, "Extra": "1"},
    {k: v for k, v in PARAMS.items() if k != "NumMedia"},
])
def test_validator_rejects_tampered_params(tampered):
    assert twilio_sms.validate_twilio_request(URL, tampered, twilio_signature()) is False


def test_validator_rejects_signature_for_another_url():
    signature = twilio_signature()
    assert twilio_sms.validate_twilio_request(URL + "?x=1", PARAMS, signature) is False
    assert twilio_sms.validate_twilio_request(
        "https://evil.test/inbound/sms", PARAMS, signature) is False


def test_validator_rejects_signature_made_with_another_token():
    signature = twilio_signature(token="someone-elses-token")
    assert twilio_sms.validate_twilio_request(URL, PARAMS, signature) is False


@pytest.mark.parametrize("token", ["", None])
def test_validator_rejects_everything_when_token_unset(monkeypatch, token):
    monkeypatch.setattr(settings, "twilio_auth_token", token)
    # Including a signature made with an empty key: what an attacker would
    # try against a server whose token is unset.
    for signature in (twilio_signature(), twilio_signature(token="")):
        assert twilio_sms.validate_twilio_request(URL, PARAMS, signature) is False


@pytest.mark.parametrize("signature", [None, ""])
def test_validator_rejects_missing_signature(signature):
    assert twilio_sms.validate_twilio_request(URL, PARAMS, signature) is False


def test_validator_accepts_signed_url_with_query_and_no_params():
    url = twilio_sms.public_webhook_url("/inbound/sms", "patient=7")
    signature = twilio_signature(url=url, params={})
    assert twilio_sms.validate_twilio_request(url, {}, signature) is True
    assert twilio_sms.validate_twilio_request(url, None, signature) is True


# --- public_webhook_url ------------------------------------------------------

def test_public_webhook_url_without_query():
    assert twilio_sms.public_webhook_url("/inbound/sms") == "https://guardian.test/inbound/sms"


def test_public_webhook_url_with_query():
    assert (twilio_sms.public_webhook_url("/inbound/sms", "a=1&b=two")
            == "https://guardian.test/inbound/sms?a=1&b=two")


def test_public_webhook_url_handles_trailing_slash_and_missing_leading_slash(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://abc.ngrok.app/")
    assert twilio_sms.public_webhook_url("/inbound/sms") == "https://abc.ngrok.app/inbound/sms"
    assert (twilio_sms.public_webhook_url("inbound/sms", "?x=1")
            == "https://abc.ngrok.app/inbound/sms?x=1")
