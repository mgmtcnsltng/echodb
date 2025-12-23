# EchoDB - PostgreSQL to ClickHouse CDC with PeerDB

Complete Docker setup for real-time data replication from PostgreSQL to ClickHouse using PeerDB.

## Features

- **Real-time CDC**: Change Data Capture from PostgreSQL to ClickHouse
- **Auto-Mirror**: Automatically create mirrors for new PostgreSQL tables
- **Environment Configurable**: All important configs via environment variables
- **Production Ready**: Includes Temporal workflow orchestration, MinIO staging, and monitoring

## Quick Start

### 1. Configure Environment

```bash
cd echodb
cp .env.example .env
# Edit .env with your configuration
```

### 2. Start Services

```bash
docker-compose up -d
```

### 3. Setup PeerDB Peers

```bash
./scripts/setup-peerdb.sh
```

### 4. Create Mirrors

**Option A: Manual mirror creation**
```bash
./scripts/create-mirror.sh api_keys
```

**Option B: Auto-mirror (recommended)**
```bash
# Install auto-mirror trigger
./scripts/install-auto-mirror.sh

# Start the worker
POSTGRES_HOST=localhost ./scripts/auto-mirror-worker.py
```

Now when you create a new table in PostgreSQL, it will automatically be mirrored to ClickHouse!

## Architecture

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│  PostgreSQL │────────>│   PeerDB    │────────>│  ClickHouse │
│  (App DB)   │  CDC    │  (ETL/ELT)  │  Sync   │  (Analytics)│
└─────────────┘         └─────────────┘         └─────────────┘
                              │
                              ▼
                       ┌─────────────┐
                       │   MinIO     │
                       │  (Staging)  │
                       └─────────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| PostgreSQL | 5432 | Application database |
| Redis | 6379 | Cache and queue |
| ClickHouse | 8123, 9000 | Analytics database |
| PeerDB Server | 9900 | ETL/ELT SQL interface |
| PeerDB UI | 3000 | Web dashboard |
| Temporal | 7233 | Workflow orchestration |
| Temporal UI | 8085 | Temporal dashboard |
| MinIO | 9001, 9002 | S3-compatible storage |

## Environment Variables

See `.env.example` for all available environment variables. Key variables:

### Database Configuration
- `POSTGRES_DB`: PostgreSQL database name (default: `postgres`)
- `CLICKHOUSE_DATABASE`: ClickHouse database name (default: `default`)
- `CLICKHOUSE_USER`: ClickHouse user (default: `default`)

### PeerDB Configuration
- `PEERDB_PASSWORD`: PeerDB server password (default: `peerdb`)
- `SOURCE_PEER_NAME`: Source peer name (default: `postgres_main`)
- `TARGET_PEER_NAME`: Target peer name (default: `clickhouse_analytics`)

### Auto-Mirror Configuration
- `SYNC_SCHEMA`: Schema to watch (default: `public`)
- `EXCLUDED_TABLES`: Tables to exclude from auto-mirror

## Scripts

| Script | Description |
|--------|-------------|
| `setup-peerdb.sh` | Create PeerDB peers |
| `create-mirror.sh` | Create a mirror for a specific table |
| `install-auto-mirror.sh` | Install PostgreSQL event trigger |
| `auto-mirror-worker.py` | Auto-mirror worker daemon |

## Initialization Scripts

| Script | Description |
|--------|-------------|
| `init-postgres.sql` | PostgreSQL schema setup |
| `init-clickhouse.sql` | ClickHouse analytics tables |
| `init-peerdb-clickhouse.sql` | ClickHouse permissions for PeerDB |

## Usage Examples

### Create a Mirror for a Single Table

```bash
./scripts/create-mirror.sh users
```

### Create Mirrors for Multiple Tables

```bash
# Connect to PeerDB
PGPASSWORD=peerdb psql -h localhost -p 9900 -U postgres

# Create mirror with multiple tables
CREATE MIRROR app_tables_mirror FROM postgres_main TO clickhouse_analytics
WITH TABLE MAPPING
(
  public.users:users,
  public.orders:orders,
  public.products:products
)
WITH (do_initial_copy = true);
```

