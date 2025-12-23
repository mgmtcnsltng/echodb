#!/bin/bash
# ============================================================
# EchoDB - PeerDB Setup Script
# ============================================================
# This script creates PeerDB peers for PostgreSQL and ClickHouse
#
# Environment Variables (from .env):
#   PEERDB_HOST: PeerDB server host (default: peerdb)
#   PEERDB_PORT: PeerDB server port (default: 9900)
#   PEERDB_USER: PeerDB user (default: postgres)
#   PEERDB_PASSWORD: PeerDB password (default: peerdb)
#   POSTGRES_HOST: PostgreSQL host (default: postgres)
#   POSTGRES_PORT: PostgreSQL port (default: 5432)
#   POSTGRES_USER: PostgreSQL user (default: postgres)
#   POSTGRES_PASSWORD: PostgreSQL password (default: postgres)
#   POSTGRES_DB: PostgreSQL database (default: postgres)
#   CLICKHOUSE_HOST: ClickHouse host (default: clickhouse)
#   CLICKHOUSE_NATIVE_PORT: ClickHouse native port (default: 9000)
#   CLICKHOUSE_DATABASE: ClickHouse database (default: default)
#   CLICKHOUSE_USER: ClickHouse user (default: default)
#   CLICKHOUSE_PASSWORD: ClickHouse password (default: default)
#   SOURCE_PEER_NAME: Source peer name (default: postgres_main)
#   TARGET_PEER_NAME: Target peer name (default: clickhouse_analytics)

set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
PEERDB_HOST="${PEERDB_HOST:-peerdb}"
PEERDB_PORT="${PEERDB_PORT:-9900}"
PEERDB_USER="${PEERDB_USER:-postgres}"
PEERDB_PASSWORD="${PEERDB_PASSWORD:-peerdb}"

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-postgres}"

CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-clickhouse}"
CLICKHOUSE_PORT="${CLICKHOUSE_NATIVE_PORT:-9000}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-default}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-default}"

SOURCE_PEER_NAME="${SOURCE_PEER_NAME:-postgres_main}"
TARGET_PEER_NAME="${TARGET_PEER_NAME:-clickhouse_analytics}"

echo "================================"
echo "EchoDB - PeerDB Setup"
echo "================================"
echo "Creating peers for PostgreSQL and ClickHouse..."
echo ""

# Function to execute SQL via PeerDB
exec_peerdb_sql() {
    local sql="$1"
    PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" -c "$sql"
}

# Create PostgreSQL peer
echo "Creating PostgreSQL peer: $SOURCE_PEER_NAME"
exec_peerdb_sql "
CREATE PEER $SOURCE_PEER_NAME FROM POSTGRES WITH
(
    host = '$POSTGRES_HOST',
    port = $POSTGRES_PORT,
    user = '$POSTGRES_USER',
    password = '$POSTGRES_PASSWORD',
    database = '$POSTGRES_DB'
);
" || echo "Note: PostgreSQL peer might already exist"

echo ""

# Create ClickHouse peer
echo "Creating ClickHouse peer: $TARGET_PEER_NAME"
exec_peerdb_sql "
CREATE PEER $TARGET_PEER_NAME FROM CLICKHOUSE WITH
(
    host = '$CLICKHOUSE_HOST',
    port = $CLICKHOUSE_PORT,
    user = '$CLICKHOUSE_USER',
    password = '$CLICKHOUSE_PASSWORD',
    database = '$CLICKHOUSE_DATABASE',
    disable_tls = true
);
" || echo "Note: ClickHouse peer might already exist"

echo ""
echo "================================"
echo "PeerDB Setup Complete!"
echo "================================"
echo ""
echo "Next steps:"
echo "  1. Create mirrors using: ./scripts/create-mirror.sh <table_name>"
echo "  2. Or start the auto-mirror worker: ./scripts/auto-mirror-worker.py"
echo ""
echo "PeerDB UI: http://localhost:3000"
echo "PeerDB CLI: PGPASSWORD=$PEERDB_PASSWORD psql -h $PEERDB_HOST -p $PEERDB_PORT -U $PEERDB_USER"
