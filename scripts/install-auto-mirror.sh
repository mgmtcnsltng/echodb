#!/bin/bash
# ============================================================
# EchoDB - Auto-Mirror Setup Script
# ============================================================
# This script sets up PostgreSQL event triggers for auto-mirroring

set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-postgres}"

SYNC_SCHEMA="${SYNC_SCHEMA:-public}"

echo "================================"
echo "EchoDB - Auto-Mirror Setup"
echo "================================"
echo "Installing PostgreSQL event trigger..."
echo "Watching schema: $SYNC_SCHEMA"
echo ""

# Run the setup script with variable substitution
# Use envsubst to replace ${SYNC_SCHEMA:-public} with actual value
export SYNC_SCHEMA
PGPASSWORD="$POSTGRES_PASSWORD" envsubst < scripts/setup-auto-mirror.sql | \
    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo ""
echo "✅ Auto-mirror trigger installed!"
echo ""
echo "Next steps:"
echo "  1. Start the auto-mirror worker: ./scripts/auto-mirror-worker.py"
echo "  2. Create tables in PostgreSQL - mirrors will be created automatically"
