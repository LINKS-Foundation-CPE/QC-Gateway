"""Database initialization and job logging functions.

This module handles the initialization of the PostgreSQL database and provides
functions for logging job submissions and their metadata. It ensures that the
required tables and columns exist and provides a simple interface for logging
job-related information.

Note for new developers:
- This module uses PostgreSQL for storing job metadata.
- The `init_db` function ensures the database and tables are properly initialized.
- The `log_job` function is used to log job submissions and their details.
"""

from datetime import datetime

import psycopg2

try:
    from middleware.config import Settings

    settings = Settings()
except Exception:
    # If Settings cannot be imported/instantiated (e.g., during tests or import in other contexts),
    # provide a minimal fallback with LOG_LEVEL defaulting to INFO.
    class _FallbackSettings:
        LOG_LEVEL = "INFO"

    settings = _FallbackSettings()
import logging
import os

logging.basicConfig(
    level=getattr(logging, getattr(settings, "LOG_LEVEL", "INFO").upper(), logging.INFO)
)
logger = logging.getLogger(__name__)


def init_db():
    conn = psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "nginxproxy"),
        user=os.getenv("POSTGRES_USER", "nginxuser"),
        password=os.getenv("POSTGRES_PASSWORD", "nginxpass"),
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
    )
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS jobs (
        jobid TEXT PRIMARY KEY,
        project_name TEXT,
        username TEXT,
        status TEXT,
        execution_start TEXT,
        execution_end TEXT,
        submitted_datetime TEXT,
        submitted_circuit TEXT,
        results TEXT,
        shots INTEGER DEFAULT 0,
        circuits_count INTEGER DEFAULT 0,
        timestamp TIMESTAMP NOT NULL
    )""")
    conn.commit()
    # Ensure columns exist for older databases (safe ALTER)
    try:
        # Add shots column if it doesn't exist
        c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS shots INTEGER DEFAULT 0")
        c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS circuits_count INTEGER DEFAULT 0")
        # Add job_type column if missing
        c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_type TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        # If ALTER fails for any reason, log and continue; table may already have the columns
        logger.exception("Failed to ensure shots/circuits_count columns exist")
    return conn


# New logging function matching /jobReport fields


def log_job(
    jobid: str,
    project_name: str | None,
    username: str,
    status: str = "submitted",
    execution_start: str = "",
    execution_end: str = "",
    submitted_datetime: str = "",
    submitted_circuit: str = "",
    results: str = "",
    job_type: str = "",
    shots: int = 0,
    circuits_count: int = 0,
):
    """Insert a job row into the `jobs` table.

    This function opens a short-lived DB connection per call to avoid
    import-time side effects and to be robust in long-running processes
    or during tests.
    """
    conn = None
    cur = None
    try:
        conn = init_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO jobs
               (jobid, project_name, username, status, execution_start, execution_end, submitted_datetime, submitted_circuit, results, job_type, shots, circuits_count, timestamp)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                jobid,
                project_name,
                username,
                status,
                execution_start,
                execution_end,
                submitted_datetime,
                submitted_circuit,
                results,
                job_type,
                shots,
                circuits_count,
                datetime.utcnow(),
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to log job to database")
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
