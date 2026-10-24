"""Retired provisioning entrypoint; deliberately performs no API or file writes.

Historical defaults must never overwrite the authoritative live agent, tools,
model, voice, phone assignment or secrets. Retained so old deployment commands
fail visibly instead of silently changing remote state.
"""
import sys


def main(argv: list[str] | None = None) -> int:
    print("Provisioning disabled: live ElevenLabs configuration is authoritative.")
    print("No API calls or env writes made. Review the live configuration manually.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
