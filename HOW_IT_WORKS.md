# How EchoDB Works

EchoDB is a real-time data replication system that automatically syncs data from PostgreSQL to ClickHouse using PeerDB with production-ready auto-mirror capabilities.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EchoDB Architecture                             │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
  │  PostgreSQL  │         │    PeerDB    │         │  ClickHouse  │
  │  (App DB)    │────────▶│   (ETL/ELT)  │────────▶│  (Analytics) │
  │              │   CDC   │              │  Sync   │              │
  └──────┬───────┘         └──────┬───────┘         └──────────────┘
         │                       │
         │ pg_notify()            │
         ▼                       ▼
  ┌──────────────┐         ┌──────────────┐
  │ Event Trigger│         │    MinIO     │
  │  (DDL Hook)  │         │  (Staging)   │
  └──────────────┘         └──────────────┘
         │
         ▼
  ┌─────────────────────────────┐
  │   Auto-Mirror Worker        │
  │  (Python + Health Checks)   │
  │  • Retry logic              │
  │  • Auto-reconnect           │
  │  • Log rotation             │
  │  • HTTP health endpoints    │
  └─────────────────────────────┘
```

## Components

### Data Layer

| Component | Purpose | Ports |
|-----------|---------|-------|
| **PostgreSQL** | Primary application database with logical replication enabled | 5432 |
| **ClickHouse** | Analytical database for fast aggregations and queries | 8123 (HTTP), 9000 (Native) |
| **Redis** | Caching and queue management | 6379 |

### ETL/ELT Layer

| Component | Purpose | Ports |
|-----------|---------|-------|
| **PeerDB Server** | SQL interface for creating and managing data mirrors | 9900 |
| **PeerDB Flow API** | gRPC/HTTP API for mirror operations | 8112 (gRPC), 8113 (HTTP) |
| **PeerDB Flow Worker** | Background worker for CDC data sync | - |
| **PeerDB Snapshot Worker** | Worker for initial snapshot operations | - |
| **PeerDB UI** | Web dashboard for visual mirror management | 3000 |
| **PeerDB Catalog** | PostgreSQL database storing PeerDB metadata | 5433 |

### Auto-Mirror Layer

| Component | Purpose | Ports |
|-----------|---------|-------|
| **Auto-Mirror Worker** | Python service that listens for table events and creates mirrors | 8080 (health checks) |
| **PostgreSQL Event Trigger** | DDL trigger that fires on CREATE TABLE commands | - |

### Storage Layer

| Component | Purpose | Ports |
|-----------|---------|-------|
| **MinIO** | S3-compatible storage for CDC staging during data transfer | 9001 (API), 9002 (Console) |

## How Auto-Mirror Works

The auto-mirror feature automatically creates PeerDB mirrors whenever a new table is created in PostgreSQL using a production-ready Python worker with built-in retry logic, auto-reconnect, and health monitoring.

### Step-by-Step Flow

```
1. USER creates table in PostgreSQL
   │
   │  CREATE TABLE users (
   │    id SERIAL PRIMARY KEY,
   │    name TEXT
   │  );
   ▼
2. POSTGRESQL Event Trigger fires
   │
   │  The event trigger "peerdb_auto_mirror_trigger"
   │  detects the CREATE TABLE command
   ▼
3. NOTIFICATION sent via pg_notify()
   │
   │  Channel: 'peerdb_create_mirror'
   │  Payload: {"schema": "public", "table": "users", "timestamp": ...}
   ▼
4. AUTO-MIRROR WORKER receives notification
   │
   │  The Python worker listens for notifications
   │  via PostgreSQL LISTEN/NOTIFY mechanism
   ▼
5. WORKER creates PeerDB mirror with retry logic
   │
   │  CREATE MIRROR users_mirror
   │  FROM postgres_main
   │  TO clickhouse_analytics
   │  WITH TABLE MAPPING (public.users:users)
   │  WITH (do_initial_copy = true);
   ▼
6. PEERDB starts CDC replication
   │
   │  • Initial snapshot: Existing data copied to ClickHouse
   │  • Ongoing CDC: New changes streamed via MinIO
   ▼
7. DATA synced to ClickHouse
   │
   │  Table 'users' now available in ClickHouse
   │  for real-time analytics queries
```

### Event Trigger Mechanism

The auto-mirror system uses PostgreSQL's event triggers:

```sql
-- Function that runs when CREATE TABLE is executed
CREATE OR REPLACE FUNCTION notify_new_table()
RETURNS event_trigger AS $$
BEGIN
  -- Send notification via pg_notify()
  PERFORM pg_notify('peerdb_create_mirror',
    json_build_object(
      'schema', 'public',
      'table', 'new_table_name',
      'timestamp', now()
    )::text
  );
