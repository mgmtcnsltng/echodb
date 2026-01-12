#!/bin/bash
# ============================================================
# EchoDB - Automated Setup Script
# ============================================================
# This script runs automatically on first startup to:
# 1. Install PostgreSQL event triggers
# 2. Create PeerDB peers
#
# Environment Variables (from .env or docker-compose.yml):
#   All POSTGRES_* variables for PostgreSQL connection
#   All CLICKHOUSE_* variables for ClickHouse connection
#   All PEERDB_* variables for PeerDB connection
#   SYNC_SCHEMA: Comma-separated list of schemas to watch
#   AUTO_SETUP_DONE: Marker file to prevent re-running

set -e

echo "================================"
echo "EchoDB - Automated Setup"
echo "================================"

# Load environment variables from .env file if it exists
if [ -f /app/.env ]; then
    echo "Loading environment from /app/.env..."
    export $(cat /app/.env | grep -v '^#' | xargs)
fi

# Default values
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-echodb}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-password}"
POSTGRES_DB="${POSTGRES_DB:-echodb}"

PEERDB_HOST="${PEERDB_HOST:-peerdb}"
PEERDB_PORT="${PEERDB_PORT:-9900}"
PEERDB_USER="${PEERDB_USER:-echodb}"
PEERDB_PASSWORD="${PEERDB_PASSWORD:-password}"

CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-clickhouse}"
CLICKHOUSE_PORT="${CLICKHOUSE_NATIVE_PORT:-9000}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-echodb}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-echodb}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-password}"

SOURCE_PEER_NAME="${SOURCE_PEER_NAME:-postgres_main}"
TARGET_PEER_NAME="${TARGET_PEER_NAME:-clickhouse_analytics}"

SYNC_SCHEMA="${SYNC_SCHEMA:-public}"

# Marker file to prevent re-running setup
MARKER_FILE="/tmp/echodb_auto_setup_done"

# Check if setup has already been done
if [ -f "$MARKER_FILE" ]; then
    echo "✅ Setup already completed (marker file exists)"
    echo "To re-run setup, remove: $MARKER_FILE"
    echo "================================"
    exit 0
fi

echo ""
echo "This will:"
echo "  1. Install PostgreSQL event triggers for auto-mirror"
echo "  2. Create PeerDB peers for PostgreSQL and ClickHouse"
echo ""
echo "Configuration:"
echo "  PostgreSQL:   ${POSTGRES_USER}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
echo "  ClickHouse:   ${CLICKHOUSE_USER}@${CLICKHOUSE_HOST}:${CLICKHOUSE_PORT}/${CLICKHOUSE_DATABASE}"
echo "  PeerDB:       ${PEERDB_USER}@${PEERDB_HOST}:${PEERDB_PORT}"
echo "  Schemas:      ${SYNC_SCHEMA}"
echo ""

# ============================================================
# Step 1: Wait for services to be ready
# ============================================================

echo "Waiting for services to be ready..."

# Wait for PostgreSQL
echo "  → PostgreSQL..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1" > /dev/null 2>&1; then
        echo "    ✅ PostgreSQL is ready"
        break
    fi
    attempt=$((attempt + 1))
    if [ $attempt -eq $max_attempts ]; then
        echo "    ❌ PostgreSQL not ready after ${max_attempts}s. Exiting."
        exit 1
    fi
    sleep 1
done

# Wait for PeerDB
echo "  → PeerDB..."
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" -c "SELECT 1" > /dev/null 2>&1; then
        echo "    ✅ PeerDB is ready"
        break
    fi
    attempt=$((attempt + 1))
    if [ $attempt -eq $max_attempts ]; then
        echo "    ❌ PeerDB not ready after ${max_attempts}s. Exiting."
        exit 1
    fi
    sleep 1
done

# Wait for ClickHouse
echo "  → ClickHouse..."
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if clickhouse-client --host "$CLICKHOUSE_HOST" --port "$CLICKHOUSE_PORT" --query "SELECT 1" > /dev/null 2>&1; then
        echo "    ✅ ClickHouse is ready"
        break
    fi
    attempt=$((attempt + 1))
    if [ $attempt -eq $max_attempts ]; then
        echo "    ⚠️  ClickHouse not ready after ${max_attempts}s, but continuing..."
        break
    fi
    sleep 1
