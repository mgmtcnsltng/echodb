#!/usr/bin/env python3
"""
EchoDB - Auto-Mirror Worker (Production-Ready)

Listens for PostgreSQL table creation events and automatically creates
PeerDB mirrors to sync them to ClickHouse.

Features:
    - Retry logic with exponential backoff
    - Connection resilience with auto-reconnect
    - Health check endpoint
    - Structured logging to file and stdout
    - Graceful shutdown handling

Requirements:
    pip install psycopg2-binary

Environment Variables:
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
    PEERDB_HOST, PEERDB_PORT, PEERDB_USER, PEERDB_PASSWORD
    SOURCE_PEER_NAME, TARGET_PEER_NAME, SYNC_SCHEMA, EXCLUDED_TABLES
    LOG_LEVEL, HEALTH_CHECK_PORT
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

# Try to import http.server for health checks (always available in Python 3)
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from typing import Optional, Set

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Import leader election module
try:
    from leader_election import LeaderElection
except ImportError:
    LeaderElection = None
    logger = logging.getLogger("echodb-auto-mirror")
    logger.warning("Leader election module not available. HA features disabled.")

# Import circuit breaker module
try:
    from circuit_breaker import (
        CircuitBreaker,
        CircuitBreakerConfig,
        CircuitBreakerOpenError,
    )
except ImportError:
    CircuitBreaker = None
    CircuitBreakerConfig = None
    CircuitBreakerOpenError = None
    if logger:
        logger.warning(
            "Circuit breaker module not available. Resilience features disabled."
        )

# ============================================================
# Configuration (with environment variable support)
# ============================================================


class Config:
    """Configuration container with environment variable support."""

    # PostgreSQL Configuration
    PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
    PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    PG_USER = os.getenv("POSTGRES_USER", "echodb")
    PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
    PG_DATABASE = os.getenv("POSTGRES_DB", "echodb")

    # PeerDB Configuration
    PEERDB_HOST = os.getenv("PEERDB_HOST", "localhost")
    PEERDB_PORT = int(os.getenv("PEERDB_PORT", "9900"))
    PEERDB_USER = os.getenv("PEERDB_USER", "echodb")
    PEERDB_PASSWORD = os.getenv("PEERDB_PASSWORD", "password")

    # Mirror Configuration
    SOURCE_PEER_NAME = os.getenv("SOURCE_PEER_NAME", "postgres_main")
    TARGET_PEER_NAME = os.getenv("TARGET_PEER_NAME", "clickhouse_analytics")
    SCHEMA_NAME = os.getenv("SYNC_SCHEMA", "public")

    # Retry Configuration
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
    RETRY_DELAY = int(os.getenv("RETRY_DELAY", "5"))  # seconds
    RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "2.0"))  # multiplier

    # Reconnect Configuration
    RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "10"))  # seconds
    MAX_RECONNECT_ATTEMPTS = int(os.getenv("MAX_RECONNECT_ATTEMPTS", "10"))

    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE = os.getenv("LOG_FILE", "/var/log/echodb/auto-mirror.log")
    LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

    # Health Check Configuration
    HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "8080"))
    HEALTH_CHECK_HOST = os.getenv("HEALTH_CHECK_HOST", "0.0.0.0")

    # Tables to exclude from auto-mirroring
    EXCLUDED_TABLES_STR = os.getenv(
        "EXCLUDED_TABLES",
        "spatial_ref_sys,geometry_columns,geography_columns,raster_columns,raster_overviews",
    )

    @classmethod
    def get_sync_schemas(cls) -> Set[str]:
        """Get schemas to sync as a set (supports comma-separated list)."""
        if cls.SCHEMA_NAME.startswith("["):
            return set(json.loads(cls.SCHEMA_NAME))
        return set(s.strip() for s in cls.SCHEMA_NAME.split(",") if s.strip())

    # Leader Election Configuration
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
    LEADER_ELECTION_TTL = int(os.getenv("LEADER_ELECTION_TTL", "30"))  # seconds
    LEADER_ELECTION_INTERVAL = int(
        os.getenv("LEADER_ELECTION_INTERVAL", "10")
    )  # seconds
    WORKER_ID = os.getenv("WORKER_ID", None)

    # Circuit Breaker Configuration
    PEERDB_FAILURE_THRESHOLD = int(os.getenv("PEERDB_FAILURE_THRESHOLD", "5"))
    PEERDB_SUCCESS_THRESHOLD = int(os.getenv("PEERDB_SUCCESS_THRESHOLD", "2"))
    PEERDB_TIMEOUT = int(os.getenv("PEERDB_TIMEOUT", "60"))  # seconds
    POSTGRES_FAILURE_THRESHOLD = int(os.getenv("POSTGRES_FAILURE_THRESHOLD", "3"))
    POSTGRES_SUCCESS_THRESHOLD = int(os.getenv("POSTGRES_SUCCESS_THRESHOLD", "2"))
    POSTGRES_TIMEOUT = int(os.getenv("POSTGRES_TIMEOUT", "30"))  # seconds

    @classmethod
    def get_excluded_tables(cls) -> Set[str]:
        """Get excluded tables as a set."""
        if cls.EXCLUDED_TABLES_STR.startswith("["):
            return set(json.loads(cls.EXCLUDED_TABLES_STR))
        return set(t.strip() for t in cls.EXCLUDED_TABLES_STR.split(",") if t.strip())


# ============================================================
# Logging Setup
# ============================================================


def setup_logging():
    """Configure logging with file and console handlers."""
    # Create logger
    logger = logging.getLogger("echodb-auto-mirror")
    logger.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))

    # Remove existing handlers
    logger.handlers.clear()

    # Create formatters
    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    simple_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)

    # File handler (with rotation)
    try:
        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(Config.LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            Config.LOG_FILE,
            maxBytes=Config.LOG_MAX_BYTES,
            backupCount=Config.LOG_BACKUP_COUNT,
        )
        file_handler.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    except (IOError, OSError) as e:
        logger.warning(f"Could not setup file logging: {e}")

    return logger


logger = setup_logging()


# ============================================================
# State Management for Health Checks
# ============================================================


class WorkerState:
    """Thread-safe state management for health checks."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._last_notification: Optional[datetime] = None
        self._mirrors_created = 0
        self._mirrors_failed = 0
        self._last_error: Optional[str] = None

        # Leader election state
        self._leader_election: Optional[LeaderElection] = None
        self._worker_id: Optional[str] = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @running.setter
    def running(self, value: bool):
        with self._lock:
            self._running = value

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @connected.setter
    def connected(self, value: bool):
        with self._lock:
            self._connected = value

    @property
    def last_notification(self) -> Optional[datetime]:
        with self._lock:
            return self._last_notification

    @last_notification.setter
    def last_notification(self, value: Optional[datetime]):
        with self._lock:
            self._last_notification = value

    def increment_mirrors_created(self):
        with self._lock:
            self._mirrors_created += 1

    def increment_mirrors_failed(self):
        with self._lock:
            self._mirrors_failed += 1

    def set_error(self, error: str):
        with self._lock:
            self._last_error = error

    def get_stats(self) -> dict:
        """Get current statistics."""
        with self._lock:
            stats = {
                "running": self._running,
                "connected": self._connected,
                "last_notification": self._last_notification.isoformat()
                if self._last_notification
                else None,
                "mirrors_created": self._mirrors_created,
                "mirrors_failed": self._mirrors_failed,
                "last_error": self._last_error,
            }

            # Add leader election info if available
            if self._leader_election:
                stats["is_leader"] = self._leader_election.is_leader
                stats["worker_id"] = self._worker_id
            else:
                stats["is_leader"] = True  # Single instance mode
                stats["worker_id"] = "single-instance"

            return stats

    @property
    def is_leader(self) -> bool:
        """Check if this worker is the leader."""
        with self._lock:
            if self._leader_election:
                return self._leader_election.is_leader
            return True  # Single instance mode is always "leader"

    @property
    def worker_id(self) -> str:
        """Get this worker's ID."""
        with self._lock:
            return self._worker_id or "single-instance"


