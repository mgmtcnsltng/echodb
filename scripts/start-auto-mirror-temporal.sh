#!/bin/bash
# ============================================================
# EchoDB - Auto-Mirror Temporal Workflow Starter
# ============================================================
# This script starts the auto-mirror Temporal workflow

set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
TEMPORAL_HOST="${TEMPORAL_HOST:-temporal}"
TEMPORAL_PORT="${TEMPORAL_PORT:-7233}"
TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-default}"

WORKFLOW_ID="echodb-auto-mirror-workflow"

echo "================================"
echo "EchoDB - Auto-Mirror Temporal"
echo "================================"
echo "Starting Temporal workflow..."
echo "Temporal: $TEMPORAL_HOST:$TEMPORAL_PORT"
echo "Workflow ID: $WORKFLOW_ID"
echo ""

# Check if Python and temporalio are installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install it first."
    exit 1
fi

# Check if temporalio package is installed
if ! python3 -c "import temporalio" 2>/dev/null; then
    echo "⚠️  temporalio package not found. Installing..."
    pip3 install temporalio psycopg2-binary
fi

# Start the workflow using Python
python3 - <<EOF
import asyncio
import os
from temporalio.client import Client

async def start_workflow():
    # Connect to Temporal
    client = await Client.connect(
        f"${TEMPORAL_HOST}:${TEMPORAL_PORT}",
        namespace="${TEMPORAL_NAMESPACE}"
    )

    # Import the workflow
    import sys
    sys.path.insert(0, "scripts/temporal")
    from auto_mirror_workflow import AutoMirrorWorkflow, AutoMirrorConfig

    # Create config
    config = AutoMirrorConfig.from_env()
    config_dict = {
        "pg_host": config.pg_host,
        "pg_port": config.pg_port,
        "pg_user": config.pg_user,
        "pg_password": config.pg_password,
        "pg_database": config.pg_database,
        "peerdb_host": config.peerdb_host,
        "peerdb_port": config.peerdb_port,
        "peerdb_user": config.peerdb_user,
        "peerdb_password": config.peerdb_password,
        "source_peer_name": config.source_peer_name,
        "target_peer_name": config.target_peer_name,
        "schema_name": config.schema_name,
        "excluded_tables": config.excluded_tables
    }

    # Check if workflow already exists
    try:
        handle = client.get_workflow_handle("${WORKFLOW_ID}")
        desc = await handle.describe()
        print(f"ℹ️  Workflow already exists with ID: ${WORKFLOW_ID}")
        print(f"   Status: {desc.status.name}")
        print(f"   Use 'Temporal UI' to manage: http://localhost:${TEMPORAL_UI_PORT:-8085}")
        return
    except Exception:
        pass  # Workflow doesn't exist, create it

    # Start the workflow
    await client.start_workflow(
        AutoMirrorWorkflow.run,
        args=[config_dict],
        id="${WORKFLOW_ID}",
        task_queue="auto-mirror-task-queue"
    )

    print("✅ Auto-mirror workflow started successfully!")
    print(f"   Workflow ID: ${WORKFLOW_ID}")
    print(f"   Task Queue: auto-mirror-task-queue")
    print(f"   Temporal UI: http://localhost:${TEMPORAL_UI_PORT:-8085}")

asyncio.run(start_workflow())
EOF

echo ""
echo "Next steps:"
echo "  1. Ensure the auto-mirror worker is running: docker compose up auto-mirror-worker"
echo "  2. View workflow in Temporal UI: http://localhost:${TEMPORAL_UI_PORT:-8085}"
echo "  3. Create tables in PostgreSQL - mirrors will be created automatically"
