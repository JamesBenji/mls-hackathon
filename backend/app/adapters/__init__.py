"""Thin adapters to external systems (Ollama, ElevenLabs, Twilio).

Core services call these; nothing outside app/adapters imports `twilio`,
`elevenlabs` or talks to Ollama's HTTP API directly.
"""
