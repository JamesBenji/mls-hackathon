"""Deployment-only startup guard; never print credentials or connection URLs."""
import os
import sys
from urllib.parse import quote


def main():
    for name in ("GUARDIAN_API_KEY", "POSTGRES_PASSWORD"):
        value = os.environ.get(name, "")
        if len(value) < 32 or value != value.strip() or any(c.isspace() for c in value):
            sys.exit(f"{name} must be a random secret of at least 32 non-whitespace characters")
    password = quote(os.environ.pop("POSTGRES_PASSWORD"), safe="")
    os.environ["DATABASE_URL"] = f"postgresql+psycopg://guardian:{password}@postgres:5432/guardian"
    os.umask(0o077)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
