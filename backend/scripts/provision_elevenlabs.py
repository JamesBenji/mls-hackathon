"""Provision the Guardian ElevenLabs agent - idempotent, re-run any time.

Creates or updates, from the settings in an env file:
  1. workspace secret  guardian_tool_auth  = "Bearer <ELEVEN_TOOL_AUTH_TOKEN>"
  2. webhook tools     schedule_callback, flag_emergency -> {PUBLIC_BASE_URL}/tools/*
  3. post-call webhook guardian-post-call (HMAC) -> {PUBLIC_BASE_URL}/calls/webhook
  4. the agent         "Guardian Check-in" (prompt: scripts/agent_prompt.md,
                       5 dynamic variables, tools, inbound voice-init webhook)
  5. phone number      ELEVEN_AGENT_PHONE_NUMBER_ID -> assigned to the agent
and writes ELEVEN_AGENT_ID / ELEVEN_WEBHOOK_SECRET (+ a generated
ELEVEN_TOOL_AUTH_TOKEN if missing) back into the env file.

Steps 2-3 and the voice-init webhook need a public https PUBLIC_BASE_URL;
with a localhost URL they are skipped (re-run once the droplet is up).
Changing the webhook URL recreates the webhook, which issues a new HMAC
secret - the env file is updated, so redeploy it to the server afterwards.

Usage (from backend/):
    python -m scripts.provision_elevenlabs [--env-file .env]
        [--public-base-url https://203-0-113-10.sslip.io] [--dry-run]
Secrets are never printed.
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

import httpx

from app.config import Settings

AGENT_NAME = "Guardian Check-in"
AGENT_TAG = "guardian"
SECRET_NAME = "guardian_tool_auth"
WEBHOOK_NAME = "guardian-post-call"
PROMPT_FILE = Path(__file__).with_name("agent_prompt.md")
AGENT_LLM = "gemini-2.5-flash"

FIRST_MESSAGE = (
    "Hello, this is Guardian, an AI assistant calling on behalf of your doctor's "
    "office for your check-in. Am I speaking with {{patient_name}}?"
)
# Defaults used when a conversation starts without our dynamic variables
# (e.g. a test call from the ElevenLabs dashboard). Every variable the prompt
# uses must be defined on the agent.
DYNAMIC_VARIABLE_PLACEHOLDERS = {
    "patient_name": "there",
    "question_plan": "1. How are you feeling overall today?",
    "last_call_summary": "none",
    "session_id": "0",
    "resume_from": "start",
}
POST_CALL_EVENTS = ["transcript", "call_initiation_failure"]


class ApiError(RuntimeError):
    pass


class ElevenApi:
    def __init__(self, base_url: str, api_key: str, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30,
                                    headers={"xi-api-key": api_key})

    def call(self, method: str, path: str, *, json: dict | None = None,
             params: dict | None = None, mutating: bool = False):
        if mutating and self.dry_run:
            print(f"   [dry-run] {method} {path}")
            return {}
        resp = self._client.request(method, path, json=json, params=params)
        if resp.status_code >= 400:
            raise ApiError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}


# --- env file ------------------------------------------------------------------

def set_env_var(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    text = pattern.sub(lambda _m: line, text) if pattern.search(text) else (
        text.rstrip("\n") + ("\n" if text else "") + line + "\n")
    path.write_text(text, encoding="utf-8")


# --- resources -----------------------------------------------------------------

def ensure_secret(api: ElevenApi, token: str) -> str:
    value = f"Bearer {token}"
    listed = api.call("GET", "/v1/convai/secrets", params={"search": SECRET_NAME})
    existing = next((s for s in listed.get("secrets", []) if s.get("name") == SECRET_NAME), None)
    if existing:
        api.call("PATCH", f"/v1/convai/secrets/{existing['secret_id']}", mutating=True,
                 json={"type": "update", "name": SECRET_NAME, "value": value})
        print(f" - secret {SECRET_NAME}: updated")
        return existing["secret_id"]
    created = api.call("POST", "/v1/convai/secrets", mutating=True,
                       json={"type": "new", "name": SECRET_NAME, "value": value})
    print(f" - secret {SECRET_NAME}: created")
    return created.get("secret_id", "<dry-run-secret>")


def _tool_configs(base_url: str, secret_id: str) -> list[dict]:
    headers = {"Authorization": {"secret_id": secret_id}}
    session_id = {"type": "string", "dynamic_variable": "session_id"}
    return [
        {
            "type": "webhook",
            "name": "schedule_callback",
            "description": (
                "Schedule a callback when the patient has not measured a vital yet "
                "(blood pressure, blood sugar, weight). Returns callback_time (HH:MM) "
                "to tell the patient."
            ),
            "response_timeout_secs": 20,
            "api_schema": {
                "url": f"{base_url}/tools/schedule_callback",
                "method": "POST",
                "content_type": "application/json",
                "request_headers": headers,
                "request_body_schema": {
                    "type": "object",
                    "description": "Callback request for the current call session.",
                    "required": ["session_id", "minutes", "missing_item", "checkpoint"],
                    "properties": {
                        "session_id": session_id,
                        "minutes": {"type": "number", "description":
                                    "Minutes until the callback. Use 15 unless the patient asks for another time."},
                        "missing_item": {"type": "string", "description":
                                         "What the patient still has to measure, e.g. 'blood pressure'."},
                        "checkpoint": {"type": "string", "description":
                                       "One short sentence: which questions were asked and what the patient answered so far."},
                    },
                },
            },
        },
        {
            "type": "webhook",
            "name": "flag_emergency",
            "description": (
                "Alert the patient's doctor immediately when the patient reports a red "
                "flag (chest pain, trouble breathing, fainting, stroke signs, severe "
                "bleeding, self-harm thoughts, severe distress, medication clearly not working)."
            ),
            "response_timeout_secs": 20,
            "api_schema": {
                "url": f"{base_url}/tools/flag_emergency",
                "method": "POST",
                "content_type": "application/json",
                "request_headers": headers,
                "request_body_schema": {
                    "type": "object",
                    "description": "Red-flag alert for the current call session.",
                    "required": ["session_id", "reason", "recommended_action"],
                    "properties": {
                        "session_id": session_id,
                        "reason": {"type": "string", "description":
                                   "The patient's concern in their own words, one short sentence."},
                        "recommended_action": {
                            "type": "string",
                            "enum": ["doctor_callback", "ambulance", "request_appointment"],
                            "description": ("ambulance if possibly life-threatening, doctor_callback if "
                                            "urgent, request_appointment if not urgent."),
                        },
                    },
                },
            },
        },
    ]


def ensure_tools(api: ElevenApi, base_url: str, secret_id: str) -> list[str]:
    ids: list[str] = []
    for config in _tool_configs(base_url, secret_id):
        name = config["name"]
        listed = api.call("GET", "/v1/convai/tools", params={"search": name, "types": "webhook"})
        existing = next((t for t in listed.get("tools", [])
                         if (t.get("tool_config") or {}).get("name") == name), None)
        if existing:
            api.call("PATCH", f"/v1/convai/tools/{existing['id']}", mutating=True,
                     json={"tool_config": config})
            print(f" - tool {name}: updated -> {config['api_schema']['url']}")
            ids.append(existing["id"])
        else:
            created = api.call("POST", "/v1/convai/tools", mutating=True, json={"tool_config": config})
            print(f" - tool {name}: created -> {config['api_schema']['url']}")
            ids.append(created.get("id", f"<dry-run-{name}>"))
    return ids


def ensure_webhook(api: ElevenApi, url: str, current_secret: str | None) -> tuple[str, str | None]:
    """Returns (webhook_id, new_secret_or_None). The HMAC secret is only returned
    on create, and the URL cannot be changed, so a URL change means recreate."""
    listed = api.call("GET", "/v1/workspace/webhooks")
    existing = next((w for w in listed.get("webhooks", []) if w.get("name") == WEBHOOK_NAME), None)
    if existing and existing.get("webhook_url") == url and current_secret:
        print(f" - webhook {WEBHOOK_NAME}: unchanged -> {url}")
        return existing["webhook_id"], None
    if existing:
        # Unlink it from the agent first; a webhook in use may refuse deletion.
        print(f" - webhook {WEBHOOK_NAME}: recreating (url changed or secret unknown)")
        try:
            api.call("DELETE", f"/v1/workspace/webhooks/{existing['webhook_id']}", mutating=True)
        except ApiError as exc:
            print(f"   ! could not delete old webhook ({exc}); creating a new one anyway")
    created = api.call("POST", "/v1/workspace/webhooks", mutating=True, json={
        "settings": {"auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": url}})
    print(f" - webhook {WEBHOOK_NAME}: created -> {url}")
    return created.get("webhook_id", "<dry-run-webhook>"), created.get("webhook_secret")


def _agent_body(prompt: str, tool_ids: list[str], secret_id: str,
                webhook_id: str | None, base_url: str | None, llm: str | None) -> dict:
    prompt_cfg: dict = {
        "prompt": prompt,
        "temperature": 0.2,
        "tool_ids": tool_ids,
        "built_in_tools": {
            "end_call": {"type": "system", "name": "end_call",
                         "params": {"system_tool_type": "end_call"}},
        },
    }
    if llm:
        prompt_cfg["llm"] = llm
    workspace_overrides: dict = {}
    if base_url:
        workspace_overrides["conversation_initiation_client_data_webhook"] = {
            "url": f"{base_url}/inbound/voice-init",
            "request_headers": {"Authorization": {"secret_id": secret_id}},
        }
    if webhook_id:
        workspace_overrides["webhooks"] = {
            "post_call_webhook_id": webhook_id,
            "events": POST_CALL_EVENTS,
            "transcript_format": "json",
        }
    platform_settings: dict = {
        "overrides": {
            # Inbound Twilio calls fetch our dynamic variables from voice-init.
            "enable_conversation_initiation_client_data_from_webhook": bool(base_url),
        },
    }
    if workspace_overrides:
        platform_settings["workspace_overrides"] = workspace_overrides
    return {
        "name": AGENT_NAME,
        "tags": [AGENT_TAG],
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE,
                "language": "en",
                "dynamic_variables": {"dynamic_variable_placeholders": DYNAMIC_VARIABLE_PLACEHOLDERS},
                "prompt": prompt_cfg,
            },
            "conversation": {"max_duration_seconds": 900},
        },
        "platform_settings": platform_settings,
    }


def ensure_agent(api: ElevenApi, agent_id: str | None, body: dict) -> str:
    if not agent_id:
        listed = api.call("GET", "/v1/convai/agents", params={"search": AGENT_NAME, "page_size": 100})
        match = next((a for a in listed.get("agents", []) if a.get("name") == AGENT_NAME), None)
        agent_id = match["agent_id"] if match else None
    if agent_id:
        api.call("PATCH", f"/v1/convai/agents/{agent_id}", mutating=True, json=body)
        print(f" - agent {AGENT_NAME}: updated ({agent_id})")
        return agent_id
    created = api.call("POST", "/v1/convai/agents/create", mutating=True, json=body)
    agent_id = created.get("agent_id", "<dry-run-agent>")
    print(f" - agent {AGENT_NAME}: created ({agent_id})")
    return agent_id


def assign_number(api: ElevenApi, phone_number_id: str, agent_id: str) -> None:
    api.call("PATCH", f"/v1/convai/phone-numbers/{phone_number_id}", mutating=True,
             json={"agent_id": agent_id})
    print(f" - phone number {phone_number_id}: assigned to the agent")


# --- main ------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env", type=Path)
    parser.add_argument("--public-base-url", default=None,
                        help="override PUBLIC_BASE_URL (must be https for webhooks/tools)")
    parser.add_argument("--dry-run", action="store_true", help="read only; print planned writes")
    args = parser.parse_args(argv)

    env_file: Path = args.env_file
    settings = Settings(_env_file=env_file)
    if not settings.eleven_api_key:
        print(f"ELEVEN_API_KEY missing in {env_file}", file=sys.stderr)
        return 2
    if not settings.eleven_agent_phone_number_id:
        print(f"ELEVEN_AGENT_PHONE_NUMBER_ID missing in {env_file}", file=sys.stderr)
        return 2

    base_url = (args.public_base_url or settings.public_base_url or "").rstrip("/")
    public = base_url.startswith("https://")
    if args.public_base_url and not args.dry_run:
        set_env_var(env_file, "PUBLIC_BASE_URL", base_url)

    token = settings.eleven_tool_auth_token
    if not token:
        token = secrets.token_urlsafe(32)
        if not args.dry_run:
            set_env_var(env_file, "ELEVEN_TOOL_AUTH_TOKEN", token)
        print(" - generated ELEVEN_TOOL_AUTH_TOKEN")

    api = ElevenApi(settings.eleven_base_url, settings.eleven_api_key, dry_run=args.dry_run)
    print(f"Provisioning ElevenLabs ({'dry run' if args.dry_run else 'live'}); "
          f"public base url: {base_url or '(none)'}")

    secret_id = ensure_secret(api, token)
    tool_ids: list[str] = []
    webhook_id: str | None = None
    if public:
        tool_ids = ensure_tools(api, base_url, secret_id)
        webhook_id, new_secret = ensure_webhook(api, f"{base_url}/calls/webhook",
                                                settings.eleven_webhook_secret)
        if new_secret and not args.dry_run:
            set_env_var(env_file, "ELEVEN_WEBHOOK_SECRET", new_secret)
            print(" - ELEVEN_WEBHOOK_SECRET written to the env file")
    else:
        print(" ! PUBLIC_BASE_URL is not https: skipping tools, post-call webhook and the "
              "inbound voice-init webhook (re-run with --public-base-url https://...)")

    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    body = _agent_body(prompt, tool_ids, secret_id, webhook_id, base_url if public else None, AGENT_LLM)
    try:
        agent_id = ensure_agent(api, settings.eleven_agent_id, body)
    except ApiError as exc:
        if "llm" not in str(exc).lower():
            raise
        print(f" ! LLM {AGENT_LLM!r} rejected; retrying with the server default")
        body = _agent_body(prompt, tool_ids, secret_id, webhook_id, base_url if public else None, None)
        agent_id = ensure_agent(api, settings.eleven_agent_id, body)
    if not args.dry_run:
        set_env_var(env_file, "ELEVEN_AGENT_ID", agent_id)

    assign_number(api, settings.eleven_agent_phone_number_id, agent_id)

    if not args.dry_run:
        agent = api.call("GET", f"/v1/convai/agents/{agent_id}")
        cfg = (agent.get("conversation_config") or {}).get("agent") or {}
        attached = (cfg.get("prompt") or {}).get("tool_ids") or []
        numbers = [n.get("phone_number_id") for n in agent.get("phone_numbers") or []]
        print(f"Verified agent {agent_id}: tools attached={len(attached)}, "
              f"phone numbers={numbers or 'none'}, "
              f"variables={sorted(((cfg.get('dynamic_variables') or {}).get('dynamic_variable_placeholders') or {}))}")
    print("Done." + ("" if public else " Re-run with the droplet URL to enable tools + webhooks."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
