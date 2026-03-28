"""
Run standalone: python backend/db/init_db.py
Executes database/schema.sql against DATABASE_URL.
Idempotent — safe to run multiple times.
"""
import os
import sys

# Allow running from repo root or from backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "database", "schema.sql"
)


def init_db():
    from db.connection import db_cursor

    schema_path = os.path.abspath(SCHEMA_PATH)
    if not os.path.exists(schema_path):
        print(f"ERROR: schema file not found at {schema_path}")
        sys.exit(1)

    with open(schema_path, "r") as f:
        sql = f.read()

    try:
        with db_cursor() as cur:
            cur.execute(sql)
        print("SUCCESS: Database schema initialised (all tables created or already exist).")
    except Exception as e:
        print(f"ERROR: Failed to initialise database: {e}")
        sys.exit(1)

    # Seed capital account (idempotent — does nothing if already seeded)
    try:
        from portfolio.capital_account import initialise
        initialise()
        total = float(os.getenv("TOTAL_CAPITAL", 0))
        print(f"SUCCESS: Capital account ready — ₹{total:,.2f} available.")
    except Exception as e:
        print(f"WARNING: Capital account seed failed: {e}")


if __name__ == "__main__":
    init_db()
