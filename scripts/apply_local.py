"""
Apply the canonical setup_database.sql to a local Postgres cluster that
does not have pgvector installed. Strips VECTOR-typed columns and ivfflat
index DDL so the rest of the schema (partitioning, audit chain, BM25,
CHECK constraints, triggers, FKs, views) can be validated locally.

Usage:
    python scripts/apply_local.py [--dsn postgresql://...]

If --dsn is omitted, defaults to the local user-space cluster on port 5433.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_SQL = REPO_ROOT / "scripts" / "setup_database.sql"
SEED_SQL = REPO_ROOT / "scripts" / "seed_reference_data.sql"

# Default to the user-space PG16 cluster used during local audit work.
DEFAULT_PSQL = Path(os.environ.get("USERPROFILE", "")) / "pg16" / "pgsql" / "bin" / "psql.exe"
DEFAULT_DSN = "host=localhost port=5433 user=chatbot dbname=ignition_chatbot"


def strip_pgvector(sql: str) -> str:
    """Remove pgvector-dependent DDL so the rest applies cleanly."""
    # 1. CREATE EXTENSION vector -> comment
    sql = re.sub(
        r"^\s*CREATE\s+EXTENSION\s+IF\s+NOT\s+EXISTS\s+vector\s*;\s*$",
        "-- [local-apply] skipped CREATE EXTENSION vector",
        sql, flags=re.I | re.M,
    )
    # 2. VECTOR(N) column type -> BYTEA placeholder (preserves column for FK/PK shape)
    sql = re.sub(r"VECTOR\(\s*\d+\s*\)", "BYTEA /* was VECTOR */", sql, flags=re.I)
    # 3. CREATE INDEX ... USING ivfflat ... ; (possibly multi-line) -> comment
    sql = re.sub(
        r"CREATE\s+INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\w+\s+ON\s+\w+\s+USING\s+ivfflat[^;]*;",
        "-- [local-apply] skipped ivfflat index",
        sql, flags=re.I | re.S,
    )
    # 3b. Two-line form where the index name line is separate from USING ivfflat
    sql = re.sub(
        r"CREATE\s+INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\w+\s*\n\s*ON\s+\w+\s+USING\s+ivfflat[^;]*;",
        "-- [local-apply] skipped ivfflat index (multiline)",
        sql, flags=re.I,
    )
    return sql


def run_psql(dsn: str, sql: str, label: str, psql_exe: Path) -> int:
    print(f"--- applying: {label}")
    # Strip UTF-8 BOM if present and pass bytes to avoid Windows cp1252 encoding error.
    if sql.startswith("\ufeff"):
        sql = sql.lstrip("\ufeff")
    proc = subprocess.run(
        [str(psql_exe), "-v", "ON_ERROR_STOP=1", "-d", dsn, "-f", "-"],
        input=sql.encode("utf-8"),
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout.decode("utf-8", errors="replace"))
    if proc.returncode != 0:
        print(f"[FAIL] {label} exit={proc.returncode}", file=sys.stderr)
        print(proc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--psql", default=str(DEFAULT_PSQL))
    parser.add_argument("--no-seed", action="store_true")
    args = parser.parse_args()

    psql_exe = Path(args.psql)
    if not psql_exe.exists():
        print(f"psql not found: {psql_exe}", file=sys.stderr)
        return 2

    setup_sql = strip_pgvector(SETUP_SQL.read_text(encoding="utf-8"))
    rc = run_psql(args.dsn, setup_sql, "setup_database.sql (pgvector-stripped)", psql_exe)
    if rc != 0:
        return rc

    if not args.no_seed and SEED_SQL.exists():
        rc = run_psql(args.dsn, SEED_SQL.read_text(encoding="utf-8"), "seed_reference_data.sql", psql_exe)
        if rc != 0:
            return rc

    # Sanity counts
    count_sql = """
    SELECT 'tables' AS k, count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'
    UNION ALL SELECT 'partitioned', count(*) FROM pg_partitioned_table
    UNION ALL SELECT 'views', count(*) FROM information_schema.views WHERE table_schema='public'
    UNION ALL SELECT 'matviews', count(*) FROM pg_matviews WHERE schemaname='public'
    UNION ALL SELECT 'check_constraints', count(*) FROM information_schema.check_constraints
        WHERE constraint_schema='public'
    UNION ALL SELECT 'failure_modes_seeded', count(*) FROM failure_modes;
    """
    return run_psql(args.dsn, count_sql, "post-apply counts", psql_exe)


if __name__ == "__main__":
    sys.exit(main())
