#!/bin/bash
# ============================================================
# EchoDB - Create Mirror Script
# ============================================================
# Usage: ./create-mirror.sh <table_name> [schema_name]
#
# Environment Variables (from .env):
#   PEERDB_HOST: PeerDB server host (default: peerdb)
#   PEERDB_PORT: PeerDB server port (default: 9900)
#   PEERDB_USER: PeerDB user (default: postgres)
#   PEERDB_PASSWORD: PeerDB password (default: peerdb)
#   SOURCE_PEER_NAME: Source peer name (default: postgres_main)
#   TARGET_PEER_NAME: Target peer name (default: clickhouse_analytics)
#   POSTGRES_DB: PostgreSQL database (default: postgres)
#   CLICKHOUSE_DATABASE: ClickHouse database (default: default)

set -e

# Check arguments
if [ -z "$1" ]; then
    echo "Usage: $0 <table_name> [schema_name]"
    echo "Example: $0 api_keys"
    echo "Example: $0 users public"
    exit 1
fi

TABLE_NAME="$1"
SCHEMA_NAME="${2:-public}"

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
PEERDB_HOST="${PEERDB_HOST:-peerdb}"
PEERDB_PORT="${PEERDB_PORT:-9900}"
PEERDB_USER="${PEERDB_USER:-postgres}"
PEERDB_PASSWORD="${PEERDB_PASSWORD:-peerdb}"
SOURCE_PEER_NAME="${SOURCE_PEER_NAME:-postgres_main}"
TARGET_PEER_NAME="${TARGET_PEER_NAME:-clickhouse_analytics}"

MIRROR_NAME="${TABLE_NAME}_mirror"

echo "================================"
echo "Creating Mirror: $MIRROR_NAME"
echo "================================"
echo "Source: $SCHEMA_NAME.$TABLE_NAME (PostgreSQL)"
echo "Target: $TABLE_NAME (ClickHouse)"
echo ""

# Create the mirror
PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" \
    "CREATE MIRROR $MIRROR_NAME FROM $SOURCE_PEER_NAME TO $TARGET_PEER_NAME
    WITH TABLE MAPPING ($SCHEMA_NAME.$TABLE_NAME:$TABLE_NAME)
    WITH (do_initial_copy = true);"

echo ""
echo "✅ Mirror created successfully!"
echo ""
echo "To check mirror status:"
echo "  PGPASSWORD=$PEERDB_PASSWORD psql -h $PEERDB_HOST -p $PEERDB_PORT -U $PEERDB_USER -c 'SELECT * FROM flows;'"