### Test Auto-Mirror

```bash
# Terminal 1: Start the worker
POSTGRES_HOST=localhost ./scripts/auto-mirror-worker.py

# Terminal 2: Create a new table
docker exec echodb-postgres-1 psql -U postgres -c "
  CREATE TABLE test_table (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT);
"

# Terminal 1: You should see:
# 📢 New table detected: public.test_table
# ✅ Mirror created: test_table_mirror
```

## PeerDB SQL Reference

See `skill.md` for complete PeerDB SQL documentation.

### Common Commands

**Show peers:**
```sql
-- Note: Currently has a bug, use catalog instead
SELECT id, name, type FROM peers;
```

**Show flows/mirrors:**
```sql
-- Note: Currently has a bug, use catalog instead
SELECT id, name, flow_status FROM flows;
```

**Drop mirror:**
```sql
DROP MIRROR mirror_name;
```

**Pause/Resume mirror:**
```sql
PAUSE MIRROR mirror_name;
RESUME MIRROR mirror_name;
```

## Troubleshooting

### PeerDB "SHOW PEERS" Returns Error

This is a known bug. Query the catalog database directly:
```bash
docker exec echodb-catalog-1 psql -U postgres -c "SELECT id, name, type FROM peers;"
```

### Mirror Creation Fails

Check PeerDB logs:
```bash
docker logs echodb-peerdb-1
```

Check ClickHouse connection:
```bash
docker exec echodb-clickhouse-1 clickhouse-client --query "SELECT 1"
```

### Auto-Mirror Worker Not Detecting Tables

1. Verify trigger is installed:
```sql
SELECT * FROM pg_event_trigger WHERE evtname = 'peerdb_auto_mirror_trigger';
```

2. Check worker can connect to PostgreSQL

3. Verify correct schema name in environment variables

### Tables Not Syncing to ClickHouse

1. Check mirror status in catalog database
2. Check flow-worker logs for errors
3. Verify ClickHouse user permissions

## Production Deployment

### Using Docker Auto-Mirror Service

Add to `docker-compose.yml`:

```yaml
  peerdb-auto-mirror:
    build:
      context: .
      dockerfile: Dockerfile.auto-mirror
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
      PEERDB_HOST: peerdb
      PEERDB_PORT: 9900
      PEERDB_PASSWORD: peerdb
    depends_on:
      - postgres
      - peerdb
    restart: unless-stopped
```

Create `Dockerfile.auto-mirror`:

```dockerfile
FROM python:3.13-slim

RUN pip install psycopg2-binary

COPY scripts/auto-mirror-worker.py /app/
WORKDIR /app

CMD ["python", "auto-mirror-worker.py"]
```

## Monitoring

### Check Service Status

```bash
docker-compose ps
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f peerdb
docker-compose logs -f flow-worker
```

### Check Mirror Status via Catalog

```bash
docker exec echodb-catalog-1 psql -U postgres -c "
  SELECT
    f.id,
    f.name,
    f.flow_status,
    f.created_at
  FROM flows f
  ORDER BY f.created_at DESC;
"
```

## Backup and Restore

### Backup PostgreSQL

```bash
docker exec echodb-postgres-1 pg_dump -U postgres postgres > backup.sql
```

### Backup ClickHouse

```bash
docker exec echodb-clickhouse-1 clickhouse-backup create
```

## Security Notes

- Change all default passwords before production deployment
- Use `openssl rand -base64 32` to generate `PEERDB_NEXTAUTH_SECRET`
- Restrict network access to sensitive ports in production
- Use TLS certificates for external connections
- Rotate credentials regularly

## License

This is a configuration template. Refer to individual component licenses:
- PeerDB: Elastic License 2.0 (ELv2)
- PostgreSQL: PostgreSQL License
- ClickHouse: Apache License 2.0
- Temporal: MIT License

## Resources

- [PeerDB Documentation](https://docs.peerdb.io)
- [PeerDB GitHub](https://github.com/PeerDB-io/peerdb)
- [ClickHouse Docs](https://clickhouse.com/docs)
- [Temporal Docs](https://temporal.io/)
