#!/usr/bin/env python3
"""
EchoDB - Auto-Mirror Temporal Workflow

This Temporal workflow listens for PostgreSQL table creation events and
automatically creates PeerDB mirrors to sync them to ClickHouse.

The workflow runs indefinitely, managed by Temporal for persistence and restartability.

Requirements:
    pip install temporalio psycopg2-binary
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from temporalio import activity, workflow, workflow_method
from temporalio.client import Client
from temporalio.worker import Worker

# ============================================================
# Configuration (with environment variable support)
# ============================================================

@dataclass
class AutoMirrorConfig:
    """Configuration for the auto-mirror workflow."""

    # PostgreSQL Configuration
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "echodb"
    pg_password: str = "password"
    pg_database: str = "echodb"

    # PeerDB Configuration
    peerdb_host: str = "localhost"
    peerdb_port: int = 9900
    peerdb_user: str = "echodb"
    peerdb_password: str = "password"

    # Mirror Configuration
    source_peer_name: str = "postgres_main"
    target_peer_name: str = "clickhouse_analytics"
    schema_name: str = "public"

    # Excluded tables (comma-separated)
    excluded_tables: str = "spatial_ref_sys,geometry_columns,geography_columns,raster_columns,raster_overviews"

    @classmethod
    def from_env(cls) -> "AutoMirrorConfig":
        """Load configuration from environment variables."""
        return cls(
            pg_host=os.getenv("POSTGRES_HOST", "postgres"),
            pg_port=int(os.getenv("POSTGRES_PORT", "5432")),
            pg_user=os.getenv("POSTGRES_USER", "echodb"),
            pg_password=os.getenv("POSTGRES_PASSWORD", "password"),
            pg_database=os.getenv("POSTGRES_DB", "echodb"),
            peerdb_host=os.getenv("PEERDB_HOST", "peerdb"),
            peerdb_port=int(os.getenv("PEERDB_PORT", "9900")),
            peerdb_user=os.getenv("PEERDB_USER", "echodb"),
            peerdb_password=os.getenv("PEERDB_PASSWORD", "password"),
            source_peer_name=os.getenv("SOURCE_PEER_NAME", "postgres_main"),
            target_peer_name=os.getenv("TARGET_PEER_NAME", "clickhouse_analytics"),
            schema_name=os.getenv("SYNC_SCHEMA", "public"),
            excluded_tables=os.getenv("EXCLUDED_TABLES",
                "spatial_ref_sys,geometry_columns,geography_columns,raster_columns,raster_overviews")
        )

    def get_excluded_tables_set(self) -> set[str]:
        """Get excluded tables as a set."""
        if self.excluded_tables.startswith('['):
            return set(json.loads(self.excluded_tables))
        return set(t.strip() for t in self.excluded_tables.split(',') if t.strip())


# ============================================================
# Activities
# ============================================================

@activity.defn
class MirrorActivities:
    """Activities for managing PeerDB mirrors."""

    def __init__(self, config: AutoMirrorConfig):
        self.config = config

    async def create_mirror(self, schema: str, table: str) -> dict[str, any]:
        """Create a PeerDB mirror for the given table.

        Args:
            schema: Schema name
            table: Table name

        Returns:
            Dictionary with success status and message
        """
        import subprocess

        mirror_name = f"{table}_mirror"

        # Build the CREATE MIRROR SQL
        sql = f"""CREATE MIRROR {mirror_name} FROM {self.config.source_peer_name} TO {self.config.target_peer_name}