END;
$$ LANGUAGE plpgsql;

-- Event trigger that calls the function
CREATE EVENT TRIGGER peerdb_auto_mirror_trigger
ON ddl_command_end
WHEN tag IN ('CREATE TABLE')
EXECUTE FUNCTION notify_new_table();
```

### Auto-Mirror Worker Features

The auto-mirror worker is a production-ready Python service with:

| Feature | Description |
|---------|-------------|
| **Retry Logic** | Exponential backoff for failed mirror creation attempts |
| **Auto-Reconnect** | Automatically reconnects to PostgreSQL if connection drops |
| **Health Checks** | HTTP endpoints for monitoring worker status (`/health`, `/ready`, `/metrics`) |
| **Log Rotation** | Automatic log file rotation to prevent disk space issues |
| **Graceful Shutdown** | Handles SIGTERM/SIGINT for clean shutdown |
| **Thread-Safe State** | Concurrent-safe metrics tracking |
| **Configurable** | All settings via environment variables |

#### Health Check Endpoints

The worker exposes HTTP endpoints on port 8080:

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Simple liveness check - returns 200 if worker is running |
| `GET /ready` | Readiness check - returns 200 if connected to PostgreSQL |
| `GET /metrics` | Detailed metrics including mirrors created, failed, and last error |

Example:
```bash
# Check worker health
curl http://localhost:8080/health
# {"status": "healthy"}

# Get detailed metrics
curl http://localhost:8080/metrics
# {"running": true, "connected": true, "mirrors_created": 5, "mirrors_failed": 0, ...}
```

## Data Flow Example

### Creating a New Table

```bash
# 1. Connect to PostgreSQL
docker exec -it echodb-postgres-1 psql -U echodb -d echodb

# 2. Create a new table
CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price DECIMAL(10, 2)
);

# 3. Insert data
INSERT INTO products (name, price) VALUES
  ('Laptop', 999.99),
  ('Mouse', 29.99);
```

### Behind the Scenes (Automatic)

```sql
-- Step 1: Event trigger fires in PostgreSQL
NOTIFY peerdb_create_mirror, '{"schema": "public", "table": "products"}';

-- Step 2: Auto-mirror worker receives notification

-- Step 3: PeerDB mirror is created automatically (with retry if needed)
CREATE MIRROR products_mirror
FROM postgres_main
TO clickhouse_analytics
WITH TABLE MAPPING (public.products:products)
WITH (do_initial_copy = true);

-- Step 4: Data appears in ClickHouse
-- Query to verify:
SELECT * FROM postgres.products;
```

### Querying Synced Data

```bash
# Connect to ClickHouse
docker exec -it echodb-clickhouse-1 clickhouse-client

# Query the synced table
SELECT * FROM postgres.products;

-- Real-time aggregation example
SELECT
  toStartOfInterval(_peerdb_timestamp, INTERVAL '1 hour') AS hour,
  COUNT(*) AS row_count
FROM postgres.products
GROUP BY hour
ORDER BY hour DESC;
```

## Configuration

### Environment Variables

Key environment variables for auto-mirror functionality:

```bash
# PostgreSQL (Source)
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=echodb
POSTGRES_PASSWORD=password
POSTGRES_DB=echodb

# ClickHouse (Target)
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=echodb
CLICKHOUSE_PASSWORD=password
CLICKHOUSE_DATABASE=echodb

# PeerDB
PEERDB_HOST=peerdb
PEERDB_PORT=9900
PEERDB_USER=echodb
PEERDB_PASSWORD=password

# Auto-Mirror Configuration
SOURCE_PEER_NAME=postgres_main      # Source peer name
TARGET_PEER_NAME=clickhouse_analytics  # Target peer name
SYNC_SCHEMA=public                   # Schema to watch
EXCLUDED_TABLES=spatial_ref_sys,geometry_columns  # Tables to skip

# Auto-Mirror Worker - Retry Configuration
MAX_RETRIES=5                       # Maximum retry attempts for mirror creation
RETRY_DELAY=5                       # Initial delay before retry (seconds)
RETRY_BACKOFF=2.0                   # Exponential backoff multiplier

# Auto-Mirror Worker - Reconnect Configuration
RECONNECT_DELAY=10                  # Delay between reconnection attempts (seconds)
MAX_RECONNECT_ATTEMPTS=10           # Maximum reconnection attempts