done

echo ""

# ============================================================
# Step 2: Install PostgreSQL triggers
# ============================================================

echo "Step 1/2: Installing PostgreSQL event triggers..."
echo "Watching schemas: ${SYNC_SCHEMA}"

# Run the setup script with variable substitution
export SYNC_SCHEMA
PGPASSWORD="$POSTGRES_PASSWORD" envsubst < /app/scripts/setup-auto-mirror.sql | \
    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB"

if [ $? -eq 0 ]; then
    echo "✅ PostgreSQL triggers installed successfully"
else
    echo "❌ Failed to install PostgreSQL triggers"
    exit 1
fi

echo ""

# ============================================================
# Step 3: Create PeerDB peers
# ============================================================

echo "Step 2/2: Creating PeerDB peers..."

# Function to execute SQL via PeerDB
exec_peerdb_sql() {
    local sql="$1"
    PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" -c "$sql"
}

# Create PostgreSQL peer
echo "  Creating PostgreSQL peer: $SOURCE_PEER_NAME"
PEER_EXISTS=$(PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" -tAc "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'peers')" 2>/dev/null || echo "f")

if [ "$PEER_EXISTS" = "t" ]; then
    exec_peerdb_sql "
    CREATE PEER $SOURCE_PEER_NAME FROM POSTGRES WITH
    (
        host = '$POSTGRES_HOST',
        port = $POSTGRES_PORT,
        user = '$POSTGRES_USER',
        password = '$POSTGRES_PASSWORD',
        database = '$POSTGRES_DB'
    );" 2>/dev/null && echo "    ✅ Created" || echo "    ℹ️  Already exists"
else
    echo "    ⚠️  PeerDB catalog not available, skipping peer creation"
    echo "    You may need to create peers manually using ./scripts/setup-peerdb.sh"
fi

echo ""

# Create ClickHouse peer
echo "  Creating ClickHouse peer: $TARGET_PEER_NAME"
if [ "$PEER_EXISTS" = "t" ]; then
    exec_peerdb_sql "
    CREATE PEER $TARGET_PEER_NAME FROM CLICKHOUSE WITH
    (
        host = '$CLICKHOUSE_HOST',
        port = $CLICKHOUSE_PORT,
        user = '$CLICKHOUSE_USER',
        password = '$CLICKHOUSE_PASSWORD',
        database = '$CLICKHOUSE_DATABASE',
        disable_tls = true
    );" 2>/dev/null && echo "    ✅ Created" || echo "    ℹ️  Already exists"
else
    echo "    ⚠️  PeerDB catalog not available, skipping peer creation"
fi

echo ""

# ============================================================
# Step 4: Create marker file
# ============================================================

echo "Creating setup completion marker..."
date > "$MARKER_FILE"
echo "Marker file created: $MARKER_FILE"

echo ""

# ============================================================
# Summary
# ============================================================

echo "================================"
echo "✅ EchoDB Automated Setup Complete!"
echo "================================"
echo ""
echo "The following have been configured:"
echo "  • PostgreSQL event triggers installed"
echo "  • PeerDB peers created (if PeerDB catalog available)"
echo "  • Auto-mirror ready for schemas: ${SYNC_SCHEMA}"
echo ""
echo "What happens next:"
echo "  1. Auto-mirror workers will start listening for table events"
echo "  2. When you CREATE TABLE in PostgreSQL, mirrors will be created automatically"
echo "  3. When you DROP TABLE in PostgreSQL, mirrors will be dropped automatically"
echo "  4. Data will replicate from PostgreSQL to ClickHouse via PeerDB"
echo ""
echo "To verify setup:"
echo "  • Check worker logs: docker logs echodb-auto-mirror-worker-1"
echo "  • Check health: curl http://localhost:8080/health"
echo "  • Create a test table in PostgreSQL and verify mirror is created"
echo ""
echo "================================"