state = WorkerState()

# Initialize circuit breakers if available
peerdb_api_breaker = None
postgres_connection_breaker = None

if CircuitBreaker and CircuitBreakerConfig:
    peerdb_api_breaker = CircuitBreaker(
        CircuitBreakerConfig(
            name="peerdb_api",
            failure_threshold=Config.PEERDB_FAILURE_THRESHOLD,
            success_threshold=Config.PEERDB_SUCCESS_THRESHOLD,
            timeout=Config.PEERDB_TIMEOUT,
        )
    )

    postgres_connection_breaker = CircuitBreaker(
        CircuitBreakerConfig(
            name="postgres_connection",
            failure_threshold=Config.POSTGRES_FAILURE_THRESHOLD,
            success_threshold=Config.POSTGRES_SUCCESS_THRESHOLD,
            timeout=Config.POSTGRES_TIMEOUT,
        )
    )

    if logger:
        logger.info(
            "Circuit breakers initialized for PeerDB API and PostgreSQL connection"
        )


# ============================================================
# Health Check Server
# ============================================================


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check endpoints."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self.send_health_response()
        elif self.path == "/ready":
            self.send_ready_response()
        elif self.path == "/metrics":
            self.send_metrics_response()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def send_health_response(self):
        """Simple health check - always returns 200 if running."""
        self.send_response(200 if state.running else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        stats = state.get_stats()
        self.wfile.write(
            json.dumps({"status": "healthy" if state.running else "unhealthy"}).encode()
        )

    def send_ready_response(self):
        """Readiness check - returns 200 if connected to PostgreSQL."""
        ready = state.running and state.connected
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        stats = state.get_stats()
        self.wfile.write(
            json.dumps(
                {
                    "status": "ready" if ready else "not_ready",
                    "connected": stats["connected"],
                }
            ).encode()
        )

    def send_metrics_response(self):
        """Metrics endpoint with detailed stats including circuit breaker state."""
        self.send_response(200 if state.running else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        stats = state.get_stats()

        # Add circuit breaker status if available
        if peerdb_api_breaker and postgres_connection_breaker:
            stats["circuit_breakers"] = {
                "peerdb_api": peerdb_api_breaker.get_status(),
                "postgres_connection": postgres_connection_breaker.get_status(),
            }

        self.wfile.write(json.dumps(stats).encode())


def start_health_check_server():
    """Start HTTP server for health checks in a separate thread."""
    try:
        server = HTTPServer(
            (Config.HEALTH_CHECK_HOST, Config.HEALTH_CHECK_PORT), HealthCheckHandler
        )
        logger.info(
            f"Health check server listening on http://{Config.HEALTH_CHECK_HOST}:{Config.HEALTH_CHECK_PORT}"
        )
        logger.info(f"  - /health  - Simple health check")
        logger.info(f"  - /ready   - Readiness check")
        logger.info(f"  - /metrics - Detailed metrics")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start health check server: {e}")


# ============================================================
# Mirror Creation with Retry Logic and Circuit Breaker
# ============================================================


def _create_mirror_internal(schema: str, table: str) -> bool:
    """Internal mirror creation without circuit breaker (called by circuit breaker)."""
    mirror_name = f"{table}_mirror"

    # Build the CREATE MIRROR SQL
    sql = f"""CREATE MIRROR {mirror_name} FROM {Config.SOURCE_PEER_NAME} TO {Config.TARGET_PEER_NAME}
WITH TABLE MAPPING ({schema}.{table}:{table})
WITH (do_initial_copy = true);"""

    retry_count = 0
    delay = Config.RETRY_DELAY

    while retry_count <= Config.MAX_RETRIES:
        try:
            logger.info(
                f"Creating mirror for {schema}.{table} (attempt {retry_count + 1}/{Config.MAX_RETRIES + 1})"
            )

            result = subprocess.run(
                [
                    "psql",
                    "-h",
                    Config.PEERDB_HOST,
                    "-p",
                    str(Config.PEERDB_PORT),
                    "-U",
                    Config.PEERDB_USER,
                    "-c",
                    sql,
                ],
                env={**os.environ, "PGPASSWORD": Config.PEERDB_PASSWORD},
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                logger.info(f"✅ Mirror created successfully: {mirror_name}")
                state.increment_mirrors_created()
                state.set_error(None)
                return True
            else:
                # Check if mirror already exists
                if "already exists" in result.stderr:
                    logger.info(f"ℹ️  Mirror already exists: {mirror_name}")
                    return True
                else:
                    error_msg = result.stderr.strip()
                    logger.warning(f"Attempt {retry_count + 1} failed: {error_msg}")
                    # Raise exception for circuit breaker to track
                    raise Exception(f"Mirror creation failed: {error_msg}")

        except subprocess.TimeoutExpired:
            logger.warning(f"Attempt {retry_count + 1} timed out")
            raise Exception(f"Timeout creating mirror for {table}")

        except Exception as e:
            # Don't log here, let circuit breaker handle it
            if "Mirror creation failed" not in str(e) and "Timeout" not in str(e):
                logger.warning(f"Attempt {retry_count + 1} raised exception: {e}")
            raise

        # Exponential backoff before retry
        retry_count += 1
        if retry_count <= Config.MAX_RETRIES:
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= Config.RETRY_BACKOFF

    # All retries failed
    state.increment_mirrors_failed()
    state.set_error(f"Failed to create mirror after {Config.MAX_RETRIES + 1} attempts")
    return False


def create_peerdb_mirror_with_retry(schema: str, table: str) -> bool:
    """Create a PeerDB mirror with exponential backoff retry logic and circuit breaker protection."""
    if peerdb_api_breaker:
        try:
            return peerdb_api_breaker.call(_create_mirror_internal, schema, table)
        except CircuitBreakerOpenError:
            logger.error(f"❌ Circuit breaker OPEN - PeerDB API unavailable")
            state.set_error("Circuit breaker open - PeerDB API unavailable")
            return False
    else:
        # No circuit breaker, call directly
        return _create_mirror_internal(schema, table)


# ============================================================
# Mirror Deletion with Retry Logic and Circuit Breaker
# ============================================================


def _drop_mirror_internal(schema: str, table: str) -> bool:
    """Internal mirror deletion without circuit breaker (called by circuit breaker)."""
    mirror_name = f"{table}_mirror"

    # Build the DROP MIRROR SQL
    sql = f"DROP MIRROR {mirror_name};"

    retry_count = 0
    delay = Config.RETRY_DELAY

    while retry_count <= Config.MAX_RETRIES:
        try:
            logger.info(
                f"Dropping mirror for {schema}.{table} (attempt {retry_count + 1}/{Config.MAX_RETRIES + 1})"
            )

            result = subprocess.run(
                [
                    "psql",
                    "-h",
                    Config.PEERDB_HOST,
                    "-p",
                    str(Config.PEERDB_PORT),
                    "-U",
                    Config.PEERDB_USER,
                    "-c",
                    sql,
                ],
                env={**os.environ, "PGPASSWORD": Config.PEERDB_PASSWORD},
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                logger.info(f"✅ Mirror dropped successfully: {mirror_name}")
                state.set_error(None)
                return True
            else:
                # Check if mirror doesn't exist (not an error)
                if "does not exist" in result.stderr or "must acquire" in result.stderr:
                    logger.info(
                        f"ℹ️  Mirror does not exist or cannot be dropped: {mirror_name}"
                    )
                    return True
                else:
                    error_msg = result.stderr.strip()
                    logger.warning(f"Attempt {retry_count + 1} failed: {error_msg}")
                    # Raise exception for circuit breaker to track
                    raise Exception(f"Mirror drop failed: {error_msg}")

        except subprocess.TimeoutExpired:
            logger.warning(f"Attempt {retry_count + 1} timed out")
            raise Exception(f"Timeout dropping mirror for {table}")

        except Exception as e:
            # Don't log here, let circuit breaker handle it
            if "Mirror drop failed" not in str(e) and "Timeout" not in str(e):
                logger.warning(f"Attempt {retry_count + 1} raised exception: {e}")
            raise

        # Exponential backoff before retry
        retry_count += 1
        if retry_count <= Config.MAX_RETRIES:
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= Config.RETRY_BACKOFF

    # All retries failed
    state.set_error(f"Failed to drop mirror after {Config.MAX_RETRIES + 1} attempts")
    return False


def drop_peerdb_mirror_with_retry(schema: str, table: str) -> bool:
    """Drop a PeerDB mirror with exponential backoff retry logic and circuit breaker protection."""
    if peerdb_api_breaker:
        try:
            return peerdb_api_breaker.call(_drop_mirror_internal, schema, table)
        except CircuitBreakerOpenError:
            logger.error(f"❌ Circuit breaker OPEN - PeerDB API unavailable")
            state.set_error("Circuit breaker open - PeerDB API unavailable")
            return True  # Not critical if mirror drop fails
    else:
        # No circuit breaker, call directly
        return _drop_mirror_internal(schema, table)


# ============================================================
# PostgreSQL Listener with Reconnect Logic
# ============================================================


def get_redis_client():
    """Get or create Redis client for duplicate detection."""
    try:
        import redis

        # Build Redis connection parameters
        redis_params = {
            "host": Config.REDIS_HOST,
            "port": Config.REDIS_PORT,
            "decode_responses": True,
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
        }

        # Only add password if it's not empty
        if Config.REDIS_PASSWORD:
            redis_params["password"] = Config.REDIS_PASSWORD

        return redis.Redis(**redis_params)
    except ImportError:
        logger.warning("Redis not available, duplicate detection disabled")
        return None
    except Exception as e:
        logger.error(f"Failed to create Redis client: {e}")
        return None


_redis_client = None


def is_duplicate_notification(notification_id: str) -> bool:
    """Check if notification has already been processed."""
    global _redis_client

    if _redis_client is None:
        _redis_client = get_redis_client()
        if _redis_client is None:
            return False  # No Redis, can't check for duplicates

    try:
        # Check if key exists
        return _redis_client.exists(f"notification:{notification_id}") == 1
    except Exception as e:
        logger.warning(f"Redis error checking duplicate: {e}")
        return False


def mark_notification_processing(notification_id: str):
    """Mark notification as currently being processed."""
    global _redis_client

    if _redis_client is None:
        _redis_client = get_redis_client()
        if _redis_client is None:
            return  # No Redis, skip marking

    try:
        # Set with expiry of 5 minutes (in case processing fails)
        _redis_client.setex(
            f"notification:{notification_id}",
            300,  # 5 minutes
            "processing",
        )
    except Exception as e:
        logger.warning(f"Redis error marking processing: {e}")


def mark_notification_processed(notification_id: str):
    """Mark notification as processed with longer expiry."""
    global _redis_client

    if _redis_client is None:
        _redis_client = get_redis_client()
        if _redis_client is None:
            return  # No Redis, skip marking

    try:
        # Set with expiry of 24 hours
        _redis_client.setex(
            f"notification:{notification_id}",
            86400,  # 24 hours
            "processed",
        )
    except Exception as e:
        logger.warning(f"Redis error marking processed: {e}")


def verify_mirror_consistency(schema: str, table: str) -> bool:
    """Verify data consistency after mirror creation with retry logic."""
    logger.info(f"🔍 Verifying consistency for {schema}.{table}...")

    # Wait briefly for initial replication
    time.sleep(2)

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # Get row counts
            pg_count = _get_postgres_count(schema, table)
            ch_count = _get_clickhouse_count(table)

            if pg_count == ch_count:
                logger.info(
                    f"✅ Consistency verified: {schema}.{table} ({pg_count} rows)"
                )
                return True
            else:
                difference = pg_count - ch_count
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"⚠️  Consistency check failed (attempt {attempt + 1}/{max_attempts}): "
                        f"PG={pg_count}, CH={ch_count}, diff={difference}. Retrying in 10s..."
                    )
                    time.sleep(10)
                    continue
                else:
                    logger.error(
                        f"❌ Consistency check failed after {max_attempts} attempts: "
                        f"{schema}.{table} - PG={pg_count}, CH={ch_count}, diff={difference}"
                    )
                    state.set_error(f"Data consistency issue: {schema}.{table}")
                    return False

        except Exception as e:
            logger.error(f"Error checking consistency for {schema}.{table}: {e}")
            if attempt < max_attempts - 1:
                time.sleep(5)
                continue
            else:
                logger.error(
                    f"❌ Failed to verify consistency after {max_attempts} attempts"
                )
                return False

    return True


def _get_postgres_count(schema: str, table: str) -> int:
    """Get row count from PostgreSQL."""
    try:
        conn = psycopg2.connect(
            host=Config.PG_HOST,
            port=Config.PG_PORT,
            user=Config.PG_USER,
            password=Config.PG_PASSWORD,
            database=Config.PG_DATABASE,
            connect_timeout=5,
        )
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except Exception as e:
        logger.error(f"Failed to get PostgreSQL count: {e}")
        return 0


def _get_clickhouse_count(table: str) -> int:
    """Get row count from ClickHouse."""
    try:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=Config.CLICKHOUSE_HOST,
            port=Config.CLICKHOUSE_PORT,
            user=Config.CLICKHOUSE_USER,
            password=Config.CLICKHOUSE_PASSWORD,
        )

        # Try different table name formats
        for table_name in [table, f"postgres.{table}"]:
            try:
                result = client.query(f"SELECT COUNT(*) AS count FROM {table_name}")
                if result.result_rows:
                    client.close()
                    return result.result_rows[0][0]
            except:
                continue

        client.close()
        return 0
    except Exception as e:
        logger.error(f"Failed to get ClickHouse count: {e}")
        return 0


def connect_to_postgresql():
    """Connect to PostgreSQL with retry logic."""
    attempt = 0

    while attempt < Config.MAX_RECONNECT_ATTEMPTS:
        try:
            logger.info(
                f"Connecting to PostgreSQL ({attempt + 1}/{Config.MAX_RECONNECT_ATTEMPTS})..."
            )
            conn = psycopg2.connect(
                host=Config.PG_HOST,
                port=Config.PG_PORT,
                user=Config.PG_USER,
                password=Config.PG_PASSWORD,
                database=Config.PG_DATABASE,
                connect_timeout=10,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            logger.info("✅ Connected to PostgreSQL")
            state.connected = True
            state.set_error(None)
            return conn

        except psycopg2.Error as e:
            error_msg = f"PostgreSQL connection failed: {e}"
            logger.warning(error_msg)
            state.set_error(error_msg)
            state.connected = False

            if attempt < Config.MAX_RECONNECT_ATTEMPTS - 1:
                logger.info(f"Retrying in {Config.RECONNECT_DELAY} seconds...")
                time.sleep(Config.RECONNECT_DELAY)

        attempt += 1

    logger.error(f"❌ Failed to connect after {Config.MAX_RECONNECT_ATTEMPTS} attempts")
    return None


def listen_for_tables(shutdown_event: threading.Event):
    """Listen for PostgreSQL table creation notifications with auto-reconnect and leader election."""
    excluded_tables = Config.get_excluded_tables()
    sync_schemas = Config.get_sync_schemas()

    # Initialize leader election
    worker_id = Config.WORKER_ID or f"worker-{uuid.uuid4().hex[:8]}"
    leader_election = None

    if LeaderElection:
        try:
            # Build leader election parameters
            election_params = {
                "redis_host": Config.REDIS_HOST,
                "redis_port": Config.REDIS_PORT,
                "worker_id": worker_id,
                "ttl": Config.LEADER_ELECTION_TTL,
            }

            # Only add password if it's not empty
            if Config.REDIS_PASSWORD:
                election_params["redis_password"] = Config.REDIS_PASSWORD

            leader_election = LeaderElection(**election_params)
            state._leader_election = leader_election
            state._worker_id = worker_id
            logger.info(f"Worker ID: {worker_id}")
            logger.info(f"Leader election enabled (TTL={Config.LEADER_ELECTION_TTL}s)")
        except Exception as e:
            logger.warning(f"Failed to initialize leader election: {e}")
            logger.info("Running in single-instance mode")
    else:
        logger.info("Leader election not available, running in single-instance mode")

    # Main loop - handle both leader election and PostgreSQL listening
    while not shutdown_event.is_set():
        # Check if we should be leader
        if leader_election:
            if not leader_election.acquire_leadership():
                # Not the leader - wait and retry
                logger.info(
                    f"⏳ Worker {worker_id} is follower, waiting for leadership..."
                )
                state.connected = False
                shutdown_event.wait(Config.LEADER_ELECTION_INTERVAL)
                continue

            # We are the leader
            logger.info(f"✅ Worker {worker_id} is now the leader")
            state.connected = True

        # Connect to PostgreSQL and process notifications
        conn = None
        try:
            # Connect to PostgreSQL
            conn = connect_to_postgresql()
            if conn is None:
                logger.error("Cannot proceed without PostgreSQL connection. Exiting...")
                if leader_election:
                    leader_election.relinquish_leadership()
                break

            cursor = conn.cursor()

            # Log configuration
            logger.info("=" * 60)
            logger.info("EchoDB Auto-Mirror Worker Configuration")
            logger.info("=" * 60)
            logger.info(f"Worker ID:      {worker_id}")
            logger.info(
                f"Mode:           {'HA (Leader)' if leader_election else 'Single Instance'}"
            )
            logger.info(
                f"PostgreSQL:     {Config.PG_USER}@{Config.PG_HOST}:{Config.PG_PORT}/{Config.PG_DATABASE}"
            )
            logger.info(
                f"PeerDB:         {Config.PEERDB_USER}@{Config.PEERDB_HOST}:{Config.PEERDB_PORT}"
            )
            logger.info(f"Source Peer:    {Config.SOURCE_PEER_NAME}")
            logger.info(f"Target Peer:    {Config.TARGET_PEER_NAME}")
            logger.info(f"Schemas:        {', '.join(sorted(sync_schemas))}")
            logger.info(f"Excluded:       {len(excluded_tables)} tables")
            logger.info(f"Max Retries:    {Config.MAX_RETRIES}")
            logger.info(f"Reconnect Delay:{Config.RECONNECT_DELAY}s")
            logger.info("=" * 60)

            # Start listening for both create and drop events
            cursor.execute("LISTEN peerdb_create_mirror")
            cursor.execute("LISTEN peerdb_drop_mirror")
            logger.info("🎧 Listening for table creation and deletion events...")

            while not shutdown_event.is_set():
                try:
                    # Wait for notifications with timeout
                    conn.poll()

                    if conn.notifies:
                        for notify in conn.notifies:
                            try:
                                # Parse the notification payload
                                payload = json.loads(notify.payload)
                                schema = payload.get("schema")
                                table = payload.get("table")

                                logger.info(
                                    f"📢 Notification received on channel '{notify.channel}': {schema}.{table}"
                                )

                                # Only process tables from the configured schemas
                                if schema not in sync_schemas:
                                    logger.debug(
                                        f"Skipping table from different schema: {schema}.{table} (not in {sync_schemas})"
                                    )
                                    continue

                                # Skip excluded tables (for creation only)
                                if (
                                    notify.channel == "peerdb_create_mirror"
                                    and table in excluded_tables
                                ):
                                    logger.info(f"⏭️  Skipping excluded table: {table}")
                                    continue

                                # Update state
                                state.last_notification = datetime.now()

                                # Check for duplicate notifications (prevent double processing)
                                notification_id = (
                                    f"{notify.channel}:{schema}.{table}:{notify.pid}"
                                )
                                if is_duplicate_notification(notification_id):
                                    logger.debug(
                                        f"⏭️  Skipping duplicate notification: {notification_id}"
                                    )
                                    continue

                                # Mark notification as being processed
                                mark_notification_processing(notification_id)

                                # Process based on notification channel
                                try:
                                    if notify.channel == "peerdb_create_mirror":
                                        # Create mirror for the new table
                                        logger.info(
                                            f"🔨 Processing new table: {schema}.{table}"
                                        )
                                        success = create_peerdb_mirror_with_retry(
                                            schema, table
                                        )

                                        # Verify data consistency after mirror creation
                                        if success:
                                            verify_mirror_consistency(schema, table)

                                    elif notify.channel == "peerdb_drop_mirror":
                                        # Drop mirror for the deleted table
                                        logger.info(
                                            f"🗑️  Processing dropped table: {schema}.{table}"
                                        )
                                        success = drop_peerdb_mirror_with_retry(
                                            schema, table
                                        )
                                    else:
                                        logger.warning(
                                            f"Unknown notification channel: {notify.channel}"
                                        )
                                        success = True  # Don't retry unknown channels
                                finally:
                                    # Mark notification as processed (with expiry)
                                    mark_notification_processed(notification_id)

                            except json.JSONDecodeError as e:
                                logger.error(
                                    f"Failed to parse notification payload: {e}"
                                )
                                state.set_error(f"JSON parse error: {e}")
                            except Exception as e:
                                logger.error(f"Error processing notification: {e}")
                                state.set_error(f"Processing error: {e}")

                        # Clear processed notifications
                        conn.notifies.clear()

                    # Small sleep to prevent tight loop
                    time.sleep(0.1)

                except psycopg2.InterfaceError as e:
                    logger.warning(f"PostgreSQL interface error: {e}")
                    state.set_error(f"Interface error: {e}")
                    break  # Break inner loop to reconnect
                except psycopg2.OperationalError as e:
                    logger.warning(f"PostgreSQL operational error: {e}")
                    state.set_error(f"Operational error: {e}")
                    break  # Break inner loop to reconnect

        except psycopg2.Error as e:
            logger.error(f"PostgreSQL error: {e}")
            state.set_error(f"PostgreSQL error: {e}")
            state.connected = False

        finally:
            if conn:
                try:
                    conn.close()
                    logger.debug("PostgreSQL connection closed")
                except:
                    pass

            # Relinquish leadership if we lose PostgreSQL connection
            if leader_election and leader_election.is_leader:
                logger.info("Relinquishing leadership due to connection loss")
                leader_election.relinquish_leadership()

        # Don't reconnect if we're shutting down
        if not shutdown_event.is_set():
            logger.info(f"Reconnecting in {Config.RECONNECT_DELAY} seconds...")
            shutdown_event.wait(Config.RECONNECT_DELAY)

    # Cleanup leader election on shutdown
    if leader_election:
        logger.info("Stopping leader election...")
        leader_election.stop()


# ============================================================
# Main
# ============================================================


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("EchoDB Auto-Mirror Worker (Production-Ready)")
    logger.info("=" * 60)

    # Start health check server in background thread
    health_thread = threading.Thread(target=start_health_check_server, daemon=True)
    health_thread.start()

    # Setup signal handlers for graceful shutdown
    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        shutdown_event.set()
        state.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Set state to running
    state.running = True

    # Start listening for table events
    try:
        listen_for_tables(shutdown_event)
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}")
        state.set_error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        logger.info("Shutting down...")
        state.running = False

        # Log final stats
        stats = state.get_stats()
        logger.info("=" * 60)
        logger.info("Final Statistics:")
        logger.info(f"  Mirrors Created: {stats['mirrors_created']}")
        logger.info(f"  Mirrors Failed:  {stats['mirrors_failed']}")
        logger.info(f"  Last Notification: {stats['last_notification']}")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
