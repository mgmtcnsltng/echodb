# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

EchoDB is a Docker-based CDC (Change Data Capture) system that replicates data from PostgreSQL to ClickHouse in real-time using PeerDB. It features an auto-mirror system that automatically creates replication mirrors for new PostgreSQL tables.

## Development Commands

### Starting the Stack
```bash
# Start all services
docker compose up -d

# View logs for all services
docker compose logs -f

# View logs for specific service
docker compose logs -f peerdb
docker compose logs -f flow-worker
docker compose logs -f auto-mirror-worker
```

### Initial Setup
```bash
# Copy and configure environment variables
cp .env.example .env

# Start services
docker compose up -d

# Create PeerDB peers (source and destination)
./scripts/setup-peerdb.sh

# Option A: Manual mirror creation for a table
./scripts/create-mirror.sh users

# Option B: Install auto-mirror trigger (recommended)
./scripts/install-auto-mirror.sh
```

### Database Connections
```bash
# PostgreSQL (application database)
docker exec -it echodb-postgres-1 psql -U echodb -d echodb

# ClickHouse (analytics database)
docker exec -it echodb-clickhouse-1 clickhouse-client

# PeerDB (via psql protocol)
PGPASSWORD=password psql -h localhost -p 9900 -U echodb

# PeerDB Catalog (metadata store)
docker exec -it echodb-catalog-1 psql -U echodb -d echodb
```

### PeerDB Operations
```bash
# List mirrors (via catalog - SHOW PEERS has known bugs)
docker exec echodb-catalog-1 psql -U echodb -c "SELECT id, name, type FROM peers;"
docker exec echodb-catalog-1 psql -U echodb -c "SELECT id, name, flow_status FROM flows;"

# Drop a mirror
PGPASSWORD=password psql -h localhost -p 9900 -U echodb -c "DROP MIRROR mirror_name;"

# Pause/Resume a mirror
PGPASSWORD=password psql -h localhost -p 9900 -U echodb -c "PAUSE MIRROR mirror_name;"
PGPASSWORD=password psql -h localhost -p 9900 -U echodb -c "RESUME MIRROR mirror_name;"
```

### Health Checks
```bash
# Check auto-mirror worker health
curl http://localhost:8080/health    # Liveness check
curl http://localhost:8080/ready     # Readiness check
curl http://localhost:8080/metrics   # Detailed metrics

# Check all service statuses
docker compose ps
```

## Architecture

### Core Components

1. **PostgreSQL** (port 5432) - Application database with logical replication enabled (`wal_level=logical`)
2. **ClickHouse** (ports 8123, 9000) - Analytical database for fast aggregations
3. **PeerDB** - ETL/ELT layer with multiple services:
   - **peerdb-server** (port 9900) - SQL interface for mirror management
   - **peerdb-ui** (port 3000) - Web dashboard
   - **flow-api** (ports 8112, 8113) - gRPC/HTTP API for mirror operations
   - **flow-worker** - Background worker for CDC replication
   - **flow-snapshot-worker** - Handles initial snapshot operations
   - **catalog** (port 5433) - PostgreSQL database storing PeerDB metadata
4. **Temporal** (port 7233) - Workflow orchestration for PeerDB
5. **MinIO** (ports 9001, 9002) - S3-compatible storage for CDC staging
6. **auto-mirror-worker** (port 8080) - Python service that auto-creates mirrors for new tables

### Auto-Mirror System

The auto-mirror system uses PostgreSQL event triggers and LISTEN/NOTIFY:

1. User creates a table in PostgreSQL
2. Event trigger `peerdb_auto_mirror_trigger` fires on `CREATE TABLE`
3. Trigger sends notification via `pg_notify()` on channel `peerdb_create_mirror`
4. Python worker (auto-mirror-worker.py) receives notification
5. Worker creates PeerDB mirror using `CREATE MIRROR` SQL
6. PeerDB starts CDC replication to ClickHouse

**Key files:**
- `scripts/auto-mirror-worker.py` - Production-ready worker with retry logic, auto-reconnect, and health checks
- `scripts/setup-auto-mirror.sql` - PostgreSQL event trigger definition
- `scripts/install-auto-mirror.sh` - Script to install the trigger

### Data Flow

```
PostgreSQL (App DB) → [CDC via logical replication] → PeerDB → MinIO (staging) → ClickHouse (Analytics)
                                                            ↑
                                                    Auto-mirror worker
                                                    (listens for table events)
```

### PeerDB Concepts

- **Peer** - A database connection (source: PostgreSQL, target: ClickHouse)
- **Mirror** - A replication pipeline from a source table to a target table
- **Flow** - The actual data replication process (synonymous with mirror)

## Environment Configuration

All configuration is done via `.env` file (copy from `.env.example`). Key variables:

- **PostgreSQL**: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- **ClickHouse**: `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_DATABASE`
- **PeerDB**: `PEERDB_HOST`, `PEERDB_PORT`, `PEERDB_USER`, `PEERDB_PASSWORD`
- **Auto-mirror**: `SOURCE_PEER_NAME`, `TARGET_PEER_NAME`, `SYNC_SCHEMA`, `EXCLUDED_TABLES`

Default peer names: `postgres_main` (source), `clickhouse_analytics` (target)

## Troubleshooting

### Known Issue: SHOW PEERS/FLOWS Returns Error
PeerDB has a bug where `SHOW PEERS` and `SHOW FLOWS` return errors. Workaround: Query the catalog database directly:
```bash
docker exec echodb-catalog-1 psql -U echodb -c "SELECT id, name, type FROM peers;"
docker exec echodb-catalog-1 psql -U echodb -c "SELECT id, name, flow_status FROM flows;"
```

### Mirror Not Created
1. Check event trigger is installed: `SELECT * FROM pg_event_trigger WHERE evtname = 'peerdb_auto_mirror_trigger';`
2. Check auto-mirror worker logs: `docker logs echodb-auto-mirror-worker-1 -f`
3. Check worker health: `curl http://localhost:8080/health`
4. Verify peers exist in catalog database

### Data Not Syncing
1. Check mirror status in catalog database
2. Check flow-worker logs: `docker logs echodb-flow-worker-1 -f`
3. Verify ClickHouse user permissions
4. Check replication slots: `SELECT * FROM pg_replication_slots;` in PostgreSQL

### Auto-Mirror Worker Not Detecting Tables
1. Verify trigger is installed
2. Check worker can connect to PostgreSQL
3. Verify correct schema name in environment variables
4. Check worker is receiving notifications via logs

## Initialization Scripts

- `init-postgres.sql` - Creates sample tables (users, products, orders, events) with sample data
- `init-clickhouse.sql` - Creates `_peerdb` database for PeerDB metadata
- `scripts/setup-auto-mirror.sql` - Installs event trigger for auto-mirror

## Monitoring

- **PeerDB UI**: http://localhost:3000 - Visual mirror management and status
- **Temporal UI**: http://localhost:8085 - Workflow orchestration monitoring
- **Auto-mirror metrics**: http://localhost:8080/metrics - Worker statistics
