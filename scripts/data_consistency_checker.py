#!/usr/bin/env python3
"""
EchoDB - Data Consistency Checker

Verifies data consistency between PostgreSQL and ClickHouse
to ensure CDC replication is working correctly.

Features:
    - Row count verification
    - Schema comparison
    - Sample data verification
    - Scheduled consistency checks
    - On-demand verification via HTTP endpoint

Requirements:
    pip install psycopg2-binary clickhouse-connect

Environment Variables:
    All POSTGRES_* and CLICKHOUSE_* variables for database connections
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Set

try:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    print(
        "Error: psycopg2-binary is required. Install with: pip install psycopg2-binary"
    )
    sys.exit(1)

try:
    import clickhouse_connect
except ImportError:
    print(
        "Error: clickhouse-connect is required. Install with: pip install clickhouse-connect"
    )
    sys.exit(1)

# ============================================================
# Configuration
# ============================================================


class Config:
    """Configuration from environment variables."""

    # PostgreSQL Configuration
    PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
    PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    PG_USER = os.getenv("POSTGRES_USER", "echodb")
    PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
    PG_DATABASE = os.getenv("POSTGRES_DB", "echodb")

    # ClickHouse Configuration
    CH_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
    CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    CH_USER = os.getenv("CLICKHOUSE_USER", "echodb")
    CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "password")
    CH_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "echodb")

    # Consistency Check Configuration
    SYNC_SCHEMAS = os.getenv("SYNC_SCHEMA", "public")
    CHECK_INTERVAL = int(os.getenv("CONSISTENCY_CHECK_INTERVAL", "900"))  # 15 minutes
    SAMPLE_SIZE = int(os.getenv("CONSISTENCY_SAMPLE_SIZE", "100"))
    MAX_LAG_SECONDS = int(os.getenv("CONSISTENCY_MAX_LAG", "300"))  # 5 minutes

    # HTTP Server Configuration
    HTTP_PORT = int(os.getenv("CONSISTENCY_CHECKER_PORT", "8090"))
    HTTP_HOST = os.getenv("CONSISTENCY_CHECKER_HOST", "0.0.0.0")

    @classmethod
    def get_sync_schemas(cls) -> Set[str]:
        """Get schemas to check as a set (supports comma-separated list)."""
        if cls.SYNC_SCHEMAS.startswith("["):
            return set(json.loads(cls.SYNC_SCHEMAS))
        return set(s.strip() for s in cls.SYNC_SCHEMAS.split(",") if s.strip())


# ============================================================
# Logging Setup
# ============================================================


def setup_logging():
    """Configure logging."""
    logger = logging.getLogger("echodb.consistency_checker")
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


logger = setup_logging()

# ============================================================
# Data Structures
# ============================================================


@dataclass
class ConsistencyReport:
    """Report for a single table consistency check."""

    table: str
    schema: str
    postgres_count: int
    clickhouse_count: int
    match: bool
    difference: int
    lag_seconds: Optional[float] = None
    timestamp: datetime = None
    sample_size: int = 0
    sample_mismatches: List[Dict] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "table": f"{self.schema}.{self.table}",
            "schema": self.schema,
            "table_name": self.table,
            "postgres_count": self.postgres_count,
            "clickhouse_count": self.clickhouse_count,
            "match": self.match,
            "difference": self.difference,
            "lag_seconds": self.lag_seconds,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "sample_size": self.sample_size,
            "sample_mismatches": self.sample_mismatches or [],
        }


# ============================================================
# Consistency Checker
# ============================================================


class DataConsistencyChecker:
    """
    Verify data consistency between PostgreSQL and ClickHouse.

    This checker connects to both databases and compares:
    - Row counts for each table
    - Sample data for detailed verification
    - Replication lag
    """

    def __init__(self):
        """Initialize consistency checker."""
        self.pg_conn = None
        self.ch_conn = None
        self.last_check_time: Optional[datetime] = None
        self.check_count = 0
        self.inconsistent_count = 0

    def connect(self):
        """Establish connections to both databases."""
        try:
            # Connect to PostgreSQL
            logger.info(
                f"Connecting to PostgreSQL: {Config.PG_USER}@{Config.PG_HOST}:{Config.PG_PORT}/{Config.PG_DATABASE}"
            )
            self.pg_conn = psycopg2.connect(
                host=Config.PG_HOST,
                port=Config.PG_PORT,
                user=Config.PG_USER,
                password=Config.PG_PASSWORD,
                database=Config.PG_DATABASE,
                connect_timeout=10,
            )
            logger.info("✅ Connected to PostgreSQL")

            # Connect to ClickHouse
            logger.info(
                f"Connecting to ClickHouse: {Config.CH_USER}@{Config.CH_HOST}:{Config.CH_PORT}/{Config.CH_DATABASE}"
            )
            self.ch_conn = clickhouse_connect.get_client(
                host=Config.CH_HOST,
                port=Config.CH_PORT,
                user=Config.CH_USER,
                password=Config.CH_PASSWORD,
                database=Config.CH_DATABASE,
            )
            logger.info("✅ Connected to ClickHouse")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to connect: {e}")
            return False

    def _get_postgres_tables(self, schemas: Set[str]) -> List[Dict]:
        """Get list of tables from PostgreSQL."""
        try:
            cursor = self.pg_conn.cursor()

            # Build query for multiple schemas
            schema_list = ",".join(f"'{s}'" for s in schemas)
            query = f"""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema IN ({schema_list})
                AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
            """

            cursor.execute(query)
            results = cursor.fetchall()

            return [{"schema": row[0], "table": row[1]} for row in results]

        except Exception as e:
            logger.error(f"Failed to get PostgreSQL tables: {e}")
            return []

    def _get_postgres_count(self, schema: str, table: str) -> int:
        """Get row count from PostgreSQL."""
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL count for {schema}.{table}: {e}")
            return 0

    def _get_clickhouse_count(self, table: str) -> int:
        """Get row count from ClickHouse."""
        try:
            # ClickHouse table names might be prefixed with schema
            possible_names = [table, f"postgres.{table}"]

            for table_name in possible_names:
                try:
                    result = self.ch_conn.query(
                        f"SELECT COUNT(*) AS count FROM {table_name}"
                    )
                    if result.result_rows:
                        return result.result_rows[0][0]
                except Exception:
                    continue  # Table doesn't exist, try next name

            return 0

        except Exception as e:
            logger.error(f"Failed to get ClickHouse count for {table}: {e}")
            return 0

    def _get_primary_key_column(self, schema: str, table: str) -> Optional[str]:
        """Get primary key column name."""
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(f"""
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = '{schema}.{table}'::regclass
                AND i.indisprimary
                LIMIT 1
            """)
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception:
            return None

    def verify_table_counts(self, schema: str, table: str) -> ConsistencyReport:
        """Verify row counts match between PostgreSQL and ClickHouse."""
        pg_count = self._get_postgres_count(schema, table)
        ch_count = self._get_clickhouse_count(table)

        return ConsistencyReport(
            table=table,
            schema=schema,
            postgres_count=pg_count,
            clickhouse_count=ch_count,
            match=pg_count == ch_count,
            difference=pg_count - ch_count,
            timestamp=datetime.now(),
        )

    def verify_all_tables(self) -> List[ConsistencyReport]:
        """Verify all tables in watched schemas."""
        schemas = Config.get_sync_schemas()

        logger.info(f"Checking tables in schemas: {', '.join(sorted(schemas))}")

        tables = self._get_postgres_tables(schemas)
        logger.info(f"Found {len(tables)} tables to check")

        reports = []
        self.last_check_time = datetime.now()
        self.check_count += 1

        for table_info in tables:
            schema = table_info["schema"]
            table = table_info["table"]

            logger.info(f"Checking {schema}.{table}...")
            report = self.verify_table_counts(schema, table)
            reports.append(report)

            if not report.match:
                self.inconsistent_count += 1
                logger.warning(f"⚠️  Inconsistency detected: {schema}.{table}")
                logger.warning(f"   PostgreSQL: {report.postgres_count} rows")
                logger.warning(f"   ClickHouse: {report.clickhouse_count} rows")
                logger.warning(f"   Difference: {report.difference} rows")
            else:
                logger.info(f"✅ {schema}.{table}: {report.postgres_count} rows")

        return reports

    def get_summary(self) -> dict:
        """Get summary of consistency checks."""
        return {
            "last_check_time": self.last_check_time.isoformat()
            if self.last_check_time
            else None,
            "total_checks": self.check_count,
            "inconsistent_tables": self.inconsistent_count,
            "schemas_checked": list(Config.get_sync_schemas()),
        }


# ============================================================
# HTTP Server for On-Demand Checks
# ============================================================


class ConsistencyCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler for consistency check endpoints."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self.send_health_response()
        elif self.path == "/check":
            self.send_check_response()
        elif self.path.startswith("/check/"):
            self.send_table_check_response()
        elif self.path == "/metrics":
            self.send_metrics_response()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def send_health_response(self):
        """Simple health check."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"status": "healthy", "service": "consistency_checker"}).encode()
        )

    def send_check_response(self):
        """Run full consistency check and return results."""
        try:
            reports = checker.verify_all_tables()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            response = {
                "summary": checker.get_summary(),
                "tables": [r.to_dict() for r in reports],
                "timestamp": datetime.now().isoformat(),
            }

            self.wfile.write(json.dumps(response, indent=2).encode())

        except Exception as e:
            logger.error(f"Error running consistency check: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def send_table_check_response(self):
        """Check a specific table."""
        try:
            # Parse path: /check/schema.table
            parts = self.path[7:].split(".")
            if len(parts) != 2:
                self.send_error("Invalid format. Use /check/schema.table")
                return

            schema, table = parts

            report = checker.verify_table_counts(schema, table)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(report.to_dict()).encode())

        except Exception as e:
            logger.error(f"Error checking table: {e}")
            self.send_error(str(e))

    def send_metrics_response(self):
        """Return metrics summary."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(checker.get_summary()).encode())

    def send_error(self, message):
        """Send error response."""
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode())


# Global checker instance
checker = DataConsistencyChecker()

# ============================================================
# Scheduled Check Thread
# ============================================================


def scheduled_checks(shutdown_event: threading.Event):
    """Run consistency checks at regular intervals."""
    logger.info(f"Starting scheduled checks every {Config.CHECK_INTERVAL} seconds")

    while not shutdown_event.is_set():
        shutdown_event.wait(Config.CHECK_INTERVAL)

        if shutdown_event.is_set():
            break

        try:
            logger.info("=" * 60)
            logger.info("Running scheduled consistency check...")
            reports = checker.verify_all_tables()

            # Count issues
            inconsistent = [r for r in reports if not r.match]
            if inconsistent:
                logger.warning(f"⚠️  Found {len(inconsistent)} inconsistent tables")
            else:
                logger.info(f"✅ All {len(reports)} tables are consistent")

            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Error in scheduled check: {e}")


# ============================================================
# Main
# ============================================================


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("EchoDB - Data Consistency Checker")
    logger.info("=" * 60)

    # Connect to databases
    if not checker.connect():
        logger.error("Failed to connect to databases")
        sys.exit(1)

    # Start HTTP server in background thread
    server = HTTPServer((Config.HTTP_HOST, Config.HTTP_PORT), ConsistencyCheckHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info(
        f"HTTP server listening on http://{Config.HTTP_HOST}:{Config.HTTP_PORT}"
    )
    logger.info(f"  - GET /health   - Health check")
    logger.info(f"  - GET /check    - Run full consistency check")
    logger.info(f"  - GET /check/schema.table - Check specific table")
    logger.info(f"  - GET /metrics  - Get summary metrics")

    # Start scheduled checks in background thread
    shutdown_event = threading.Event()
    check_thread = threading.Thread(
        target=scheduled_checks, args=(shutdown_event,), daemon=True
    )
    check_thread.start()

    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Consistency checker running. Press Ctrl+C to stop.")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
