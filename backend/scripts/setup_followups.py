"""Non-destructive schema preflight. Default is read-only; --apply creates missing tables.

Never drops tables, rewrites patient data, adds guessed defaults or provisions APIs.
"""
import argparse

from sqlalchemy import inspect

from app.database import Base, engine
from app.main import app  # noqa: F401 -- register existing and additive models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing_columns = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            print(f"Missing table: {table.name}")
            continue
        actual = {col["name"] for col in inspector.get_columns(table.name)}
        missing_columns.extend(f"{table.name}.{col.name}" for col in table.columns if col.name not in actual)
    if missing_columns:
        print("STOP: existing schema predates the colleague baseline. create_all cannot add columns:")
        print("\n".join(missing_columns))
        print("Back up the database and prepare reviewed additive ALTER TABLE statements before deployment.")
        return 1
    if args.apply:
        Base.metadata.create_all(engine)
        print("Missing tables created; existing tables and data unchanged.")
    else:
        print("Read-only preflight complete. Use --apply after backup to create missing tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
