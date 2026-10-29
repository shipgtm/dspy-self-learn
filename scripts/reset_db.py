"""Wipe all local data so the demo can start over.

Empties every table, restarts the completion counter, and deletes locally
saved program versions (.data/programs). Pass --workflows to also delete
local workflow run history (.workflow-data); stop `vercel dev` first.

    uv run scripts/reset_db.py
    uv run scripts/reset_db.py --yes --workflows
"""

import argparse
import os
import shutil
from pathlib import Path

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/emails")
ROOT = Path(__file__).resolve().parent.parent
PROGRAMS_DIR = ROOT / os.environ.get("DATA_DIR", ".data") / "programs"
WORKFLOW_DIR = ROOT / ".workflow-data"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--workflows", action="store_true", help="also delete .workflow-data")
    args = parser.parse_args()

    targets = [DATABASE_URL, str(PROGRAMS_DIR)] + ([str(WORKFLOW_DIR)] if args.workflows else [])
    if not args.yes:
        print("This permanently deletes:\n  " + "\n  ".join(targets))
        if input("Continue? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("TRUNCATE contacts, program_versions, optimization_runs RESTART IDENTITY")
        conn.execute("ALTER SEQUENCE completions RESTART")
    print("Emptied contacts, program_versions, optimization_runs; reset completions.")

    shutil.rmtree(PROGRAMS_DIR, ignore_errors=True)
    print(f"Deleted {PROGRAMS_DIR}")

    if args.workflows:
        shutil.rmtree(WORKFLOW_DIR, ignore_errors=True)
        print(f"Deleted {WORKFLOW_DIR}")


if __name__ == "__main__":
    main()