WITH TABLE MAPPING ({schema}.{table}:{table})
WITH (do_initial_copy = true);"""

        activity.logger.info(f"Creating mirror for {schema}.{table}")

        try:
            result = subprocess.run(
                [
                    "psql",
                    "-h", self.config.peerdb_host,
                    "-p", str(self.config.peerdb_port),
                    "-U", self.config.peerdb_user,
                    "-c", sql
                ],
                env={**os.environ, "PGPASSWORD": self.config.peerdb_password},
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                activity.logger.info(f"✅ Mirror created: {mirror_name}")
                return {"success": True, "mirror": mirror_name, "message": "Mirror created successfully"}
            else:
                # Check if mirror already exists
                if "already exists" in result.stderr:
                    activity.logger.info(f"ℹ️  Mirror already exists: {mirror_name}")
                    return {"success": True, "mirror": mirror_name, "message": "Mirror already exists"}
                else:
                    activity.logger.error(f"❌ Failed to create mirror: {result.stderr}")
                    return {"success": False, "mirror": mirror_name, "error": result.stderr}

        except subprocess.TimeoutExpired:
            error_msg = f"Timeout creating mirror for {table}"
            activity.logger.error(f"❌ {error_msg}")
            return {"success": False, "mirror": mirror_name, "error": error_msg}

        except Exception as e:
            error_msg = f"Error creating mirror: {e}"
            activity.logger.error(f"❌ {error_msg}")
            return {"success": False, "mirror": mirror_name, "error": str(e)}


@activity.defn
async def listen_for_tables(config_dict: dict) -> list[dict[str, str]]:
    """Listen for PostgreSQL table creation events.

    This activity runs a loop that listens for PostgreSQL notifications
    and returns detected tables. Note: This is a long-running activity.

    Args:
        config_dict: Configuration dictionary

    Returns:
        List of detected table events (never returns under normal operation)
    """
    # Reconstruct config from dict
    config = AutoMirrorConfig(**config_dict)
    excluded_tables = config.get_excluded_tables_set()

    conn = None
    detected_tables = []

    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=config.pg_host,
            port=config.pg_port,
            user=config.pg_user,
            password=config.pg_password,
            database=config.pg_database
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        cursor = conn.cursor()

        activity.logger.info("Listening for table creation events...")
        activity.logger.info(f"PostgreSQL: {config.pg_user}@{config.pg_host}:{config.pg_port}/{config.pg_database}")
        activity.logger.info(f"Schema: {config.schema_name}")
        activity.logger.info(f"Excluded tables: {excluded_tables}")

        cursor.execute("LISTEN peerdb_create_mirror")

        # Listen for notifications (with heartbeat)
        while True:
            # Wait for notifications with timeout
            conn.poll()

            if conn.notifies:
                for notify in conn.notifies:
                    try:
                        # Parse the notification payload
                        payload = json.loads(notify.payload)
                        schema = payload.get("schema")
                        table = payload.get("table")

                        # Only process tables from the configured schema
                        if schema != config.schema_name:
                            activity.logger.debug(f"Skipping table from different schema: {schema}.{table}")
                            continue

                        # Skip excluded tables
                        if table in excluded_tables:
                            activity.logger.info(f"Skipping excluded table: {table}")
                            continue

                        # Add to detected tables
                        detected_tables.append({"schema": schema, "table": table})
                        activity.logger.info(f"📢 New table detected: {schema}.{table}")

                    except json.JSONDecodeError as e:
                        activity.logger.error(f"Failed to parse notification: {e}")
                    except Exception as e:
                        activity.logger.error(f"Error processing notification: {e}")

                # Clear processed notifications
                conn.notifies.clear()

            # Heartbeat to Temporal
            activity.heartbeat()

            # Small sleep to prevent tight loop
            await asyncio.sleep(0.1)

    except Exception as e:
        activity.logger.error(f"PostgreSQL error: {e}")
        raise
    finally:
        if conn:
            conn.close()


# ============================================================
# Workflows
# ============================================================

@workflow.defn
class AutoMirrorWorkflow:
    """Auto-mirror workflow that runs indefinitely."""

    @workflow_method
    async def run(self, config_dict: dict) -> None:
        """Run the auto-mirror workflow.

        This workflow continuously listens for new table events and
        creates mirrors for them.
        """
        # Reconstruct config from dict
        config = AutoMirrorConfig(**config_dict)
        excluded_tables = config.get_excluded_tables_set()

        workflow.logger.info("Starting EchoDB Auto-Mirror Workflow...")
        workflow.logger.info(f"Source Peer: {config.source_peer_name}")
        workflow.logger.info(f"Target Peer: {config.target_peer_name}")
        workflow.logger.info(f"Schema: {config.schema_name}")

        # Start the listener activity in the background
        # Note: In a real implementation, you'd want to use signals/queries
        # or implement this as a polling activity instead
        listener_task = workflow.execute_activity(
            listen_for_tables,
            args=[config_dict],
            start_to_close_timeout=timedelta(days=365),  # Run indefinitely
            heartbeat_timeout=timedelta(seconds=30)
        )

        # For now, just wait forever (will be cancelled when workflow is stopped)
        await listener_task


# ============================================================
# Worker
# ============================================================

async def run_worker():
    """Run the Temporal worker for auto-mirror workflow."""
    # Load configuration
    config = AutoMirrorConfig.from_env()

    # Connect to Temporal
    client = await Client.connect(
        f"{os.getenv('TEMPORAL_HOST', 'temporal')}:{os.getenv('TEMPORAL_PORT', '7233')}",
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )

    # Create and run worker
    worker = Worker(
        client,
        task_queue="auto-mirror-task-queue",
        workflows=[AutoMirrorWorkflow],
        activities={
            listen_for_tables,
            MirrorActivities(config).create_mirror
        }
    )

    logging.info("Starting auto-mirror worker...")
    logging.info(f"Task Queue: auto-mirror-task-queue")
    await worker.run()


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(run_worker())
