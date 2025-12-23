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

import os
import sys
import json
import subprocess
import time
import logging
import signal
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Optional, Set

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Try to import http.server for health checks (always available in Python 3)
from http.server import HTTPServer, BaseHTTPRequestHandler

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
        "spatial_ref_sys,geometry_columns,geography_columns,raster_columns,raster_overviews"
    )

    @classmethod
    def get_excluded_tables(cls) -> Set[str]:
        """Get excluded tables as a set."""
        if cls.EXCLUDED_TABLES_STR.startswith('['):
            return set(json.loads(cls.EXCLUDED_TABLES_STR))
        return set(t.strip() for t in cls.EXCLUDED_TABLES_STR.split(',') if t.strip())


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
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    simple_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
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
            backupCount=Config.LOG_BACKUP_COUNT
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
            return {
                "running": self._running,
                "connected": self._connected,
                "last_notification": self._last_notification.isoformat() if self._last_notification else None,
                "mirrors_created": self._mirrors_created,
                "mirrors_failed": self._mirrors_failed,
                "last_error": self._last_error
            }


state = WorkerState()


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
        self.wfile.write(json.dumps({"status": "healthy" if state.running else "unhealthy"}).encode())

    def send_ready_response(self):
        """Readiness check - returns 200 if connected to PostgreSQL."""
        ready = state.running and state.connected
        self.send_response(200 if ready else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        stats = state.get_stats()
        self.wfile.write(json.dumps({
            "status": "ready" if ready else "not_ready",
            "connected": stats["connected"]
        }).encode())

    def send_metrics_response(self):
        """Metrics endpoint with detailed stats."""
        self.send_response(200 if state.running else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(state.get_stats()).encode())


def start_health_check_server():
    """Start HTTP server for health checks in a separate thread."""
    try:
        server = HTTPServer((Config.HEALTH_CHECK_HOST, Config.HEALTH_CHECK_PORT), HealthCheckHandler)
        logger.info(f"Health check server listening on http://{Config.HEALTH_CHECK_HOST}:{Config.HEALTH_CHECK_PORT}")
        logger.info(f"  - /health  - Simple health check")
        logger.info(f"  - /ready   - Readiness check")
        logger.info(f"  - /metrics - Detailed metrics")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start health check server: {e}")


# ============================================================
# Mirror Creation with Retry Logic
# ============================================================

def create_peerdb_mirror_with_retry(schema: str, table: str) -> bool:
    """Create a PeerDB mirror with exponential backoff retry logic."""
    mirror_name = f"{table}_mirror"

    # Build the CREATE MIRROR SQL
    sql = f"""CREATE MIRROR {mirror_name} FROM {Config.SOURCE_PEER_NAME} TO {Config.TARGET_PEER_NAME}
WITH TABLE MAPPING ({schema}.{table}:{table})
WITH (do_initial_copy = true);"""

    retry_count = 0
    delay = Config.RETRY_DELAY

    while retry_count <= Config.MAX_RETRIES:
        try:
            logger.info(f"Creating mirror for {schema}.{table} (attempt {retry_count + 1}/{Config.MAX_RETRIES + 1})")

            result = subprocess.run(
                ["psql", "-h", Config.PEERDB_HOST, "-p", str(Config.PEERDB_PORT),
                 "-U", Config.PEERDB_USER, "-c", sql],
                env={**os.environ, "PGPASSWORD": Config.PEERDB_PASSWORD},
                capture_output=True,
                text=True,
                timeout=60
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

                    # Last attempt failed
                    if retry_count == Config.MAX_RETRIES:
                        logger.error(f"❌ Failed to create mirror after {Config.MAX_RETRIES + 1} attempts: {error_msg}")
                        state.increment_mirrors_failed()
                        state.set_error(f"Mirror creation failed: {error_msg}")
                        return False

        except subprocess.TimeoutExpired:
            logger.warning(f"Attempt {retry_count + 1} timed out")
            if retry_count == Config.MAX_RETRIES:
                error_msg = f"Timeout creating mirror for {table}"
                logger.error(f"❌ {error_msg}")
                state.increment_mirrors_failed()
                state.set_error(error_msg)
                return False

        except Exception as e:
            logger.warning(f"Attempt {retry_count + 1} raised exception: {e}")
            if retry_count == Config.MAX_RETRIES:
                error_msg = f"Exception creating mirror: {e}"
                logger.error(f"❌ {error_msg}")
                state.increment_mirrors_failed()
                state.set_error(error_msg)
                return False

        # Exponential backoff before retry
        retry_count += 1
        if retry_count <= Config.MAX_RETRIES:
            logger.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= Config.RETRY_BACKOFF

    return False


# ============================================================
# PostgreSQL Listener with Reconnect Logic
# ============================================================

def connect_to_postgresql():
    """Connect to PostgreSQL with retry logic."""
    attempt = 0

    while attempt < Config.MAX_RECONNECT_ATTEMPTS:
        try:
            logger.info(f"Connecting to PostgreSQL ({attempt + 1}/{Config.MAX_RECONNECT_ATTEMPTS})...")
            conn = psycopg2.connect(
                host=Config.PG_HOST,
                port=Config.PG_PORT,
                user=Config.PG_USER,
                password=Config.PG_PASSWORD,
                database=Config.PG_DATABASE,
                connect_timeout=10
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
    """Listen for PostgreSQL table creation notifications with auto-reconnect."""
    excluded_tables = Config.get_excluded_tables()

    while not shutdown_event.is_set():
        conn = None
        try:
            # Connect to PostgreSQL
            conn = connect_to_postgresql()
            if conn is None:
                logger.error("Cannot proceed without PostgreSQL connection. Exiting...")
                break

            cursor = conn.cursor()

            # Log configuration
            logger.info("=" * 60)
            logger.info("EchoDB Auto-Mirror Worker Configuration")
            logger.info("=" * 60)
            logger.info(f"PostgreSQL:     {Config.PG_USER}@{Config.PG_HOST}:{Config.PG_PORT}/{Config.PG_DATABASE}")
            logger.info(f"PeerDB:         {Config.PEERDB_USER}@{Config.PEERDB_HOST}:{Config.PEERDB_PORT}")
            logger.info(f"Source Peer:    {Config.SOURCE_PEER_NAME}")
            logger.info(f"Target Peer:    {Config.TARGET_PEER_NAME}")
            logger.info(f"Schema:         {Config.SCHEMA_NAME}")
            logger.info(f"Excluded:       {len(excluded_tables)} tables")
            logger.info(f"Max Retries:    {Config.MAX_RETRIES}")
            logger.info(f"Reconnect Delay:{Config.RECONNECT_DELAY}s")
            logger.info("=" * 60)

            # Start listening
            cursor.execute("LISTEN peerdb_create_mirror")
            logger.info("🎧 Listening for table creation events...")

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

                                logger.info(f"📢 Notification received: {schema}.{table}")

                                # Only process tables from the configured schema
                                if schema != Config.SCHEMA_NAME:
                                    logger.debug(f"Skipping table from different schema: {schema}.{table}")
                                    continue

                                # Skip excluded tables
                                if table in excluded_tables:
                                    logger.info(f"⏭️  Skipping excluded table: {table}")
                                    continue

                                # Update state
                                state.last_notification = datetime.now()

                                # Create mirror for the new table
                                logger.info(f"🔨 Processing new table: {schema}.{table}")
                                create_peerdb_mirror_with_retry(schema, table)

                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to parse notification payload: {e}")
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

        # Don't reconnect if we're shutting down
        if not shutdown_event.is_set():
            logger.info(f"Reconnecting in {Config.RECONNECT_DELAY} seconds...")
            shutdown_event.wait(Config.RECONNECT_DELAY)


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
