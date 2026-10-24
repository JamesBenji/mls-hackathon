"""Issue one patient bearer credential from a trusted server terminal.

Uses existing records only; never initializes, seeds, or resets the database.
Successful stdout contains a secret: do not capture or share it in logs.
"""

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient-id", type=int, required=True)
    args = parser.parse_args()
    if args.patient_id <= 0:
        parser.error("--patient-id must be a positive existing patient ID")

    from sqlalchemy.exc import SQLAlchemyError

    from app.database import SessionLocal
    from app.models import Doctor, Patient
    from app.photos.auth import issue_credential

    try:
        with SessionLocal() as db:
            patient = db.get(Patient, args.patient_id)
            if patient is None:
                parser.error("Patient not found; no credential issued")
            if db.get(Doctor, patient.doctor_id) is None:
                parser.error("Associated doctor not found; no credential issued")

            token, credential = issue_credential(db, "patient", patient.id)
            payload = {
                "access_token": token,
                "token_type": "bearer",
                "patient_id": patient.id,
                "expires_at": credential.expires_at.isoformat(),
            }
            db.commit()
    except SQLAlchemyError:
        # Database exceptions can contain connection details or SQL parameters.
        print("Database operation failed; check server configuration and schema privately.",
              file=sys.stderr)
        return 1

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