# Auto-Mirror Worker - Logging Configuration
LOG_LEVEL=INFO                      # Log level (DEBUG, INFO, WARNING, ERROR)
LOG_FILE=/var/log/echodb/auto-mirror.log

# Auto-Mirror Worker - Health Check Configuration
HEALTH_CHECK_PORT=8080              # Port for health check endpoints
HEALTH_CHECK_HOST=0.0.0.0           # Host for health check endpoints
```

### Excluding Tables from Auto-Mirror

Some tables should not be mirrored (e.g., temporary tables, system tables):

```bash
# In .env file
EXCLUDED_TABLES=temp_*,_tmp_*,spatial_ref_sys,geometry_columns
```

## Monitoring

### Health Check Endpoints

Monitor the auto-mirror worker via HTTP endpoints:

```bash
# Check if worker is running (liveness)
curl http://localhost:8080/health

# Check if worker is connected to PostgreSQL (readiness)
curl http://localhost:8080/ready

# Get detailed metrics
curl http://localhost:8080/metrics
```

### PeerDB UI

Manage mirrors visually at http://localhost:3000:

- View all mirrors and their status
- Monitor replication lag
- Check row counts
- View sync errors

### CLI Monitoring

```bash
# Check auto-mirror worker logs
docker logs echodb-auto-mirror-worker-1 -f

# View log file (persistent in volume)
docker exec echodb-auto-mirror-worker-1 cat /var/log/echodb/auto-mirror.log

# Check worker health status
docker exec echodb-auto-mirror-worker-1 python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/metrics').read().decode())"

# Check PeerDB flow worker logs
docker logs echodb-flow-worker-1 -f

# List all mirrors in PeerDB
docker exec -it echodb-peerdb-1 psql -h peerdb -U echodb -d echodb -c "SELECT * FROM peerdb.mirrors;"

# Check ClickHouse for synced tables
docker exec -it echodb-clickhouse-1 clickhouse-client -q "SHOW TABLES FROM postgres"
```

## Troubleshooting

### Mirror Not Created

1. **Check event trigger is installed:**
   ```sql
   SELECT * FROM pg_event_trigger WHERE evtname = 'peerdb_auto_mirror_trigger';
   ```

2. **Check auto-mirror worker is running:**
   ```bash
   docker compose ps auto-mirror-worker
   docker logs echodb-auto-mirror-worker-1 --tail 50
   ```

3. **Check worker health:**
   ```bash
   curl http://localhost:8080/health
   curl http://localhost:8080/ready
   curl http://localhost:8080/metrics
   ```

4. **Verify peers exist in PeerDB:**
   ```bash
   docker exec -it echodb-peerdb-1 psql -h peerdb -U echodb -d echodb -c "SELECT * FROM peerdb.peers;"
   ```

### Data Not Syncing

1. **Check mirror status:**
   ```sql
   SELECT * FROM peerdb.mirrors WHERE mirror_name = 'your_table_mirror';
   ```

2. **Check replication slots:**
   ```bash
   docker exec -it echodb-postgres-1 psql -U echodb -d echodb -c "SELECT * FROM pg_replication_slots;"
   ```

3. **View PeerDB flow worker logs:**
   ```bash
   docker logs echodb-flow-worker-1 -f
   ```

### Worker Not Responding

1. **Check health endpoints:**
   ```bash
   curl -v http://localhost:8080/health
   curl -v http://localhost:8080/ready
   ```

2. **Check worker logs for errors:**
   ```bash
   docker logs echodb-auto-mirror-worker-1 --tail 100
   ```

3. **Restart the worker:**
   ```bash
   docker compose restart auto-mirror-worker
   ```

4. **Verify PostgreSQL connection:**
   ```bash
   docker exec -it echodb-auto-mirror-worker-1 python -c "
   import psycopg2
   conn = psycopg2.connect(
       host='postgres',
       port=5432,
       user='echodb',
       password='password',
       database='echodb'
   )
   print('Connection successful')
   conn.close()
   "
   ```

## Next Steps

- **Initial Setup:** Run `./scripts/install-auto-mirror.sh` to install the event trigger
- **Create Peers:** Set up source (PostgreSQL) and target (ClickHouse) peers in PeerDB
- **Start Services:** Run `docker compose up -d` to start all services including auto-mirror worker
- **Create Tables:** Any new table will be automatically mirrored to ClickHouse
- **Monitor:** Use Temporal UI and PeerDB UI to monitor replication

---

For more information, see the main [README.md](./README.md).
