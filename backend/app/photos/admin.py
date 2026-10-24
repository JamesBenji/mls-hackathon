"""Server-side provisioning. Never run the destructive demo seed on an existing DB."""

import argparse
import json

from sqlalchemy import inspect

from app import models
from app.database import Base, SessionLocal, engine
from app.photos.auth import issue_credential
from app.photos.models import PHOTO_TABLES, AccessCredential


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "init-schema", help="Create missing tables only, never drop/alter existing tables"
    )
    doctor = commands.add_parser(
        "issue-doctor", help="Print a new bearer secret ONCE; store securely"
    )
    doctor.add_argument("doctor_id", type=int)
    revoke = commands.add_parser("revoke", help="Revoke a credential by its non-secret database ID")
    revoke.add_argument("credential_id", type=int)
    args = parser.parse_args()
    if args.command == "init-schema":
        # create_all is additive only. It does not migrate existing clinical columns.
        Base.metadata.create_all(engine, checkfirst=True)
        actual = set(inspect(engine).get_table_names())
        assert all(table.name in actual for table in PHOTO_TABLES)
        print("Missing tables created; existing tables and data preserved.")
        return
    with SessionLocal() as db:
        if args.command == "issue-doctor":
            if db.get(models.Doctor, args.doctor_id) is None:
                parser.error("Doctor does not exist; provision an existing clinical doctor first")
            token, credential = issue_credential(db, "doctor", args.doctor_id)
            db.commit()
            print(
                json.dumps(
                    {
                        "access_token": token,
                        "credential_id": credential.id,
                        "expires_at": credential.expires_at.isoformat(),
                    }
                )
            )
        else:
            credential = db.get(AccessCredential, args.credential_id)
            if credential is None:
                parser.error("Credential does not exist")
            credential.revoked = True
            db.commit()
            print("Credential revoked.")


if __name__ == "__main__":
    main()
