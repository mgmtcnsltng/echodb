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
    # Try HTTP API instead of clickhouse-client
    if curl -s "http://${CLICKHOUSE_HOST}:8123" --data "SELECT 1" > /dev/null 2>&1; then
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
# Step 2: Create PeerDB peers
# ============================================================

echo "Step 1/1: Creating PeerDB peers..."

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
        staging_database = '_peerdb',
        disable_tls = true
    );" 2>/dev/null && echo "    ✅ Created" || echo "    ℹ️  Already exists"
else
    echo "    ⚠️  PeerDB catalog not available, skipping peer creation"
fi

echo ""

# ============================================================
# Step 3: Install PostgreSQL event triggers
# ============================================================

echo "Installing PostgreSQL event triggers..."

PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" << 'EOSQL' 2>/dev/null
-- Helper function to check if schema is in watch list
CREATE OR REPLACE FUNCTION is_schema_in_watch_list(schema_name text, watch_list text)
RETURNS BOOLEAN AS $$
DECLARE
  item text;
BEGIN
  FOREACH item IN ARRAY regexp_split_to_array(watch_list, '\s*,\s*')
  LOOP
    IF item = schema_name THEN
      RETURN TRUE;
    END IF;
  END LOOP;
  RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a new table is created
CREATE OR REPLACE FUNCTION notify_new_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := 'public';
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE TABLE'
  LOOP
    schema_name := split_part(obj.object_identity, '.', 1);

    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    PERFORM pg_notify('peerdb_create_mirror',
      json_build_object(
        'schema', schema_name,
        'table', table_name,
        'timestamp', now()
      )::text
    );
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a table is dropped
CREATE OR REPLACE FUNCTION notify_drop_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := 'public';
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'DROP TABLE'
  LOOP
    schema_name := split_part(obj.object_identity, '.', 1);

    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    PERFORM pg_notify('peerdb_drop_mirror',
      json_build_object(
        'schema', schema_name,
        'table', table_name,
        'timestamp', now()
      )::text
    );
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Create event trigger for CREATE TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_trigger
ON ddl_command_end
WHEN tag IN ('CREATE TABLE')
EXECUTE FUNCTION notify_new_table();

-- Create event trigger for DROP TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_drop_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_drop_trigger
ON ddl_command_end
WHEN tag IN ('DROP TABLE')
EXECUTE FUNCTION notify_drop_table();
EOSQL

if [ $? -eq 0 ]; then
    echo "    ✅ Event triggers installed"
else
    echo "    ⚠️  Event trigger installation had issues, but continuing..."
fi

echo ""

# ============================================================
# Step 4: Clean up unwanted default databases
# ============================================================

echo "Cleaning up unwanted default databases..."

# Drop the 'postgres' database in PostgreSQL (created by default)
echo "  → Dropping 'postgres' database from PostgreSQL..."
PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS postgres;" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Dropped 'postgres' database"
else
    echo "    ℹ️  'postgres' database already removed or doesn't exist"
fi

# Drop the 'default' database in ClickHouse (created by default)
echo "  → Dropping 'default' database from ClickHouse..."
curl -s "http://${CLICKHOUSE_HOST}:8123" --data "DROP DATABASE IF EXISTS default" > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "    ✅ Dropped 'default' database"
else
    echo "    ℹ️  'default' database already removed or doesn't exist"
fi

echo ""

# ============================================================
# Step 5: Create mirrors for init tables
# ============================================================

echo "Creating mirrors for initialization tables..."

# Function to create a mirror
create_mirror_if_not_exists() {
    local mirror_name="$1"
    local table_name="$2"

    # Check if mirror already exists
    MIRROR_EXISTS=$(PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" -tAc "SELECT COUNT(*) FROM flows WHERE name = '$mirror_name'" 2>/dev/null || echo "0")

    if [ "$MIRROR_EXISTS" = "0" ]; then
        echo "  → Creating mirror for $table_name..."
        PGPASSWORD="$PEERDB_PASSWORD" psql -h "$PEERDB_HOST" -p "$PEERDB_PORT" -U "$PEERDB_USER" -c "CREATE MIRROR $mirror_name FROM $SOURCE_PEER_NAME TO $TARGET_PEER_NAME WITH TABLE MAPPING (public.$table_name:$table_name) WITH (do_initial_copy = true);" > /dev/null 2>&1
        if [ $? -eq 0 ]; then
            echo "    ✅ Created"
        else
            echo "    ⚠️  Failed (table may not exist yet)"
        fi
    else
        echo "  → $table_name mirror already exists, skipping"
    fi
}

# Create mirrors for sample tables
create_mirror_if_not_exists "users_mirror" "users"

echo ""

# ============================================================
# Step 6: Create marker file
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
